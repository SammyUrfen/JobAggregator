"""Tier A: python-jobspy wrapper (Phase 4).

One JobSpySource drives `jobspy.scrape_jobs` across cfg.sources.jobspy.sites x search_terms,
converts the returned pandas DataFrame rows -> normalized Job objects tagged
`source="jobspy_<site>"`, and reports per-site success via `SourceResult.sub_results` so the
stale-delete guard is per-site (a LinkedIn 429 must NOT zero out Naukri). `fetch()` never raises.

The `jobspy`/`pandas` imports are lazy (inside `_scrape_jobs`, the ONE seam tests monkeypatch)
so the CLI stays importable without the heavy deps, and unit tests need no network.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, SupportsFloat, SupportsInt

from job_aggregator.models.job import Job
from job_aggregator.pipeline.dedup import canonical_url, content_hash
from job_aggregator.pipeline.salary import salary_bucket, to_inr_month
from job_aggregator.sources.base import Source, SourceResult, parse_iso

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config, JobSpyConfig

log = logging.getLogger(__name__)

# Indeed/Glassdoor require country_indeed; Indeed silently drops other filters if is_remote is
# also sent, so omit is_remote for it.
_SITES_REQUIRING_COUNTRY = frozenset({"indeed", "glassdoor"})
_SITES_NO_IS_REMOTE = frozenset({"indeed"})
_VERBOSE = 1
_DESCRIPTION_FORMAT = "markdown"
_INTERVAL_TO_PERIOD = {"yearly": "year", "annual": "year", "monthly": "month", "hourly": "hour"}
_TRUE_STRINGS = frozenset({"true", "1", "yes"})
_FALSE_STRINGS = frozenset({"false", "0", "no"})


def _is_missing(value: object) -> bool:
    """True for None or a NaN/NaT sentinel (self-inequality; no pandas import needed)."""
    return value is None or value != value  # noqa: PLR0124 - NaN != NaN is the intended check


def _clean_str(value: object) -> str | None:
    if _is_missing(value):
        return None
    s = str(value).strip()
    return s or None


def _clean_float(value: object) -> float | None:
    if _is_missing(value) or isinstance(value, bool):
        return None
    if isinstance(value, SupportsFloat):  # int/float and numpy scalars
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _str_to_bool(value: str) -> bool | None:
    v = value.strip().lower()
    if v in _TRUE_STRINGS:
        return True
    if v in _FALSE_STRINGS:
        return False
    return None


def _clean_bool(value: object) -> bool | None:
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _str_to_bool(value)
    if isinstance(value, SupportsInt):  # numpy bool_/int
        return bool(int(value))
    return None


def _clean_dt(value: object) -> datetime | None:
    """pandas Timestamp (a datetime subclass) / date / ISO string -> aware UTC datetime."""
    if _is_missing(value):
        return None
    if isinstance(value, datetime):  # includes pandas Timestamp
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        return parse_iso(value)  # shared ISO parser (base)
    return None


def _salary_raw_repr(
    mn: float | None, mx: float | None, ccy: str | None, interval: str | None
) -> str | None:
    """A human-readable original-salary string for auditing, e.g. "USD 60000-90000/yearly"."""
    if mn is None and mx is None:
        return None
    if mn is not None and mx is not None:
        amount = f"{round(mn)}-{round(mx)}"
    else:
        single = mn if mn is not None else mx
        assert single is not None  # exactly one bound is set here
        amount = str(round(single))
    prefix = f"{ccy} " if ccy else ""
    suffix = f"/{interval}" if interval else ""
    return f"{prefix}{amount}{suffix}"


def _scrape_jobs(**kwargs: Any) -> Any:
    """The ONE seam tests monkeypatch. Imports jobspy lazily (heavy dep)."""
    from jobspy import scrape_jobs

    return scrape_jobs(**kwargs)


def _build_scrape_kwargs(site: str, term: str, jc: JobSpyConfig) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "site_name": [site],
        "search_term": term,
        "location": jc.location,
        "results_wanted": jc.results_wanted,
        "hours_old": jc.hours_old,
        "description_format": _DESCRIPTION_FORMAT,
        "verbose": _VERBOSE,
    }
    if site in _SITES_REQUIRING_COUNTRY:
        kwargs["country_indeed"] = jc.country_indeed
    if jc.is_remote and site not in _SITES_NO_IS_REMOTE:
        kwargs["is_remote"] = True
    if jc.proxies:
        kwargs["proxies"] = jc.proxies
    return kwargs


def _map_salary(row: Any, cfg: Config) -> dict[str, Any]:
    """Salary fields for a Job: normalized INR/month when convertible, else raw-only + unparsed."""
    interval = _clean_str(row.get("interval"))
    currency = _clean_str(row.get("currency"))
    min_raw = _clean_float(row.get("min_amount"))
    max_raw = _clean_float(row.get("max_amount"))
    raw_repr = _salary_raw_repr(min_raw, max_raw, currency, interval)
    period = _INTERVAL_TO_PERIOD.get(interval.lower()) if interval else None
    ccy = currency.upper() if currency else None
    base = cfg.salary.currency.upper()
    known = {base, *(k.upper() for k in cfg.salary.fx_rates)}
    if period and ccy in known and (min_raw is not None or max_raw is not None):
        rates = cfg.salary.fx_rates
        s_min = to_inr_month(round(min_raw), ccy, period, rates) if min_raw is not None else None
        s_max = to_inr_month(round(max_raw), ccy, period, rates) if max_raw is not None else None
        return {
            "salary_min": s_min,
            "salary_max": s_max,
            "salary_currency": base,
            "salary_period": "month",
            "salary_raw": raw_repr,
            "salary_parsed": True,
        }
    return {"salary_raw": raw_repr, "salary_parsed": False}


@dataclass
class _SiteStat:
    calls: int = 0
    errors: int = 0
    rows: int = 0
    jobs: int = 0
    last_error: str | None = None

    @property
    def succeeded(self) -> bool:
        # Suspicious-empty is per-site: a site that produced no usable jobs did NOT "succeed",
        # so the runner leaves its previously-seen jobs untouched.
        return self.jobs > 0


class JobSpySource(Source):
    name = "jobspy"

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        # clock is unused: jobspy windows by hours_old, not an injected now.
        jc = cfg.sources.jobspy
        started = time.monotonic()
        if not jc.sites or not jc.search_terms:
            return SourceResult(source=self.name, succeeded=True, jobs=[], n_fetched=0)
        stats = {site: _SiteStat() for site in jc.sites}
        seen: set[tuple[str, str]] = set()
        all_jobs: list[Job] = []
        for site in jc.sites:
            st = stats[site]
            for term in jc.search_terms:
                st.calls += 1
                try:
                    df = _scrape_jobs(**_build_scrape_kwargs(site, term, jc))
                except Exception as exc:  # fetch never raises: record the site error + continue
                    st.errors += 1
                    st.last_error = f"{type(exc).__name__}: {exc}"
                    log.warning("jobspy %s/%r failed: %s", site, term, exc)
                    continue
                rows = [] if df is None else df.to_dict(orient="records")
                st.rows += len(rows)
                for row in rows:
                    job = self._row_to_job(row, site, cfg)
                    if job is None:
                        continue
                    key = (site, job.job_uid)
                    if key in seen:
                        continue
                    seen.add(key)
                    all_jobs.append(job)
                    st.jobs += 1
        elapsed = int((time.monotonic() - started) * 1000)
        subs = [(f"jobspy_{site}", stats[site].succeeded, stats[site].jobs) for site in jc.sites]
        failed = [
            f"jobspy_{site}: {stats[site].last_error or 'empty'}"
            for site in jc.sites
            if not stats[site].succeeded
        ]
        return SourceResult(
            source=self.name,
            succeeded=any(st.succeeded for st in stats.values()),
            jobs=all_jobs,
            n_fetched=len(all_jobs),
            duration_ms=elapsed,
            error="; ".join(failed) or None,
            sub_results=subs,
        )

    def _row_to_job(self, row: Any, site: str, cfg: Config) -> Job | None:
        title = _clean_str(row.get("title"))
        company = _clean_str(row.get("company"))
        url = _clean_str(row.get("job_url"))
        if not title or not company or not url:
            return None  # required fields missing -> drop the row
        location = _clean_str(row.get("location"))
        job = Job(
            job_uid=content_hash(company, title, location or ""),
            source=f"jobspy_{site}",
            source_native_id=None,
            title=title,
            company=company,
            location=location,
            is_remote=_clean_bool(row.get("is_remote")),
            url=canonical_url(url),
            description=_clean_str(row.get("description")),
            posted_at=_clean_dt(row.get("date_posted")),
            **_map_salary(row, cfg),
        )
        job.salary_bucket = salary_bucket(job, cfg)  # Job is mutable
        return job
