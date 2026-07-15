"""RemoteOK free JSON API (Phase 3). Attribution required; element[0] is a legal notice."""

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
    parse_iso,
    pos_int_or_none,
)

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

_URL = "https://remoteok.com/api"


class RemoteOkSource(Source):
    name = "remoteok"

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        with make_client() as client:
            try:
                data = get_json(client, _URL)
            except SourceError as exc:
                return SourceResult.failed(self.name, str(exc), duration_ms=elapsed_ms(start))
        items = data if isinstance(data, list) else []
        # Strip element[0] legal notice: keep only real postings (dicts with id AND position).
        postings = [
            it for it in items if isinstance(it, dict) and it.get("id") and it.get("position")
        ]
        return build_result(self.name, postings, self._map, duration_ms=elapsed_ms(start))

    @staticmethod
    def _map(item: Any) -> RawPosting:
        return RawPosting(
            source="remoteok",
            source_native_id=str(item.get("id")),
            title=str(item.get("position", "")),
            company=str(item.get("company", "")),
            url=str(item.get("url", "")),
            location=item.get("location") or "Remote",
            is_remote=True,
            description=item.get("description"),
            salary_min=pos_int_or_none(item.get("salary_min")),
            salary_max=pos_int_or_none(item.get("salary_max")),
            salary_currency="USD",
            posted_at=parse_iso(item.get("date")) or from_epoch_seconds(item.get("epoch")),
        )
