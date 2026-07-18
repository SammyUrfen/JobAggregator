"""Agentic apply session (Track D v2): pure helpers + the browser-cookie importer.

The browser/CLI-bound `AgenticSession.fill_form` is live-verified (it needs a display and the
claude CLI); everything decidable without them — the safety-contract prompt, the report parser,
the MCP config, the claude invocation, and Firefox-format cookie extraction — is covered here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from job_aggregator.apply.agentic import (
    MCP_SERVER_NAME,
    build_apply_prompt,
    build_claude_command,
    extract_result_text,
    free_port,
    mcp_config,
    parse_agent_report,
)
from job_aggregator.apply.cookies import base_domain, find_cookie_db, load_cookies_for_url
from job_aggregator.apply.driver import ApplicationFields

_FIELDS = ApplicationFields(
    full_name="Test Person",
    first_name="Test",
    last_name="Person",
    email="t@example.com",
    resume_path="/tmp/resume.pdf",
    phone="9999999999",
)

# ── prompt: the safety contract ──────────────────────────────────────────────────────────


def test_prompt_contains_the_never_submit_rule() -> None:
    prompt = build_apply_prompt(_FIELDS, "https://x.example/job")
    assert "NEVER submit the application" in prompt
    assert "A human reviews and submits" in prompt


def test_prompt_contains_reach_the_form_and_captcha_wait_rules() -> None:
    prompt = build_apply_prompt(_FIELDS, "https://x.example/job")
    assert "REACH the form" in prompt  # posting page -> Apply/Easy Apply click-through
    assert "Easy Apply" in prompt
    assert "captcha" in prompt.lower()
    assert "browser_wait_for" in prompt  # waits while the human solves it
    assert "credentials" in prompt  # anti-thrash: never type credentials / auto-solve walls
    assert "thrashing logs the session out" in prompt


def test_prompt_embeds_only_applicant_data_and_resume() -> None:
    prompt = build_apply_prompt(_FIELDS, "https://x.example/job")
    assert '"email": "t@example.com"' in prompt
    assert "/tmp/resume.pdf" in prompt
    assert "Never invent an answer" in prompt


def test_prompt_names_the_mcp_tools() -> None:
    prompt = build_apply_prompt(_FIELDS, "https://x.example/job")
    assert f"mcp__{MCP_SERVER_NAME}__" in prompt


# ── report parser ────────────────────────────────────────────────────────────────────────


def test_parse_report_happy_path() -> None:
    out = (
        "I filled the form.\n"
        'RESULT: {"filled": ["email", "phone"], "unfilled": ["cover"], '
        '"needs_login": false, "notes": "on review step"}'
    )
    filled, unfilled, needs_login, notes = parse_agent_report(out)
    assert filled == ["email", "phone"]
    assert unfilled == ["cover"]
    assert needs_login is False
    assert notes == "on review step"


def test_parse_report_needs_login() -> None:
    out = (
        "blocked.\n"
        'RESULT: {"filled": [], "unfilled": [], "needs_login": true, "notes": "login wall"}'
    )
    assert parse_agent_report(out)[2] is True


def test_parse_report_missing_line_degrades() -> None:
    filled, unfilled, needs_login, notes = parse_agent_report("the model rambled with no report")
    assert filled == [] and unfilled == []
    assert needs_login is False
    assert "rambled" in notes  # raw tail preserved for the human


def test_parse_report_malformed_json_degrades() -> None:
    filled, _, _, notes = parse_agent_report("RESULT: {not json")
    assert filled == []
    assert "unparsed" in notes


# ── MCP config + claude command ──────────────────────────────────────────────────────────


def test_mcp_config_attaches_to_our_cdp_port() -> None:
    cfg = mcp_config(9977)
    server = cfg["mcpServers"][MCP_SERVER_NAME]
    assert server["type"] == "stdio"  # required by claude's --mcp-config parser
    assert server["command"] == "npx"
    assert "--cdp-endpoint" in server["args"]
    assert "http://127.0.0.1:9977" in server["args"]
    assert any(a.startswith("@playwright/mcp@") for a in server["args"])  # version-pinned


def test_build_claude_command_least_privilege() -> None:
    cmd = build_claude_command("claude", "PROMPT", "/tmp/mcp.json", model="sonnet")
    joined = " ".join(cmd)
    assert cmd[:3] == ["claude", "-p", "PROMPT"]
    assert "--mcp-config" in cmd
    assert "--strict-mcp-config" in cmd  # the user's other MCP servers stay out of scope
    assert f"mcp__{MCP_SERVER_NAME}__*" in cmd  # ONLY the browser tools are allowed
    assert "Bash" in joined and "--disallowedTools" in cmd  # no shell/file access
    assert "--max-turns" not in cmd  # flag doesn't exist in claude v2.1.x; timeout is the bound
    assert "stream-json" in cmd and "--verbose" in cmd  # live-tailable transcript
    assert cmd[-2:] == ["--model", "sonnet"]


def test_build_claude_command_no_model_inherits_default() -> None:
    assert "--model" not in build_claude_command("claude", "P", "/tmp/m.json", model=None)


def test_extract_result_text_from_stream() -> None:
    stream = "\n".join(
        [
            '{"type":"system","subtype":"init"}',
            '{"type":"assistant","message":{}}',
            "npm warn something non-json",
            '{"type":"result","is_error":false,"result":"RESULT: {\\"filled\\": []}"}',
        ]
    )
    text, is_error = extract_result_text(stream)
    assert text == 'RESULT: {"filled": []}'
    assert is_error is False


def test_extract_result_text_error_flag_and_degrade() -> None:
    text, is_error = extract_result_text('{"type":"result","is_error":true,"result":"boom"}')
    assert (text, is_error) == ("boom", True)
    raw, is_error = extract_result_text("killed before any result event")
    assert raw == "killed before any result event"
    assert is_error is False


def test_free_port_is_bindable() -> None:
    import socket

    port = free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))  # must be free right after


# ── browser-cookie import (Firefox/Zen format) ───────────────────────────────────────────


def _write_cookie_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT NOT NULL "
        "DEFAULT '', name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, "
        "lastAccessed INTEGER, creationTime INTEGER, isSecure INTEGER, isHttpOnly INTEGER, "
        "inBrowserElement INTEGER DEFAULT 0, sameSite INTEGER DEFAULT 0, "
        "schemeMap INTEGER DEFAULT 0, isPartitionedAttributeSet INTEGER DEFAULT 0, "
        "updateTime INTEGER)"
    )
    conn.executemany(
        "INSERT INTO moz_cookies (name, value, host, path, expiry, isSecure, isHttpOnly, "
        "sameSite) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.linkedin.com", "linkedin.com"),
        ("linkedin.com", "linkedin.com"),
        ("boards.greenhouse.io", "greenhouse.io"),
        ("internshala.com", "internshala.com"),
        ("jobs.foo.co.in", "foo.co.in"),
        ("unstop.com", "unstop.com"),
    ],
)
def test_base_domain(host: str, expected: str) -> None:
    assert base_domain(host) == expected


def test_load_cookies_filters_to_target_domain(tmp_path: Path) -> None:
    db = tmp_path / "cookies.sqlite"
    _write_cookie_db(
        db,
        [
            ("li_at", "SECRET1", ".linkedin.com", "/", 2000000000, 1, 1, 1),
            ("lang", "v=2", ".www.linkedin.com", "/", 2000000000, 1, 0, 0),
            ("gh_sess", "SECRET2", ".github.com", "/", 2000000000, 1, 1, 2),  # other site
        ],
    )
    cookies = load_cookies_for_url("https://www.linkedin.com/jobs/view/123", db_path=db)
    names = {c["name"] for c in cookies}
    assert names == {"li_at", "lang"}  # github row excluded — only the target domain's jar
    li = next(c for c in cookies if c["name"] == "li_at")
    assert li["domain"] == ".linkedin.com"
    assert li["httpOnly"] is True and li["secure"] is True
    assert li["sameSite"] == "Lax"  # firefox 1 -> Lax
    strictish = next(c for c in cookies if c["name"] == "lang")
    assert strictish["sameSite"] == "None"  # firefox 0 -> None


def test_load_cookies_missing_db_is_empty(tmp_path: Path) -> None:
    assert load_cookies_for_url("https://x.example/", db_path=tmp_path / "nope.sqlite") == []


def test_load_cookies_garbage_db_is_empty_not_crash(tmp_path: Path) -> None:
    bad = tmp_path / "cookies.sqlite"
    bad.write_text("this is not sqlite")
    assert load_cookies_for_url("https://x.example/", db_path=bad) == []


def test_find_cookie_db_explicit_path_wins(tmp_path: Path) -> None:
    db = tmp_path / "cookies.sqlite"
    _write_cookie_db(db, [])
    assert find_cookie_db(db) == db
    assert find_cookie_db(tmp_path / "absent.sqlite") is None


@pytest.mark.parametrize(
    ("expiry", "expected"),
    [
        (1784013663, 1784013663),  # stock Firefox: seconds pass through
        (1784013663000, 1784013663),  # Zen: MILLISECONDS -> normalized to seconds
        (0, -1),  # session cookie
        (None, -1),
        (-5, -1),
        ("garbage", -1),
    ],
)
def test_expires_seconds_normalizes_zen_milliseconds(expiry: object, expected: int) -> None:
    from job_aggregator.apply.cookies import _expires_seconds

    assert _expires_seconds(expiry) == expected


def test_load_cookies_millisecond_expiry_normalized(tmp_path: Path) -> None:
    """The Zen quirk that killed every apply attempt: ms-scale expiry passed to Playwright as
    'seconds' (year ~58,000) is rejected, and one bad cookie sank the whole context."""
    db = tmp_path / "cookies.sqlite"
    _write_cookie_db(db, [("access_token", "S", ".unstop.com", "/", 1786605647691, 1, 1, 1)])
    (cookie,) = load_cookies_for_url("https://unstop.com/internships/x", db_path=db)
    assert cookie["expires"] == 1786605647  # seconds, within Playwright's valid range


def test_prompt_includes_extra_context_when_present() -> None:
    fields = ApplicationFields(
        full_name="Test Person",
        first_name="Test",
        last_name="Person",
        email="t@example.com",
        resume_path="/tmp/r.pdf",
        extra_context="Notice period: 0 days. Open to relocation. Expected stipend: 20k/month.",
    )
    prompt = build_apply_prompt(fields, "https://x.example/job")
    assert "ADDITIONAL CONTEXT" in prompt
    assert "Notice period: 0 days" in prompt
    assert "never invent beyond it" in prompt  # still bound by the no-fabrication rule


def test_prompt_omits_context_section_when_absent() -> None:
    fields = ApplicationFields(
        full_name="T",
        first_name="T",
        last_name="P",
        email="t@e.com",
        resume_path="/tmp/r.pdf",
    )
    assert "ADDITIONAL CONTEXT" not in build_apply_prompt(fields, "https://x/job")
