"""Feed-quality fixes in storage + runner (2026-09-15 audit).

- upsert link refresh: the same source's repost moves the row to its new link and drops the
  cached full_description; a different source keeps the first-seen link.
- the list query hides 'stale' rows unless hidden rows are shown.
- the runner retires stored rows that fail today's filters (applied/bookmarked exempt).
- the runner calls the closure sweep after expire_stale and survives any sweep failure.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import pytest

from _fakes import FakeSource, RecordingNotifier, make_job
from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.pipeline import runner
from job_aggregator.pipeline.runner import run_cycle
from job_aggregator.sources import closure
from job_aggregator.storage import jobs_repo, runs_repo

# Stipend figures around the owner's 12,000 INR/month internship floor (config default).
UNDER_FLOOR = 5000
OVER_FLOOR = 15000


def _row(conn: sqlite3.Connection, uid: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute("SELECT * FROM jobs WHERE job_uid=?", (uid,)).fetchone()
    assert row is not None
    return row


def _inr_month(low: int, high: int) -> dict[str, object]:
    """Stored (already normalized) pay fields, as the runner persists them."""
    return {
        "salary_min": low,
        "salary_max": high,
        "salary_currency": "INR",
        "salary_period": "month",
        "salary_parsed": True,
    }


@pytest.fixture(autouse=True)
def no_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the closure sweep with a no-op, so no run here can touch the network. Tests of
    the sweep itself patch it again."""
    monkeypatch.setattr(closure, "sweep", lambda *_: {})


# ── (a) upsert link refresh ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "url", "native_id", "want_url", "want_native", "want_cache"),
    [
        # Repost under a new id by the same source: follow it, drop the old closed JD.
        ("internshala", "https://s/2", "2", "https://s/2", "2", None),
        # Same source, same link: the cached JD still describes this posting.
        ("internshala", "https://s/1", "1", "https://s/1", "1", "cached JD"),
        # Same posting id, link with a fresh per-request token (Adzuna "se="): not a repost.
        ("internshala", "https://s/1?se=abc", "1", "https://s/1", "1", "cached JD"),
        # A different source found the same posting later: first-seen provenance wins.
        ("unstop", "https://u/9", "9", "https://s/1", "1", "cached JD"),
    ],
)
def test_upsert_refreshes_link_only_for_same_source(
    conn: sqlite3.Connection,
    clock: FixedClock,
    source: str,
    url: str,
    native_id: str,
    want_url: str,
    want_native: str,
    want_cache: str | None,
) -> None:
    run1 = runs_repo.start_run(conn, "manual", clock)
    first = make_job("p", source="internshala", url="https://s/1", source_native_id="1")
    jobs_repo.upsert_job(conn, first, run1, clock)
    jobs_repo.set_user_flag(conn, "p", "full_description", "cached JD")

    run2 = runs_repo.start_run(conn, "manual", clock)
    again = make_job("p", source=source, url=url, source_native_id=native_id)
    assert jobs_repo.upsert_job(conn, again, run2, clock) == "updated"

    row = _row(conn, "p")
    assert (row["url"], row["source_native_id"], row["full_description"]) == (
        want_url,
        want_native,
        want_cache,
    )
    assert row["source"] == "internshala"  # provenance never moves


@pytest.mark.parametrize("case", ["applied", "bookmarked", "same_run"])
def test_upsert_keeps_link_of_flagged_row_and_within_one_run(
    conn: sqlite3.Connection, clock: FixedClock, case: str
) -> None:
    run1 = runs_repo.start_run(conn, "manual", clock)
    first = make_job("p", source="internshala", url="https://s/1", source_native_id="1")
    jobs_repo.upsert_job(conn, first, run1, clock)
    jobs_repo.set_user_flag(conn, "p", "full_description", "cached JD")
    if case == "same_run":
        run2 = run1  # a second open posting with the same uid, in the same fetch
    else:
        jobs_repo.set_user_flag(conn, "p", case, True)
        run2 = runs_repo.start_run(conn, "manual", clock)

    repost = make_job("p", source="internshala", url="https://s/2", source_native_id="2")
    jobs_repo.upsert_job(conn, repost, run2, clock)

    row = _row(conn, "p")
    assert (row["url"], row["source_native_id"], row["full_description"]) == (
        "https://s/1",
        "1",
        "cached JD",
    )


