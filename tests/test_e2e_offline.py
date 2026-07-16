"""Phase 9 — offline end-to-end: a full run_cycle over a monkeypatched registry, no network.

Exercises the real dispatcher path (_build_sources -> registry.build_enabled_sources) and the
whole lifecycle: dedup collapse -> idempotent re-upsert -> guarded stale -> grace-boundary delete.
"""

from __future__ import annotations

import sqlite3

import pytest

from _fakes import FakeSource, make_job
from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.pipeline.runner import run_cycle
from job_aggregator.sources import registry


def _visible(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM jobs WHERE status != 'deleted'").fetchone()[0])


@pytest.mark.slow
def test_offline_full_lifecycle(
    conn: sqlite3.Connection,
    clock: FixedClock,
    sample_config: Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _sources(jobs: list) -> list:  # type: ignore[type-arg]
        return [FakeSource("fake", jobs)]

    three = [
        make_job("dup", source="fake", url="https://x/1"),
        make_job("dup", source="fake", url="https://x/1"),  # near-duplicate -> collapses
        make_job("solo", source="fake", url="https://x/2"),
    ]

    # cycle 1: 3 postings, two collapse -> 2 new rows.
    monkeypatch.setattr(registry, "build_enabled_sources", lambda cfg: _sources(list(three)))
    s1 = run_cycle(conn, sample_config, clock, "manual", notifiers=[])
    assert s1.n_new == 2
    assert _visible(conn) == 2

    # cycle 2: same postings -> idempotent upsert (0 new, 3 updated), still 2 rows.
    s2 = run_cycle(conn, sample_config, clock, "manual", notifiers=[])
    assert (s2.n_new, s2.n_updated) == (0, 3)
    assert _visible(conn) == 2

    # cycle 3: source now empty (succeeded) -> jobs go stale but stay within grace.
    monkeypatch.setattr(registry, "build_enabled_sources", lambda cfg: _sources([]))
    clock.advance(days=sample_config.schedule.grace_days - 1)
    run_cycle(conn, sample_config, clock, "manual", notifiers=[])
    assert _visible(conn) == 2  # stale, not yet deleted

    # cycle 4: past the grace boundary -> deleted (gone from the default view).
    clock.advance(days=2)
    s4 = run_cycle(conn, sample_config, clock, "manual", notifiers=[])
    assert s4.n_expired > 0
    assert _visible(conn) == 0
