"""FastAPI app factory (Phase 8). Owns the daily scheduler via the lifespan context."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_app() -> "FastAPI":
    """Build the FastAPI app: mount /static, configure Jinja2 (job_aggregator.paths), include
    the jobs/config/runs routers, and start/stop JobScheduler in the lifespan. Phase 8."""
    raise NotImplementedError("Phase 8: create FastAPI app + lifespan scheduler")
