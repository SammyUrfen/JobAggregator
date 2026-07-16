"""Unstop public JSON source (Phase 3).

India internships/jobs. Loops the configured opportunity kinds, filters on `updated_at` recency
(the API surfaces stale 2022 posts otherwise), and reads status from `subtype` (never `type`).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from functools import partial
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

_URL = "https://unstop.com/api/public/opportunity/search-result"
_PER_PAGE = 30
# Unstop currency icon tokens -> ISO currency.
_UNSTOP_CCY = {"fa-rupee": "INR", "fa-inr": "INR", "fa-dollar": "USD", "fa-usd": "USD"}
# Unstop pay_in tokens -> our normalized period.
_UNSTOP_PERIOD = {"month": "month", "year": "year", "week": "week", "hour": "hour"}


class UnstopSource(Source):
    name = "unstop"

    def __init__(
        self,
        opportunities: list[str],
        search_terms: list[str],
        max_age_days: int,
        max_pages: int = 5,
    ) -> None:
        self.opportunities = opportunities
        self.search_terms = search_terms
        self.max_age_days = max_age_days
        self.max_pages = max_pages

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        cutoff = clock.now() - timedelta(days=self.max_age_days)
        all_items: list[Any] = []
        errors: list[str] = []
        ok = 0
        with make_client() as client:

            def fetch_page(page: int, opp: str) -> list[Any]:
                data = get_json(
                    client, _URL, params={"opportunity": opp, "per_page": _PER_PAGE, "page": page}
                )
                inner = ((data.get("data") or {}).get("data")) if isinstance(data, dict) else None
                return inner if isinstance(inner, list) else []

            for opp in self.opportunities:
                try:
                    # partial binds THIS opp (avoids the late-binding loop-closure trap).
                    items = paginate_until_empty(
                        partial(fetch_page, opp=opp),
                        max_pages=self.max_pages,
                        page_size=_PER_PAGE,
                    )
                except SourceError as exc:
                    errors.append(f"{opp}: {exc}")
                    continue
                ok += 1
                all_items.extend(items)
        if ok == 0:
            return SourceResult.failed(
                self.name, f"all opportunities failed: {errors}", duration_ms=elapsed_ms(start)
            )
        return build_result(
            self.name,
            all_items,
            lambda item: self._map(item, cutoff),
            duration_ms=elapsed_ms(start),
        )

    def _map(self, item: Any, cutoff: datetime) -> RawPosting | None:
        posted = parse_iso(item.get("updated_at")) or parse_iso(item.get("start_date"))
        # Drop stale postings; an unparseable date is kept (can't prove it's old).
        if posted is not None and posted < cutoff:
            return None
        detail = item.get("jobDetail") or {}
        disclosed = detail.get("show_salary") == 1 and not detail.get("not_disclosed")
        org = item.get("organisation") or {}
        return RawPosting(
            source="unstop",
            source_native_id=str(item.get("id")),
            title=str(item.get("title", "")),
            company=str(org.get("name") or item.get("organisation_name") or ""),
            url=str(item.get("public_url") or item.get("seo_url") or ""),
            is_remote=None,
            salary_min=pos_int_or_none(detail.get("min_salary")) if disclosed else None,
            salary_max=pos_int_or_none(detail.get("max_salary")) if disclosed else None,
            salary_currency=_UNSTOP_CCY.get(str(detail.get("currency", "")).lower())
            if disclosed
            else None,
            salary_period=_UNSTOP_PERIOD.get(str(detail.get("pay_in", "")).lower())
            if disclosed
            else None,
            posted_at=posted,
        )
