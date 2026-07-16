"""SmartRecruiters ATS postings (Phase 3). Per-company-id loop; supports ?country=in."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from job_aggregator.sources._http import get_json, make_client
from job_aggregator.sources.base import (
    RawPosting,
    Source,
    SourceResult,
    elapsed_ms,
    parse_iso,
    run_ats,
)

if TYPE_CHECKING:
    import httpx

    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

_RESULT_LIMIT = 100


class SmartRecruitersSource(Source):
    name = "smartrecruiters"

    def __init__(self, company_ids: list[str], country: str = "in") -> None:
        self.company_ids = company_ids
        self.country = country

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        with make_client() as client:
            result = run_ats(self.name, self.company_ids, self._fetch_one, client)
        result.duration_ms = elapsed_ms(start)
        return result

    def _fetch_one(self, client: httpx.Client, company_id: str) -> list[RawPosting]:
        url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
        data = get_json(
            client, url, params={"country": self.country.lower(), "limit": _RESULT_LIMIT}
        )
        content = data.get("content") if isinstance(data, dict) else None
        items = content if isinstance(content, list) else []
        return [self._map(it, company_id) for it in items]

    @staticmethod
    def _map(item: Any, company_id: str) -> RawPosting:
        loc = item.get("location") or {}
        parts = [loc.get("city"), loc.get("region"), loc.get("country")]
        location = ", ".join(str(p) for p in parts if p) or None
        return RawPosting(
            source=f"smartrecruiters_{company_id}",  # per-company tag -> per-company stale guard
            source_native_id=str(item.get("id")),
            title=str(item.get("name", "")),
            company=str((item.get("company") or {}).get("name") or company_id),
            url=str(item.get("ref", "")),
            location=location,
            is_remote=bool(loc.get("remote")),
            posted_at=parse_iso(item.get("releasedDate")),
        )
