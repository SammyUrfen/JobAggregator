"""apply.procs: the dashboard's apply kill switch (register / live / stop_all).

We can't spawn real detached processes deterministically in CI, so the /proc identity check
(`_is_apply_proc`) is monkeypatched to a controllable set; the file bookkeeping, dedup,
pruning, and the TERM→grace→KILL sequence are asserted directly.
"""

from __future__ import annotations

import signal
from pathlib import Path

import pytest

from job_aggregator.apply import procs


@pytest.fixture
def pid_file(tmp_path: Path) -> Path:
    return tmp_path / "apply_pids"


def test_register_and_live_prunes_dead(pid_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    procs.register_pid(101, path=pid_file)
    procs.register_pid(102, path=pid_file)
    procs.register_pid(101, path=pid_file)  # dup
    alive = {101}  # 102 has since finished
    monkeypatch.setattr(procs, "_is_apply_proc", lambda pid: pid in alive)
    assert procs.live_pids(path=pid_file) == [101]  # deduped, dead 102 pruned
    assert pid_file.read_text().split() == ["101"]  # file rewritten to just the live one


def test_live_pids_empty_when_no_file(pid_file: Path) -> None:
    assert procs.live_pids(path=pid_file) == []


def test_stop_all_term_then_kill_survivor(pid_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    procs.register_pid(201, path=pid_file)  # obeys TERM
    procs.register_pid(202, path=pid_file)  # ignores TERM -> must be KILLed
    alive = {201, 202}
    signals: list[tuple[int, int]] = []

    def fake_signal(pid: int, sig: int) -> None:
        signals.append((pid, sig))
        if sig == signal.SIGTERM and pid == 201:
            alive.discard(201)  # 201 dies on TERM

    monkeypatch.setattr(procs, "_is_apply_proc", lambda pid: pid in alive)
    monkeypatch.setattr(procs, "_signal_group", fake_signal)

    n = procs.stop_all(path=pid_file, grace_s=0, sleep=lambda _s: None)
    assert n == 2
    assert (201, signal.SIGTERM) in signals
    assert (202, signal.SIGTERM) in signals
    assert (202, signal.SIGKILL) in signals  # survived TERM -> hard-killed
    assert (201, signal.SIGKILL) not in signals  # already dead, not re-signalled
    assert pid_file.read_text() == ""  # registry cleared


def test_stop_all_no_runs(pid_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(procs, "_is_apply_proc", lambda pid: False)
    assert procs.stop_all(path=pid_file, sleep=lambda _s: None) == 0


def test_is_apply_proc_reads_proc_self() -> None:
    import os

    # our own process cmdline is pytest, not "job_aggregator apply" -> not an apply proc
    assert procs._is_apply_proc(os.getpid()) is False
    # a PID that cannot exist -> False, never raises
    assert procs._is_apply_proc(2**31 - 1) is False
