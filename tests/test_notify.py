"""Phase 7 — notify: digest formatting, send behavior, RSS well-formedness, new_only reads.

See PLAN.md Part II (Phase 7) for the exact table-driven cases to implement.
"""

from __future__ import annotations

import json
import sqlite3
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

import httpx
import respx

from _fakes import make_job as mkjob
from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.notify.base import FeedScope, build_notifiers
from job_aggregator.notify.email import EmailNotifier, build_email
from job_aggregator.notify.rss import RssNotifier, render_feed
from job_aggregator.notify.telegram import TELEGRAM_MESSAGE_LIMIT, TelegramNotifier, build_digest
from job_aggregator.storage import jobs_repo, runs_repo

ATOM_NS = "http://www.w3.org/2005/Atom"
_NS = {"a": ATOM_NS}
FEED_NOW = datetime(2026, 7, 15, 3, 0, tzinfo=UTC)


# ── Telegram build_digest ───────────────────────────────────────────────────────────────


def test_build_digest_singular() -> None:
    text = build_digest([mkjob("a")])
    assert "1 new job" in text
    assert "1 new jobs" not in text


def test_build_digest_plural_and_cap() -> None:
    jobs = [mkjob(f"u{i}", title=f"Role {i}", company="Acme") for i in range(25)]
    text = build_digest(jobs, max_jobs=20)
    assert "25 new jobs" in text
    assert "\n20. " in text
    assert "\n21. " not in text
    assert "…and 5 more." in text


def test_build_digest_escapes_html() -> None:
    text = build_digest([mkjob("a", title="C++ & <Go>", company="A&B")])
    assert "&lt;Go&gt;" in text
    assert "&amp;" in text
    assert "<Go>" not in text  # raw angle brackets never leak through


def test_build_digest_truncates_to_limit() -> None:
    jobs = [mkjob(f"u{i}", title="X" * 200, company="Y" * 200) for i in range(100)]
    text = build_digest(jobs, max_jobs=100)
    assert len(text) <= TELEGRAM_MESSAGE_LIMIT


# ── Telegram send ───────────────────────────────────────────────────────────────────────


def test_telegram_posts_message(monkeypatch: Any) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    with respx.mock:
        route = respx.route(method="POST", host="api.telegram.org").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        TelegramNotifier().notify_new([mkjob("a")], Config())
    body = json.loads(route.calls.last.request.content)
    assert body["chat_id"] == "C"
    assert body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] is True


def test_telegram_dry_run_when_token_missing(monkeypatch: Any) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with respx.mock:
        route = respx.route(method="POST", host="api.telegram.org").mock(
            return_value=httpx.Response(200)
        )
        TelegramNotifier().notify_new([mkjob("a")], Config())
        assert route.call_count == 0


def test_telegram_empty_jobs_no_send(monkeypatch: Any) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    with respx.mock:
        route = respx.route(method="POST", host="api.telegram.org").mock(
            return_value=httpx.Response(200)
        )
        TelegramNotifier().notify_new([], Config())
        assert route.call_count == 0


def test_telegram_swallows_http_error(monkeypatch: Any) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "C")
    with respx.mock:
        respx.route(method="POST", host="api.telegram.org").mock(return_value=httpx.Response(500))
        TelegramNotifier().notify_new([mkjob("a")], Config())  # must NOT raise


# ── Email ───────────────────────────────────────────────────────────────────────────────


class _FakeSMTP:
    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.tls = False
        self.logged_in: tuple[str, str] | None = None
        self.quit_called = False

    def starttls(self) -> None:
        self.tls = True

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, msg: Any) -> None:
        self.sent.append(msg)

    def quit(self) -> None:
        self.quit_called = True


def _cfg_with_recipient(to: str = "me@example.com") -> Config:
    cfg = Config()
    cfg.notify.email.to = to
    return cfg


def test_build_email_singular() -> None:
    subject, body = build_email([mkjob("a", title="Backend Intern", company="Acme")])
    assert subject == "[JobAggregator] 1 new job"
    assert "Backend Intern — Acme" in body


def test_email_sends_without_creds(monkeypatch: Any) -> None:
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    fake = _FakeSMTP()
    EmailNotifier(smtp=fake).notify_new([mkjob("a")], _cfg_with_recipient())
    assert len(fake.sent) == 1
    assert fake.sent[0]["To"] == "me@example.com"
    assert fake.tls is False
    assert fake.logged_in is None
    assert fake.quit_called is False  # injected SMTP is never quit-ed


def test_email_starttls_login_with_creds(monkeypatch: Any) -> None:
    monkeypatch.setenv("SMTP_USER", "u")
    monkeypatch.setenv("SMTP_PASSWORD", "p")
    fake = _FakeSMTP()
    EmailNotifier(smtp=fake).notify_new([mkjob("a")], _cfg_with_recipient())
    assert fake.tls is True
    assert fake.logged_in == ("u", "p")
    assert len(fake.sent) == 1


