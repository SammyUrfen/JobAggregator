"""jobs table access (Phase 1). Upsert with user-flag preservation; filtered queries.

Invariants (PLAN §1 + §4.1):
- Bookkeeping timestamps are UTC ISO-8601 from `clock.now().isoformat()` so lexicographic
  ordering == chronological. `posted_at` is display-only (may carry a source's own tz).
- USER FLAGS (`applied`/`bookmarked`/`hidden`/`notes`) MUST survive a re-scrape: the upsert's
  ON CONFLICT clause never touches them, and `source`/`url`/`first_seen_at` keep first-seen
  provenance while everything else (salary, description, score, ...) is refreshed.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Literal

from job_aggregator.clock import Clock
from job_aggregator.models.job import Job, JobStatus, SalaryBucket

UpsertOutcome = Literal["new", "updated"]

# Columns the user owns (or that are expensively cached); a source re-fetch must never overwrite
# them — none appear in the upsert's INSERT/DO UPDATE, so they default on insert + persist on
# conflict. full_description is fetched on demand (Internshala's real JD) and must not be
# clobbered by the source's short listing text.
_USER_FLAG_FIELDS = frozenset(
    {"applied", "bookmarked", "hidden", "seen", "notes", "extra_context", "full_description"}
)
_BOOL_FLAG_FIELDS = frozenset({"applied", "bookmarked", "hidden", "seen"})

# Dashboard pagination bounds.
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

# The ONLY sort expressions get_jobs will interpolate — a closed whitelist keeps it
# injection-proof. Unknown `sort` values fall back to "score".
_SORT_SQL = {
    "score": "match_score DESC, posted_at DESC",
    "posted": "posted_at DESC",
    "company": "company COLLATE NOCASE ASC, title COLLATE NOCASE ASC",
}

# INSERT ... ON CONFLICT DO UPDATE. The DO UPDATE set deliberately EXCLUDES source,
# source_native_id, url, first_seen_at (first-seen provenance) and every user flag.
_UPSERT_SQL = """
INSERT INTO jobs (
    job_uid, source, source_native_id, title, company, location, is_remote, url,
    description, salary_min, salary_max, salary_currency, salary_period, salary_raw,
    salary_parsed, salary_bucket, match_score, is_internship, posted_at,
    first_seen_at, last_seen_at, last_seen_cycle, status
) VALUES (
    :job_uid, :source, :source_native_id, :title, :company, :location, :is_remote, :url,
    :description, :salary_min, :salary_max, :salary_currency, :salary_period, :salary_raw,
    :salary_parsed, :salary_bucket, :match_score, :is_internship, :posted_at,
    :now, :now, :run_id, :status_new
)
ON CONFLICT(job_uid) DO UPDATE SET
    last_seen_at=excluded.last_seen_at, last_seen_cycle=excluded.last_seen_cycle,
    status=:status_active,
    title=excluded.title, company=excluded.company, location=excluded.location,
    is_remote=excluded.is_remote, description=excluded.description,
    salary_min=excluded.salary_min, salary_max=excluded.salary_max,
    salary_currency=excluded.salary_currency, salary_period=excluded.salary_period,
    salary_raw=excluded.salary_raw, salary_parsed=excluded.salary_parsed,
    salary_bucket=excluded.salary_bucket, match_score=excluded.match_score,
    is_internship=excluded.is_internship, posted_at=excluded.posted_at
    -- NOT updated: source, source_native_id, url, first_seen_at (provenance);
    --             applied, bookmarked, hidden, notes (USER FLAGS MUST SURVIVE UPSERTS)
