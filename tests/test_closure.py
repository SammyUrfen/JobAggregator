"""Closed-application checks (sources/closure.py), schema v5, and the dashboard's check on open.

Every HTTP call is respx-mocked with markup copied from the 2026-09-15 audit captures (the exact
marker lines, not whole pages). respx's default assert_all_mocked makes any unplanned request
fail the test, so "no request" cases prove themselves. Time is a FixedClock, and the sweep's
sleep advances that clock instead of waiting.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.config.store import seed_from_yaml
from job_aggregator.dashboard.app import create_app
from job_aggregator.sources import closure
from job_aggregator.storage.db import SCHEMA_VERSION, connect, init_db, migrate

LI_URL = "https://www.linkedin.com/jobs/view/4449990045"
LI_EXPIRED = "https://www.linkedin.com/jobs/civil-engineering-intern-jobs?trk=expired_jd_redirect"
LI_OPEN_PAGE = '<h1 class="top-card-layout__title topcard__title">Engineering Intern</h1>'
LI_CLOSED_PAGE = (
    '<figure class="closed-job closed-job__flavor topcard__flavor-row">'
    "<figcaption>No longer accepting applications</figcaption></figure>" + LI_OPEN_PAGE
)
# A LinkedIn sign-up wall: no posting markers, and an attribute that merely names reCAPTCHA.
LI_AUTHWALL_PAGE = '<title>Sign Up | LinkedIn</title><body data-recaptcha-v3-integration="x">'
ISH_URL = "https://internshala.com/internship/detail/backend-internship-at-acme1784350966"
UNSTOP_API = "https://unstop.com/api/public/competition/1731337"


def _ish_page(status: str) -> str:
    # A normal Internshala page also carries a reCAPTCHA site key, so "captcha" is no block signal.
    return (
        '<script>var recaptchaSiteKey = "x";</script>'
        '<div class="internship_details"><p>Real JD: build backend APIs.</p></div>'
        f'<input type="hidden" id="status" value="{status}">'
    )


def _unstop(reg_status: str) -> dict[str, Any]:
    return {"data": {"competition": {"regnRequirements": {"reg_status": reg_status}}}}


# (case id, source, stored url, native id, url the check opens or None, response, verdict)
CHECK_CASES: list[tuple[str, str, str, str | None, str | None, Any, str]] = [
    ("li-301-expired", "jobspy_linkedin", LI_URL, None, LI_URL,
     httpx.Response(301, headers={"location": LI_EXPIRED}), "closed"),
    ("li-200-closed-job", "jobspy_linkedin", LI_URL, None, LI_URL,
     httpx.Response(200, text=LI_CLOSED_PAGE), "closed"),
    ("li-200-open", "jobspy_linkedin", LI_URL, None, LI_URL,
     httpx.Response(200, text=LI_OPEN_PAGE), "open"),
    ("li-authwall-redirect-with-trk", "jobspy_linkedin", LI_URL, None, LI_URL,
     httpx.Response(302, headers={"location": "https://www.linkedin.com/authwall?trk=expired_jd_redirect"}),
     "unknown"),
    ("li-authwall-page", "jobspy_linkedin", LI_URL, None, LI_URL,
     httpx.Response(200, text=LI_AUTHWALL_PAGE), "unknown"),
    ("li-999", "jobspy_linkedin", LI_URL, None, LI_URL, httpx.Response(999), "unknown"),
    ("li-429", "jobspy_linkedin", LI_URL, None, LI_URL, httpx.Response(429), "unknown"),
    ("li-timeout", "jobspy_linkedin", LI_URL, None, LI_URL, httpx.ConnectTimeout("t"), "unknown"),
    ("li-url-without-id", "jobspy_linkedin", "https://example.com/jobs/l1", None, None, None,
     "unknown"),
    ("ish-closed", "internshala", ISH_URL, None, ISH_URL,
     httpx.Response(200, text=_ish_page("closed")), "closed"),
    ("ish-expired", "internshala", ISH_URL, None, ISH_URL,
     httpx.Response(200, text=_ish_page("expired")), "closed"),
    ("ish-active", "internshala", ISH_URL, None, ISH_URL,
     httpx.Response(200, text=_ish_page("active")), "open"),
    ("ish-no-status-captcha", "internshala", ISH_URL, None, ISH_URL,
     httpx.Response(200, text="<title>captcha</title><form id='challenge'>"), "unknown"),
    ("ish-not-a-detail-url", "internshala", "http://evil.com/internshala.com/internship/detail/x",
     None, None, None, "unknown"),
    ("unstop-finished", "unstop", "https://unstop.com/internships/x-1731337", "1731337", UNSTOP_API,
     httpx.Response(200, json=_unstop("FINISHED")), "closed"),
    ("unstop-started", "unstop", "https://unstop.com/internships/x-1731337", "1731337", UNSTOP_API,
     httpx.Response(200, json=_unstop("STARTED")), "open"),
    ("unstop-id-from-url", "unstop", "https://unstop.com/internships/x-1731337", None, UNSTOP_API,
     httpx.Response(200, json=_unstop("FINISHED")), "closed"),
    ("unstop-html-block", "unstop", "https://unstop.com/internships/x-1731337", "1731337",
     UNSTOP_API, httpx.Response(200, text="<title>Attention Required</title>"), "unknown"),
    ("unstop-403", "unstop", "https://unstop.com/internships/x-1731337", "1731337", UNSTOP_API,
     httpx.Response(403, json=_unstop("FINISHED")), "unknown"),
    ("remoteok-not-checkable", "remoteok", "https://remoteok.com/remote-jobs/1", "1", None, None,
     "unknown"),
    ("adzuna-not-checkable", "adzuna", "https://www.adzuna.in/details/5862987204", "5862987204",
     None, None, "unknown"),
]  # fmt: skip


@pytest.mark.parametrize(
    ("source", "url", "native_id", "check_url", "response", "expected"),
    [c[1:] for c in CHECK_CASES],
    ids=[c[0] for c in CHECK_CASES],
)
def test_check_posting_reads_only_exact_markers(
    source: str,
    url: str,
    native_id: str | None,
    check_url: str | None,
    response: Any,
    expected: str,
) -> None:
    with respx.mock:
        route = None
        if check_url is not None:
            route = respx.get(check_url)
            if isinstance(response, Exception):
                route.mock(side_effect=response)
            else:
                route.mock(return_value=response)
        assert closure.check_posting(source, url, native_id) == expected
        assert route is None or route.call_count == 1


def test_linkedin_check_does_not_follow_the_redirect() -> None:
    """The expired marker IS the redirect: following it would lose it and cost a request."""
    with respx.mock:
        respx.get(LI_URL).mock(return_value=httpx.Response(301, headers={"location": LI_EXPIRED}))
        search = respx.get(LI_EXPIRED).mock(return_value=httpx.Response(200, text=LI_OPEN_PAGE))
        assert closure.check_posting("jobspy_linkedin", LI_URL, None) == "closed"
        assert search.call_count == 0


# ── schema v5 ────────────────────────────────────────────────────────────────────────────────


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}


def test_migrate_v4_db_adds_closure_checked_at(tmp_path: Path) -> None:
    conn = connect(tmp_path / "old.db")
    init_db(conn)
    assert "closure_checked_at" in _columns(conn)  # fresh schema.sql already has it
    # Rebuild a v4 database: the column missing and user_version 4.
    conn.execute("ALTER TABLE jobs DROP COLUMN closure_checked_at")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    migrate(conn)
    assert "closure_checked_at" in _columns(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 5
    migrate(conn)  # idempotent on a v5 database
    assert "closure_checked_at" in _columns(conn)
    conn.close()


# ── sweep ────────────────────────────────────────────────────────────────────────────────────


def _insert(
    conn: sqlite3.Connection,
    uid: str,
    source: str,
    url: str,
    *,
    native_id: str | None = None,
    score: float = 5.0,
    checked_at: str | None = None,
    first_seen: str = "2026-01-01",
    status: str = "active",
    applied: int = 0,
    bookmarked: int = 0,
    hidden: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO jobs (job_uid, source, source_native_id, title, company, url, match_score, "
        "first_seen_at, last_seen_at, last_seen_cycle, status, applied, bookmarked, hidden, "
        "closure_checked_at) VALUES (?, ?, ?, 'Intern', 'Acme', ?, ?, ?, "
        "'2026-01-01', (SELECT MAX(run_id) FROM runs), ?, ?, ?, ?, ?)",
        (
            uid,
            source,
            native_id,
            url,
            score,
            first_seen,
            status,
            applied,
            bookmarked,
            hidden,
            checked_at,
        ),
    )
    conn.commit()


def _row(conn: sqlite3.Connection, uid: str) -> sqlite3.Row:
    row: sqlite3.Row = conn.execute("SELECT * FROM jobs WHERE job_uid = ?", (uid,)).fetchone()
    return row


def _ust(n: int) -> str:
    return f"https://unstop.com/internships/x-{n}"


UNSTOP_API_RE = r"https://unstop\.com/api/public/competition/\d+"


def _li(n: int) -> str:
    return f"https://www.linkedin.com/jobs/view/{n}"


def _clock_sleep(clock: FixedClock) -> tuple[list[float], Any]:
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock.advance(seconds=seconds)

    return slept, sleep


@pytest.mark.parametrize(
    ("limit", "expected_order"),
    [(0, []), (1, ["1"]), (2, ["1", "2"]), (3, ["1", "2", "3"]), (25, ["1", "2", "3", "8"])],
)
def test_sweep_limit_and_order(
    conn: sqlite3.Connection,
    run_id: int,
    clock: FixedClock,
    cfg: Config,
    limit: int,
    expected_order: list[str],
) -> None:
    """Oldest knowledge first: a never-checked row counts as checked when first seen, and a tie
    goes to the higher score. A row first seen today waits behind a check 5 days old. A row checked
    inside the 3-day window, a deleted row, a hidden row and an unchecked source are left out."""
    now = clock.now()
    _insert(conn, "never-new", "unstop", _ust(8), score=50.0, first_seen=now.isoformat())
    ten_days_ago = (now - timedelta(days=10)).isoformat()
    _insert(conn, "never-high", "unstop", _ust(1), score=9.0, first_seen=ten_days_ago)
    _insert(conn, "never-low", "unstop", _ust(2), score=1.0, first_seen=ten_days_ago)
    _insert(
        conn,
        "old-check",
        "unstop",
        _ust(3),
        score=99.0,
        checked_at=(now - timedelta(days=5)).isoformat(),
    )
    _insert(
        conn, "fresh-check", "unstop", _ust(4), checked_at=(now - timedelta(days=1)).isoformat()
    )
    _insert(conn, "deleted", "unstop", _ust(5), status="deleted")
    _insert(conn, "hidden", "unstop", _ust(6), hidden=1)
    _insert(conn, "remoteok", "remoteok", "https://remoteok.com/remote-jobs/7", native_id="7")
    cfg.schedule.closure_checks_per_run = limit
    _, sleep = _clock_sleep(clock)
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=UNSTOP_API_RE).mock(
            return_value=httpx.Response(200, json=_unstop("STARTED"))
        )
        counts = closure.sweep(conn, cfg, clock, sleep=sleep)
        opened = [str(call.request.url).rsplit("/", 1)[-1] for call in router.calls]
    assert opened == expected_order
    assert counts == {"closed": 0, "open": len(expected_order), "unknown": 0, "skipped_blocked": 0}


def test_sweep_waits_per_domain(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, cfg: Config
) -> None:
    """The gap is per site and counts time already spent: after LinkedIn's 5 s wait, the next
    Unstop request is already 5 s past the last one, so it does not wait again."""
    for uid, source, url, score in [
        ("l1", "jobspy_linkedin", _li(11), 9.0),
        ("a1", "unstop", _ust(21), 8.0),
        ("l2", "jobspy_linkedin", _li(12), 7.0),
        ("a2", "unstop", _ust(22), 6.0),
        ("a3", "unstop", _ust(23), 5.0),
    ]:
        _insert(conn, uid, source, url, score=score)
    slept, sleep = _clock_sleep(clock)
    with respx.mock:
        respx.get(url__regex=r"https://www\.linkedin\.com/jobs/view/\d+").mock(
            return_value=httpx.Response(200, text=LI_OPEN_PAGE)
        )
        respx.get(url__regex=UNSTOP_API_RE).mock(
            return_value=httpx.Response(200, json=_unstop("STARTED"))
        )
        counts = closure.sweep(conn, cfg, clock, sleep=sleep)
    assert slept == [closure.LINKEDIN_GAP_S, closure.DEFAULT_GAP_S]  # before l2, before a3
    assert counts["open"] == 5


def test_sweep_stops_a_domain_after_its_first_block(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, cfg: Config
) -> None:
    _insert(conn, "li-no-id", "jobspy_linkedin", "https://example.com/jobs/l1", score=10.0)
    _insert(conn, "li-429", "jobspy_linkedin", _li(31), score=9.0)
    _insert(conn, "ust-open", "unstop", _ust(41), score=8.0)
    _insert(conn, "li-skipped", "jobspy_linkedin", _li(32), score=7.0)
    _, sleep = _clock_sleep(clock)
    with respx.mock(assert_all_called=False) as router:
        blocked = router.get(_li(31)).mock(return_value=httpx.Response(429))
        skipped = router.get(_li(32)).mock(return_value=httpx.Response(200, text=LI_OPEN_PAGE))
        router.get("https://unstop.com/api/public/competition/41").mock(
            return_value=httpx.Response(200, json=_unstop("STARTED"))
        )
        counts = closure.sweep(conn, cfg, clock, sleep=sleep)
        assert (blocked.call_count, skipped.call_count) == (1, 0)
    # A row with no usable id makes no request, so it does not stop LinkedIn.
    assert counts == {"closed": 0, "open": 1, "unknown": 2, "skipped_blocked": 1}
    assert _row(conn, "li-429")["closure_checked_at"] == clock.now().isoformat()
    assert _row(conn, "li-skipped")["closure_checked_at"] is None  # first in line next sweep


def test_sweep_budget_counts_only_requests(
    conn: sqlite3.Connection, run_id: int, clock: FixedClock, cfg: Config
) -> None:
    """A blocked site's rows and a row with no usable id cost no budget, so the checks go to the
    sites that still answer."""
    _insert(conn, "li-no-id", "jobspy_linkedin", "https://example.com/jobs/l1", score=10.0)
    _insert(conn, "li-429", "jobspy_linkedin", _li(31), score=9.0)
    _insert(conn, "li-skip-1", "jobspy_linkedin", _li(32), score=8.0)
    _insert(conn, "li-skip-2", "jobspy_linkedin", _li(33), score=7.0)
    _insert(conn, "ust-1", "unstop", _ust(41), score=6.0)
    _insert(conn, "ust-2", "unstop", _ust(42), score=5.0)
    cfg.schedule.closure_checks_per_run = 2
    _, sleep = _clock_sleep(clock)
    with respx.mock(assert_all_called=False) as router:
        router.get(_li(31)).mock(return_value=httpx.Response(429))
        unstop = router.get(url__regex=UNSTOP_API_RE).mock(
            return_value=httpx.Response(200, json=_unstop("STARTED"))
        )
        counts = closure.sweep(conn, cfg, clock, sleep=sleep)
        assert unstop.call_count == 1  # li-429 and ust-1 use the budget of 2
    assert counts == {"closed": 0, "open": 1, "unknown": 2, "skipped_blocked": 2}


@pytest.mark.parametrize(
    ("applied", "bookmarked", "expected_status"),
    [(0, 0, "deleted"), (1, 0, "active"), (0, 1, "active"), (1, 1, "active")],
)
def test_sweep_retires_closed_unless_applied_or_bookmarked(
    conn: sqlite3.Connection,
    run_id: int,
    clock: FixedClock,
    cfg: Config,
    applied: int,
    bookmarked: int,
    expected_status: str,
) -> None:
    _insert(
        conn,
        "u",
        "unstop",
        "https://unstop.com/internships/x-1731337",
        native_id="1731337",
        applied=applied,
        bookmarked=bookmarked,
    )
    with respx.mock:
        respx.get(UNSTOP_API).mock(return_value=httpx.Response(200, json=_unstop("FINISHED")))
        counts = closure.sweep(conn, cfg, clock)
    assert counts["closed"] == 1
    row = _row(conn, "u")
    assert row["status"] == expected_status
    assert row["closure_checked_at"] == clock.now().isoformat()


# ── dashboard: check on open ─────────────────────────────────────────────────────────────────


@pytest.fixture
def dash(conn: sqlite3.Connection, run_id: int, clock: FixedClock, tmp_path: Path) -> TestClient:
    """A dashboard over the same DB file as `conn`. No `with` block, so the lifespan (and the
    real scheduler) never starts."""
    seed_from_yaml(conn)
    return TestClient(create_app(db_path=tmp_path / "jobs.db", clock=clock))


@pytest.mark.parametrize(("applied", "expected_status"), [(0, "deleted"), (1, "active")])
def test_open_closed_linkedin_posting_banners_and_retires(
    dash: TestClient, conn: sqlite3.Connection, applied: int, expected_status: str
) -> None:
    _insert(conn, "li", "jobspy_linkedin", LI_URL, applied=applied)
    with respx.mock:
        page = respx.get(LI_URL).mock(
            return_value=httpx.Response(301, headers={"location": LI_EXPIRED})
        )
        first = dash.get("/api/jobs/li/detail")
        second = dash.get("/api/jobs/li/detail")  # checked moments ago: no second request
        assert page.call_count == 1
    assert first.status_code == 200
    assert "Applications closed" in first.text
    assert "Applications closed" not in second.text
    assert _row(conn, "li")["status"] == expected_status


def test_open_internshala_reads_status_from_the_description_page(
    dash: TestClient, conn: sqlite3.Connection
) -> None:
    _insert(conn, "ish", "internshala", ISH_URL)
    with respx.mock:
        page = respx.get(ISH_URL).mock(return_value=httpx.Response(200, text=_ish_page("closed")))
        r = dash.get("/api/jobs/ish/detail")
        assert page.call_count == 1  # one fetch serves both the JD and the closed state
    assert "Real JD: build backend APIs." in r.text
    assert "Applications closed" in r.text
    row = _row(conn, "ish")
    assert row["status"] == "deleted"
    assert "Real JD" in row["full_description"]


@pytest.mark.parametrize(
    ("source", "checked_ago", "expected_calls"),
    [
        ("unstop", None, 1),
        ("unstop", timedelta(days=2), 1),
        ("unstop", timedelta(hours=1), 0),
        ("remoteok", None, 0),
    ],
)
def test_open_rechecks_at_most_daily(
    dash: TestClient,
    conn: sqlite3.Connection,
    clock: FixedClock,
    source: str,
    checked_ago: timedelta | None,
    expected_calls: int,
) -> None:
    checked_at = None if checked_ago is None else (clock.now() - checked_ago).isoformat()
    _insert(
        conn,
        "j",
        source,
        "https://unstop.com/internships/x-1731337",
        native_id="1731337",
        checked_at=checked_at,
    )
    with respx.mock(assert_all_called=False) as router:
        api = router.get(UNSTOP_API).mock(return_value=httpx.Response(200, json=_unstop("STARTED")))
        r = dash.get("/api/jobs/j/detail")
        assert api.call_count == expected_calls
    assert r.status_code == 200
    assert "Applications closed" not in r.text
    assert _row(conn, "j")["status"] == "active"
