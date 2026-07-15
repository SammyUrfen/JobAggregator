"""runs + source_runs access (Phase 1)."""

from __future__ import annotations

import sqlite3

from job_aggregator.clock import Clock


def start_run(conn: sqlite3.Connection, trigger: str, clock: Clock) -> int:
    """INSERT a runs row (status='running') and return its run_id. Phase 1."""
    raise NotImplementedError("Phase 1: start run")


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    *,
    n_sources_ok: int,
    n_sources_err: int,
    n_new: int,
    n_updated: int,
    n_expired: int,
    clock: Clock,
    error: str | None = None,
) -> None:
    """Finalize a run row with counts + finished_at. Phase 1."""
    raise NotImplementedError("Phase 1: finish run")


def record_source_run(
    conn: sqlite3.Connection,
    run_id: int,
    source: str,
    *,
    succeeded: bool,
    n_fetched: int | None,
    duration_ms: int | None,
    error: str | None = None,
) -> None:
    """INSERT one source_runs row. `succeeded` gates stale-deletion (PLAN §4.5). Phase 1."""
    raise NotImplementedError("Phase 1: record source run")


def current_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The run with status='running', if any (used by the run-lock + /api/runs/current)."""
    raise NotImplementedError("Phase 1: current running run")


def recent_runs(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    raise NotImplementedError("Phase 1: recent runs")


def last_successful_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Most recent run with status='success' ONLY — drives catch-up (PLAN §6, Phase 6)."""
    raise NotImplementedError("Phase 1: last successful run")
