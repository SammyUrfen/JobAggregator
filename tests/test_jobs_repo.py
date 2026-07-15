"""Phase 1 — storage.jobs_repo + runs_repo.

insert, idempotent re-upsert, user-flag preservation, filtered queries, run bookkeeping.

See PLAN.md Part II (Phase 1) for the exact table-driven cases to implement.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from job_aggregator.clock import FixedClock
from job_aggregator.models.job import Job, SalaryBucket
from job_aggregator.storage import jobs_repo, runs_repo
from job_aggregator.storage.db import init_db

JobFactory = Callable[..., Job]


def _seed_three(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    """Insert a fixed 3-job set used by the filter/pagination tests (uids a/b/c)."""
    jobs_repo.upsert_job(
        conn,
        make_job(
            job_uid="a",
            source="greenhouse",
            title="Backend Intern",
            company="Acme",
            is_remote=True,
            salary_bucket=SalaryBucket.PASS,
        ),
        run_id,
        clock,
    )
    jobs_repo.upsert_job(
        conn,
        make_job(
            job_uid="b",
            source="lever",
            title="ML Engineer Intern",
            company="Beta",
            is_remote=False,
            salary_bucket=SalaryBucket.UNKNOWN,
        ),
        run_id,
        clock,
    )
    jobs_repo.upsert_job(
        conn,
        make_job(
            job_uid="c",
            source="greenhouse",
            title="Data Platform Intern",
            company="Gamma",
            is_remote=True,
            salary_bucket=SalaryBucket.PASS,
        ),
        run_id,
        clock,
    )


# ── Schema / connection ─────────────────────────────────────────────────────────────────


def test_all_tables_created(conn: sqlite3.Connection) -> None:
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "runs", "source_runs", "config"} <= names


def test_init_db_idempotent(conn: sqlite3.Connection) -> None:
    init_db(conn)  # second application must not raise
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"jobs", "runs", "source_runs", "config"} <= names


def test_connection_pragmas(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert isinstance(conn.execute("SELECT 1 AS x").fetchone(), sqlite3.Row)


def test_unknown_run_id_is_fk_violation(
    conn: sqlite3.Connection, clock: FixedClock, make_job: JobFactory
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        jobs_repo.upsert_job(conn, make_job(job_uid="x"), 9999, clock)


# ── upsert semantics ────────────────────────────────────────────────────────────────────


def test_upsert_insert_new(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    job = make_job()
    assert jobs_repo.upsert_job(conn, job, run_id, clock) == "new"
    row = conn.execute("SELECT * FROM jobs WHERE job_uid=?", (job.job_uid,)).fetchone()
    assert row["status"] == "new"
    assert row["first_seen_at"] == row["last_seen_at"]
    assert row["last_seen_cycle"] == run_id
    assert (row["applied"], row["bookmarked"], row["hidden"]) == (0, 0, 0)
    assert row["notes"] is None


def test_reupsert_updates_single_row(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    job = make_job()
    jobs_repo.upsert_job(conn, job, run_id, clock)
    first = conn.execute("SELECT * FROM jobs WHERE job_uid=?", (job.job_uid,)).fetchone()

    clock.advance(days=1)
    run2 = runs_repo.start_run(conn, "manual", clock)
    assert jobs_repo.upsert_job(conn, job, run2, clock) == "updated"

    assert conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"] == 1
    row = conn.execute("SELECT * FROM jobs WHERE job_uid=?", (job.job_uid,)).fetchone()
    assert row["status"] == "active"
    assert row["last_seen_cycle"] == run2
    assert row["first_seen_at"] == first["first_seen_at"]  # provenance timestamp frozen
    assert row["last_seen_at"] > first["last_seen_at"]  # ISO lexicographic == chronological


def test_user_flags_preserved_across_upsert(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    job = make_job()
    jobs_repo.upsert_job(conn, job, run_id, clock)
    jobs_repo.set_user_flag(conn, job.job_uid, "applied", True)
    jobs_repo.set_user_flag(conn, job.job_uid, "bookmarked", True)
    jobs_repo.set_user_flag(conn, job.job_uid, "notes", "referred by a friend")

    run2 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, job, run2, clock)

    row = conn.execute("SELECT * FROM jobs WHERE job_uid=?", (job.job_uid,)).fetchone()
    assert row["applied"] == 1
    assert row["bookmarked"] == 1
    assert row["notes"] == "referred by a friend"


def test_first_seen_provenance_preserved_mutable_refreshed(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    original = make_job(
        job_uid="p",
        source="greenhouse",
        url="https://gh.example/jobs/1",
        description="old description",
        salary_min=None,
        match_score=1.0,
    )
    jobs_repo.upsert_job(conn, original, run_id, clock)

    refreshed = make_job(
        job_uid="p",
        source="lever",  # different source on the re-fetch
        url="https://lever.example/x",
        description="new description",
        salary_min=50000,
        match_score=9.0,
    )
    run2 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, refreshed, run2, clock)

    row = conn.execute("SELECT * FROM jobs WHERE job_uid='p'").fetchone()
    assert row["source"] == "greenhouse"  # provenance: first-seen source kept
    assert row["url"] == "https://gh.example/jobs/1"  # provenance: first-seen url kept
    assert row["description"] == "new description"  # mutable field refreshed
    assert row["salary_min"] == 50000
    assert row["match_score"] == 9.0


# ── get_jobs / count_jobs / count_by_status ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (jobs_repo.get_jobs, {"a", "b", "c"}),
        (lambda c: jobs_repo.get_jobs(c, source="greenhouse"), {"a", "c"}),
        (lambda c: jobs_repo.get_jobs(c, remote=True), {"a", "c"}),
        (lambda c: jobs_repo.get_jobs(c, remote=False), {"b"}),
        (lambda c: jobs_repo.get_jobs(c, bucket="pass"), {"a", "c"}),
        (lambda c: jobs_repo.get_jobs(c, q="ml"), {"b"}),
    ],
)
def test_get_jobs_filters(
    conn: sqlite3.Connection,
    run_id: int,
    clock: FixedClock,
    make_job: JobFactory,
    query: Callable[[sqlite3.Connection], list[sqlite3.Row]],
    expected: set[str],
) -> None:
    _seed_three(conn, run_id, clock, make_job)
    assert {r["job_uid"] for r in query(conn)} == expected


def test_default_excludes_deleted_and_hidden(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    _seed_three(conn, run_id, clock, make_job)
    conn.execute("UPDATE jobs SET status='deleted' WHERE job_uid='a'")
    conn.commit()
    jobs_repo.set_user_flag(conn, "b", "hidden", True)

    assert {r["job_uid"] for r in jobs_repo.get_jobs(conn)} == {"c"}
    assert {r["job_uid"] for r in jobs_repo.get_jobs(conn, status=["deleted"])} == {"a"}
    assert {r["job_uid"] for r in jobs_repo.get_jobs(conn, include_hidden=True)} == {"b", "c"}


def test_pagination_and_count(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    for i in range(5):
        jobs_repo.upsert_job(conn, make_job(job_uid=f"j{i}", company=f"C{i}"), run_id, clock)
    assert jobs_repo.count_jobs(conn) == 5
    page1 = jobs_repo.get_jobs(conn, sort="company", limit=2, offset=0)
    page2 = jobs_repo.get_jobs(conn, sort="company", limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert {r["job_uid"] for r in page1}.isdisjoint({r["job_uid"] for r in page2})


def test_score_sort_nulls_last(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    jobs_repo.upsert_job(conn, make_job(job_uid="hi", match_score=9.0), run_id, clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="lo", match_score=1.0), run_id, clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="null", match_score=None), run_id, clock)
    order = [r["job_uid"] for r in jobs_repo.get_jobs(conn, sort="score")]
    assert order == ["hi", "lo", "null"]


def test_count_by_status(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    jobs_repo.upsert_job(conn, make_job(job_uid="a"), run_id, clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="b"), run_id, clock)
    run2 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="a"), run2, clock)  # a -> active
    counts = jobs_repo.count_by_status(conn)
    assert counts.get("new") == 1
    assert counts.get("active") == 1


# ── set_user_flag ───────────────────────────────────────────────────────────────────────


def test_set_user_flag_rejects_unknown_field(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    jobs_repo.upsert_job(conn, make_job(job_uid="a"), run_id, clock)
    with pytest.raises(ValueError):
        jobs_repo.set_user_flag(conn, "a", "status", True)


def test_set_user_flag_unknown_uid_returns_false(conn: sqlite3.Connection) -> None:
    assert jobs_repo.set_user_flag(conn, "nope", "applied", True) is False


def test_set_notes_can_clear_to_none(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    jobs_repo.upsert_job(conn, make_job(job_uid="a"), run_id, clock)
    jobs_repo.set_user_flag(conn, "a", "notes", "hello")
    assert jobs_repo.set_user_flag(conn, "a", "notes", None) is True
    assert conn.execute("SELECT notes FROM jobs WHERE job_uid='a'").fetchone()["notes"] is None


# ── run bookkeeping ─────────────────────────────────────────────────────────────────────


def test_start_run_sets_running_and_current(conn: sqlite3.Connection, clock: FixedClock) -> None:
    rid = runs_repo.start_run(conn, "manual", clock)
    assert isinstance(rid, int)
    cur = runs_repo.current_run(conn)
    assert cur is not None
    assert cur["run_id"] == rid
    assert cur["status"] == "running"


def test_start_run_bad_trigger(conn: sqlite3.Connection, clock: FixedClock) -> None:
    with pytest.raises(ValueError):
        runs_repo.start_run(conn, "bogus", clock)


def test_finish_run_records_counts_and_clears_current(
    conn: sqlite3.Connection, clock: FixedClock
) -> None:
    rid = runs_repo.start_run(conn, "manual", clock)
    runs_repo.finish_run(conn, rid, "success", n_sources_ok=4, n_new=3, n_updated=2, clock=clock)
    assert runs_repo.current_run(conn) is None
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (rid,)).fetchone()
    assert row["status"] == "success"
    assert (row["n_sources_ok"], row["n_new"], row["n_updated"]) == (4, 3, 2)
    assert row["finished_at"] is not None


def test_finish_run_bad_status(conn: sqlite3.Connection, clock: FixedClock) -> None:
    rid = runs_repo.start_run(conn, "manual", clock)
    with pytest.raises(ValueError):
        runs_repo.finish_run(conn, rid, "done", clock=clock)


def test_record_source_run_persists_failure(conn: sqlite3.Connection, clock: FixedClock) -> None:
    rid = runs_repo.start_run(conn, "manual", clock)
    runs_repo.record_source_run(
        conn, rid, "jobspy_linkedin", succeeded=False, n_fetched=0, error="429"
    )
    row = conn.execute(
        "SELECT * FROM source_runs WHERE run_id=? AND source=?", (rid, "jobspy_linkedin")
    ).fetchone()
    assert row["succeeded"] == 0
    assert row["error"] == "429"


def test_record_source_run_upsert_latest_wins(conn: sqlite3.Connection, clock: FixedClock) -> None:
    rid = runs_repo.start_run(conn, "manual", clock)
    runs_repo.record_source_run(conn, rid, "remoteok", succeeded=False, error="timeout")
    runs_repo.record_source_run(conn, rid, "remoteok", succeeded=True, n_fetched=12)
    rows = conn.execute(
        "SELECT * FROM source_runs WHERE run_id=? AND source='remoteok'", (rid,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["succeeded"] == 1
    assert rows[0]["n_fetched"] == 12
    assert rows[0]["error"] is None


def test_recent_runs_newest_first(conn: sqlite3.Connection, clock: FixedClock) -> None:
    ids = [runs_repo.start_run(conn, "manual", clock) for _ in range(3)]
    rows = runs_repo.recent_runs(conn, limit=2)
    assert [r["run_id"] for r in rows] == sorted(ids, reverse=True)[:2]


def test_last_successful_run_is_strict(conn: sqlite3.Connection, clock: FixedClock) -> None:
    r1 = runs_repo.start_run(conn, "manual", clock)
    runs_repo.finish_run(conn, r1, "success", clock=clock)
    r2 = runs_repo.start_run(conn, "manual", clock)
    runs_repo.finish_run(conn, r2, "partial", clock=clock)  # later, but NOT success
    last = runs_repo.last_successful_run(conn)
    assert last is not None
    assert last["run_id"] == r1


# ── read helpers for Phase 7 ────────────────────────────────────────────────────────────


def test_row_to_job_roundtrip(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    posted = datetime(2026, 6, 1, tzinfo=UTC)
    job = make_job(
        job_uid="rt",
        is_remote=True,
        salary_min=30000,
        salary_max=50000,
        salary_currency="INR",
        salary_period="month",
        salary_raw="30k-50k",
        salary_parsed=True,
        salary_bucket=SalaryBucket.PASS,
        match_score=7.5,
        posted_at=posted,
    )
    jobs_repo.upsert_job(conn, job, run_id, clock)
    row = conn.execute("SELECT * FROM jobs WHERE job_uid='rt'").fetchone()
    got = jobs_repo._row_to_job(row)
    assert got.job_uid == "rt"
    assert got.is_remote is True
    assert got.salary_parsed is True
    assert got.salary_bucket is SalaryBucket.PASS
    assert got.match_score == 7.5
    assert got.posted_at == posted


def test_jobs_new_in_run_only_this_run(
    conn: sqlite3.Connection, clock: FixedClock, make_job: JobFactory
) -> None:
    r1 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="old"), r1, clock)  # stays 'new', cycle r1
    r2 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="fresh"), r2, clock)  # 'new', cycle r2
    assert {j.job_uid for j in jobs_repo.jobs_new_in_run(conn, r2)} == {"fresh"}


def test_recent_active_excludes_hidden_and_deleted(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, make_job: JobFactory
) -> None:
    jobs_repo.upsert_job(conn, make_job(job_uid="vis"), run_id, clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="hid"), run_id, clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="del"), run_id, clock)
    jobs_repo.set_user_flag(conn, "hid", "hidden", True)
    conn.execute("UPDATE jobs SET status='deleted' WHERE job_uid='del'")
    conn.commit()
    assert {j.job_uid for j in jobs_repo.recent_active_jobs(conn, limit=10)} == {"vis"}