def test_email_dry_run_when_no_recipient() -> None:
    fake = _FakeSMTP()
    EmailNotifier(smtp=fake).notify_new([mkjob("a")], Config())  # to="" default
    assert fake.sent == []


def test_email_swallows_send_error() -> None:
    class _Boom(_FakeSMTP):
        def send_message(self, msg: Any) -> None:
            raise RuntimeError("smtp down")

    EmailNotifier(smtp=_Boom()).notify_new([mkjob("a")], _cfg_with_recipient())  # must NOT raise


# ── RSS ─────────────────────────────────────────────────────────────────────────────────


def test_render_feed_well_formed() -> None:
    jobs = [
        mkjob(
            f"u{i}", title=f"Role {i}", company="Acme", posted_at=datetime(2026, 7, 10, tzinfo=UTC)
        )
        for i in range(3)
    ]
    root = ET.fromstring(render_feed(jobs, Config(), FixedClock(FEED_NOW)))
    assert len(root.findall("a:entry", _NS)) == 3
    updated = root.find("a:updated", _NS)
    assert updated is not None
    assert updated.text == "2026-07-15T03:00:00+00:00"


def test_render_feed_escapes_round_trip() -> None:
    root = ET.fromstring(
        render_feed([mkjob("a", title="C & D <x>", company="Acme")], Config(), FixedClock(FEED_NOW))
    )
    title = root.find("a:entry/a:title", _NS)
    assert title is not None
    assert title.text == "C & D <x> — Acme"


def test_render_feed_caps_at_max_items() -> None:
    cfg = Config()
    cfg.notify.rss.max_items = 2
    jobs = [mkjob(f"u{i}") for i in range(5)]
    root = ET.fromstring(render_feed(jobs, cfg, FixedClock(FEED_NOW)))
    assert len(root.findall("a:entry", _NS)) == 2


def test_render_feed_empty_is_valid() -> None:
    root = ET.fromstring(render_feed([], Config(), FixedClock(FEED_NOW)))
    assert root.findall("a:entry", _NS) == []


def test_rss_notifier_writes_atomically(tmp_path: Any) -> None:
    out = tmp_path / "feed.xml"
    RssNotifier(clock=FixedClock(FEED_NOW), out_path=out).notify_new(
        [mkjob("a", title="Role", company="Acme")], Config()
    )
    assert out.exists()
    ET.fromstring(out.read_text())  # parses => well-formed
    assert not (tmp_path / "feed.xml.tmp").exists()  # no temp file left behind


# ── Factory ─────────────────────────────────────────────────────────────────────────────


def test_build_notifiers_default_is_rss_only() -> None:
    notifiers = build_notifiers(Config())
    assert [n.name for n in notifiers] == ["rss"]
    assert notifiers[0].feed_scope is FeedScope.RECENT_ACTIVE


def test_build_notifiers_all_enabled() -> None:
    cfg = Config()
    cfg.notify.telegram.enabled = True
    cfg.notify.email.enabled = True
    cfg.notify.rss.enabled = True
    assert {n.name for n in build_notifiers(cfg)} == {"telegram", "email", "rss"}


def test_build_notifiers_all_disabled() -> None:
    cfg = Config()
    cfg.notify.rss.enabled = False
    assert build_notifiers(cfg) == []


# ── new_only feed reads (jobs_repo) ─────────────────────────────────────────────────────


def test_jobs_new_in_run_selects_only_this_run(conn: sqlite3.Connection, clock: FixedClock) -> None:
    r1 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, mkjob("a", source="s"), r1, clock)  # A new in r1
    r2 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, mkjob("a", source="s"), r2, clock)  # A re-seen -> active
    jobs_repo.upsert_job(conn, mkjob("b", source="s", company="Beta"), r2, clock)  # B new in r2
    assert {j.job_uid for j in jobs_repo.jobs_new_in_run(conn, r2)} == {"b"}


def test_jobs_new_in_run_excludes_stuck_new_from_failed_source(
    conn: sqlite3.Connection, clock: FixedClock
) -> None:
    r1 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, mkjob("stuck", source="s"), r1, clock)  # 'new', source later fails
    r2 = runs_repo.start_run(conn, "manual", clock)
    jobs_repo.upsert_job(conn, mkjob("fresh", source="s"), r2, clock)
    assert {j.job_uid for j in jobs_repo.jobs_new_in_run(conn, r2)} == {"fresh"}


def test_recent_active_excludes_hidden_and_deleted(
    conn: sqlite3.Connection, clock: FixedClock
) -> None:
    r1 = runs_repo.start_run(conn, "manual", clock)
    for uid in ("vis", "hid", "del"):
        jobs_repo.upsert_job(conn, mkjob(uid, source="s"), r1, clock)
    jobs_repo.set_user_flag(conn, "hid", "hidden", True)
    conn.execute("UPDATE jobs SET status='deleted' WHERE job_uid='del'")
    conn.commit()
    assert {j.job_uid for j in jobs_repo.recent_active_jobs(conn, 10)} == {"vis"}
