"""Phase 2 — pipeline.salary.

parse_salary variants, to_inr_month (year/FX), salary_bucket PASS/UNKNOWN/FAIL
incl remote vs in-office thresholds.

See PLAN.md Part II (Phase 2) for the exact table-driven cases to implement.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job, SalaryBucket
from job_aggregator.pipeline import salary

JobFactory = Callable[..., Job]


@pytest.mark.parametrize(
    ("amount", "currency", "period", "expected"),
    [
        (600000, "INR", "year", 50000),  # 600000 / 12
        (60000, "USD", "year", 415000),  # 60000 * 83 / 12
        (40000, "INR", "month", 40000),  # already monthly INR
        (10, "GBP", "hour", round(10 * 105 * (2080 / 12))),  # hourly GBP -> INR/month
    ],
)
def test_to_inr_month(
    amount: float, currency: str, period: str, expected: int, fx_rates: dict[str, float]
) -> None:
    assert salary.to_inr_month(amount, currency, period, fx_rates) == expected


def test_representative_inr() -> None:
    # PLAN §4.3: compare using salary_max if present, else salary_min (keep-if-top-could-clear).
    assert salary.representative_inr(30000, 50000) == 50000
    assert salary.representative_inr(None, 50000) == 50000
    assert salary.representative_inr(30000, None) == 30000
    assert salary.representative_inr(None, None) is None


def test_wide_range_kept_when_max_clears_floor(cfg: Config, make_job: JobFactory) -> None:
    # A remote 10k-40k job (floor 30k): max clears the floor so it must PASS, not be dropped.
    job = make_job(is_remote=True, salary_parsed=True, salary_min=10000, salary_max=40000)
    assert salary.salary_bucket(job, cfg) is SalaryBucket.PASS


@pytest.mark.parametrize(
    ("job_kwargs", "expected"),
    [
        ({"salary_parsed": False}, SalaryBucket.UNKNOWN),
        ({"salary_parsed": True, "salary_min": None, "salary_max": None}, SalaryBucket.UNKNOWN),
        (
            {"is_remote": True, "salary_parsed": True, "salary_min": 50000, "salary_max": 50000},
            SalaryBucket.PASS,  # remote, 50k >= 30k floor
        ),
        (
            {"is_remote": False, "salary_parsed": True, "salary_min": 45000, "salary_max": 45000},
            SalaryBucket.FAIL,  # in-office, 45k < 80k floor
        ),
    ],
)
def test_salary_bucket(
    job_kwargs: dict[str, object],
    expected: SalaryBucket,
    make_job: JobFactory,
    cfg: Config,
) -> None:
    job = make_job(**job_kwargs)
    assert salary.salary_bucket(job, cfg) is expected


def test_convert_bounds_unknown_currency_passes_through() -> None:
    rates = {"USD": 83.0}
    assert salary.convert_bounds(100, 200, "JPY", "month", rates) == (100, 200, False)
    assert salary.convert_bounds(100, None, None, "month", rates) == (100, None, False)


def test_convert_bounds_converts_and_marks_parsed(fx_rates: dict[str, float]) -> None:
    lo, hi, parsed = salary.convert_bounds(600000, 900000, "INR", "year", fx_rates)
    assert (lo, hi, parsed) == (50000, 75000, True)
