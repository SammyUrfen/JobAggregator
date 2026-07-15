"""Shared normalization helpers (Phase 2). Each source adapter calls build_job()."""

from __future__ import annotations

from datetime import datetime

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job


def clean_text(s: str | None) -> str | None:
    """Collapse whitespace, strip; return None for empty. Phase 2."""
    raise NotImplementedError("Phase 2: clean_text")


def parse_date(raw: object) -> datetime | None:
    """Parse assorted date formats (ISO, epoch, 'YYYY-MM-DD') -> aware UTC datetime|None."""
    raise NotImplementedError("Phase 2: parse_date")


def build_job(cfg: Config, **fields: object) -> Job:
    """Assemble a normalized Job: canonicalize url, compute job_uid (content hash), parse +
    bucket salary, set match_score via filters. Central place so every source is consistent.
    Phase 2 (uses dedup, salary; filters/score applied in the runner or here per PLAN §4)."""
    raise NotImplementedError("Phase 2: build normalized Job")
