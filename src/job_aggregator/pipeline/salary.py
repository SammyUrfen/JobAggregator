"""Salary normalization to INR/month + gating buckets (Phase 2, pure). See PLAN §4.3.

Frozen public API: `to_inr_month` and `salary_bucket`. `representative_inr` and `convert_bounds`
are internal helpers (the latter backs `normalize.build_job`). No free-text scraping lives here:
sources pass structured (amount, currency, period) triples; any text parsing is a source-adapter
concern (Phase 3/4). Everything is INR/month so the dashboard and thresholds compare apples.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from job_aggregator.models.job import Job, SalaryBucket

if TYPE_CHECKING:
    from job_aggregator.config.schema import Config

# Multiplier from a pay period to one month. Weekly/daily/hourly use conventional full-time
# counts (52 weeks, 260 working days, 2080 working hours per year), divided by 12.
_PERIOD_TO_MONTH = {
    "month": 1.0,
    "year": 1 / 12,
    "week": 52 / 12,
    "day": 260 / 12,
    "hour": 2080 / 12,
}


def representative_inr(mn: int | None, mx: int | None) -> int | None:
    """The figure the salary gate compares to the floor. PLAN §4.3: use salary_max if present,
    else salary_min — i.e. keep a job if the top of its range could clear the floor (a wide
    range like 10k-40k should not be dropped just because its midpoint is below the floor)."""
    return mx if mx is not None else mn


def to_inr_month(amount: float, currency: str, period: str, rates: Mapping[str, float]) -> int:
    """Convert one amount to INR/month.

    Caller guarantees `currency` is 'INR' or present in `rates`, and `period` is one of
    month/year/week/day/hour (an unknown period is assumed monthly).
    """
    cur = currency.upper()
    fx = 1.0 if cur == "INR" else float(rates[cur])
    pmonth = _PERIOD_TO_MONTH.get(period, 1.0)
    return round(amount * fx * pmonth)


def convert_bounds(
    min_v: int | None,
    max_v: int | None,
    currency: str | None,
    period: str | None,
    rates: Mapping[str, float],
) -> tuple[int | None, int | None, bool]:
    """Convert structured min/max pay to INR/month (backs build_job).

    Returns (min_inr, max_inr, parsed). `parsed` is True only when currency AND period are
    present, the currency is INR or in `rates`, and at least one bound converted; otherwise the
    original bounds pass through untouched with parsed=False (the caller flags UNKNOWN).
    """
    if period is None or currency is None:
        return min_v, max_v, False
    cur = currency.upper()
    if cur != "INR" and cur not in rates:
        return min_v, max_v, False
    per = period.lower()
    conv_min = None if min_v is None else to_inr_month(min_v, cur, per, rates)
    conv_max = None if max_v is None else to_inr_month(max_v, cur, per, rates)
    return conv_min, conv_max, conv_min is not None or conv_max is not None


def salary_bucket(job: Job, cfg: Config) -> SalaryBucket:
    """PASS if the parsed INR/month figure meets the applicable floor, FAIL if below, else
    UNKNOWN. Remote roles use `min_remote`; in-office use `min_in_office`."""
    if not job.salary_parsed or (job.salary_min is None and job.salary_max is None):
        return SalaryBucket.UNKNOWN
    rep = representative_inr(job.salary_min, job.salary_max)
    if rep is None:
        return SalaryBucket.UNKNOWN
    floor = cfg.salary.min_remote if job.is_remote else cfg.salary.min_in_office
    return SalaryBucket.PASS if rep >= floor else SalaryBucket.FAIL
