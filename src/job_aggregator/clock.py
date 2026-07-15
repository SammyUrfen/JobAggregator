"""A tiny injectable clock so time-dependent logic (stale-deletion, run bookkeeping,
catch-up-on-startup) is deterministic under test.

Every function that needs "now" takes a `Clock` argument rather than calling
`datetime.now()` directly. Tests pass a `FixedClock`; production passes a `SystemClock`.
This is the same discipline as dependency-injecting a clock in a distributed system — it
keeps the stale-delete grace-window tests exact (PLAN §4.5, Phase 5).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    """Production clock: real wall-clock time in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Test clock: returns a fixed instant, advanceable via `advance()`."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        self._instant = instant

    def now(self) -> datetime:
        return self._instant

    def advance(self, *, seconds: float = 0, days: float = 0) -> None:
        from datetime import timedelta

        self._instant = self._instant + timedelta(seconds=seconds, days=days)
