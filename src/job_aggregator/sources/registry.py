"""Build the list of enabled Source instances from config (Phase 3; JobSpy added Phase 4)."""

from __future__ import annotations

from job_aggregator.config.schema import Config
from job_aggregator.sources.base import Source


def build_enabled_sources(cfg: Config) -> list[Source]:
    """Instantiate every enabled source per cfg.sources. Phase 3 wires Tier B/C; Phase 4
    adds JobSpySource. Phase 5's runner calls this."""
    raise NotImplementedError("Phase 3/4: build enabled sources from config")
