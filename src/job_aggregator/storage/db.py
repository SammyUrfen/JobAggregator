"""SQLite connection + schema init (Phase 1). Hand-written SQL, WAL mode, no ORM.

Two invariants this module upholds (PLAN §1):
- One `sqlite3.Connection` per thread (`check_same_thread=True`, the default). The scheduler
  opens its own connection inside each run; the dashboard opens one per request. WAL makes a
  single writer + concurrent readers safe across those separate connections.
- `PRAGMA foreign_keys` is per-connection and NOT persisted in the file, so `connect()` must
  re-issue it every time — otherwise the stale-delete FK guard silently goes dark.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from job_aggregator.paths import SCHEMA_SQL_PATH

# How long a blocked writer waits for the WAL lock before raising "database is locked".
# 5s comfortably covers our single-writer workload; a run never contends with itself.
BUSY_TIMEOUT_MS = 5000
# Forward-only schema version stamped in PRAGMA user_version; bump when a migration lands.
# v2: jobs.is_internship column + title-regex backfill of existing rows.
# v3: jobs.extra_context column (user context feeding tailoring + apply field-fill).
SCHEMA_VERSION = 3

_MEMORY_DB = ":memory:"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with `row_factory=sqlite3.Row` and the WAL/foreign_keys pragmas.

    Creates the parent directory for a file DB. Each run/request must open its own connection
    (sqlite3 connections are not shareable across threads).
    """
    path_str = str(db_path)
    if path_str != _MEMORY_DB:
        Path(path_str).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path_str)
    conn.row_factory = sqlite3.Row
    # WAL: durable single-writer + lock-free readers across separate connections (laptop-safe).
    conn.execute("PRAGMA journal_mode = WAL")
    # Per-connection and not persisted — re-issue so FK constraints (stale-delete guard) are live.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Apply `storage/schema.sql` idempotently (executescript), commit, then run migrations."""
    conn.executescript(SCHEMA_SQL_PATH.read_text())
    conn.commit()
    migrate(conn)


def migrate(conn: sqlite3.Connection) -> None:
    """Forward-only migration keyed on `PRAGMA user_version`. v0->v1 just stamps the version;
    v1->v2 adds jobs.is_internship and backfills it from titles; v2->v3 adds jobs.extra_context."""
    row = conn.execute("PRAGMA user_version").fetchone()
    current: int = 0 if row is None else int(row[0])
    _V2, _V3 = 2, 3  # migration ids  # noqa: N806 - read as constants
    if current < _V2:
        _migrate_v2_is_internship(conn)
    if current < _V3:
        _add_column_if_absent(conn, "extra_context", "TEXT")
    if current < SCHEMA_VERSION:
        # PRAGMA does not accept bound params; SCHEMA_VERSION is an int constant we control,
        # so interpolating it is safe (never user input).
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


def _add_column_if_absent(conn: sqlite3.Connection, name: str, decl: str) -> None:
    """Idempotent `ALTER TABLE jobs ADD COLUMN` — a fresh schema.sql already has the column, so
    only an upgraded older DB needs it. `name`/`decl` are our own constants, never user input."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    if name not in cols:
        conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")
    conn.commit()


def _migrate_v2_is_internship(conn: sqlite3.Connection) -> None:
    """Add jobs.is_internship (if absent — a fresh schema.sql already has it) and backfill from
    titles with the SAME detector new rows use, so old and new rows agree."""
    from job_aggregator.pipeline.filters import detect_internship

    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "is_internship" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN is_internship INTEGER NOT NULL DEFAULT 0")
    # Index access, not r["title"]: migrate() must work on any connection, row_factory or not.
    rows = conn.execute("SELECT job_uid, title FROM jobs").fetchall()
    intern_uids = [(r[0],) for r in rows if detect_internship(r[1])]
    if intern_uids:
        conn.executemany("UPDATE jobs SET is_internship = 1 WHERE job_uid = ?", intern_uids)
    conn.commit()
