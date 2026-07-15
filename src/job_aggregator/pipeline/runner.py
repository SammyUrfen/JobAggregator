"""run_cycle — the heart of the pipeline (Phase 5). Exact ordering in PLAN §4.1."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from job_aggregator.clock import Clock
from job_aggregator.config.schema import Config


@dataclass
class RunSummary:
    run_id: int
    status: str            # 'success' | 'partial' | 'failed'
    n_sources_ok: int
    n_sources_err: int
    n_new: int
    n_updated: int
    n_expired: int

    def __str__(self) -> str:  # human-friendly CLI output
        return (
            f"run #{self.run_id} [{self.status}] "
            f"sources ok={self.n_sources_ok} err={self.n_sources_err} | "
            f"new={self.n_new} updated={self.n_updated} expired={self.n_expired}"
        )


def run_cycle(conn: sqlite3.Connection, cfg: Config, clock: Clock, trigger: str) -> RunSummary:
    """Start run -> fetch all sources concurrently (ThreadPoolExecutor) -> record per-source
    success -> filter -> dedup-upsert -> stale-delete (guarded) -> notify (new-only) ->
    finish run. Never let one source kill the cycle. Phase 5."""
    raise NotImplementedError("Phase 5: run_cycle orchestration")
