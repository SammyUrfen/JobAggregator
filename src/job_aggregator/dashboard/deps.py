"""FastAPI dependencies + error envelope (Phase 8).

Per-request sqlite connection (opened/closed in the threadpool so sync handlers stay safe),
the effective Config, the scheduler + templates off app.state, and the shared JSON error
envelope. Nothing here touches app.state at import time.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol, cast

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from job_aggregator.config.store import load_effective_config
from job_aggregator.errors import ErrorCode
from job_aggregator.storage.db import connect

if TYPE_CHECKING:
    from datetime import datetime

    from job_aggregator.config.schema import Config

# HTTP status for each application error code (dashboard envelope).
_STATUS_BY_CODE = {
    ErrorCode.CONFIG_INVALID: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.RUN_IN_PROGRESS: 409,
    ErrorCode.STORAGE_ERROR: 500,
    ErrorCode.NOTIFY_FAILED: 500,
    ErrorCode.SOURCE_FETCH_FAILED: 502,
    ErrorCode.SOURCE_PARSE_FAILED: 502,
    ErrorCode.INTERNAL: 500,
}


class SchedulerProtocol(Protocol):
    """Structural scheduler interface the dashboard needs (real JobScheduler + test fakes)."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    # Returns the run_id, or "busy" / "failed" (None accepted from legacy test fakes = busy).
    def trigger_now(self, trigger: str = "manual") -> int | str | None: ...
    def reschedule_daily(self, run_hour: int) -> None: ...
    @property
    def next_run_at(self) -> datetime | None: ...


def status_for(code: ErrorCode) -> int:
    return _STATUS_BY_CODE.get(code, 500)


def error_envelope(
    code: str, message: str, status: int, details: dict[str, Any] | None = None
) -> JSONResponse:
    """The one JSON error shape: {"error": {code, message, [details]}} at `status`."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse(status_code=status, content={"error": error})


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_config(conn: sqlite3.Connection = Depends(get_conn)) -> Config:
    return load_effective_config(conn)


def get_scheduler(request: Request) -> SchedulerProtocol:
    return cast("SchedulerProtocol", request.app.state.scheduler)


def get_templates(request: Request) -> Jinja2Templates:
    return cast(Jinja2Templates, request.app.state.templates)


def header_context(conn: sqlite3.Connection, scheduler: SchedulerProtocol) -> dict[str, Any]:
    """Last-run summary + next-run time for the page header (rendered on every page)."""
    row = conn.execute(
        "SELECT run_id, status, trigger, started_at, finished_at, n_new, n_updated, n_expired, "
        "error FROM runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return {
        "last_run": dict(row) if row is not None else None,
        "next_run_at": scheduler.next_run_at,
    }
