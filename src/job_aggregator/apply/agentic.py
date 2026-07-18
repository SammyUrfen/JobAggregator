"""Agentic apply session (Track D v2): Claude Code drives the visible apply browser over MCP.

WHY this replaces one-shot Set-of-Marks grounding: a job URL is usually a POSTING page, not a
form — the real form sits behind "Apply / Easy Apply / Quick Apply", sometimes a multi-step
wizard, sometimes a captcha or login wall. A single fill pass cannot navigate that; an agent
loop can. The shape:

    our Python  ──launches──▶  headful Chromium (CDP port open, user's Zen cookies imported)
    our Python  ──spawns───▶   `claude -p` with @playwright/mcp attached via --cdp-endpoint
    Claude      ──drives───▶   THE SAME Chromium window the human is watching:
                               reach the form → fill from the applicant data → attach résumé →
                               STOP (never submit). Captcha/login wall? It waits, polling,
                               while the human solves it in that very window.

The safety contract is unchanged and enforced twice: the prompt forbids submitting, and the
human still reviews + submits in the browser afterwards (`FillResult.submitted` is always
False). Verified plumbing (2026-07-18, this machine): @playwright/mcp 0.0.78 `--cdp-endpoint`
attaches to a playwright-python Chromium and reuses its existing page/context.

Only `fill_form` touches the browser/CLI; the prompt builder, report parser, and MCP config
are pure and unit-tested.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from job_aggregator.apply.driver import FillResult
from job_aggregator.errors import AgentError

if TYPE_CHECKING:
    from job_aggregator.apply.driver import ApplicationFields

log = logging.getLogger(__name__)

# The MCP server name in the generated config; claude sees tools as mcp__<name>__browser_*.
MCP_SERVER_NAME = "browser"
# Generous default: a human may be solving a captcha in the middle of the session.
DEFAULT_TIMEOUT_S = 900.0
# The machine-readable last line the prompt asks for; parse_agent_report scans for it.
_REPORT_PREFIX = "RESULT:"


def free_port() -> int:
    """An OS-assigned free TCP port for Chromium's CDP endpoint (127.0.0.1-only)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def mcp_config(port: int) -> dict[str, Any]:
    """The --mcp-config payload: one stdio server, @playwright/mcp attached to OUR Chromium.
    Version-pinned so a future breaking MCP release can't silently change apply behavior."""
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "type": "stdio",  # required by claude's --mcp-config parser (verified v2.1.214)
                "command": "npx",
                "args": [
                    "-y",
                    "@playwright/mcp@0.0.78",
                    "--cdp-endpoint",
                    f"http://127.0.0.1:{port}",
                ],
            }
        }
    }


def build_apply_prompt(fields: ApplicationFields, url: str) -> str:
    """The agent's instructions. The HARD RULES section is the safety contract — tests assert
    the never-submit and captcha-wait clauses stay present verbatim-ish."""
    data = dict(fields.text_map())
    return f"""You are operating a browser (the mcp tools named mcp__{MCP_SERVER_NAME}__*) to
fill ONE job application. The browser currently shows: {url}

APPLICANT DATA (the ONLY facts you may enter):
{json.dumps(data, indent=2)}
Résumé file to upload where a resume/CV is asked: {fields.resume_path}

HARD RULES — read carefully:
1. NEVER submit the application. Do not click Submit / Send / Apply now (final) /
   Review & submit, and never press Enter in a way that submits. Fill everything, then STOP.
   A human reviews and submits.
2. First REACH the form: this page is usually the posting, not the form. Click the apply
   affordance (Apply / Easy Apply / Quick Apply / Apply now). Navigating steps of a wizard
   with Next / Continue / Review is allowed — only the FINAL submitting action is forbidden.
3. If a captcha, OTP, or login wall appears: the human is watching this very browser window
   and will solve it. Say what you see, then wait with mcp__{MCP_SERVER_NAME}__browser_wait_for
   (about 15 seconds at a time) and re-check with a fresh snapshot, repeating for up to
   10 minutes. Continue once it clears. Do NOT try to solve a captcha yourself and do NOT
   enter credentials.
4. Enter ONLY the applicant data above. Leave anything you don't have empty (cover letter,
   demographic surveys, salary expectations) and report it as unfilled. Never invent an answer.
5. If the page offers several apply paths, prefer the one that stays on this site
   (Easy/Quick Apply) over an external redirect; follow the redirect only if it is the sole
   path.
6. If a new tab opens, switch to it with mcp__{MCP_SERVER_NAME}__browser_tabs.

When you are done (form filled, or genuinely stuck), end your reply with ONE final line,
machine-parsed:
{_REPORT_PREFIX} {{"filled": ["<field names you filled>"], "unfilled": ["<fields left \
empty>"], "needs_login": <true if a login/captcha wall blocked you to the end>, \
"notes": "<one line: where the form stands now>"}}"""


