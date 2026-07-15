"""Adzuna jobs API (Phase 3). Keys injected from env by the registry; INR for country=in."""

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

_RESULTS_PER_PAGE = 50
# Adzuna localizes pay to the country's currency; map the ISO country to it.
_ADZUNA_CCY = {"in": "INR", "gb": "GBP", "us": "USD", "au": "AUD", "ca": "CAD", "de": "EUR"}


class AdzunaSource(Source):
    name = "adzuna"

    def __init__(self, country: str, app_id: str, app_key: str) -> None:
        self.country = country
        self.app_id = app_id
        self.app_key = app_key

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        url = f"https://api.adzuna.com/v1/api/jobs/{self.country}/search/1"
        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "results_per_page": _RESULTS_PER_PAGE,
            "content-type": "application/json",
            "sort_by": "date",
        }
        with make_client() as client:
            try:
                data = get_json(client, url, params=params)
            except SourceError as exc:
                return SourceResult.failed(self.name, str(exc), duration_ms=elapsed_ms(start))
        results = data.get("results") if isinstance(data, dict) else None
        items = results if isinstance(results, list) else []
        return build_result(self.name, items, self._map, duration_ms=elapsed_ms(start))

    def _map(self, item: Any) -> RawPosting:
        company = (item.get("company") or {}).get("display_name", "")
        location = (item.get("location") or {}).get("display_name")
        return RawPosting(
            source="adzuna",
            source_native_id=str(item.get("id")),
            title=str(item.get("title", "")),
            company=str(company),
            url=str(item.get("redirect_url", "")),
            location=location,
            is_remote=None,
            description=item.get("description"),
            salary_min=pos_int_or_none(item.get("salary_min")),
            salary_max=pos_int_or_none(item.get("salary_max")),
            salary_currency=_ADZUNA_CCY.get(self.country.lower()),
            salary_period="year",
            posted_at=parse_iso(item.get("created")),
        )
