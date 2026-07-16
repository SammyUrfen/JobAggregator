"""The Source contract + shared normalization helpers (Phase 3). See PLAN §3.

Golden rule: `fetch()` NEVER raises. Any failure (exception, non-2xx, network error, or a
"suspicious empty" from a normally-populated source) becomes `SourceResult(succeeded=False)`.
A failed source is one we "couldn't see", so the stale-delete pass leaves its jobs untouched —
the correctness crux (PLAN §4.5). Dedup identity is imported from pipeline.dedup (single source
of truth); base.py does NOT re-implement it.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from job_aggregator.errors import SourceError
from job_aggregator.models.job import Job
from job_aggregator.pipeline.dedup import canonical_url, content_hash
from job_aggregator.pipeline.normalize import clean_text

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

# ATS partial-success policy: False = a source succeeds if >=1 company fetched OK (coverage
# beats strictness for a personal feed); True = succeed only if EVERY company fetched OK.
ATS_REQUIRE_ALL_COMPANIES = False


@dataclass(slots=True)
class RawPosting:
    """A source record after field-mapping but before Job assembly. Salary amounts are RAW
    (source currency/period); the runner buckets after Phase 3."""

    source: str
    title: str
    company: str
    url: str
    source_native_id: str | None = None
    location: str | None = None
    is_remote: bool | None = None
    description: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # 'year' | 'month' | 'week' | 'day' | 'hour' | None
    posted_at: datetime | None = None  # parsed aware-datetime


@dataclass
class SourceResult:
    """The outcome of one source fetch within a cycle. `sub_results` lets one adapter report
    multiple logical sub-sources (per-site/per-company) so the stale-guard is fine-grained."""

    source: str
    succeeded: bool
    jobs: list[Job] = field(default_factory=list)
    n_fetched: int = 0
    duration_ms: int = 0
    error: str | None = None
    sub_results: list[tuple[str, bool, int]] = field(default_factory=list)

    @classmethod
    def ok(cls, source: str, jobs: list[Job], *, duration_ms: int = 0) -> SourceResult:
        return cls(
            source=source, succeeded=True, jobs=jobs, n_fetched=len(jobs), duration_ms=duration_ms
        )

    @classmethod
    def failed(
        cls, source: str, error: str, *, jobs: list[Job] | None = None, duration_ms: int = 0
    ) -> SourceResult:
        j = jobs or []
        return cls(
            source=source,
            succeeded=False,
            jobs=j,
            n_fetched=len(j),
            duration_ms=duration_ms,
            error=error,
        )


class Source(ABC):
    """Base class for all sources. `name` is the stable id used in jobs.source + source_runs."""

    #: stable identifier, e.g. "greenhouse", "remoteok", "unstop"
    name: ClassVar[str]

    @abstractmethod
    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        """Fetch, normalize, and return this source's jobs. Must not raise."""
        raise NotImplementedError


def to_job(raw: RawPosting) -> Job:
    """Assemble a Job from a RawPosting. `job_uid` is the cross-source content hash; the URL is
    canonicalized; `salary_bucket` is left None (the runner sets it uniformly before filtering)."""
    return Job(
        job_uid=content_hash(raw.company, raw.title, raw.location),
        source=raw.source,
        source_native_id=raw.source_native_id,
        title=clean_text(raw.title) or raw.title.strip(),
        company=clean_text(raw.company) or raw.company.strip(),
        location=raw.location,
        is_remote=raw.is_remote,
        url=canonical_url(raw.url),
        description=raw.description,
        salary_min=raw.salary_min,
        salary_max=raw.salary_max,
        salary_currency=raw.salary_currency,
        salary_period=raw.salary_period,
        salary_parsed=(raw.salary_min is not None or raw.salary_max is not None),
        posted_at=raw.posted_at,
    )


# ── Parse helpers adapters call (all tolerant; never raise) ──────────────────────────────


