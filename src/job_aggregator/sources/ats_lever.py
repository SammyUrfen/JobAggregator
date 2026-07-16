"""Lever ATS postings API (Phase 3). Per-slug loop; workplaceType is the remote signal."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from job_aggregator.errors import SourceError
from job_aggregator.sources._http import get_json, make_client
from job_aggregator.sources.base import (
    RawPosting,
    Source,
    SourceResult,
    elapsed_ms,
    from_epoch_millis,
    pos_int_or_none,
    run_ats,
)

if TYPE_CHECKING:
    import httpx

    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

# Lever workplaceType -> tri-state remote flag.
_LEVER_REMOTE = {"remote": True, "onsite": False, "hybrid": False}
# Lever salaryRange.interval -> our normalized period.
_LEVER_PERIOD = {
    "per-year-salary": "year",
    "per-month-salary": "month",
    "per-week-salary": "week",
    "per-hour-salary": "hour",
}


class LeverSource(Source):
    name = "lever"

    def __init__(self, slugs: list[str]) -> None:
        self.slugs = slugs

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        with make_client() as client:
            result = run_ats(self.name, self.slugs, self._fetch_one, client)
        result.duration_ms = elapsed_ms(start)
        return result

    def _fetch_one(self, client: httpx.Client, slug: str) -> list[RawPosting]:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        data = get_json(client, url)
        # An invalid slug returns 200 with {"ok": false, ...} instead of an array.
        if isinstance(data, dict) and data.get("ok") is False:
            raise SourceError(f"lever slug not found: {slug}", details={"slug": slug})
        items = data if isinstance(data, list) else []
        return [self._map(it, slug) for it in items]

    @staticmethod
    def _map(item: Any, slug: str) -> RawPosting:
        wt = item.get("workplaceType")
        is_remote = _LEVER_REMOTE.get(str(wt).lower()) if wt else None
        sr = item.get("salaryRange") or {}
        categories = item.get("categories") or {}
        return RawPosting(
            source=f"lever_{slug}",  # per-company tag -> per-company stale guard
            source_native_id=str(item.get("id")),
            title=str(item.get("text", "")),
            company=slug,
            url=str(item.get("hostedUrl", "")),
            location=categories.get("location"),
            is_remote=is_remote,
            description=item.get("description"),
            salary_min=pos_int_or_none(sr.get("min")),
            salary_max=pos_int_or_none(sr.get("max")),
            salary_currency=sr.get("currency"),
            salary_period=_LEVER_PERIOD.get(str(sr.get("interval", "")).lower()),
            posted_at=from_epoch_millis(item.get("createdAt")),
        )
