"""jobs table access (Phase 1). Upsert with user-flag preservation; filtered queries."""

from __future__ import annotations

import sqlite3

from job_aggregator.clock import Clock
from job_aggregator.models.job import Job


def upsert_job(conn: sqlite3.Connection, job: Job, run_id: int, clock: Clock) -> str:
    """INSERT a new job (status='new') or UPDATE an existing one by job_uid.

    On UPDATE: refresh last_seen_at/last_seen_cycle, promote status to 'active', refresh
    mutable fields (salary_*, description, url, match_score) — but NEVER overwrite the user
    fields applied/bookmarked/hidden/notes (ON CONFLICT DO UPDATE must exclude them).
    Returns 'new' or 'updated'. See PLAN §4.1 step 6, §4.2. Phase 1."""
    raise NotImplementedError("Phase 1: upsert with user-flag preservation")


def get_jobs(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    source: str | None = None,
    remote: bool | None = None,
    bucket: str | None = None,
    status: list[str] | None = None,
    applied: bool | None = None,
    bookmarked: bool | None = None,
    include_hidden: bool = False,
    sort: str = "score",
    limit: int = 50,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """Filtered/sorted/paginated jobs for the dashboard. Default excludes 'deleted' and
    hidden. sort in {score, posted, company}. Phase 1 (consumed by Phase 8)."""
    raise NotImplementedError("Phase 1: filtered job query")


def set_user_flag(conn: sqlite3.Connection, job_uid: str, field: str, value: object) -> None:
    """Set one of applied|bookmarked|hidden|notes on a job. Validate `field`. Phase 1."""
    raise NotImplementedError("Phase 1: set user flag")


def count_jobs(conn: sqlite3.Connection, **filters: object) -> int:
    """Total rows matching the same filters as get_jobs (for pagination). Phase 1."""
    raise NotImplementedError("Phase 1: count filtered jobs")


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {status: count} for dashboard headers. Phase 1."""
    raise NotImplementedError("Phase 1: status counts")


def jobs_new_in_run(conn: sqlite3.Connection, run_id: int) -> list[Job]:
    """Jobs whose status became 'new' in this run — the notify (new-only) set. Phase 1/7."""
    raise NotImplementedError("Phase 1: new jobs in run")


def recent_active_jobs(conn: sqlite3.Connection, limit: int) -> list[Job]:
    """Most recent active jobs for the RSS feed. Phase 1/7."""
    raise NotImplementedError("Phase 1: recent active jobs")


def _row_to_job(row: sqlite3.Row) -> Job:
    """Map a jobs row -> Job model (persistence-only columns dropped). Phase 1."""
    raise NotImplementedError("Phase 1: row to Job")