def extract_result_text(stream_output: str) -> tuple[str, bool]:
    """(final result text, is_error) from a stream-json transcript.

    The last {"type": "result"} event carries the agent's final message in "result" and an
    "is_error" flag. A transcript with no result event (killed mid-run, non-JSON noise) degrades
    to the raw text so parse_agent_report / error paths still have something to show."""
    result_text, is_error, found = stream_output, False, False
    for raw_line in stream_output.splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result_text = str(event.get("result") or "")
            is_error = bool(event.get("is_error"))
            found = True
    if not found:
        log.warning("agent stream had no result event; using raw output")
    return result_text, is_error


def parse_agent_report(output: str) -> tuple[list[str], list[str], bool, str]:
    """(filled, unfilled, needs_login, notes) from the agent's final RESULT: line.

    Lenient by design — a missing/garbled line degrades to empty lists + the tail of the raw
    output as notes (the human is about to look at the browser anyway; never crash here)."""
    for line in reversed(output.strip().splitlines()):
        stripped = line.strip()
        if not stripped.startswith(_REPORT_PREFIX):
            continue
        try:
            payload = json.loads(stripped[len(_REPORT_PREFIX) :].strip())
            filled = [str(x) for x in payload.get("filled", [])]
            unfilled = [str(x) for x in payload.get("unfilled", [])]
            return filled, unfilled, bool(payload.get("needs_login")), str(payload.get("notes", ""))
        except (ValueError, TypeError, AttributeError):
            break  # malformed RESULT line -> fall through to the degraded path
    tail = output.strip()[-300:]
    return [], [], False, f"agent report unparsed; raw tail: {tail}"


