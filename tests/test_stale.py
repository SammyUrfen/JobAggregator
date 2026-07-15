"""Phase 5 — pipeline.stale.expire_stale.

only succeeded sources expire; failed source untouched; grace stale->deleted.

See PLAN.md Part II (Phase 5) for the exact table-driven cases to implement.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job
from job_aggregator.pipeline.stale import expire_stale
from job_aggregator.storage import jobs_repo, runs_repo

JobFactory = Callable[..., Job]


def _status(conn: sqlite3.Connection, uid: str) -> str:
    row = conn.execute("SELECT status FROM jobs WHERE job_uid=?", (uid,)).fetchone()
    return str(row["status"])


def _seed(
    conn: sqlite3.Connection, clock: FixedClock, make_job: JobFactory, uid: str, src: str
) -> int:
    run_id = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, make_job(job_uid=uid, source=src), run_id, clock)
    return run_id


def test_soft_stale_from_succeeded_source(
    conn: sqlite3.Connection, clock: FixedClock, cfg: Config, make_job: JobFactory
) -> None:
    _seed(conn, clock, make_job, "a", "src")
    r2 = runs_repo.start_run(conn, "manual", clock)  # new cycle, a not re-seen
    assert expire_stale(conn, r2, {"src"}, cfg, clock) == 1
    assert _status(conn, "a") == "stale"


def test_failed_source_jobs_untouched(
    conn: sqlite3.Connection, clock: FixedClock, cfg: Config, make_job: JobFactory
) -> None:
    _seed(conn, clock, make_job, "a", "src")
    r2 = runs_repo.start_run(conn, "manual", clock)
    assert expire_stale(conn, r2, set(), cfg, clock) == 0  # src not in succeeded set
    assert _status(conn, "a") == "new"


def test_only_succeeded_sources_expire(
    conn: sqlite3.Connection, clock: FixedClock, cfg: Config, make_job: JobFactory
) -> None:
    r1 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="a", source="up"), r1, clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="b", source="down"), r1, clock)
    r2 = runs_repo.start_run(conn, "manual", clock)
    assert expire_stale(conn, r2, {"up"}, cfg, clock) == 1
    assert _status(conn, "a") == "stale"
    assert _status(conn, "b") == "new"  # 'down' never iterated -> untouched


def test_within_grace_stays_stale(
    conn: sqlite3.Connection, clock: FixedClock, cfg: Config, make_job: JobFactory
) -> None:
    _seed(conn, clock, make_job, "a", "src")
    r2 = runs_repo.start_run(conn, "manual", clock)
    expire_stale(conn, r2, {"src"}, cfg, clock)  # -> stale
    clock.advance(days=cfg.schedule.grace_days - 1)  # still within grace
    r3 = runs_repo.start_run(conn, "manual", clock)
    assert expire_stale(conn, r3, {"src"}, cfg, clock) == 0
    assert _status(conn, "a") == "stale"


def test_grace_window_stale_to_deleted(
    conn: sqlite3.Connection, clock: FixedClock, cfg: Config, make_job: JobFactory
) -> None:
    _seed(conn, clock, make_job, "a", "src")
    r2 = runs_repo.start_run(conn, "manual", clock)
    expire_stale(conn, r2, {"src"}, cfg, clock)  # -> stale
    clock.advance(days=cfg.schedule.grace_days + 1)  # past grace
    r3 = runs_repo.start_run(conn, "manual", clock)
    assert expire_stale(conn, r3, {"src"}, cfg, clock) == 1
    assert _status(conn, "a") == "deleted"


def test_seen_this_cycle_not_staled(
    conn: sqlite3.Connection, clock: FixedClock, cfg: Config, make_job: JobFactory
) -> None:
    r2 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, make_job(job_uid="a", source="src"), r2, clock)  # last_seen_cycle=r2
    assert expire_stale(conn, r2, {"src"}, cfg, clock) == 0
    assert _status(conn, "a") == "new"


def test_deleted_is_idempotent(
    conn: sqlite3.Connection, clock: FixedClock, cfg: Config, make_job: JobFactory
) -> None:
    _seed(conn, clock, make_job, "a", "src")
    r2 = runs_repo.start_run(conn, "manual", clock)
    expire_stale(conn, r2, {"src"}, cfg, clock)  # stale
    clock.advance(days=cfg.schedule.grace_days + 1)
    r3 = runs_repo.start_run(conn, "manual", clock)
    expire_stale(conn, r3, {"src"}, cfg, clock)  # deleted
    r4 = runs_repo.start_run(conn, "manual", clock)
    assert expire_stale(conn, r4, {"src"}, cfg, clock) == 0  # nothing left to touch
    assert _status(conn, "a") == "deleted"


def test_empty_succeeded_set_is_noop(
    conn: sqlite3.Connection, clock: FixedClock, cfg: Config, make_job: JobFactory
) -> None:
    _seed(conn, clock, make_job, "a", "src")
    r2 = runs_repo.start_run(conn, "manual", clock)
    assert expire_stale(conn, r2, set(), cfg, clock) == 0
    assert _status(conn, "a") == "new"
