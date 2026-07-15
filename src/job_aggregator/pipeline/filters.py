"""Keyword scoring + hard filters (Phase 2). See PLAN §4.4."""

from __future__ import annotations

from dataclasses import dataclass, field

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job


@dataclass(frozen=True)
class FilterVerdict:
    keep: bool
    score: float
    reasons: list[str] = field(default_factory=list)


def score_and_filter(job: Job, cfg: Config) -> FilterVerdict:
    """Hard-drop on: exclude keyword in title; no level_required match (when require_level);
    salary bucket FAIL; location not configured AND not remote. Otherwise keep with a score
    (+role match, +bonus keywords, +remote boost, +recency). Phase 2."""
    raise NotImplementedError("Phase 2: score and filter")
