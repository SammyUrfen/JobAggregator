"""Phase 6 — scheduler.JobScheduler catch-up + run-lock.

FixedClock + fake runner; run-lock prevents overlap.

Deterministic: run_cycle / load_effective_config / runs_repo.* are monkeypatched at their source
modules (the scheduler imports them lazily inside methods, so the patched attrs are picked up).

See PLAN.md Part II (Phase 6) for the exact table-driven cases to implement.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from job_aggregator.clock import FixedClock
from job_aggregator.config import store
from job_aggregator.pipeline import runner
from job_aggregator.scheduler.scheduler import (
    CATCH_UP_THRESHOLD,
    TRIGGER_CATCHUP,
    JobScheduler,
)
from job_aggregator.storage import runs_repo

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


class _DummyConn:
    def close(self) -> None:
        pass


class _FakeScheduler:
    """Records add_job calls; pretends to be a running BackgroundScheduler."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.running = True

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.jobs.append({"func": func, **kwargs})

    def get_job(self, job_id: str) -> Any:
        return None


def _cfg(catch_up: bool) -> SimpleNamespace:
    return SimpleNamespace(schedule=SimpleNamespace(catch_up_on_startup=catch_up, run_hour_local=3))


def _ok_summary(run_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(run_id=run_id)


def _sched() -> JobScheduler:
    return JobScheduler(connect_fn=_DummyConn, clock=FixedClock(NOW))


@pytest.mark.parametrize(
    ("delta_hours", "expected"),
    [
        (None, True),  # never ran a successful cycle
        (24, True),  # exactly at threshold (>=)
        (25, True),
        (23, False),  # within threshold
        (-5, False),  # future skew -> not due
    ],
)
def test_should_catch_up(delta_hours: int | None, expected: bool) -> None:
    last = None if delta_hours is None else NOW - timedelta(hours=delta_hours)
    assert JobScheduler._should_catch_up(last, NOW, CATCH_UP_THRESHOLD) is expected


def test_catch_up_submits_when_no_prior_success(monkeypatch: pytest.MonkeyPatch) -> None:
    sched = _sched()
    fake = _FakeScheduler()
    sched._scheduler = fake
    monkeypatch.setattr(store, "load_effective_config", lambda conn: _cfg(catch_up=True))
    monkeypatch.setattr(runs_repo, "last_completed_run", lambda conn: None)
    sched.catch_up_on_startup()
    assert len(fake.jobs) == 1
    assert fake.jobs[0]["args"] == [TRIGGER_CATCHUP]


def test_catch_up_skips_when_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    sched = _sched()
    fake = _FakeScheduler()
    sched._scheduler = fake
    recent = (NOW - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(store, "load_effective_config", lambda conn: _cfg(catch_up=True))
    monkeypatch.setattr(
        runs_repo, "last_completed_run", lambda conn: {"finished_at": recent, "started_at": recent}
    )
    sched.catch_up_on_startup()
    assert fake.jobs == []


def test_catch_up_disabled_never_submits(monkeypatch: pytest.MonkeyPatch) -> None:
    sched = _sched()
    fake = _FakeScheduler()
    sched._scheduler = fake
    monkeypatch.setattr(store, "load_effective_config", lambda conn: _cfg(catch_up=False))
    sched.catch_up_on_startup()
    assert fake.jobs == []


def test_lock_prevents_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def blocking_run_cycle(conn: Any, cfg: Any, clock: Any, trigger: str) -> Any:
        calls.append(trigger)
        entered.set()
        release.wait(2)
        return _ok_summary()

    monkeypatch.setattr(runner, "run_cycle", blocking_run_cycle)
    monkeypatch.setattr(store, "load_effective_config", lambda conn: _cfg(catch_up=True))
    monkeypatch.setattr(runs_repo, "current_run", lambda conn: None)
    sched = _sched()

    worker = threading.Thread(target=sched.trigger_now, args=("manual",))
    worker.start()
    assert entered.wait(2)  # first run is inside run_cycle, holding the lock
    assert sched.trigger_now("manual") is None  # second call finds the lock held
    release.set()
    worker.join(2)
    assert calls == ["manual"]  # the body was entered exactly once


def test_db_active_run_skips_before_run_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[int] = []

    def spy_run_cycle(*args: Any, **kwargs: Any) -> Any:
        called.append(1)
        return _ok_summary()

    monkeypatch.setattr(runner, "run_cycle", spy_run_cycle)
    monkeypatch.setattr(store, "load_effective_config", lambda conn: _cfg(catch_up=True))
    monkeypatch.setattr(runs_repo, "current_run", lambda conn: {"run_id": 1})  # truthy
    sched = _sched()
    assert sched.trigger_now("manual") is None
    assert called == []  # run_cycle never invoked while a DB run is active


def test_trigger_now_returns_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner, "run_cycle", lambda conn, cfg, clock, trigger: _ok_summary(run_id=42)
    )
    monkeypatch.setattr(store, "load_effective_config", lambda conn: _cfg(catch_up=True))
    monkeypatch.setattr(runs_repo, "current_run", lambda conn: None)
    sched = _sched()
    assert sched.trigger_now("manual") == 42