# ── (b) the list hides stale rows ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, {"n", "a", "sa"}),  # an applied stale row stays listed
        ({"include_hidden": True}, {"n", "a", "s", "sa"}),
        ({"status": ["stale"]}, {"s", "sa"}),
        ({"status": ["deleted"]}, {"d"}),
    ],
)
def test_list_hides_stale_unless_hidden_rows_are_shown(
    conn: sqlite3.Connection,
    run_id: int,
    clock: FixedClock,
    kwargs: dict[str, Any],
    expected: set[str],
) -> None:
    rows = (("n", "new"), ("a", "active"), ("s", "stale"), ("sa", "stale"), ("d", "deleted"))
    for uid, status in rows:
        jobs_repo.upsert_job(conn, make_job(uid, company=f"C-{uid}"), run_id, clock)
        conn.execute("UPDATE jobs SET status=? WHERE job_uid=?", (status, uid))
    jobs_repo.set_user_flag(conn, "sa", "applied", True)
    conn.commit()
    assert {r["job_uid"] for r in jobs_repo.get_jobs(conn, **kwargs)} == expected
    assert jobs_repo.count_jobs(conn, **kwargs) == len(expected)


# ── (c) retire stored rows that fail today's filters ────────────────────────────────────

_RETIRE_CASES: list[tuple[str, dict[str, object], dict[str, object], bool]] = [
    # (uid, job fields, row flags set after insert, retired?)
    ("under_floor", _inr_month(UNDER_FLOOR, UNDER_FLOOR), {}, True),
    ("unpaid_stored_as_zero", _inr_month(0, 0), {}, True),
    ("range_top_clears_floor", _inr_month(UNDER_FLOOR, OVER_FLOOR), {}, False),
    ("pay_not_stated", {}, {}, False),
    ("senior_title", {"title": "Senior Backend Engineer"}, {}, True),
    ("six_month_internship", {"description": "Duration: 6 months"}, {}, True),
    ("duration_not_stated", {"description": "Build Go services."}, {}, False),
    # The listing text is silent; the cached full JD states the duration.
    (
        "full_jd_duration",
        {"description": "backend"},
        {"full_description": "Duration: 6 months"},
        True,
    ),
    # No pay stored; the cached full JD states a stipend under the floor.
    (
        "full_jd_low_stipend",
        {"description": "backend"},
        {"full_description": "Stipend: ₹5,000 /month"},
        True,
    ),
    ("applied_is_exempt", _inr_month(UNDER_FLOOR, UNDER_FLOOR), {"applied": True}, False),
    ("bookmarked_is_exempt", _inr_month(UNDER_FLOOR, UNDER_FLOOR), {"bookmarked": True}, False),
]


def _seed_retire_cases(conn: sqlite3.Connection, run_id: int, clock: FixedClock) -> None:
    for i, (uid, fields, flags, _) in enumerate(_RETIRE_CASES):
        job = make_job(uid, company=f"Co{i}", **fields)  # distinct companies: no dedup collapse
        jobs_repo.upsert_job(conn, job, run_id, clock)
        for flag, value in flags.items():
            jobs_repo.set_user_flag(conn, uid, flag, value)


def test_retire_drops_exactly_the_rows_that_fail_today(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, sample_config: Config
) -> None:
    _seed_retire_cases(conn, run_id, clock)
    conn.execute("UPDATE jobs SET status='stale' WHERE job_uid='under_floor'")  # stale is visible
    conn.commit()

    reasons = runner._retire_failing_jobs(conn, sample_config)

    for uid, _, _, retired in _RETIRE_CASES:
        assert (_row(conn, uid)["status"] == "deleted") is retired, uid
    assert sum(reasons.values()) == sum(1 for *_, retired in _RETIRE_CASES if retired)
    assert reasons["salary_below_floor"] == 3  # under_floor, unpaid_stored_as_zero, full JD
    assert reasons["excluded:senior"] == 1