def elapsed_ms(start_perf: float) -> int:
    """Milliseconds since a time.perf_counter() reading (for SourceResult.duration_ms)."""
    return int((time.perf_counter() - start_perf) * 1000)


def _digit_str(value: object) -> bool:
    return isinstance(value, str) and value.strip().lstrip("-").isdigit()


def from_epoch_seconds(value: object) -> datetime | None:
    """Epoch seconds (int/float/digit-string) -> aware UTC datetime, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if _digit_str(value):
        return from_epoch_seconds(int(str(value).strip()))
    return None


def from_epoch_millis(value: object) -> datetime | None:
    """Epoch milliseconds -> aware UTC datetime, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return from_epoch_seconds(float(value) / 1000.0)
    if _digit_str(value):
        return from_epoch_millis(int(str(value).strip()))
    return None


def parse_iso(value: object) -> datetime | None:
    """ISO-8601 string (trailing 'Z' or offset ok) -> aware UTC datetime, or None."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def pos_int_or_none(value: object) -> int | None:
    """A strictly-positive integer, else None (0, '0', None, non-numeric all map to None)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        iv = int(value)
        return iv if iv > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        iv = int(value.strip())
        return iv if iv > 0 else None
    return None


def build_result(
    source: str,
    raw_items: list[Any],
    mapper: Callable[[Any], RawPosting | None],
    *,
    duration_ms: int = 0,
) -> SourceResult:
    """Tier-B builder encoding the suspicious-empty rule.

    `raw_items` = REAL postings (structural noise pre-stripped). Empty -> succeeded=False (a
    populated feed returning nothing is suspicious, so Phase 5 won't expire its jobs). A mapper
    returning None drops a legitimately-filtered posting (still succeeded=True).
    """
    if not raw_items:
        return SourceResult.failed(
            source, "suspicious empty: source returned 0 items", duration_ms=duration_ms
        )
    jobs = [to_job(r) for item in raw_items if (r := mapper(item)) is not None]
    return SourceResult.ok(source, jobs, duration_ms=duration_ms)


def ats_sub_name(source: str, company: str) -> str:
    """The per-company (sub-)source id, e.g. 'greenhouse_razorpay'. Each company's jobs are
    tagged with this so the stale-delete guard is per-company (a transient 500 on one board
    must not expire another company's still-valid postings) — the same isolation JobSpy gets
    per-site. Adapters MUST tag Job.source identically."""
    return f"{source}_{company}"


def run_ats(
    source: str,
    companies: list[str],
    fetch_one: Callable[[Any, str], list[RawPosting]],
    client: Any,
) -> SourceResult:
    """ATS per-company loop with per-company failure isolation. A per-company EMPTY result is
    legitimate (no openings); only an ERROR (SourceError) marks a company failed. Emits one
    sub_results entry per company so expire_stale only touches companies that succeeded."""
    jobs: list[Job] = []
    failed: list[str] = []
    sub_results: list[tuple[str, bool, int]] = []
    ok = 0
    for company in companies:
        sub_name = ats_sub_name(source, company)
        try:
            raws = fetch_one(client, company)
        except SourceError as exc:
            failed.append(f"{company}: {exc}")
            sub_results.append((sub_name, False, 0))
            continue
        ok += 1
        company_jobs = [to_job(r) for r in raws]
        jobs.extend(company_jobs)
        sub_results.append((sub_name, True, len(company_jobs)))
    error = "; ".join(failed) or None
    if ATS_REQUIRE_ALL_COMPANIES and failed:
        return SourceResult.failed(source, f"strict mode: {len(failed)} failed: {failed}")
    if ok == 0:
        return SourceResult.failed(source, f"all {len(companies)} companies failed: {failed}")
    return SourceResult(
        source=source,
        succeeded=True,
        jobs=jobs,
        n_fetched=len(jobs),
        error=error,
        sub_results=sub_results,
    )
