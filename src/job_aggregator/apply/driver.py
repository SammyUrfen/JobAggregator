"""Browser-driver seam for the apply agent (Track D, opt-in).

The orchestrator only ever talks to the `BrowserDriver` Protocol, so the whole browser stack
(Playwright, and browser-use for unknown forms) stays behind this seam — lazy-imported inside
`PlaywrightDriver`, mirroring `jobspy_source._scrape_jobs` — and tests drive a `FakeDriver` with no
browser at all.

SAFETY CONTRACT: a driver FILLS a form and STOPS. It NEVER clicks Submit. The human reviews the
filled page in the real (headful) browser and submits themselves. `FillResult.submitted` is always
False. Enforced again in `agent.apply_to_job`, which refuses to run if `apply.auto_submit` is set.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from job_aggregator.apply.backends import AgentBackend


@dataclass
class ApplicationFields:
    """The applicant data a form needs, derived from the profile + the tailored résumé PDF."""

    full_name: str
    first_name: str
    last_name: str
    email: str
    resume_path: str
    phone: str | None = None
    location: str | None = None
    linkedin: str | None = None
    github: str | None = None
    cover_note: str | None = None
    # Free-text the user pasted for THIS job (full posting, notes, screening-question answers).
    # NOT a form value — guidance the agent may use to answer specific fields (see text_map).
    extra_context: str | None = None

    def text_map(self) -> dict[str, str]:
        """Non-file fields as a flat {logical_name: value} map (drops None + the résumé file)."""
        pairs = {
            "full_name": self.full_name,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "phone": self.phone,
            "location": self.location,
            "linkedin": self.linkedin,
            "github": self.github,
            "cover_note": self.cover_note,
        }
        return {k: v for k, v in pairs.items() if v}


@dataclass
class FillResult:
    """Outcome of one fill_form call. `submitted` is ALWAYS False by contract (human submits)."""

    filled: list[str] = field(default_factory=list)  # logical fields the driver placed
    unfilled: list[str] = field(default_factory=list)  # fields it could not place
    needs_login: bool = False  # a login wall blocked the form
    screenshot_path: str | None = None
    new_state: dict[str, Any] | None = None  # storageState to persist (after a fresh login)
    submitted: bool = False


class BrowserDriver(Protocol):
    def fill_form(
        self,
        url: str,
        fields: ApplicationFields,
        *,
        selectors: dict[str, str] | None = None,
        storage_state: dict[str, Any] | None = None,
        headful: bool = True,
    ) -> FillResult: ...


class FakeDriver:
    """Test double: records calls and returns a canned FillResult. No browser, no [apply] extra."""

    def __init__(self, result: FillResult | None = None) -> None:
        self._result = result or FillResult(filled=["email"], screenshot_path="/tmp/fake.png")
        self.calls: list[dict[str, Any]] = []

    def fill_form(
        self,
        url: str,
        fields: ApplicationFields,
        *,
        selectors: dict[str, str] | None = None,
        storage_state: dict[str, Any] | None = None,
        headful: bool = True,
    ) -> FillResult:
        self.calls.append(
            {
                "url": url,
                "fields": fields,
                "selectors": selectors,
                "storage_state": storage_state,
                "headful": headful,
            }
        )
        return self._result


# Best-effort CSS selectors for an UNKNOWN form (the browser-use/heuristic fallback). Ordered by
# preference; the driver tries each until one resolves. Refine against live forms.
_GENERIC_SELECTORS: dict[str, tuple[str, ...]] = {
    "first_name": (
        "input[name*='first' i]",
        "input[id*='first' i]",
        "input[autocomplete='given-name']",
    ),
    "last_name": (
        "input[name*='last' i]",
        "input[id*='last' i]",
        "input[autocomplete='family-name']",
    ),
    "full_name": ("input[name*='name' i]", "input[id*='name' i]", "input[autocomplete='name']"),
    "email": ("input[type='email']", "input[name*='email' i]", "input[id*='email' i]"),
    "phone": ("input[type='tel']", "input[name*='phone' i]", "input[id*='phone' i]"),
    "location": ("input[name*='location' i]", "input[id*='location' i]", "input[name*='city' i]"),
    "linkedin": ("input[name*='linkedin' i]", "input[id*='linkedin' i]"),
    "github": ("input[name*='github' i]", "input[id*='github' i]"),
    "resume": ("input[type='file']",),
}


class PlaywrightDriver:
    """Headful Playwright driver. Fills in passes: deterministic ATS `selectors` (the reliable
    core), then LLM **Set-of-Marks grounding** (code detects the fields, the backend maps the
    applicant's values onto them — handles arbitrary/unknown forms), else generic selector patterns
    when no backend is set. Attaches the résumé, screenshots, then PAUSES for the human to review +
    submit — it never clicks Submit itself.

    All Playwright work is lazy-imported and `# pragma: no cover`: it needs the `[apply]` extra + a
    real display and is verified live on the user's desktop, not in CI. The grounding logic
    (`grounding.plan_fills`) and orchestration are unit-tested with a fake backend / `FakeDriver`.
    """

    def __init__(self, *, backend: AgentBackend | None = None, pause: bool = True) -> None:
        # backend: LLM for Set-of-Marks grounding (None -> deterministic + generic selectors only).
        # pause=True blocks (input()) so the human reviews + submits before the browser closes.
        self._backend = backend
        self._pause = pause

    def fill_form(
        self,
        url: str,
        fields: ApplicationFields,
        *,
        selectors: dict[str, str] | None = None,
        storage_state: dict[str, Any] | None = None,
        headful: bool = True,
    ) -> FillResult:  # pragma: no cover - needs a real browser + the [apply] extra
        from job_aggregator.paths import sessions_dir

        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            from job_aggregator.errors import AgentError

            raise AgentError(
                "the apply extra is not installed; run: pip install -e '.[apply]' && "
                "playwright install chromium",
                details={"missing": "playwright"},
            ) from exc

        shot = str(sessions_dir().parent / "apply_last.png")
        filled: list[str] = []
        unfilled: list[str] = []
        with sync_playwright() as _p:
            p: Any = _p  # erase playwright's precise types at the boundary (all Any downstream)
            browser = p.chromium.launch(headless=not headful)
            ctx = (
                browser.new_context(storage_state=storage_state)
                if storage_state
                else browser.new_context()
            )
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded")
            filled, unfilled = self._fill(page, fields, selectors)
            page.screenshot(path=shot, full_page=True)
            new_state = ctx.storage_state()  # capture cookies now (before the window is closed)
            if self._pause:
                # Block until the human closes the browser — works from the CLI AND when spawned by
                # the dashboard (no stdin/terminal needed, unlike input()).
                print(
                    f"\nForm pre-filled ({len(filled)} field(s)). Unfilled: {unfilled or 'none'}."
                )
                print(
                    "Review + SUBMIT it yourself in the browser, then CLOSE the window when done."
                )
                while browser.is_connected():
                    time.sleep(0.5)
            else:
                browser.close()
        return FillResult(
            filled=filled, unfilled=unfilled, screenshot_path=shot, new_state=new_state
        )

    def _fill(
        self, page: Any, fields: ApplicationFields, selectors: dict[str, str] | None
    ) -> tuple[list[str], list[str]]:  # pragma: no cover - browser-bound
        from job_aggregator.apply.detector import detect_fields
        from job_aggregator.apply.grounding import plan_fills

        filled: list[str] = []
        typed: set[str] = set()
        text = fields.text_map()
        # 1. deterministic ATS selectors (reliable core for a known ATS)
        if selectors:
            for name, value in text.items():
                if name in selectors and self._try_fill(page, [selectors[name]], value):
                    filled.append(name)
                    typed.add(value)
        # 2. LLM Set-of-Marks grounding for whatever the selectors didn't place (arbitrary forms)
        if self._backend is not None:
            detected = detect_fields(page)
            by_index = {f.index: f for f in detected}
            for idx, value in plan_fills(detected, fields, self._backend).items():
                f = by_index.get(idx)
                if f is not None:
                    page.mouse.click(f.x, f.y)
                    page.keyboard.type(value, delay=20)
                    filled.append(f.label or f.name or f.type)
                    typed.add(value)
        else:
            # 3. no-LLM fallback: generic name/id selector patterns
            for name, value in text.items():
                if value not in typed and self._try_fill(
                    page, list(_GENERIC_SELECTORS.get(name, ())), value
                ):
                    filled.append(name)
                    typed.add(value)
        # 4. résumé upload (a file input)
        rcss = (
            [selectors["resume"]]
            if selectors and "resume" in selectors
            else list(_GENERIC_SELECTORS["resume"])
        )
        if self._try_upload(page, rcss, fields.resume_path):
            filled.append("resume")
        unfilled = [k for k, v in text.items() if v not in typed]
        return filled, unfilled

    @staticmethod
    def _try_fill(page: Any, css_list: list[str], value: str) -> bool:  # pragma: no cover
        for css in css_list:
            loc = page.locator(css).first
            if loc.count() > 0:
                loc.fill(value)
                return True
        return False

    @staticmethod
    def _try_upload(page: Any, css_list: list[str], path: str) -> bool:  # pragma: no cover
        for css in css_list:
            loc = page.locator(css).first
            if loc.count() > 0:
                loc.set_input_files(path)
                return True
        return False
