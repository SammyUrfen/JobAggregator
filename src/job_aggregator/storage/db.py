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
SCHEMA_VERSION = 1

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
    """Forward-only migration keyed on `PRAGMA user_version`. v0->v1 just stamps the version."""
    row = conn.execute("PRAGMA user_version").fetchone()
    current: int = 0 if row is None else int(row[0])
    if current < SCHEMA_VERSION:
        # PRAGMA does not accept bound params; SCHEMA_VERSION is an int constant we control,
        # so interpolating it is safe (never user input).
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
