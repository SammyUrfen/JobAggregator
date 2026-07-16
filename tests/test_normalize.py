"""Phase 2 — pipeline.normalize.

clean_text (None/whitespace/ZWSP/nbsp), parse_date (ISO/offset/epoch s vs ms/garbage),
build_job smoke.

See PLAN.md Part II (Phase 2) for the exact table-driven cases to implement.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job
from job_aggregator.pipeline import dedup
from job_aggregator.pipeline.normalize import build_job, clean_text, parse_date


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("  hello   world  ", "hello world"),
        ("a\u200bb", "ab"),  # zero-width space removed
        ("a\u00a0b", "a b"),  # nbsp folded to space by NFKC
    ],
)
def test_clean_text(raw: str | None, expected: str | None) -> None:
    assert clean_text(raw) == expected


def test_parse_date_iso_applies_offset() -> None:
    got = parse_date("2026-06-01T12:00:00+05:30")
    assert got == datetime(2026, 6, 1, 6, 30, tzinfo=UTC)


def test_parse_date_iso_z_suffix() -> None:
    assert parse_date("2026-06-01T00:00:00Z") == datetime(2026, 6, 1, tzinfo=UTC)


def test_parse_date_epoch_seconds_and_ms_same_instant() -> None:
    secs = parse_date(1_700_000_000)
    millis = parse_date(1_700_000_000_000)
    assert secs is not None
    assert secs == millis


@pytest.mark.parametrize("garbage", ["not a date", "", None, True, "2026-13-99"])
def test_parse_date_garbage_is_none(garbage: object) -> None:
    assert parse_date(garbage) is None


def test_parse_date_date_object() -> None:
    from datetime import date

    assert parse_date(date(2026, 6, 1)) == datetime(2026, 6, 1, tzinfo=UTC)


def test_parse_date_absurd_epoch_is_none() -> None:
    assert parse_date(10**20) is None  # out-of-range timestamp -> None, never raises


def test_build_job_coerces_numeric_and_bool_fields(cfg: Config) -> None:
    job = build_job(
        cfg,
        source="x",
        company="Acme",
        title="Backend Engineer Intern",
        url="https://x/1",
        match_score=7,  # int -> float
        is_remote=True,
        salary_min=600000.0,  # float -> int, then INR/year -> INR/month
        salary_currency="INR",
        salary_period="year",
    )
    assert job.match_score == 7.0
    assert job.is_remote is True
    assert job.salary_min == 50000


def test_build_job_smoke(cfg: Config) -> None:
    job = build_job(
        cfg,
        source="greenhouse",
        company="Acme",
        title="Backend Engineer Intern",
        location="Remote",
        is_remote=True,
        url="https://x.com/j?utm_source=y",
    )
    assert isinstance(job, Job)
    assert job.job_uid == dedup.content_hash("Acme", "Backend Engineer Intern", "Remote")
    assert job.url == "https://x.com/j"  # tracking param stripped
    assert job.salary_bucket is not None  # bucket always set (UNKNOWN here)


def test_build_job_converts_salary(cfg: Config) -> None:
    job = build_job(
        cfg,
        source="greenhouse",
        company="Acme",
        title="Backend Engineer Intern",
        is_remote=True,
        salary_min=600000,
        salary_max=900000,
        salary_currency="inr",
        salary_period="year",
    )
    assert job.salary_parsed is True
    assert (job.salary_min, job.salary_max) == (50000, 75000)  # INR/year -> INR/month
    assert job.salary_period == "month"
