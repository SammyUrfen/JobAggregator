"""In-process daily scheduler with startup catch-up + run-lock (Phase 6). See PLAN §6.

APScheduler `BackgroundScheduler` (3.x): a cycle is blocking sqlite/httpx work, so it runs on
the scheduler's own pool thread, never an event loop. Each run opens its OWN sqlite connection
inside the job body (connections aren't thread-safe to share) via the `connect_fn` factory.

Overlap protection: a process-local non-blocking `threading.Lock` funnels manual + scheduled runs
WITHIN this process (they never overlap), plus a `runs_repo.current_run` DB check. The DB check is
best-effort across PROCESSES — the check-then-insert is not one atomic transaction — so in a
multi-process deployment (e.g. a systemd `.timer` running `job-aggregator run`), don't also let
the in-process scheduler run: set `JOBAGG_DISABLE_SCHEDULER=1` for `serve`. Orphaned 'running' rows
left by a crash are reaped at startup by `reconcile_orphan_runs`.
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
CATCHUP_POLL_JOB_ID = "catchup_poll"
# Generous misfire grace: a laptop asleep at run_hour still fires on wake within the window.
MISFIRE_GRACE_SECONDS = 3600
MAX_INSTANCES = 1
# Catch up if the last COMPLETED run (success OR partial) is older than this threshold.
CATCH_UP_THRESHOLD = timedelta(hours=24)
# How often to re-check "is a fetch overdue?" The daily cron only fires at run_hour with a 1h
# misfire grace, so a laptop asleep THROUGH run_hour and woken hours later (past the grace) would
# skip that day entirely with no catch-up until the process restarts. This periodic due-check is
# the safety net: after wake the interval fires on its next tick and catches up. Cheap (one DB
# read per tick; a cycle runs only when >24h overdue, so at most once/day).
CATCHUP_POLL_MINUTES = 30


class JobScheduler:
    def __init__(self, connect_fn: Callable[[], object], clock: Clock) -> None:
        self._connect_fn = connect_fn
        self._clock = clock
        self._lock = threading.Lock()
        self._scheduler: BackgroundScheduler | None = None

    def start(self) -> None:
        """Register the daily cron + the periodic catch-up poll, start the scheduler, then run
        startup catch-up."""
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        from job_aggregator.config.store import load_effective_config
        from job_aggregator.storage import runs_repo

        conn = cast("sqlite3.Connection", self._connect_fn())
        try:
            run_hour = load_effective_config(conn).schedule.run_hour_local
            reaped = runs_repo.reconcile_orphan_runs(conn, self._clock)
            if reaped:
                log.warning("reconciled %d orphaned 'running' run(s) from a prior crash", reaped)
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
        # Safety net for sleep/wake + cron misfires (see CATCHUP_POLL_MINUTES): re-check whether a
        # fetch is overdue on a fixed interval. misfire_grace_time=None so the FIRST tick after the
        # laptop wakes fires regardless of how long it slept; coalesce collapses backlog to one.
        self._scheduler.add_job(
            self._catch_up_if_due,
            trigger=IntervalTrigger(minutes=CATCHUP_POLL_MINUTES),
            args=[TRIGGER_CATCHUP],
            id=CATCHUP_POLL_JOB_ID,
            replace_existing=True,
            misfire_grace_time=None,
            coalesce=True,
            max_instances=MAX_INSTANCES,
        )
        self._scheduler.start()
        log.info(
            "scheduler started; daily run at %02d:00 local, catch-up poll every %dm",
            run_hour,
            CATCHUP_POLL_MINUTES,
        )
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

    def _is_catch_up_due(self) -> bool:
        """True if catch-up is enabled AND the last COMPLETED run (success or partial) is older
        than ~24h. Gating on 'completed' rather than strict success means a permanently-blocked
        source (himalayas 403 / naukri recaptcha) — which keeps every run 'partial' — doesn't
        force a re-run every check; a 'failed' run (nothing fetched) is excluded so it still does.
        Opens + closes its own connection (called from the scheduler's pool thread)."""
        from job_aggregator.config.store import load_effective_config
        from job_aggregator.storage import runs_repo

        conn = cast("sqlite3.Connection", self._connect_fn())
        try:
            if not load_effective_config(conn).schedule.catch_up_on_startup:
                return False
            last_row = runs_repo.last_completed_run(conn)
        finally:
            conn.close()
        return self._should_catch_up(
            _run_finished_at(last_row), self._clock.now(), CATCH_UP_THRESHOLD
        )

    def catch_up_on_startup(self) -> None:
        """Submit an immediate run at process start if a fetch is overdue (the sleeping-laptop
        reality). The periodic poll (below) covers a sleep/wake WHILE the process keeps running."""
        if self._is_catch_up_due():
            self._submit_async(TRIGGER_CATCHUP)
        else:
            log.info("recent run completed; startup catch-up not needed")

    def _catch_up_if_due(self, trigger: str) -> None:
        """Periodic (every CATCHUP_POLL_MINUTES) due-check — the sleep/wake + cron-misfire safety
        net. Runs SYNCHRONOUSLY on the scheduler pool thread when overdue; the run-lock + DB
        current-run check make it a no-op if the daily cron (or another poll) is already running,
        so it can never double-fetch. Never raises (a failed cycle is logged by _run_locked)."""
        if self._is_catch_up_due():
            log.info("catch-up poll: a fetch is overdue; running now")
            self._run_locked(trigger)

    @staticmethod
    def _should_catch_up(
        last_completed: datetime | None, now: datetime, threshold: timedelta
    ) -> bool:
        if last_completed is None:  # never made progress (no run, or last cycle fetched nothing)
            return True
        return (now - last_completed) >= threshold

    def trigger_now(self, trigger: str = TRIGGER_MANUAL) -> int | str:
        """Run synchronously now. Returns the run_id, or the sentinel "busy" (another run holds
        the lock / an active DB run exists) or "failed" (the cycle raised — details in the log).
        Distinct sentinels because the dashboard must not report a CRASHED run as "already in
        progress" (that alias sent the user chasing a phantom run)."""
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

    def _run_locked(self, trigger: str) -> int | str:
        from job_aggregator.config.store import load_effective_config
        from job_aggregator.pipeline.runner import run_cycle
        from job_aggregator.storage import runs_repo

        if not self._lock.acquire(blocking=False):
            log.warning("a run is already in progress in this process; skipping %s", trigger)
            return "busy"
        conn: sqlite3.Connection | None = None
        try:
            conn = cast("sqlite3.Connection", self._connect_fn())
            if runs_repo.current_run(conn) is not None:
                log.warning("an active run exists in the DB; skipping %s", trigger)
                return "busy"
            cfg = load_effective_config(conn)
            summary = run_cycle(conn, cfg, self._clock, trigger=trigger)
            log.info("run finished (%s): %s", trigger, summary)
            return summary.run_id
        except Exception:
            log.exception("run cycle raised for trigger=%s", trigger)
            return "failed"
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
