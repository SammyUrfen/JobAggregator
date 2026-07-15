"""Greenhouse ATS boards-api (Phase 3). Per-token loop, no auth, no salary in the listing."""

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


class GreenhouseSource(Source):
    name = "greenhouse"

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        with make_client() as client:
            result = run_ats(self.name, self.tokens, self._fetch_one, client)
        result.duration_ms = elapsed_ms(start)
        return result

    def _fetch_one(self, client: httpx.Client, token: str) -> list[RawPosting]:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        data = get_json(client, url)  # raises SourceError on 404 (invalid token) etc.
        jobs = data.get("jobs") if isinstance(data, dict) else None
        items = jobs if isinstance(jobs, list) else []
        return [self._map(it, token) for it in items]

    @staticmethod
    def _map(item: Any, token: str) -> RawPosting:
        location = (item.get("location") or {}).get("name") or ""
        return RawPosting(
            source="greenhouse",
            source_native_id=str(item.get("id")),
            title=str(item.get("title", "")),
            company=str(item.get("company_name") or token),
            url=str(item.get("absolute_url", "")),
            location=location or None,
            is_remote=True if "remote" in str(location).lower() else None,
            description=item.get("content"),
            posted_at=parse_iso(item.get("updated_at")) or parse_iso(item.get("first_published")),
        )