def build_claude_command(
    claude_bin: str, prompt: str, mcp_config_path: str | Path, model: str | None = None
) -> list[str]:
    """The verified headless invocation (claude CLI v2.1.214, probed 2026-07-18): -p prints the
    final message and exits; --strict-mcp-config ignores the user's other MCP servers (only OUR
    browser attaches); --allowedTools with the literal-prefix wildcard auto-permits ONLY the
    browser tools, and --disallowedTools shuts off shell/file/web access (least privilege).
    No --max-turns: the flag does not exist in this CLI version — the wall-clock timeout in
    _run_claude is the runaway bound. `model`: without it the child inherits the user's default
    (observed: opus-1M — slow + quota-hungry for a form fill), so apply defaults to sonnet."""
    cmd = [
        claude_bin,
        "-p",
        prompt,
        "--mcp-config",
        str(mcp_config_path),
        "--strict-mcp-config",
        "--allowedTools",
        f"mcp__{MCP_SERVER_NAME}__*",
        "--disallowedTools",
        "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch",
        # stream-json (+ --verbose, required with it in -p mode) emits one JSON event per line
        # AS THE AGENT WORKS — plain "text" buffers everything until the end, which made a slow
        # session indistinguishable from a hung one. extract_result_text pulls the final answer.
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if model:
        cmd += ["--model", model]
    return cmd


@dataclass
class AgenticSession:
    """BrowserDriver-compatible driver that delegates navigation + filling to a Claude session.

    Satisfies the same `fill_form` contract as PlaywrightDriver, so `apply_to_job` needs no
    changes — the driver seam absorbs the whole feature. `selectors` is accepted and ignored
    (the agent reads the live page instead)."""

    claude_bin: str = "claude"
    timeout_s: float = DEFAULT_TIMEOUT_S
    model: str | None = "sonnet"  # None = the user's default (may be a slow flagship)
    use_browser_cookies: bool = True
    cookie_db: str | None = None
    log_path: str | None = None  # LIVE agent transcript (tail -f while it runs)
    pause: bool = True  # block until the human closes the browser (review + submit)

    def fill_form(
        self,
        url: str,
        fields: ApplicationFields,
        *,
        selectors: dict[str, str] | None = None,
        storage_state: dict[str, Any] | None = None,
        headful: bool = True,
    ) -> FillResult:  # pragma: no cover - needs a real browser + the claude CLI
        import tempfile

        from job_aggregator.apply.cookies import load_cookies_for_url
        from job_aggregator.paths import sessions_dir

        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise AgentError(
                "the apply extra is not installed; run: pip install -e '.[apply]' && "
                "playwright install chromium",
                details={"missing": "playwright"},
            ) from exc

        port = free_port()
        shot = str(sessions_dir().parent / "apply_last.png")
        cookies = (
            load_cookies_for_url(url, db_path=self.cookie_db or None)
            if self.use_browser_cookies
            else []
        )
        with sync_playwright() as _p:
            p: Any = _p  # erase playwright's precise types at the boundary
            browser = p.chromium.launch(
                headless=not headful, args=[f"--remote-debugging-port={port}"]
            )
            ctx = (
                browser.new_context(storage_state=storage_state)
                if storage_state
                else browser.new_context()
            )
            if cookies:
                # Imported browser cookies go on TOP of any saved session: the user's real
                # browser login is the fresher of the two.
                ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded")

            with tempfile.NamedTemporaryFile(
                "w", suffix=".json", prefix="jobagg-mcp-", delete=False
            ) as fh:
                json.dump(mcp_config(port), fh)
                cfg_path = fh.name
            stream = self._run_claude(
                build_claude_command(
                    self.claude_bin, build_apply_prompt(fields, url), cfg_path, self.model
                )
            )
            Path(cfg_path).unlink(missing_ok=True)

            output, agent_errored = extract_result_text(stream)
            if agent_errored:
                log.warning("apply agent reported an error result: %s", output[:200])
            filled, unfilled, needs_login, notes = parse_agent_report(output)
            log.info("agentic apply: filled=%s unfilled=%s notes=%s", filled, unfilled, notes)
            try:
                page.screenshot(path=shot, full_page=True)
            except Exception:  # the agent may have navigated/closed the page — non-fatal
                log.debug("post-agent screenshot failed", exc_info=True)
            # Capture cookies NOW: includes any login the human performed during the session.
            new_state = ctx.storage_state()
            if self.pause:
                print(f"\nAgent finished: {notes or 'see the browser window.'}")
                filled_s, unfilled_s = ", ".join(filled) or "none", ", ".join(unfilled) or "none"
                print(f"Filled: {filled_s} | unfilled: {unfilled_s}")
                print(
                    "Review + SUBMIT it yourself in the browser, then CLOSE the window when done."
                )
                while browser.is_connected():
                    time.sleep(0.5)
            else:
                browser.close()
        return FillResult(
            filled=filled,
            unfilled=unfilled,
            needs_login=needs_login,
            screenshot_path=shot,
            new_state=new_state,
        )

    def _run_claude(self, cmd: list[str]) -> str:  # pragma: no cover - spawns the real CLI
        """Run the claude session with output STREAMED to the log file (not buffered in a pipe):
        progress is tail-able while the agent works, and a timeout still leaves the partial
        transcript on disk — the earlier buffered version lost everything on timeout, which is
        exactly when you need the log most. A dead/failed agent is an AgentError with the log
        location — never a silent empty fill."""
        from job_aggregator.paths import data_dir

        log_file = Path(self.log_path) if self.log_path else data_dir() / "apply_agent.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "w", encoding="utf-8") as fh:
            fh.write(f"$ {cmd[0]} -p <prompt> … (model={self.model or 'default'})\n")
            fh.flush()
            try:
                # stdin=DEVNULL: -p takes the prompt from argv; an open-but-silent stdin makes
                # the CLI wait ~3s and print a warning (probed behavior).
                # cwd=data_dir(): @playwright/mcp restricts browser_file_upload to its
                # workspace root (= the spawning process's cwd when no roots are configured),
                # and the tailored résumé PDF lives under data/resumes — run the agent FROM
                # data/ so the upload is inside the allowed root by construction (smoke-test
                # proven: a résumé outside cwd is denied as "outside allowed roots").
                proc = subprocess.Popen(
                    cmd,
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    text=True,
                    stdin=subprocess.DEVNULL,
                    cwd=str(data_dir()),
                )
            except OSError as exc:
                raise AgentError(f"could not run {cmd[0]!r}: {exc}") from exc
            try:
                rc = proc.wait(timeout=self.timeout_s)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                proc.wait()
                raise AgentError(
                    f"apply agent timed out after {self.timeout_s:.0f}s — the browser stays "
                    f"open; finish or close it yourself. Partial transcript: {log_file}",
                ) from exc
        output = log_file.read_text(encoding="utf-8")
        if rc != 0:
            raise AgentError(f"apply agent exited {rc}: {output.strip()[-300:]}")
        return output
