"""Jooble API (Phase 3). Key injected from env by the registry; POST JSON body."""

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
)

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config


_PAGES_PER_QUERY = 3  # bound total requests: Jooble is queried once per role term (see registry).


class JoobleSource(Source):
    name = "jooble"

    def __init__(self, api_key: str, queries: list[str], location: str, max_pages: int = 5) -> None:
        self.api_key = api_key
        # One Jooble query per role term. A comma-joined dump of every role returns 0 results;
        # Jooble matches a single "backend engineer"-style phrase far better (verified live).
        self.queries = queries
        self.location = location
        self.max_pages = max_pages

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        url = f"https://jooble.org/api/{self.api_key}"
        pages = min(self.max_pages, _PAGES_PER_QUERY)
        seen: set[str] = set()
        items: list[Any] = []
        exhausted = True
        with make_client() as client:
            for query in self.queries:

                def fetch_page(page: int, query: str = query) -> list[Any]:
                    body = {"keywords": query, "location": self.location, "page": page}
                    data = get_json(client, url, method="POST", json_body=body)
                    jobs = data.get("jobs") if isinstance(data, dict) else None
                    return jobs if isinstance(jobs, list) else []

                try:
                    # Jooble advertises no fixed page size -> stop on the first empty page.
                    page_items, walk_done = paginate_until_empty(fetch_page, max_pages=pages)
                except SourceError as exc:
                    # A first-page failure on the FIRST query is systemic (bad key/network) -> fail;
                    # a later query failing keeps what earlier queries already returned.
                    if not items:
                        return SourceResult.failed(
                            self.name, str(exc), duration_ms=elapsed_ms(start)
                        )
                    exhausted = False
                    continue
                exhausted = exhausted and walk_done
                for it in page_items:
                    key = str(it.get("id") or it.get("link") or "")
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                    items.append(it)
        return build_result(
            self.name, items, self._map, duration_ms=elapsed_ms(start), exhaustive=exhausted
        )

    def _map(self, item: Any) -> RawPosting:
        return RawPosting(
            source="jooble",
            source_native_id=str(item.get("id")),
            title=str(item.get("title", "")),
            company=str(item.get("company") or ""),
            url=str(item.get("link", "")),
            location=item.get("location"),
            is_remote=None,
            description=item.get("snippet"),
            # Jooble rarely exposes structured pay; leave salary unparsed.
            posted_at=parse_iso(item.get("updated")),
        )
