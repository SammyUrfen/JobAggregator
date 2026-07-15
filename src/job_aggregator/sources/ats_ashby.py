"""Ashby ATS posting-api (per-org loop; case-sensitive org) (Phase 3)."""

from __future__ import annotations

from job_aggregator.clock import Clock
from job_aggregator.config.schema import Config
from job_aggregator.sources.base import Source, SourceResult


class AshbySource(Source):
    name = "ashby"

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        """Fetch, normalize -> Job list, and return a SourceResult. MUST NOT raise: convert
        any error/suspicious-empty into succeeded=False. Phase 3."""
        raise NotImplementedError("Phase 3: implement ashby fetch")
