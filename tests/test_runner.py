"""Phase 5 — pipeline.runner.run_cycle with FAKE sources + FixedClock.

cross-source dedup, guarded stale-delete, user-flag preservation end-to-end.

See PLAN.md Part II (Phase 5) for the exact table-driven cases to implement.
"""

from __future__ import annotations

import sqlite3

from _fakes import FakeSource, RaisingSource, RecordingNotifier, make_job
from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.pipeline.runner import run_cycle
from job_aggregator.storage import jobs_repo


def _count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"])


def _row(conn: sqlite3.Connection, uid: str) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute("SELECT * FROM jobs WHERE job_uid=?", (uid,)).fetchone()
    assert row is not None
    return row


def test_cross_source_dedup_collapses(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    a = FakeSource("srcA", [make_job("x", source="srcA")])
    b = FakeSource("srcB", [make_job("x", source="srcB")])
    summary = run_cycle(conn, sample_config, clock, "manual", sources=[a, b], notifiers=[])
    assert _count(conn) == 1
    assert (summary.n_new, summary.n_updated) == (1, 1)
    assert _row(conn, "x")["source"] == "srcA"  # first in input order wins provenance


def test_stale_delete_only_touches_succeeded_sources(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[FakeSource("A", [make_job("a1", source="A")])],
        notifiers=[],
    )
    summary = run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[
            FakeSource("A", []),  # A succeeds but empty -> stales a1
            FakeSource("B", [make_job("b1", source="B")], succeeded=False, error="429"),
        ],
        notifiers=[],
    )
    assert summary.status == "partial"
    assert (summary.n_sources_ok, summary.n_sources_err, summary.n_expired) == (1, 1, 1)
    assert _row(conn, "a1")["status"] == "stale"
    assert _count(conn) == 1  # b1 never ingested (B failed)


def test_failed_source_untouched_across_cycles(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[FakeSource("X", [make_job("x", source="X")])],
        notifiers=[],
    )
    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[FakeSource("X", [make_job("x", source="X")])],
        notifiers=[],
    )  # -> active
    for _ in range(3):
        clock.advance(days=2)  # push well past grace
        run_cycle(
            conn,
            sample_config,
            clock,
            "manual",
            sources=[FakeSource("X", [], succeeded=False, error="down")],
            notifiers=[],
        )
    assert _row(conn, "x")["status"] == "active"  # never staled: X never succeeded again


def test_grace_window_stale_to_deleted_full_cycle(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[FakeSource("X", [make_job("x", source="X")])],
        notifiers=[],
    )
    run_cycle(
        conn, sample_config, clock, "manual", sources=[FakeSource("X", [])], notifiers=[]
    )  # X ok but empty -> x stale
    clock.advance(days=sample_config.schedule.grace_days + 1)
    run_cycle(
        conn, sample_config, clock, "manual", sources=[FakeSource("X", [])], notifiers=[]
    )  # -> deleted
    assert _row(conn, "x")["status"] == "deleted"


def test_user_flags_preserved_across_cycle(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[FakeSource("X", [make_job("x", source="X")])],
        notifiers=[],
    )
    jobs_repo.set_user_flag(conn, "x", "bookmarked", True)
    jobs_repo.set_user_flag(conn, "x", "applied", True)
    jobs_repo.set_user_flag(conn, "x", "notes", "great fit")
    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[FakeSource("X", [make_job("x", source="X")])],
        notifiers=[],
    )  # re-seen
    row = _row(conn, "x")
    assert row["bookmarked"] == 1
    assert row["applied"] == 1
    assert row["notes"] == "great fit"
    assert row["status"] == "active"


def test_all_sources_fail(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    summary = run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[
            FakeSource("A", [], succeeded=False, error="e"),
            FakeSource("B", [], succeeded=False, error="e"),
        ],
        notifiers=[],
    )
    assert summary.status == "failed"
    assert _count(conn) == 0


def test_no_sources_is_success_noop(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    summary = run_cycle(conn, sample_config, clock, "manual", sources=[], notifiers=[])
    assert summary.status == "success"
    assert (summary.n_sources_ok, summary.n_sources_err, summary.n_new, summary.n_expired) == (
        0,
        0,
        0,
        0,
    )


def test_new_jobs_notified_new_only(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    recorder = RecordingNotifier()
    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[FakeSource("X", [make_job("a", source="X")])],
        notifiers=[recorder],
    )
    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[
            FakeSource("X", [make_job("c", source="X", title="ML Engineer Intern", company="Beta")])
        ],
        notifiers=[recorder],
    )
    assert recorder.calls == [["a"], ["c"]]  # only NEW jobs, once each


def test_source_that_raises_is_caught(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    summary = run_cycle(
        conn, sample_config, clock, "manual", sources=[RaisingSource("boom")], notifiers=[]
    )
    assert summary.status == "failed"
    row = conn.execute("SELECT succeeded FROM source_runs WHERE source='boom'").fetchone()
    assert row["succeeded"] == 0


def test_filtered_job_not_inserted(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    summary = run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[FakeSource("X", [make_job("s", source="X", title="Senior Backend Engineer")])],
        notifiers=[],
    )
    assert _count(conn) == 0
    assert summary.n_filtered_out == 1
    assert summary.status == "success"  # source succeeded; the job was just filtered out


def test_tier_b_salary_normalized_to_inr_month(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    # A Tier-B job ships raw USD/hour pay; the runner must convert it to INR/month before
    # bucketing so a high-paying remote role isn't wrongly dropped as below-floor.
    raw = make_job(
        "s",
        source="himalayas",
        is_remote=True,
        salary_min=50,
        salary_max=50,
        salary_currency="USD",
        salary_period="hour",
        salary_parsed=True,
    )
    run_cycle(
        conn, sample_config, clock, "manual", sources=[FakeSource("himalayas", [raw])], notifiers=[]
    )
    row = _row(conn, "s")
    assert row["salary_currency"] == "INR"
    assert row["salary_period"] == "month"
    assert row["salary_min"] > 100000  # 50 USD/hr ≈ 719k INR/month
    assert row["salary_bucket"] == "pass"  # remote, far above the 30k floor


def test_subsource_guard_is_per_site(
    conn: sqlite3.Connection, clock: FixedClock, sample_config: Config
) -> None:
    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[
            FakeSource(
                "jobspy",
                [make_job("n1", source="jobspy_naukri"), make_job("l1", source="jobspy_linkedin")],
                sub_results=[("jobspy_naukri", True, 1), ("jobspy_linkedin", True, 1)],
            )
        ],
        notifiers=[],
    )
    run_cycle(
        conn,
        sample_config,
        clock,
        "manual",
        sources=[
            FakeSource(
                "jobspy",
                [],  # naukri ok-but-empty; linkedin failed
                sub_results=[("jobspy_naukri", True, 1), ("jobspy_linkedin", False, 0)],
            )
        ],
        notifiers=[],
    )
    r2 = int(conn.execute("SELECT MAX(run_id) AS m FROM runs").fetchone()["m"])
    subs = conn.execute(
        "SELECT source, succeeded FROM source_runs WHERE run_id=?", (r2,)
    ).fetchall()
    assert {row["source"]: row["succeeded"] for row in subs} == {
        "jobspy_naukri": 1,
        "jobspy_linkedin": 0,
    }
    assert _row(conn, "l1")["status"] == "new"  # linkedin failed -> untouched
    assert _row(conn, "n1")["status"] == "stale"  # naukri ok, n1 not re-seen -> stale
