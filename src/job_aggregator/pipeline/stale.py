"""Stale-deletion with the per-source success guard (Phase 5). THE correctness crux.

Only sources that SUCCEEDED this cycle may expire their jobs. A blocked/failed source's
jobs are left untouched (they did not disappear, we just couldn't see them). See PLAN §4.5.
"""

from __future__ import annotations

import sqlite3

from job_aggregator.clock import Clock
from job_aggregator.config.schema import Config


def expire_stale(
    conn: sqlite3.Connection,
    run_id: int,
    succeeded_sources: set[str],
    cfg: Config,
    clock: Clock,
) -> int:
    """For each succeeded source: mark not-seen-this-cycle jobs 'stale', then 'deleted' once
    older than cfg.schedule.grace_days. Returns the number newly expired. Phase 5."""
    raise NotImplementedError("Phase 5: stale-delete with success guard")
