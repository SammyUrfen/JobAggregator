"""Deterministic per-ATS form maps + `detect_ats` (Track D)."""

from __future__ import annotations

from job_aggregator.apply.ats.ashby import ASHBY
from job_aggregator.apply.ats.base import AtsForm, match_ats
from job_aggregator.apply.ats.greenhouse import GREENHOUSE
from job_aggregator.apply.ats.lever import LEVER
from job_aggregator.apply.ats.smartrecruiters import SMARTRECRUITERS

# Order is stable; detection is host-substring based so order rarely matters.
ATS_FORMS: tuple[AtsForm, ...] = (GREENHOUSE, LEVER, ASHBY, SMARTRECRUITERS)


def detect_ats(url: str) -> AtsForm | None:
    """The deterministic form map for a known ATS URL, else None (unknown -> generic fallback)."""
    return match_ats(url, ATS_FORMS)


__all__ = ["ATS_FORMS", "AtsForm", "detect_ats"]