def test_retire_is_idempotent_and_never_touches_deleted_rows(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, sample_config: Config
) -> None:
    _seed_retire_cases(conn, run_id, clock)
    first = runner._retire_failing_jobs(conn, sample_config)
    snapshot = {r["job_uid"]: r["status"] for r in conn.execute("SELECT * FROM jobs")}

    assert sum(first.values()) > 0
    assert runner._retire_failing_jobs(conn, sample_config) == {}
    assert {r["job_uid"]: r["status"] for r in conn.execute("SELECT * FROM jobs")} == snapshot


def test_retire_guard_spares_a_row_flagged_after_selection(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock
) -> None:
    jobs_repo.upsert_job(conn, make_job("late"), run_id, clock)
    jobs_repo.set_user_flag(conn, "late", "bookmarked", True)  # flagged between select + update
    assert jobs_repo.retire_jobs(conn, ["late"]) == 0
    assert _row(conn, "late")["status"] == "new"


def test_run_applies_a_raised_floor_to_rows_stored_under_the_old_one(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    low = make_job("low", source="win", **_inr_month(UNDER_FLOOR, UNDER_FLOOR))
    sample_config.salary.min_internship = 0  # the old config kept any stated stipend
    run_cycle(
        conn, sample_config, clock, "manual", sources=[FakeSource("win", [low])], notifiers=[]
    )
    assert _row(conn, "low")["status"] == "new"

    sample_config.salary.min_internship = 12000  # the owner raises the floor
    # A windowed source that no longer returns the row: expiry alone would keep it for 30 days.
    idle = FakeSource("win", [], exhaustive=False)
    run_cycle(conn, sample_config, clock, "manual", sources=[idle], notifiers=[])
    assert _row(conn, "low")["status"] == "deleted"


def test_refetched_failing_row_ends_the_run_retired_and_unnotified(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    job = make_job("jd")
    run_cycle(conn, sample_config, clock, "manual", sources=[FakeSource("X", [job])], notifiers=[])
    jobs_repo.set_user_flag(conn, "jd", "full_description", "Internship duration: 6 months")

    recorder = RecordingNotifier()
    summary = run_cycle(
        conn, sample_config, clock, "manual", sources=[FakeSource("X", [job])], notifiers=[recorder]
    )
    assert summary.status == "success"
    assert _row(conn, "jd")["status"] == "deleted"  # the upsert revived it, the retire step won
    assert recorder.calls == [[]]


# ── (d) closure sweep ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("checks", "calls"), [(25, 1), (0, 0)])
def test_sweep_runs_after_expiry_only_when_enabled(
    conn: sqlite3.Connection,
    clock: FixedClock,
    sample_config: Config,
    monkeypatch: pytest.MonkeyPatch,
    checks: int,
    calls: int,
) -> None:
    seen: list[tuple[Any, ...]] = []

    def _sweep(c: sqlite3.Connection, cfg: Config, clk: FixedClock) -> dict[str, int]:
        # expire_stale has already run: the row the source dropped is stale by now.
        seen.append((c, cfg, clk, _row(c, "gone")["status"]))
        return {"closed": 0}

    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[FakeSource("X", [make_job("gone", source="X")])],
        notifiers=[],
    )
    monkeypatch.setattr(closure, "sweep", _sweep)
    sample_config.schedule.closure_checks_per_run = checks
    run_cycle(conn, sample_config, clock, "manual", sources=[FakeSource("X", [])], notifiers=[])
    assert seen == [(conn, sample_config, clock, "stale")] * calls


@pytest.mark.parametrize("error", [RuntimeError("boom"), NotImplementedError(), OSError("net")])
def test_sweep_failure_never_fails_the_run(
    conn: sqlite3.Connection,
    clock: FixedClock,
    sample_config: Config,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    def _raise(*_: object) -> dict[str, int]:
        raise error

    monkeypatch.setattr(closure, "sweep", _raise)
    recorder = RecordingNotifier()
    with caplog.at_level(logging.ERROR, logger=runner.__name__):
        summary = run_cycle(
            conn,
            sample_config,
            clock,
            "manual",
            sources=[FakeSource("X", [make_job("ok")])],
            notifiers=[recorder],
        )
    assert summary.status == "success"
    assert recorder.runs == [summary.run_id]  # the run still finished and reported
    assert "closure sweep raised" in caplog.text
