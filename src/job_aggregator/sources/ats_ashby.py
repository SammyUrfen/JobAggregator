"""Ashby ATS posting-api (Phase 3). Per-org loop; org is case-sensitive; compensation optional."""

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
    pos_int_or_none,
    run_ats,
)

if TYPE_CHECKING:
    import httpx

    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

_PERIOD_UNITS = ("year", "month", "week", "day", "hour")


def _ashby_period(value: str) -> str | None:
    """Ashby interval like "1 YEAR"/"1 HOUR" -> our normalized period token."""
    v = value.strip().lower()
    return next((unit for unit in _PERIOD_UNITS if unit in v), None)


class AshbySource(Source):
    name = "ashby"

    def __init__(self, orgs: list[str]) -> None:
        self.orgs = orgs

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        with make_client() as client:
            result = run_ats(self.name, self.orgs, self._fetch_one, client)
        result.duration_ms = elapsed_ms(start)
        return result

    def _fetch_one(self, client: httpx.Client, org: str) -> list[RawPosting]:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"
        data = get_json(client, url)
        jobs = data.get("jobs") if isinstance(data, dict) else None
        items = jobs if isinstance(jobs, list) else []
        # Keep listed postings only (isListed absent is treated as listed).
        listed = [it for it in items if it.get("isListed") is not False]
        return [self._map(it, org) for it in listed]

    @staticmethod
    def _map(item: Any, org: str) -> RawPosting:
        smin = smax = None
        ccy = period = None
        comp = item.get("compensation") or {}
        for comp_part in comp.get("summaryComponents") or []:
            if comp_part.get("compensationType") == "Salary":
                smin = pos_int_or_none(comp_part.get("minValue"))
                smax = pos_int_or_none(comp_part.get("maxValue"))
                ccy = comp_part.get("currencyCode")
                period = _ashby_period(str(comp_part.get("interval", "")))
                break
        return RawPosting(
            source=f"ashby_{org}",  # per-company tag -> per-company stale guard
            source_native_id=str(item.get("id")),
            title=str(item.get("title", "")),
            company=str(item.get("organizationName") or org),
            url=str(item.get("jobUrl") or item.get("applyUrl") or ""),
            location=item.get("location"),
            is_remote=bool(item.get("isRemote")),
            description=item.get("descriptionHtml"),
            salary_min=smin,
            salary_max=smax,
            salary_currency=ccy,
            salary_period=period,
            posted_at=parse_iso(item.get("publishedAt")),
        )
