"""In-process daily scheduler with startup catch-up + run-lock (Phase 6). See PLAN §6.

APScheduler `BackgroundScheduler` (3.x): a cycle is blocking sqlite/httpx work, so it runs on
the scheduler's own pool thread, never an event loop. Each run opens its OWN sqlite connection
inside the job body (connections aren't thread-safe to share) via the `connect_fn` factory. One
lock funnel: a process-local non-blocking `threading.Lock` PLUS a `runs_repo.current_run` DB
check, so manual + scheduled + cross-process runs never overlap.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from apscheduler.schedulers.background import BackgroundScheduler

    from job_aggregator.clock import Clock

log = logging.getLogger(__name__)

# Trigger vocabulary — frozen by schema.sql's runs.trigger CHECK.
TRIGGER_SCHEDULE = "schedule"
TRIGGER_MANUAL = "manual"
TRIGGER_CATCHUP = "startup_catchup"

DAILY_JOB_ID = "daily_cycle"
IMMEDIATE_JOB_ID = "immediate_cycle"
# Generous misfire grace: a laptop asleep at run_hour still fires on wake within the window.
MISFIRE_GRACE_SECONDS = 3600
MAX_INSTANCES = 1
# Catch up if the last SUCCESS is older than this (daily cadence + slack).
CATCH_UP_THRESHOLD = timedelta(hours=24)


class JobScheduler:
    def __init__(self, connect_fn: Callable[[], object], clock: Clock) -> None:
        self._connect_fn = connect_fn
        self._clock = clock
        self._lock = threading.Lock()
        self._scheduler: BackgroundScheduler | None = None

    def start(self) -> None:
        """Register the daily cron job, start the scheduler, then run startup catch-up."""
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger

        from job_aggregator.config.store import load_effective_config

        conn = cast("sqlite3.Connection", self._connect_fn())
        try:
            run_hour = load_effective_config(conn).schedule.run_hour_local
        finally:
            conn.close()
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(
            self._run_locked,
            trigger=CronTrigger(hour=run_hour),
            args=[TRIGGER_SCHEDULE],
            id=DAILY_JOB_ID,
            replace_existing=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
            coalesce=True,
            max_instances=MAX_INSTANCES,
        )
        self._scheduler.start()
        log.info("scheduler started; daily run at %02d:00 local", run_hour)
        self.catch_up_on_startup()

    def stop(self) -> None:
        """Shut down without waiting for a running job. Safe if never started."""
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("scheduler stopped")

    @property
    def next_run_at(self) -> datetime | None:
        if self._scheduler is None:
            return None
        job = self._scheduler.get_job(DAILY_JOB_ID)
        if job is None:
            return None
        result: datetime | None = getattr(job, "next_run_time", None)
        return result

    def catch_up_on_startup(self) -> None:
        """Submit an immediate run if no SUCCESS in the last ~24h (the sleeping-laptop reality)."""
        from job_aggregator.config.store import load_effective_config
        from job_aggregator.storage import runs_repo

        conn = cast("sqlite3.Connection", self._connect_fn())
        try:
            if not load_effective_config(conn).schedule.catch_up_on_startup:
                log.info("startup catch-up disabled; skipping")
                return
            last_row = runs_repo.last_successful_run(conn)
        finally:
            conn.close()
        last = _run_finished_at(last_row)
        if self._should_catch_up(last, self._clock.now(), CATCH_UP_THRESHOLD):
            self._submit_async(TRIGGER_CATCHUP)
        else:
            log.info("recent success at %s; catch-up not needed", last)

    @staticmethod
    def _should_catch_up(
        last_success: datetime | None, now: datetime, threshold: timedelta
    ) -> bool:
        if last_success is None:  # never ran a successful cycle
            return True
        return (now - last_success) >= threshold

    def trigger_now(self, trigger: str = TRIGGER_MANUAL) -> int | None:
        """Run synchronously now; returns the run_id, or None if a run is already in progress."""
        return self._run_locked(trigger)

    def _submit_async(self, trigger: str) -> None:
        if self._scheduler is None or not self._scheduler.running:
            raise RuntimeError("scheduler not started; call start() first")
        self._scheduler.add_job(
            self._run_locked,
            args=[trigger],
            id=f"{IMMEDIATE_JOB_ID}:{trigger}",
            replace_existing=True,
            coalesce=True,
            max_instances=MAX_INSTANCES,
            misfire_grace_time=None,
        )

    def _run_locked(self, trigger: str) -> int | None:
        from job_aggregator.config.store import load_effective_config
        from job_aggregator.pipeline.runner import run_cycle
        from job_aggregator.storage import runs_repo

        if not self._lock.acquire(blocking=False):
            log.warning("a run is already in progress in this process; skipping %s", trigger)
            return None
        conn: sqlite3.Connection | None = None
        try:
            conn = cast("sqlite3.Connection", self._connect_fn())
            if runs_repo.current_run(conn) is not None:
                log.warning("an active run exists in the DB; skipping %s", trigger)
                return None
            cfg = load_effective_config(conn)
            summary = run_cycle(conn, cfg, self._clock, trigger=trigger)
            log.info("run finished (%s): %s", trigger, summary)
            return summary.run_id
        except Exception:
            log.exception("run cycle raised for trigger=%s", trigger)
            return None
        finally:
            if conn is not None:
                conn.close()
            self._lock.release()


def _run_finished_at(row: Any) -> datetime | None:
    """The finished (or started) time of a runs row as an aware UTC datetime, or None."""
    if row is None:
        return None
    raw = row["finished_at"] or row["started_at"]
    if raw is None:
        return None
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
