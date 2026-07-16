"""runs + source_runs access (Phase 1).

`source_runs.succeeded` is the correctness crux: the stale-delete pass (Phase 5) only expires
jobs from sources that succeeded this cycle. `last_successful_run` is intentionally strict
(status='success' only) so catch-up re-attempts after any partial failure (Phase 6).
"""

from __future__ import annotations

import sqlite3

from job_aggregator.clock import Clock
from job_aggregator.errors import StorageError

_VALID_TRIGGERS = frozenset({"schedule", "manual", "startup_catchup"})
_VALID_RUN_STATUSES = frozenset({"running", "success", "partial", "failed"})
RECENT_RUNS_DEFAULT_LIMIT = 20


def start_run(conn: sqlite3.Connection, trigger: str, clock: Clock) -> int:
    """INSERT a runs row (status='running') and return its run_id."""
    if trigger not in _VALID_TRIGGERS:
        raise ValueError(f"invalid trigger: {trigger!r}")
    cur = conn.execute(
        "INSERT INTO runs (started_at, status, trigger) VALUES (?, 'running', ?)",
        (clock.now().isoformat(), trigger),
    )
    conn.commit()
    run_id = cur.lastrowid
    if run_id is None:  # pragma: no cover - AUTOINCREMENT PK always yields a rowid on INSERT
        raise StorageError("INSERT into runs returned no run_id")
    return run_id


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    *,
    n_sources_ok: int = 0,
    n_sources_err: int = 0,
    n_new: int = 0,
    n_updated: int = 0,
    n_expired: int = 0,
    clock: Clock,
    error: str | None = None,
) -> None:
    """Finalize a run row with counts + finished_at."""
    if status not in _VALID_RUN_STATUSES:
        raise ValueError(f"invalid run status: {status!r}")
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, n_sources_ok = ?, n_sources_err = ?, "
        "n_new = ?, n_updated = ?, n_expired = ?, error = ? WHERE run_id = ?",
        (
            clock.now().isoformat(),
            status,
            n_sources_ok,
            n_sources_err,
            n_new,
            n_updated,
            n_expired,
            error,
            run_id,
        ),
    )
    conn.commit()


def record_source_run(
    conn: sqlite3.Connection,
    run_id: int,
    source: str,
    *,
    succeeded: bool,
    n_fetched: int | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> None:
    """INSERT (or upsert) one source_runs row. `succeeded` gates stale-deletion (PLAN §4.5)."""
    conn.execute(
        "INSERT INTO source_runs (run_id, source, succeeded, n_fetched, duration_ms, error) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(run_id, source) DO UPDATE SET "
        "succeeded=excluded.succeeded, n_fetched=excluded.n_fetched, "
        "duration_ms=excluded.duration_ms, error=excluded.error",
        (run_id, source, int(succeeded), n_fetched, duration_ms, error),
    )
    conn.commit()


def current_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The most recent run with status='running', if any (run-lock + /api/runs/current)."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM runs WHERE status = 'running' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return row


def recent_runs(
    conn: sqlite3.Connection, limit: int = RECENT_RUNS_DEFAULT_LIMIT
) -> list[sqlite3.Row]:
    """Most recent runs, newest first (run-history page)."""
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (limit,)
    ).fetchall()
    return rows


def last_successful_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Most recent run with status='success' ONLY (a partial run must trigger catch-up)."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM runs WHERE status = 'success' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return row


def last_completed_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Most recent run that made progress: status IN ('success', 'partial').

    Startup catch-up gates on THIS, not strict success. A permanently-blocked source (himalayas 403,
    naukri recaptcha, an empty feed) makes every run 'partial', so gating on success alone would
    re-run a full cycle on every `serve` boot. A 'failed' run (no source succeeded)
    is intentionally excluded so a truly empty cycle still forces a fresh catch-up.
    """
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM runs WHERE status IN ('success', 'partial') ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return row


def reconcile_orphan_runs(conn: sqlite3.Connection, clock: Clock) -> int:
    """Finalize any 'running' run left behind by a crash/kill (finish_run never ran). Returns the
    count reaped. Called at process startup: the in-process scheduler is single-instance, so a
    'running' row at boot is by definition abandoned — and if left as-is, current_run() would keep
    rejecting every future cycle (the permanent-wedge bug). Idempotent (no-op when none exist)."""
    cur = conn.execute(
        "UPDATE runs SET status = 'failed', finished_at = ?, "
        "error = COALESCE(error, 'abandoned: process restarted while a run was in progress') "
        "WHERE status = 'running'",
        (clock.now().isoformat(),),
    )
    conn.commit()
    return cur.rowcount
