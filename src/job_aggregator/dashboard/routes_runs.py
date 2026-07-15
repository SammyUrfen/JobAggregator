"""Runs routes (Phase 8): GET /runs history + POST /api/runs (trigger) + status poll."""

from __future__ import annotations

# from fastapi import APIRouter
# router = APIRouter()
#   GET /runs             -> runs.html (history + per-source breakdown)
#   POST /api/runs        -> scheduler.trigger_now('manual') -> 202 + run_id (409 if running)
#   GET /api/runs/current -> current running run status (for JS polling)
raise_note = "Phase 8: implement runs routes"
