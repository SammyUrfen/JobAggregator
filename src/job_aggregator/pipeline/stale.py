"""Stale-deletion with the per-source success guard (Phase 5). THE correctness crux.

Only sources that SUCCEEDED this cycle may expire their jobs. A blocked/failed source's jobs
are left untouched (they did not disappear — we just couldn't see them). See PLAN §4.5. This is
the bug the ecosystem gets wrong: a source absent from `succeeded_sources` is never iterated, so
neither UPDATE can physically reach its rows.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

logger = logging.getLogger(__name__)


def expire_stale(
    conn: sqlite3.Connection,
    run_id: int,
    succeeded_sources: set[str],
    cfg: Config,
    clock: Clock,
) -> int:
    """For each SUCCEEDED source: mark jobs not seen this cycle 'stale', then 'deleted' once
    older than grace_days. Returns newly-stale + newly-deleted. Empty succeeded set -> 0.

    Soft: status IN ('new','active') AND last_seen_cycle < run_id (seen-this-cycle rows have
    last_seen_cycle == run_id, so they are safe). Hard: status='stale' AND last_seen_at older
    than the grace cutoff. 'deleted' rows are never re-touched (idempotent); resurrection is
    upsert_job's job.
    """
    grace_days = cfg.schedule.grace_days
    cutoff_iso = (clock.now() - timedelta(days=grace_days)).isoformat()
    cur = conn.cursor()
    n = 0
    for source in sorted(succeeded_sources):  # sorted -> reproducible
        cur.execute(
            "UPDATE jobs SET status='stale' "
            "WHERE source=? AND last_seen_cycle<? AND status IN ('new','active')",
            (source, run_id),
        )
        n += cur.rowcount
        cur.execute(
            "UPDATE jobs SET status='deleted' "
            "WHERE source=? AND status='stale' AND julianday(last_seen_at) < julianday(?)",
            (source, cutoff_iso),
        )
        n += cur.rowcount
    conn.commit()
    logger.debug("expire_stale run=%d sources=%d expired=%d", run_id, len(succeeded_sources), n)
    return n
