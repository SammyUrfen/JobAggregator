"""Adzuna India API (ADZUNA_APP_ID/KEY env; INR salary) (Phase 3)."""

from __future__ import annotations

from job_aggregator.clock import Clock
from job_aggregator.config.schema import Config
from job_aggregator.sources.base import Source, SourceResult


class AdzunaSource(Source):
    name = "adzuna"

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        """Fetch, normalize -> Job list, and return a SourceResult. MUST NOT raise: convert
        any error/suspicious-empty into succeeded=False. Phase 3."""
        raise NotImplementedError("Phase 3: implement adzuna fetch")
