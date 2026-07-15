"""SQLite connection + schema init (Phase 1). Hand-written SQL, WAL mode, no ORM."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with row_factory=sqlite3.Row and the WAL/foreign_keys pragmas.
    NOTE: sqlite3 connections are not shareable across threads — each run/request opens
    its own (see scheduler Phase 6, dashboard deps Phase 8)."""
    raise NotImplementedError("Phase 1: connect with Row factory + pragmas")


def init_db(conn: sqlite3.Connection) -> None:
    """Apply storage/schema.sql idempotently (executescript). Phase 1."""
    raise NotImplementedError("Phase 1: apply schema.sql")


def migrate(conn: sqlite3.Connection) -> None:
    """Placeholder for forward migrations. v0.1 has none. Phase 1."""
    raise NotImplementedError("Phase 1: no-op for now")
