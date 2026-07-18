"""Himalayas remote-jobs API (Phase 3). Coverage, not freshness (~24h refresh)."""

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
    from_epoch_seconds,
    pos_int_or_none,
)

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

_URL = "https://himalayas.app/jobs/api/search"
_RESULT_LIMIT = 100
# Himalayas sits behind Cloudflare that 403-challenges a realistic Chrome UA but lets a bare
# "Mozilla/5.0" through. Best-effort: if Cloudflare tightens this, the source fails gracefully and
# (thanks to the per-source stale guard) leaves its existing jobs untouched.
_HIMALAYAS_UA = "Mozilla/5.0"
# Himalayas salaryPeriod tokens -> our normalized period.
_HIMA_PERIOD = {
    "annual": "year",
    "yearly": "year",
    "monthly": "month",
    "hourly": "hour",
    "weekly": "week",
}


class HimalayasSource(Source):
    name = "himalayas"

    def __init__(self, country: str = "IN") -> None:
        self.country = country

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        with make_client(user_agent=_HIMALAYAS_UA) as client:
            try:
                data = get_json(
                    client, _URL, params={"country": self.country.upper(), "limit": _RESULT_LIMIT}
                )
            except SourceError as exc:
                return SourceResult.failed(self.name, str(exc), duration_ms=elapsed_ms(start))
        jobs = data.get("jobs") if isinstance(data, dict) else None
        items = jobs if isinstance(jobs, list) else []
        # limit=100: fewer items than the limit means we saw the full country inventory.
        return build_result(
            self.name,
            items,
            self._map,
            duration_ms=elapsed_ms(start),
            exhaustive=len(items) < _RESULT_LIMIT,
        )

    @staticmethod
    def _map(item: Any) -> RawPosting:
        locs = item.get("locationRestrictions")
        location = locs[0] if isinstance(locs, list) and locs else "Remote"
        return RawPosting(
            source="himalayas",
            source_native_id=str(item.get("guid")),
            title=str(item.get("title", "")),
            company=str(item.get("companyName", "")),
            url=str(item.get("applicationLink", "")),
            location=location,
            is_remote=True,
            # The API ships a full HTML description (+excerpt); discarding it made the
            # must_have gate run title-only and false-drop real matches (verified live).
            description=item.get("description") or item.get("excerpt"),
            salary_min=pos_int_or_none(item.get("minSalary")),
            salary_max=pos_int_or_none(item.get("maxSalary")),
            salary_currency=item.get("currency"),
            salary_period=_HIMA_PERIOD.get(str(item.get("salaryPeriod", "")).lower()),
            posted_at=from_epoch_seconds(item.get("pubDate")),
        )
