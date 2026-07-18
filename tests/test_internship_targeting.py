"""Internship-first pipeline: detection, score boost, relaxed gates, experience bar,
stipend floor, v2 migration backfill, and windowed-source age-based stale expiry.

Table-driven + deterministic (FixedClock; no network) per the repo's testing conventions.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job, SalaryBucket
from job_aggregator.pipeline.filters import (
    IN_OFFICE_UNKNOWN_SALARY_PENALTY,
    INTERNSHIP_BONUS,
    detect_internship,
    required_years,
    score_and_filter,
)
from job_aggregator.pipeline.salary import salary_bucket
from job_aggregator.pipeline.stale import expire_stale
from job_aggregator.storage.db import connect, init_db, migrate

JobFactory = Callable[..., Job]

# ── detect_internship ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Backend Engineer Intern", True),
        ("Software Development Internship", True),
        ("Graduate Trainee - Systems", True),
        ("Apprentice Developer", True),
        ("SDE Apprenticeship Program", True),
        ("Backend Engineer", False),
        # boundary: intern-prefixed words must NOT match
        ("International Sales Manager", False),
        ("Internal Tools Engineer", False),
        ("INTERN - Machine Learning", True),  # case-insensitive
    ],
)
def test_detect_internship(title: str, expected: bool) -> None:
    assert detect_internship(title) is expected


# ── required_years ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3+ years of experience with Python", 3),
        ("minimum 5 years experience in backend", 5),
        ("3-5 years of relevant experience", 3),  # a range's LOWER bound is the entry bar
        ("0-2 years of experience", 0),
        ("experience of 4+ years required", 4),
        ("we need 2 yrs experience", 2),
        # multiple demands -> the MAX is the bar
        ("2+ years with Python and 6+ years of software experience", 6),
        # 'years' with no experience context must NOT match
        ("a 2 year fixed-term contract", None),
        ("our platform served users for 10 years. Great learning experience", None),
        ("no experience required", None),
        ("", None),
    ],
)
def test_required_years(text: str, expected: int | None) -> None:
    assert required_years(text.lower()) == expected


# ── experience gate in score_and_filter ──────────────────────────────────────────────────


def test_experience_gate_drops_out_of_level_job(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(
        title="Backend Engineer",
        description="You have 5+ years of experience building backend APIs in Python.",
    )
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is False
    assert verdict.reasons == ["experience:5y"]


def test_experience_gate_allows_within_bar(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(
        title="Backend Engineer",
        description="0-2 years of experience with Python backend APIs.",
    )
    assert score_and_filter(job, cfg).keep is True


def test_experience_gate_exempts_internships(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(
        title="Backend Engineer Intern",
        description="Ideal: 3+ years of experience (flexible for strong candidates). Python.",
        is_internship=True,
    )
    assert score_and_filter(job, cfg).keep is True


def test_experience_gate_zero_disables(make_job: JobFactory, cfg: Config) -> None:
    c = cfg.model_copy(deep=True)
    c.keywords.max_experience_years = 0
    job = make_job(
        title="Backend Engineer",
        description="8+ years of experience with backend systems.",
    )
    assert score_and_filter(job, c).keep is True


# ── internship boost + relaxed role gate ─────────────────────────────────────────────────


def test_internship_gets_score_boost(make_job: JobFactory, cfg: Config) -> None:
    base = make_job(title="Backend Engineer", description="Python backend APIs")
    intern = make_job(
        title="Backend Engineer Intern",
        description="Python backend APIs",
        is_internship=True,
    )
    v_base, v_intern = score_and_filter(base, cfg), score_and_filter(intern, cfg)
    assert v_base.keep and v_intern.keep
    assert v_intern.score == v_base.score + INTERNSHIP_BONUS
    assert "internship" in v_intern.reasons


def test_internship_role_gate_relaxed_by_stack_anchor(make_job: JobFactory, cfg: Config) -> None:
    # "Java Developer Internship" matches NO configured role phrase; the java/spring stack
    # anchor alone must qualify it (the strict gate killed 118/153 real tech internships).
    job = make_job(
        title="Java Developer Internship",
        description="Build REST services with Java and Spring Boot.",
        is_internship=True,
    )
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is True
    assert "internship" in verdict.reasons
    # No role phrase matched, so the whole score is the intern boost minus the on-site
    # unknown-stipend demotion (the factory job is non-remote with no salary).
    assert verdict.score == INTERNSHIP_BONUS - IN_OFFICE_UNKNOWN_SALARY_PENALTY


def test_internship_without_anchor_still_dropped(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(
        title="Travel Agent Internship",
        description="Assist customers in planning holidays.",
        is_internship=True,
    )
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is False
    assert verdict.reasons == ["no_role_match"]


def test_off_domain_intern_titles_hard_excluded(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(title="Digital Marketing Internship", is_internship=True)
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is False
    assert verdict.reasons == ["excluded:marketing"]


@pytest.mark.parametrize(
    ("title", "excluded_token"),
    [
        ("Sr. Golang Developer", "sr"),
        ("Software Engineer III", "iii"),
        ("Software Developer - II - Backend", "ii"),
        ("SDE-2, Billing Platform", "sde-2"),
        ("Frontend Developer", "frontend"),
        ("SAP Fiori Developer", "sap"),
    ],
)
def test_out_of_level_and_off_stack_titles_excluded(
    make_job: JobFactory, cfg: Config, title: str, excluded_token: str
) -> None:
    verdict = score_and_filter(make_job(title=title, description="backend python"), cfg)
    assert verdict.keep is False
    assert verdict.reasons == [f"excluded:{excluded_token}"]


# ── internship stipend floor ─────────────────────────────────────────────────────────────


def test_internship_stipend_below_remote_floor_passes(make_job: JobFactory, cfg: Config) -> None:
    # ₹8k/month stipend: FAIL for a remote full-time job (floor 30k) but fine for an intern.
    intern = make_job(
        title="SDE Intern",
        is_remote=True,
        is_internship=True,
        salary_min=8000,
        salary_max=8000,
        salary_currency="INR",
        salary_period="month",
        salary_parsed=True,
    )
    fulltime = intern.model_copy(update={"is_internship": False})
    assert salary_bucket(intern, cfg) is SalaryBucket.PASS
    assert salary_bucket(fulltime, cfg) is SalaryBucket.FAIL


def test_internship_floor_configurable(make_job: JobFactory, cfg: Config) -> None:
    c = cfg.model_copy(deep=True)
    c.salary.min_internship = 10000
    intern = make_job(
        title="SDE Intern",
        is_remote=True,
        is_internship=True,
        salary_min=8000,
        salary_max=8000,
        salary_currency="INR",
        salary_period="month",
        salary_parsed=True,
    )
    assert salary_bucket(intern, c) is SalaryBucket.FAIL


# ── v2 migration: is_internship column + backfill ────────────────────────────────────────

_V1_JOBS_SQL = """
CREATE TABLE jobs (
  job_uid TEXT PRIMARY KEY, source TEXT NOT NULL, source_native_id TEXT, title TEXT NOT NULL,
  company TEXT NOT NULL, location TEXT, is_remote INTEGER, url TEXT NOT NULL, description TEXT,
  salary_min INTEGER, salary_max INTEGER, salary_currency TEXT, salary_period TEXT,
  salary_raw TEXT, salary_parsed INTEGER NOT NULL DEFAULT 0, salary_bucket TEXT,
  match_score REAL, posted_at TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  last_seen_cycle INTEGER NOT NULL, status TEXT NOT NULL,
  applied INTEGER NOT NULL DEFAULT 0, bookmarked INTEGER NOT NULL DEFAULT 0,
  hidden INTEGER NOT NULL DEFAULT 0, notes TEXT
)
"""


def _insert_v1_job(conn: sqlite3.Connection, uid: str, title: str) -> None:
    conn.execute(
        "INSERT INTO jobs (job_uid, source, title, company, url, first_seen_at, last_seen_at, "
        "last_seen_cycle, status) VALUES (?, 'src', ?, 'Acme', 'https://x', 't', 't', 1, 'active')",
        (uid, title),
    )


def test_migrate_v2_adds_column_and_backfills(tmp_path: object) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(_V1_JOBS_SQL)
    _insert_v1_job(conn, "a", "Backend Engineer Intern")
    _insert_v1_job(conn, "b", "Senior Backend Engineer")
    _insert_v1_job(conn, "c", "International Sales Lead")  # boundary: must stay 0
    conn.commit()
    migrate(conn)
    rows = dict(conn.execute("SELECT job_uid, is_internship FROM jobs"))
    assert rows == {"a": 1, "b": 0, "c": 0}
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 2


def test_migrate_v2_idempotent_on_fresh_schema(tmp_path_factory: pytest.TempPathFactory) -> None:
    db = tmp_path_factory.mktemp("mig") / "fresh.db"
    conn = connect(db)
    init_db(conn)  # fresh schema already has the column; migrate must not raise
    migrate(conn)  # second call: no-op
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "is_internship" in cols


# ── windowed-source age-based stale expiry ───────────────────────────────────────────────

_FIXED_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _stale_db(tmp_path_factory: pytest.TempPathFactory) -> sqlite3.Connection:
    conn = connect(tmp_path_factory.mktemp("stale") / "s.db")
    init_db(conn)
    for run_id in (1, 2):
        conn.execute(
            "INSERT INTO runs (run_id, started_at, status, trigger) VALUES (?, ?, 'success', 'x')",
            (run_id, _FIXED_NOW.isoformat()),
        )
    conn.commit()
    return conn


def _insert_seen_job(
    conn: sqlite3.Connection, uid: str, *, cycle: int, posted_at: str | None
) -> None:
    conn.execute(
        "INSERT INTO jobs (job_uid, source, title, company, url, posted_at, first_seen_at, "
        "last_seen_at, last_seen_cycle, status) VALUES (?, 'win', 'T', 'C', 'u', ?, ?, ?, ?, "
        "'active')",
        (uid, posted_at, _FIXED_NOW.isoformat(), _FIXED_NOW.isoformat(), cycle),
    )
    conn.commit()


def test_windowed_source_keeps_fresh_unseen_jobs(
    tmp_path_factory: pytest.TempPathFactory, cfg: Config
) -> None:
    """A job absent from a WINDOWED fetch stays active while its posting is young — absence
    from a truncated view is not evidence of death (the old behavior deleted it in days)."""
    conn = _stale_db(tmp_path_factory)
    fresh = (_FIXED_NOW - timedelta(days=3)).isoformat()
    _insert_seen_job(conn, "young", cycle=1, posted_at=fresh)
    n = expire_stale(conn, 2, {"win"}, cfg, FixedClock(_FIXED_NOW), windowed_sources={"win"})
    assert n == 0
    assert conn.execute("SELECT status FROM jobs WHERE job_uid='young'").fetchone()[0] == "active"


def test_windowed_source_retires_old_unseen_jobs(
    tmp_path_factory: pytest.TempPathFactory, cfg: Config
) -> None:
    conn = _stale_db(tmp_path_factory)
    old = (_FIXED_NOW - timedelta(days=cfg.schedule.windowed_retire_days + 5)).isoformat()
    _insert_seen_job(conn, "old", cycle=1, posted_at=old)
    n = expire_stale(conn, 2, {"win"}, cfg, FixedClock(_FIXED_NOW), windowed_sources={"win"})
    assert n == 1
    assert conn.execute("SELECT status FROM jobs WHERE job_uid='old'").fetchone()[0] == "stale"


def test_windowed_source_no_posted_at_uses_first_seen(
    tmp_path_factory: pytest.TempPathFactory, cfg: Config
) -> None:
    conn = _stale_db(tmp_path_factory)
    _insert_seen_job(conn, "dateless", cycle=1, posted_at=None)  # first_seen_at = FIXED_NOW
    n = expire_stale(conn, 2, {"win"}, cfg, FixedClock(_FIXED_NOW), windowed_sources={"win"})
    assert n == 0  # first_seen is recent -> kept


def test_exhaustive_source_still_absence_expires(
    tmp_path_factory: pytest.TempPathFactory, cfg: Config
) -> None:
    conn = _stale_db(tmp_path_factory)
    fresh = (_FIXED_NOW - timedelta(days=1)).isoformat()
    _insert_seen_job(conn, "gone", cycle=1, posted_at=fresh)
    # NOT in windowed_sources -> classic behavior: unseen this cycle = stale, however young.
    n = expire_stale(conn, 2, {"win"}, cfg, FixedClock(_FIXED_NOW))
    assert n == 1
    assert conn.execute("SELECT status FROM jobs WHERE job_uid='gone'").fetchone()[0] == "stale"
