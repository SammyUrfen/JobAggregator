"""Phase 8 — dashboard via FastAPI TestClient.

GET / /config /runs -> 200; job actions; config PUT validation; run-now + poll; theme + favicon.

See PLAN.md Part II (Phase 8) for the exact table-driven cases to implement.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_aggregator.clock import FixedClock
from job_aggregator.config.store import seed_from_yaml
from job_aggregator.dashboard.app import create_app
from job_aggregator.dashboard.routes_jobs import (
    MAX_DESC_CHARS,
    html_to_text,
    render_description_html,
)
from job_aggregator.storage.db import connect, init_db

FIXED_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
_JOB_COLS = (
    "job_uid, source, title, company, location, is_remote, url, salary_min, salary_max, "
    "salary_currency, salary_period, salary_parsed, salary_bucket, match_score, posted_at, "
    "first_seen_at, last_seen_at, last_seen_cycle, status, applied, bookmarked, hidden"
)
_JOB_SQL = f"INSERT INTO jobs ({_JOB_COLS}) VALUES ({', '.join('?' * 22)})"
_NOW = "2026-07-15T00:00:00+00:00"


class FakeScheduler:
    """Implements SchedulerProtocol. trigger_now inserts a 'running' run (unless busy)."""

    def __init__(self, db_path: str, *, busy: bool = False) -> None:
        self.db_path = db_path
        self.busy = busy
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    @property
    def next_run_at(self) -> datetime | None:
        return datetime(2026, 7, 17, 3, 0, tzinfo=UTC)

    def trigger_now(self, trigger: str = "manual") -> int | None:
        if self.busy:
            return None
        conn = connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO runs (started_at, status, trigger) VALUES (?, 'running', ?)",
                (FIXED_NOW.isoformat(), trigger),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def _seed_runs(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO runs (run_id, started_at, finished_at, status, trigger, n_new, n_updated, "
        "n_expired) VALUES (1, '2026-07-15T03:00:00+00:00', '2026-07-15T03:01:00+00:00', "
        "'success', 'schedule', 3, 1, 0)"
    )
    conn.execute(
        "INSERT INTO source_runs (run_id, source, succeeded, n_fetched, duration_ms) "
        "VALUES (1, 'remoteok', 1, 42, 1200)"
    )
    conn.execute(
        "INSERT INTO source_runs (run_id, source, succeeded, n_fetched, duration_ms, error) "
        "VALUES (1, 'naukri', 0, 0, 500, '429 Too Many Requests')"
    )
    conn.commit()


def _seed_jobs(conn: sqlite3.Connection) -> None:
    conn.execute(
        _JOB_SQL,
        (
            "j1",
            "remoteok",
            "Backend Engineer Intern",
            "Acme",
            "Remote",
            1,
            "https://x/j1",
            250000,
            250000,
            "INR",
            "month",
            1,
            "pass",
            9.0,
            "2026-07-14",
            _NOW,
            _NOW,
            1,
            "active",
            1,
            0,
            0,
        ),
    )
    conn.execute(
        _JOB_SQL,
        (
            "j2",
            "naukri",
            "ML Intern",
            "Beta",
            "Bengaluru",
            0,
            "https://x/j2",
            None,
            None,
            None,
            None,
            0,
            "unknown",
            5.0,
            "2026-07-13",
            _NOW,
            _NOW,
            1,
            "new",
            0,
            1,
            0,
        ),
    )
    conn.execute(
        _JOB_SQL,
        (
            "j3",
            "linkedin",
            "Data Intern",
            "Gamma",
            "Remote",
            1,
            "https://x/j3",
            300000,
            300000,
            "INR",
            "month",
            1,
            "pass",
            7.0,
            "2026-07-12",
            _NOW,
            _NOW,
            1,
            "active",
            0,
            0,
            1,
        ),
    )
    conn.commit()


def _bare_db(tmp_path: Path) -> str:
    p = tmp_path / "jobs.db"
    conn = connect(p)
    init_db(conn)
    seed_from_yaml(conn)
    conn.close()
    return str(p)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = _bare_db(tmp_path)
    conn = connect(path)
    _seed_runs(conn)  # run_id=1 must exist before jobs (FK last_seen_cycle)
    _seed_jobs(conn)
    conn.close()
    return path


@pytest.fixture
def client(db_path: str) -> Iterator[TestClient]:
    app = create_app(db_path=db_path, clock=FixedClock(FIXED_NOW), scheduler=FakeScheduler(db_path))
    with TestClient(app) as c:
        yield c


def _uid_order(html: str) -> list[str]:
    # One match per card (the <article> carries data-uid; its inner action buttons do too, so we
    # anchor on the card element to keep exactly one hit per job, in render order).
    return re.findall(r'<article class="job-card" data-uid="(j\d+)"', html)


# ── index + filters ─────────────────────────────────────────────────────────────────────


def test_index_renders_jobs_and_header(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Backend Engineer Intern" in r.text
    assert "JobAggregator" in r.text  # header brand
    assert "Last: success" in r.text  # header last-run summary


def test_index_renders_cards_and_modal_container(client: TestClient) -> None:
    r = client.get("/")
    assert 'class="jobs-grid"' in r.text
    assert 'class="job-card"' in r.text
    assert 'id="job-modal"' in r.text  # the (hidden) detail modal is present for JS to fill


def test_default_hides_hidden(client: TestClient) -> None:
    visible = _uid_order(client.get("/").text)
    assert "j3" not in visible  # hidden by default
    assert set(visible) == {"j1", "j2"}
    with_hidden = _uid_order(client.get("/?show_hidden=1").text)
    assert "j3" in with_hidden


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?source=remoteok", {"j1"}),
        ("?remote=yes&show_hidden=1", {"j1", "j3"}),
        ("?remote=no", {"j2"}),
        ("?bucket=pass&show_hidden=1", {"j1", "j3"}),
        ("?bucket=unknown", {"j2"}),
        ("?applied=1", {"j1"}),
        ("?bookmarked=1", {"j2"}),
        ("?q=backend", {"j1"}),
    ],
)
def test_filters(client: TestClient, query: str, expected: set[str]) -> None:
    assert set(_uid_order(client.get("/" + query).text)) == expected


@pytest.mark.parametrize(
    ("sort", "order"),
    [
        ("score", ["j1", "j3", "j2"]),
        ("date", ["j1", "j2", "j3"]),
        ("salary", ["j3", "j1", "j2"]),  # salary_min DESC, NULL last
    ],
)
def test_sort(client: TestClient, sort: str, order: list[str]) -> None:
    html = client.get(f"/?show_hidden=1&sort={sort}").text
    assert _uid_order(html) == order


def test_empty_state(client: TestClient) -> None:
    r = client.get("/?q=zzznomatch")
    assert r.status_code == 200
    assert 'data-testid="empty"' in r.text


def test_pagination(tmp_path: Path) -> None:
    path = _bare_db(tmp_path)
    conn = connect(path)
    _seed_runs(conn)
    for i in range(60):
        conn.execute(
            _JOB_SQL,
            (
                f"j{i}",
                "remoteok",
                f"Role {i}",
                "Acme",
                "Remote",
                1,
                f"https://x/{i}",
                None,
                None,
                None,
                None,
                0,
                "unknown",
                float(i),
                "2026-07-14",
                _NOW,
                _NOW,
                1,
                "active",
                0,
                0,
                0,
            ),
        )
    conn.commit()
    conn.close()
    app = create_app(db_path=path, clock=FixedClock(FIXED_NOW), scheduler=FakeScheduler(path))
    with TestClient(app) as c:
        page1 = c.get("/?page=1").text
        assert len(_uid_order(page1)) == 50
        assert "Next →" in page1
        page2 = c.get("/?page=2").text
        assert len(_uid_order(page2)) == 10
        assert "← Prev" in page2


# ── row actions ─────────────────────────────────────────────────────────────────────────


def test_action_apply_persists_and_returns_row(client: TestClient, db_path: str) -> None:
    r = client.post("/api/jobs/j2/action", json={"action": "apply"})
    assert r.status_code == 200
    assert 'data-uid="j2"' in r.text
    conn = connect(db_path)
    assert conn.execute("SELECT applied FROM jobs WHERE job_uid='j2'").fetchone()["applied"] == 1
    conn.close()


def test_action_hide_then_unhide(client: TestClient, db_path: str) -> None:
    client.post("/api/jobs/j1/action", json={"action": "hide"})
    conn = connect(db_path)
    assert conn.execute("SELECT hidden FROM jobs WHERE job_uid='j1'").fetchone()["hidden"] == 1
    conn.close()
    client.post("/api/jobs/j1/action", json={"action": "unhide"})
    conn = connect(db_path)
    assert conn.execute("SELECT hidden FROM jobs WHERE job_uid='j1'").fetchone()["hidden"] == 0
    conn.close()


def test_action_unknown_uid_404(client: TestClient) -> None:
    r = client.post("/api/jobs/nope/action", json={"action": "apply"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_action_invalid_action_422(client: TestClient) -> None:
    r = client.post("/api/jobs/j1/action", json={"action": "frobnicate"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_action_returns_card_partial(client: TestClient) -> None:
    r = client.post("/api/jobs/j2/action", json={"action": "apply"})
    assert r.status_code == 200
    assert 'class="job-card"' in r.text and 'data-uid="j2"' in r.text  # swappable card, not a row


# ── detail modal (Track B) ────────────────────────────────────────────────────────────────


def test_detail_renders_link_apply_and_facts(client: TestClient) -> None:
    r = client.get("/api/jobs/j1/detail")
    assert r.status_code == 200
    assert "https://x/j1" in r.text  # link to the original posting
    assert "Open original posting" in r.text
    assert "data-apply-url=" in r.text  # the Apply button carries the posting URL
    assert "Backend Engineer Intern" in r.text


def test_detail_unknown_uid_404(client: TestClient) -> None:
    r = client.get("/api/jobs/nope/detail")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_detail_renders_safe_html_description(client: TestClient, db_path: str) -> None:
    conn = connect(db_path)
    conn.execute(
        "UPDATE jobs SET description = ? WHERE job_uid = 'j1'",
        ("<p>Build <b>systems</b> &amp; things.</p><script>alert('xss')</script>",),
    )
    conn.commit()
    conn.close()
    r = client.get("/api/jobs/j1/detail")
    assert r.status_code == 200
    assert "<strong>systems</strong>" in r.text  # <b> rewritten to an allowlisted <strong>
    assert "&amp; things." in r.text  # '&' re-escaped as text, never executed
    assert "<script>" not in r.text  # script tag never emitted
    assert "alert(" not in r.text  # script *content* dropped entirely


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("plain text", "plain text"),
        ("<p>one</p><p>two</p>", "one\ntwo"),
        ("a<br>b", "a\nb"),
        ("<ul><li>x</li><li>y</li></ul>", "x\ny"),
        ("R&amp;D &lt;tag&gt;", "R&D <tag>"),  # entities decoded to text (Jinja re-escapes later)
        ("<script>evil()</script>keep", "keep"),  # script content dropped
        ("<p>a</p>\n\n\n<p>b</p>", "a\nb"),  # blank-line runs collapse
    ],
)
def test_html_to_text(raw: str | None, expected: str) -> None:
    assert html_to_text(raw) == expected


def test_html_to_text_caps_length() -> None:
    assert len(html_to_text("x" * (MAX_DESC_CHARS + 500))) == MAX_DESC_CHARS


@pytest.mark.parametrize(
    ("raw", "must_contain", "must_not_contain"),
    [
        ("<script>evil()</script>keep", ["keep"], ["<script>", "evil("]),
        ("<style>.x{}</style>hi", ["hi"], ["<style>"]),
        ('<p onclick="steal()">hi</p>', ["<p>", "hi"], ["onclick", "steal("]),
        ('<a href="javascript:alert(1)">x</a>', ["x"], ["javascript:", "<a "]),
        ('<a href="https://safe.io/p">go</a>', ['href="https://safe.io/p"', "nofollow"], []),
        ("<b>bold</b> <i>it</i>", ["<strong>bold</strong>", "<em>it</em>"], ["<b>", "<i>"]),
        ("<iframe src=x></iframe>text", ["text"], ["<iframe"]),
        ("<img src=x onerror=alert(1)>cap", ["cap"], ["<img", "onerror"]),
        ("<p>tom & jerry</p>", ["tom &amp; jerry"], []),  # bare text escaped by us
        ("<ul><li>one</li></ul>", ["<ul>", "<li>one</li>"], []),
    ],
)
def test_render_description_html_is_safe(
    raw: str, must_contain: list[str], must_not_contain: list[str]
) -> None:
    out = render_description_html(raw)
    for s in must_contain:
        assert s in out, f"expected {s!r} in {out!r}"
    for s in must_not_contain:
        assert s not in out, f"did not expect {s!r} in {out!r}"


def test_render_description_html_empty_and_capped() -> None:
    assert render_description_html(None) == ""
    assert render_description_html("") == ""
    assert render_description_html("<div><span></span></div>") == ""  # only markup -> fallback
    capped = render_description_html("<p>" + "x" * (MAX_DESC_CHARS + 500) + "</p>")
    assert len(capped) <= MAX_DESC_CHARS + 20  # visible text capped; small tag overhead allowed


def test_tailor_route_returns_preview(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from job_aggregator.dashboard import routes_jobs
    from job_aggregator.paths import PROFILE_EXAMPLE_YAML
    from job_aggregator.profile.store import load_profile

    example = load_profile(PROFILE_EXAMPLE_YAML)  # the committed placeholder validates
    monkeypatch.setattr(routes_jobs, "load_profile", lambda: example)
    monkeypatch.setattr(routes_jobs, "compile_pdf", lambda tex, out: out)  # skip real LaTeX
    monkeypatch.setenv("JOBAGG_DATA_DIR", str(tmp_path))
    r = client.post("/api/jobs/j1/tailor")
    assert r.status_code == 200
    assert "preservation" in r.text
    assert "/api/jobs/j1/resume.pdf" in r.text  # PDF link present when compile succeeds


def test_tailor_route_backend_seam_is_none_by_default() -> None:
    from job_aggregator.config.schema import Config
    from job_aggregator.dashboard.routes_jobs import _tailor_backend

    # default seam = no LLM backend -> pure deterministic selection, no network, no fabrication
    assert _tailor_backend(Config()) is None


def test_tailor_route_unknown_uid_404(client: TestClient) -> None:
    r = client.post("/api/jobs/nope/tailor")  # row is None -> 404 before load_profile
    assert r.status_code == 404


def test_resume_pdf_rejects_non_hex_uid(client: TestClient) -> None:
    # path-traversal guard: uid must be sha256 hex before it can touch the filesystem
    assert client.get("/api/jobs/j1/resume.pdf").status_code == 404  # not 64-hex
    assert (
        client.get("/api/jobs/" + "z" * 64 + "/resume.pdf").status_code == 404
    )  # 64 chars, not hex


def test_resume_pdf_404_when_absent(client: TestClient) -> None:
    r = client.get("/api/jobs/" + "a" * 64 + "/resume.pdf")  # valid hex, no file on disk
    assert r.status_code == 404


def test_create_app_reads_jobagg_db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `serve --db X` reaches the uvicorn factory only via JOBAGG_DB — create_app must honor it.
    path = _bare_db(tmp_path)
    monkeypatch.setenv("JOBAGG_DB", path)
    app = create_app(clock=FixedClock(FIXED_NOW), scheduler=FakeScheduler(path))
    assert app.state.db_path == path


def test_create_app_explicit_db_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBAGG_DB", "/should/be/ignored.db")
    path = _bare_db(tmp_path)
    app = create_app(db_path=path, clock=FixedClock(FIXED_NOW), scheduler=FakeScheduler(path))
    assert app.state.db_path == path


# ── config ──────────────────────────────────────────────────────────────────────────────


def test_config_page(client: TestClient) -> None:
    r = client.get("/config")
    assert r.status_code == 200
    assert 'name="run_hour_local"' in r.text
    assert 'value="3"' in r.text


def test_config_put_valid_saves_and_preserves_fx_rates(client: TestClient, db_path: str) -> None:
    r = client.put("/api/config", data={"run_hour_local": "5", "salary_min_remote": "40000"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    conn = connect(db_path)
    cfg = __import__("json").loads(
        conn.execute("SELECT data FROM config WHERE id=1").fetchone()["data"]
    )
    conn.close()
    assert cfg["schedule"]["run_hour_local"] == 5
    assert cfg["salary"]["min_remote"] == 40000
    assert cfg["salary"]["fx_rates"] == {"USD": 83.0, "EUR": 90.0, "GBP": 105.0}  # preserved


def test_config_put_persists_must_have(client: TestClient, db_path: str) -> None:
    r = client.put("/api/config", data={"keywords_must_have": "backend\ngo\nkubernetes"})
    assert r.status_code == 200
    conn = connect(db_path)
    cfg = __import__("json").loads(
        conn.execute("SELECT data FROM config WHERE id=1").fetchone()["data"]
    )
    conn.close()
    assert cfg["keywords"]["must_have"] == ["backend", "go", "kubernetes"]


def test_config_put_can_disable_toggles(client: TestClient, db_path: str) -> None:
    # Regression: a boolean toggle must be turn-OFF-able, not just turn-on-able. The JS sends an
    # explicit "false" for unchecked boxes; verify the server disables the setting.
    r = client.put("/api/config", data={"src_remoteok": "false", "notify_rss_enabled": "false"})
    assert r.status_code == 200
    conn = connect(db_path)
    cfg = __import__("json").loads(
        conn.execute("SELECT data FROM config WHERE id=1").fetchone()["data"]
    )
    conn.close()
    assert cfg["sources"]["remoteok"]["enabled"] is False
    assert cfg["notify"]["rss"]["enabled"] is False


@pytest.mark.parametrize(
    ("field_name", "value", "dotted"),
    [
        ("run_hour_local", "99", "schedule.run_hour_local"),
        ("salary_min_remote", "-5", "salary.min_remote"),
        ("salary_on_missing", "bogus", "salary.on_missing"),
    ],
)
def test_config_put_invalid_422(
    client: TestClient, field_name: str, value: str, dotted: str
) -> None:
    r = client.put("/api/config", data={field_name: value})
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "config_invalid"
    assert body["error"]["details"]["errors"][0]["field"] == dotted


# ── runs ────────────────────────────────────────────────────────────────────────────────


def test_runs_page(client: TestClient) -> None:
    r = client.get("/runs")
    assert r.status_code == 200
    assert "success" in r.text
    assert "remoteok" in r.text  # source breakdown
    assert "naukri" in r.text


def test_run_now_returns_202_then_current_running(client: TestClient) -> None:
    r = client.post("/api/runs")
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    assert isinstance(run_id, int)
    cur = client.get("/api/runs/current").json()
    assert cur["status"] == "running"
    assert cur["run_id"] == run_id


def test_run_now_conflict_409(db_path: str) -> None:
    app = create_app(
        db_path=db_path, clock=FixedClock(FIXED_NOW), scheduler=FakeScheduler(db_path, busy=True)
    )
    with TestClient(app) as c:
        r = c.post("/api/runs")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "run_in_progress"


def test_current_idle_when_no_runs(tmp_path: Path) -> None:
    path = _bare_db(tmp_path)  # config only, no runs/jobs
    app = create_app(db_path=path, clock=FixedClock(FIXED_NOW), scheduler=FakeScheduler(path))
    with TestClient(app) as c:
        assert c.get("/api/runs/current").json()["status"] == "idle"


# ── static + lifespan ───────────────────────────────────────────────────────────────────


def test_theme_css_tokens(client: TestClient) -> None:
    css = client.get("/static/css/theme.css").text
    for token in ("#E23F3F", "#FF6B5B", "#FBF3EA", "#241713", '[data-theme="dark"]'):
        assert token in css


def test_favicon_served(client: TestClient) -> None:
    r = client.get("/static/favicon.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg")


def test_lifespan_starts_and_stops_scheduler(db_path: str) -> None:
    sched = FakeScheduler(db_path)
    app = create_app(db_path=db_path, clock=FixedClock(FIXED_NOW), scheduler=sched)
    with TestClient(app):
        assert sched.started is True
    assert sched.stopped is True
