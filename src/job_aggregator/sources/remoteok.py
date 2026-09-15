"""RemoteOK free JSON API (Phase 3). Attribution required; element[0] is a legal notice."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from job_aggregator.errors import SourceError
from job_aggregator.pipeline import signals
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
# RemoteOK keeps a posting in its feed long after the origin closes it, and the API has no status
# field. Audit 2026-09-15: 53 of 99 feed items were older than 30 days, two of those were closed
# at the origin, and both sampled open postings were younger (2 and 24 days).
_MAX_AGE = timedelta(days=30)
# salary_min/max are annual USD (the page JSON-LD unit is YEAR). A top below this is no annual
# salary (the feed carries a "30-36", likely hourly), so it counts as unknown pay, not a FAIL.
_MIN_ANNUAL_USD = 1000


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
        cutoff = clock.now() - _MAX_AGE
        # Old postings drop in the mapper, not before build_result: a feed of only old postings
        # is a successful fetch (their stored rows then expire), not a suspicious empty.
        return build_result(
            self.name, postings, lambda it: self._map(it, cutoff), duration_ms=elapsed_ms(start)
        )

    @staticmethod
    def _map(item: Any, cutoff: datetime) -> RawPosting | None:
        posted_at = parse_iso(item.get("date")) or from_epoch_seconds(item.get("epoch"))
        if posted_at is not None and posted_at < cutoff:
            return None  # an undated posting stays: silence is never a reason to drop
        location = str(item.get("location") or "").strip()
        # A location that names a place ("Los Angeles", "Remote - US") limits where the hire may
        # live, so that row must pass the location gate instead of the remote bypass.
        # ponytail: a bare Indian city with no "India" ("Pune") then drops as location_mismatch,
        # even when the text says fully remote (1 stored row on 2026-09-15). Add ", India" for
        # known Indian cities if such rows show up often.
        placeless = not signals.place_words(location)
        salary_min = pos_int_or_none(item.get("salary_min"))
        salary_max = pos_int_or_none(item.get("salary_max"))
        if max(salary_min or 0, salary_max or 0) < _MIN_ANNUAL_USD:
            salary_min = salary_max = None
        return RawPosting(
            source="remoteok",
            source_native_id=str(item.get("id")),
            title=str(item.get("position", "")),
            company=str(item.get("company", "")),
            url=str(item.get("url", "")),
            location=location or "Remote",
            is_remote=True if placeless else None,
            description=item.get("description"),
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency="USD",
            salary_period="year",
            posted_at=posted_at,
        )
