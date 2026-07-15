"""Salary parsing + INR/month normalization + gating buckets (Phase 2). See PLAN §4.3."""

from __future__ import annotations

from dataclasses import dataclass

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job, SalaryBucket


@dataclass
class ParsedSalary:
    min_inr_month: int | None
    max_inr_month: int | None
    currency: str | None
    period: str | None
    parsed: bool


def parse_salary(
    raw: str | None,
    currency: str | None,
    period: str | None,
    min_v: int | None,
    max_v: int | None,
    fx_rates: dict[str, float],
) -> ParsedSalary:
    """Best-effort parse of assorted salary encodings -> INR/month. parsed=True only when a
    real amount+currency+period were convertible. Phase 2."""
    raise NotImplementedError("Phase 2: parse salary")


def to_inr_month(amount: float, currency: str, period: str, rates: dict[str, float]) -> int:
    """Convert (amount, currency, period) to INR/month. year->/12; FX via `rates`. Phase 2."""
    raise NotImplementedError("Phase 2: to INR/month")


def salary_bucket(job: Job, cfg: Config) -> SalaryBucket:
    """PASS if parsed & >= threshold (30k remote / 80k in-office); FAIL if parsed & below;
    else UNKNOWN (kept + flagged). Compares salary_max if present else salary_min. Phase 2."""
    raise NotImplementedError("Phase 2: salary bucket")
