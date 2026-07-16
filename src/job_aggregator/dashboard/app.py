"""FastAPI app factory (Phase 8). Owns the daily scheduler via the lifespan context.

Server-rendered (sync `def` handlers run in Starlette's threadpool, so blocking sqlite3 is
fine). `create_app()` with no args fully works (uvicorn factory=True); tests inject
db_path/clock/scheduler. The app never touches the DB at build time.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from job_aggregator.dashboard import routes_config, routes_jobs, routes_runs
from job_aggregator.dashboard.deps import error_envelope, status_for
from job_aggregator.errors import JobAggregatorError
from job_aggregator.paths import STATIC_DIR, TEMPLATES_DIR, default_db_path

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from job_aggregator.clock import Clock
    from job_aggregator.dashboard.deps import SchedulerProtocol

log = logging.getLogger(__name__)


def _ensure_initialized(db_path: str) -> None:
    """Idempotently create the schema + seed config so `serve` works on a fresh machine (no
    separate `initdb` needed). CREATE IF NOT EXISTS + seed-only-if-absent make this a no-op on
    an already-initialized DB."""
    from job_aggregator.config.store import seed_from_yaml
    from job_aggregator.storage.db import connect, init_db

    conn = connect(db_path)
    try:
        init_db(conn)
        seed_from_yaml(conn)
    finally:
        conn.close()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    _ensure_initialized(app.state.db_path)  # first-run friendliness: no bare 500 before initdb
    # JOBAGG_DISABLE_SCHEDULER lets an OS-timer deployment (Phase 9) run without the in-process one.
    manage = not os.environ.get("JOBAGG_DISABLE_SCHEDULER")
    if manage:
        app.state.scheduler.start()
    try:
        yield
    finally:
        if manage:
            app.state.scheduler.stop()


def create_app(
    *,
    db_path: str | Path | None = None,
    clock: Clock | None = None,
    scheduler: SchedulerProtocol | None = None,
) -> FastAPI:
    from job_aggregator.clock import SystemClock
    from job_aggregator.scheduler.scheduler import JobScheduler
    from job_aggregator.storage.db import connect

    resolved_db = str(db_path or default_db_path())
    resolved_clock: Clock = clock or SystemClock()
    resolved_scheduler: SchedulerProtocol = scheduler or JobScheduler(
        connect_fn=lambda: connect(resolved_db), clock=resolved_clock
    )

    app = FastAPI(title="JobAggregator", lifespan=_lifespan)
    app.state.db_path = resolved_db
    app.state.clock = resolved_clock
    app.state.scheduler = resolved_scheduler
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(routes_jobs.router)
    app.include_router(routes_config.router)
    app.include_router(routes_runs.router)
    _register_handlers(app)
    return app


def _register_handlers(app: FastAPI) -> None:
    @app.exception_handler(JobAggregatorError)
    async def _on_app_error(request: Request, exc: JobAggregatorError) -> JSONResponse:
        return error_envelope(
            exc.code.value, exc.message, status_for(exc.code), exc.details or None
        )

    @app.exception_handler(RequestValidationError)
    async def _on_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return error_envelope(
            "validation_error", "request validation failed", 422, {"errors": errors}
        )

    @app.exception_handler(Exception)
    async def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled dashboard error")
        return error_envelope("internal", "internal server error", 500)
