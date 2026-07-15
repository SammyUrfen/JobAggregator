"""Unstop public JSON source (Phase 3).

India internships: read `subtype` (not `type`) and filter on `updated_at` recency.
"""

from __future__ import annotations

from job_aggregator.clock import Clock
from job_aggregator.config.schema import Config
from job_aggregator.sources.base import Source, SourceResult


class UnstopSource(Source):
    name = "unstop"

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        """Fetch, normalize -> Job list, and return a SourceResult. MUST NOT raise: convert
        any error/suspicious-empty into succeeded=False. Phase 3."""
        raise NotImplementedError("Phase 3: implement unstop fetch")
