"""Runs routes (Phase 8): GET /runs (history) + POST /api/runs (trigger) + status poll.

'Run now' offloads the blocking scheduler call off the event loop; a busy scheduler maps to a
409. The current-run poller (JS) stops as soon as status leaves 'running'.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from job_aggregator.dashboard.deps import (
    SchedulerProtocol,
    get_conn,
    get_scheduler,
    get_templates,
    header_context,
)
from job_aggregator.errors import RunInProgressError

router = APIRouter()

RUNS_HISTORY_LIMIT = 50


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _list_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT run_id, trigger, status, started_at, finished_at, n_new, n_updated, n_expired, "
        "error FROM runs ORDER BY started_at DESC LIMIT ?",
        (RUNS_HISTORY_LIMIT,),
    ).fetchall()
    return rows


def _source_runs(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT source, succeeded, n_fetched, duration_ms, error FROM source_runs "
        "WHERE run_id = ? ORDER BY source",
        (run_id,),
    ).fetchall()
    return rows


def _current_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The running row if any, else the most recent run (None only when there are no runs)."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM runs WHERE status = 'running' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    return row


@router.get("/runs", response_class=HTMLResponse)
def runs_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    scheduler: SchedulerProtocol = Depends(get_scheduler),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    current = _current_run(conn)
    source_runs = _source_runs(conn, current["run_id"]) if current is not None else []
    context: dict[str, Any] = {
        **header_context(conn, scheduler),
        "runs": _list_runs(conn),
        "current": current,
        "source_runs": source_runs,
    }
    return templates.TemplateResponse(request, "runs.html", context)


@router.post("/api/runs", status_code=202)
async def run_now(scheduler: SchedulerProtocol = Depends(get_scheduler)) -> dict[str, Any]:
    run_id = await run_in_threadpool(scheduler.trigger_now, "manual")  # off the event loop
    if run_id is None:
        raise RunInProgressError("a run is already in progress")
    return {"run_id": run_id, "status": "running"}


@router.get("/api/runs/current")
def current_run_status(
    conn: sqlite3.Connection = Depends(get_conn),
    scheduler: SchedulerProtocol = Depends(get_scheduler),
) -> dict[str, Any]:
    current = _current_run(conn)
    if current is None:
        return {"status": "idle", "next_run_at": _iso(scheduler.next_run_at)}
    return {
        "run_id": current["run_id"],
        "status": current["status"],
        "trigger": current["trigger"],
        "counts": {
            "new": current["n_new"],
            "updated": current["n_updated"],
            "expired": current["n_expired"],
        },
        "sources": [dict(s) for s in _source_runs(conn, current["run_id"])],
        "next_run_at": _iso(scheduler.next_run_at),
    }