"""


def upsert_job(conn: sqlite3.Connection, job: Job, run_id: int, clock: Clock) -> UpsertOutcome:
    """INSERT a new job (status='new') or refresh an existing one by job_uid (status='active').

    Returns `"new"` on INSERT, `"updated"` on conflict (Phase 5 counts n_new via `== "new"`).
    """
    now = clock.now().isoformat()
    existed = (
        conn.execute("SELECT 1 FROM jobs WHERE job_uid = ?", (job.job_uid,)).fetchone() is not None
    )
    params: dict[str, object] = {
        "job_uid": job.job_uid,
        "source": job.source,
        "source_native_id": job.source_native_id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "is_remote": None if job.is_remote is None else int(job.is_remote),
        "url": job.url,
        "description": job.description,
        "salary_min": job.salary_min,
        "salary_max": job.salary_max,
        "salary_currency": job.salary_currency,
        "salary_period": job.salary_period,
        "salary_raw": job.salary_raw,
        "salary_parsed": int(job.salary_parsed),
        "salary_bucket": None if job.salary_bucket is None else job.salary_bucket.value,
        "match_score": job.match_score,
        "is_internship": int(job.is_internship),
        "posted_at": None if job.posted_at is None else job.posted_at.isoformat(),
        "now": now,
        "run_id": run_id,
        "status_new": JobStatus.NEW.value,
        "status_active": JobStatus.ACTIVE.value,
    }
    conn.execute(_UPSERT_SQL, params)
    conn.commit()
    return "updated" if existed else "new"


def _build_where(
    *,
    q: str | None,
    source: str | None,
    remote: bool | None,
    bucket: str | None,
    status: list[str] | None,
    include_hidden: bool,
    applied: bool | None,
    bookmarked: bool | None,
) -> tuple[str, dict[str, object]]:
    """Assemble a parameterized WHERE clause shared by get_jobs + count_jobs.

    Default (status is None) hides soft-deleted rows; `include_hidden=False` also drops hidden.
    All values bind as params — nothing here is interpolated.
    """
    clauses: list[str] = []
    params: dict[str, object] = {}
    if q:
        clauses.append("(title LIKE :q OR company LIKE :q)")
        params["q"] = f"%{q}%"
    if source is not None:
        clauses.append("source = :source")
        params["source"] = source
    if bucket is not None:
        clauses.append("salary_bucket = :bucket")
        params["bucket"] = bucket
    if remote is not None:
        clauses.append("is_remote = :remote")
        params["remote"] = int(remote)
    if applied is not None:
        clauses.append("applied = :applied")
        params["applied"] = int(applied)
    if bookmarked is not None:
        clauses.append("bookmarked = :bookmarked")
        params["bookmarked"] = int(bookmarked)
    if status is not None:
        if status:
            keys = [f"status_{i}" for i in range(len(status))]
            for key, value in zip(keys, status, strict=True):
                params[key] = value
            placeholders = ", ".join(f":{key}" for key in keys)
            clauses.append(f"status IN ({placeholders})")
        else:
            # Explicit empty list => match nothing (avoids invalid `IN ()`).
            clauses.append("1 = 0")
    else:
        clauses.append("status != 'deleted'")
    if not include_hidden:
        clauses.append("hidden = 0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, params


def get_jobs(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    source: str | None = None,
    remote: bool | None = None,
    bucket: str | None = None,
    status: list[str] | None = None,
    include_hidden: bool = False,
    applied: bool | None = None,
    bookmarked: bool | None = None,
    sort: str = "score",
    limit: int = DEFAULT_PAGE_LIMIT,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """Filtered/sorted/paginated jobs for the dashboard. Default excludes 'deleted' + hidden.

    `sort` in {score, posted, company} (unknown -> score). `limit` is clamped to
    [1, MAX_PAGE_LIMIT]; `offset` floored at 0.
    """
    where, params = _build_where(
        q=q,
        source=source,
        remote=remote,
        bucket=bucket,
        status=status,
        include_hidden=include_hidden,
        applied=applied,
        bookmarked=bookmarked,
    )
    order_by = _SORT_SQL.get(sort, _SORT_SQL["score"])
    params["_limit"] = max(1, min(limit, MAX_PAGE_LIMIT))
    params["_offset"] = max(0, offset)
    sql = f"SELECT * FROM jobs {where} ORDER BY {order_by} LIMIT :_limit OFFSET :_offset"
    rows: list[sqlite3.Row] = conn.execute(sql, params).fetchall()
    return rows


def count_jobs(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    source: str | None = None,
    remote: bool | None = None,
    bucket: str | None = None,
    status: list[str] | None = None,
    include_hidden: bool = False,
    applied: bool | None = None,
    bookmarked: bool | None = None,
) -> int:
    """Total rows matching the same filters as get_jobs (drives pagination)."""
    where, params = _build_where(
        q=q,
        source=source,
        remote=remote,
        bucket=bucket,
        status=status,
        include_hidden=include_hidden,
        applied=applied,
        bookmarked=bookmarked,
    )
    row = conn.execute(f"SELECT COUNT(*) AS n FROM jobs {where}", params).fetchone()
    return 0 if row is None else int(row["n"])


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {status: count} across all jobs, for dashboard headers."""
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
    return {row["status"]: int(row["n"]) for row in rows}


def set_user_flag(conn: sqlite3.Connection, job_uid: str, field: str, value: object) -> bool:
    """Set one of applied|bookmarked|hidden|notes on a job.

    Rejects any other `field` with ValueError. Bool flags coerce to 0/1; notes to str|None.
    Returns True if a row was updated, False if no job matched `job_uid`.
    """
    if field not in _USER_FLAG_FIELDS:
        raise ValueError(f"unknown user-flag field: {field!r}")
    stored: object
    if field in _BOOL_FLAG_FIELDS:
        stored = int(bool(value))
    else:  # notes
        stored = None if value is None else str(value)
    # `field` is whitelisted above, so interpolating it into the SQL is safe.
    cur = conn.execute(f"UPDATE jobs SET {field} = ? WHERE job_uid = ?", (stored, job_uid))
    conn.commit()
    return cur.rowcount > 0


def _row_to_job(row: sqlite3.Row) -> Job:
    """Map a jobs row -> Job (persistence-only columns dropped; int/ISO fields decoded)."""
    bucket = row["salary_bucket"]
    posted = row["posted_at"]
    return Job(
        job_uid=row["job_uid"],
        source=row["source"],
        source_native_id=row["source_native_id"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        is_remote=None if row["is_remote"] is None else bool(row["is_remote"]),
        url=row["url"],
        description=row["description"],
        salary_min=row["salary_min"],
        salary_max=row["salary_max"],
        salary_currency=row["salary_currency"],
        salary_period=row["salary_period"],
        salary_raw=row["salary_raw"],
        salary_parsed=bool(row["salary_parsed"]),
        salary_bucket=None if bucket is None else SalaryBucket(bucket),
        match_score=row["match_score"],
        is_internship=bool(row["is_internship"]),
        posted_at=None if posted is None else datetime.fromisoformat(posted),
    )


def jobs_new_in_run(conn: sqlite3.Connection, run_id: int) -> list[Job]:
    """Jobs that became 'new' in THIS run — the notify (new-only) set.

    A job stuck 'new' from an earlier run (its source failed since) keeps its older
    last_seen_cycle and is excluded here: "notify once, never again".
    """
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status = 'new' AND last_seen_cycle = ? ORDER BY match_score DESC",
        (run_id,),
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def recent_active_jobs(conn: sqlite3.Connection, limit: int) -> list[Job]:
    """Most recent visible active/new jobs for the RSS feed (hidden + deleted excluded)."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE status IN ('new', 'active') AND hidden = 0 "
        "ORDER BY COALESCE(posted_at, '') DESC, last_seen_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_row_to_job(r) for r in rows]
