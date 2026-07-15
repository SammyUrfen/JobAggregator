"""Jooble API (Phase 3). Key injected from env by the registry; POST JSON body."""

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
)

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config


class JoobleSource(Source):
    name = "jooble"

    def __init__(self, api_key: str, keywords: str, location: str) -> None:
        self.api_key = api_key
        self.keywords = keywords
        self.location = location

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        url = f"https://jooble.org/api/{self.api_key}"
        body = {"keywords": self.keywords, "location": self.location}
        with make_client() as client:
            try:
                data = get_json(client, url, method="POST", json_body=body)
            except SourceError as exc:
                return SourceResult.failed(self.name, str(exc), duration_ms=elapsed_ms(start))
        jobs = data.get("jobs") if isinstance(data, dict) else None
        items = jobs if isinstance(jobs, list) else []
        return build_result(self.name, items, self._map, duration_ms=elapsed_ms(start))

    def _map(self, item: Any) -> RawPosting:
        return RawPosting(
            source="jooble",
            source_native_id=str(item.get("id")),
            title=str(item.get("title", "")),
            company=str(item.get("company") or self.keywords),
            url=str(item.get("link", "")),
            location=item.get("location"),
            is_remote=None,
            description=item.get("snippet"),
            # Jooble rarely exposes structured pay; leave salary unparsed.
            posted_at=parse_iso(item.get("updated")),
        )
