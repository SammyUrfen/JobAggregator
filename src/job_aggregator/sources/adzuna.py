"""Adzuna jobs API (Phase 3). Keys injected from env by the registry; INR for country=in."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from job_aggregator.errors import SourceError
from job_aggregator.sources._http import get_json, make_client, paginate_until_empty
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


def _what_or(cfg: Config) -> str:
    """Space-separated OR query from the configured role keywords (recall over precision — the
    local filter still narrows). Empty roles -> "" -> Adzuna returns generic recent jobs."""
    words = dict.fromkeys(w for role in cfg.keywords.roles for w in role.lower().split())
    return " ".join(words)


class AdzunaSource(Source):
    name = "adzuna"

    def __init__(self, country: str, app_id: str, app_key: str, max_pages: int = 10) -> None:
        self.country = country
        self.app_id = app_id
        self.app_key = app_key
        self.max_pages = max_pages

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        base = f"https://api.adzuna.com/v1/api/jobs/{self.country}/search"
        what_or = _what_or(cfg)
        with make_client() as client:

            def fetch_page(page: int) -> list[Any]:
                params: dict[str, Any] = {
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "results_per_page": _RESULTS_PER_PAGE,
                    "content-type": "application/json",
                    "sort_by": "date",
                }
                if what_or:  # query-target the fetch instead of pulling generic recent jobs
                    params["what_or"] = what_or
                data = get_json(client, f"{base}/{page}", params=params)
                results = data.get("results") if isinstance(data, dict) else None
                return results if isinstance(results, list) else []

            try:
                items = paginate_until_empty(
                    fetch_page, max_pages=self.max_pages, page_size=_RESULTS_PER_PAGE
                )
            except SourceError as exc:
                return SourceResult.failed(self.name, str(exc), duration_ms=elapsed_ms(start))
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
