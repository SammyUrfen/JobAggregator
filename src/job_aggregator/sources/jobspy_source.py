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
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, SupportsFloat

from job_aggregator.models.job import Job
from job_aggregator.pipeline import signals
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
# "html" (not "markdown") so every source feeds the dashboard's one HTML renderer; markdown would
# otherwise render as literal "**bold**"/"- item" text in the detail modal.
_DESCRIPTION_FORMAT = "html"
_INTERVAL_TO_PERIOD = {"yearly": "year", "annual": "year", "monthly": "month", "hourly": "hour"}
# jobspy builds an Indeed location from city, state code and ISO country code ("KA, IN"). The
# location gate matches whole tokens against names such as "India", so "IN" never matched and
# only jobspy's false remote flag let these rows through (audit 2026-09-15). Indeed only: on
# LinkedIn a trailing "IN" is the US state Indiana ("Indianapolis, IN").
# Indeed ignores hours_old when job_type is set, so it returns postings from months ago: a live run
# on 2026-09-15 stored Indeed internships posted 3, 7 and 10 months earlier. A posting older than
# this is dropped at ingest (an undated one stays: silence is never a reason to drop).
_MAX_POSTING_AGE = timedelta(days=30)
_INDEED_INDIA_CODE = "IN"
_INDIA = "India"


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
    if jc.job_type:
        kwargs["job_type"] = jc.job_type
        if site == "indeed":
            # jobspy limitation (indeed/__init__.py): Indeed cannot combine hours_old with a
            # job_type/is_remote filter — the type filter wins here (an internship hunt needs
            # typed results more than a freshness window; dedup absorbs the re-fetch overlap).
            kwargs.pop("hours_old")
    if site == "linkedin" and jc.linkedin_fetch_description:
        # Without this LinkedIn rows carry NO description, so the must_have/role gates ran
        # title-only and killed generic "Software Intern" titles. One extra request per job.
        kwargs["linkedin_fetch_description"] = True
    if site in _SITES_REQUIRING_COUNTRY:
        kwargs["country_indeed"] = jc.country_indeed
    if jc.is_remote and site not in _SITES_NO_IS_REMOTE:
        kwargs["is_remote"] = True
    if jc.proxies:
        kwargs["proxies"] = jc.proxies
    return kwargs


def _indeed_location(location: str | None) -> str | None:
    """Indeed's trailing country code IN -> India ("KA, IN" -> "KA, India")."""
    if location is None:
        return None
    head, sep, code = location.rpartition(", ")
    return f"{head}{sep}{_INDIA}" if code == _INDEED_INDIA_CODE else location


def _map_salary(row: Any, cfg: Config, description: str | None) -> dict[str, Any]:
    """Salary fields for a Job: normalized INR/month when convertible, else the monthly INR pay
    the description states (0 = unpaid), else raw-only + unparsed."""
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
    # The audit found 0 of 414 jobspy rows with usable structured pay, while the fetched JD text
    # states it ("Stipend: ₹30,000 - ₹50,000 per month", "unpaid"). Without this every row sat in
    # 'unknown' and the stipend floor could never act. Unpaid (0) is set here directly: the
    # min = max = 0 INR/month shape is the contract salary_bucket FAILs on.
    stated = signals.stated_monthly_pay_inr(description)
    if stated is not None:
        return {
            "salary_min": stated,
            "salary_max": stated,
            "salary_currency": "INR",
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
    too_old: int = 0
    last_error: str | None = None

    @property
    def succeeded(self) -> bool:
        # Suspicious-empty is per-site: a site that produced no usable jobs did NOT "succeed",
        # so the runner leaves its previously-seen jobs untouched. A posting dropped for its age
        # still proves the site answered with real rows.
        return self.jobs > 0 or self.too_old > 0


class JobSpySource(Source):
    name = "jobspy"

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        jc = cfg.sources.jobspy
        cutoff = clock.now() - _MAX_POSTING_AGE
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
                    if job.posted_at is not None and job.posted_at < cutoff:
                        st.too_old += 1
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
            # results_wanted + hours_old make every jobspy fetch a WINDOW, never the site's
            # complete view — its jobs must age out, not be deleted on absence (see stale.py).
            exhaustive=False,
        )

    def _row_to_job(self, row: Any, site: str, cfg: Config) -> Job | None:
        title = _clean_str(row.get("title"))
        company = _clean_str(row.get("company"))
        url = _clean_str(row.get("job_url"))
        if not title or not company or not url:
            return None  # required fields missing -> drop the row
        raw_location = _clean_str(row.get("location"))
        location = _indeed_location(raw_location) if site == "indeed" else raw_location
        description = _clean_str(row.get("description"))
        # jobspy's own is_remote is a bare substring test ("no remote", "remote diagnostics" count
        # as remote): 4 of 11 audited remote LinkedIn rows were on-site. Only a stated mode counts.
        mode = signals.work_mode("\n".join(filter(None, (title, description, location))))
        job = Job(
            # Hashed on the RAW location so the Indeed expansion keeps existing job_uids, and
            # with them the owner's seen/applied marks.
            job_uid=content_hash(company, title, raw_location or ""),
            source=f"jobspy_{site}",
            source_native_id=None,
            title=title,
            company=company,
            location=location,
            is_remote=signals.remote_flag(mode),
            url=canonical_url(url),
            description=description,
            posted_at=_clean_dt(row.get("date_posted")),
            **_map_salary(row, cfg, description),
        )
        job.salary_bucket = salary_bucket(job, cfg)  # Job is mutable
        return job
