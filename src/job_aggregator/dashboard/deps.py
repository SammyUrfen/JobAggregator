"""FastAPI dependencies (Phase 8): per-request DB connection + effective config."""

from __future__ import annotations

from collections.abc import Iterator


def get_conn() -> Iterator[object]:
    """Yield a per-request sqlite3.Connection (own connection per request/thread). Phase 8."""
    raise NotImplementedError("Phase 8: request-scoped connection")


def get_config() -> object:
    """Load the effective Config for a request. Phase 8."""
    raise NotImplementedError("Phase 8: load config dependency")
