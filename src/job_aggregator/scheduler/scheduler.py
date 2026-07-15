"""In-process daily scheduler with startup catch-up + run-lock (Phase 6). See PLAN §6.

APScheduler BackgroundScheduler (3.x). Daily CronTrigger(hour=run_hour_local). Because a
laptop sleeps, catch_up_on_startup() runs a cycle if none succeeded in ~24h. A threading.Lock
plus a DB 'running' check prevents manual + scheduled overlap. Each run opens its OWN sqlite
connection (connections are not thread-safe to share).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from job_aggregator.clock import Clock


class JobScheduler:
    def __init__(
        self,
        connect_fn: Callable[[], object],  # returns a fresh sqlite3.Connection per run
        clock: Clock,
    ) -> None:
        raise NotImplementedError("Phase 6: init APScheduler + lock")

    def start(self) -> None:
        """Add the daily cron job, start the scheduler, then catch_up_on_startup()."""
        raise NotImplementedError("Phase 6: start")

    def stop(self) -> None:
        raise NotImplementedError("Phase 6: shutdown")

    def catch_up_on_startup(self) -> None:
        raise NotImplementedError("Phase 6: run now if no success in ~24h")

    def trigger_now(self, trigger: str = "manual") -> int | None:
        """Submit a run if none in progress; returns run_id or None if already running."""
        raise NotImplementedError("Phase 6: trigger now (locked)")

    @property
    def next_run_at(self) -> datetime | None:
        """Scheduled time of the next daily cron fire, or None if not started. Drives the
        dashboard header "next run" display (PLAN §7). Phase 6."""
        raise NotImplementedError("Phase 6: next scheduled run time")
