"""Jobicy remote-jobs API (Phase 3). First-class internship + salary fields."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from job_aggregator.errors import SourceError
from job_aggregator.sources._http import get_json, make_client
from job_aggregator.sources.base import (
    RawPosting,
    Source,
    SourceResult,
    build_result,
    elapsed_ms,
    parse_iso,
    pos_int_or_none,
)

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

_URL = "https://jobicy.com/api/v2/remote-jobs"
_RESULT_COUNT = 50
_JOBICY_PERIOD = {
    "annual": "year",
    "yearly": "year",
    "monthly": "month",
    "hourly": "hour",
    "weekly": "week",
}


class JobicySource(Source):
    name = "jobicy"

    def __init__(self, job_type: str | None = None) -> None:
        self.job_type = job_type

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        with make_client() as client:
            try:
                data = get_json(client, _URL, params={"count": _RESULT_COUNT})
            except SourceError as exc:
                return SourceResult.failed(self.name, str(exc), duration_ms=elapsed_ms(start))
        jobs = data.get("jobs") if isinstance(data, dict) else None
        items = jobs if isinstance(jobs, list) else []
        return build_result(self.name, items, self._map, duration_ms=elapsed_ms(start))

    def _map(self, item: Any) -> RawPosting | None:
        # Client-side job_type filter: Jobicy has no server-side type param. A miss is a
        # legitimate drop (None), not a failure.
        if self.job_type:
            types = item.get("jobType") or []
            wanted = self.job_type.lower()
            if isinstance(types, list) and not any(wanted in str(t).lower() for t in types):
                return None
        return RawPosting(
            source="jobicy",
            source_native_id=str(item.get("id")),
            title=str(item.get("jobTitle", "")),
            company=str(item.get("companyName", "")),
            url=str(item.get("url", "")),
            location=item.get("jobGeo") or "Remote",
            is_remote=True,
            salary_min=pos_int_or_none(item.get("salaryMin")),
            salary_max=pos_int_or_none(item.get("salaryMax")),
            salary_currency=item.get("salaryCurrency"),
            salary_period=_JOBICY_PERIOD.get(str(item.get("salaryPeriod", "")).lower()),
            posted_at=parse_iso(item.get("pubDate")),
        )
