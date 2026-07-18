# Auto-Apply extension — design & feasibility (research-grounded)

> Status: **Tracks A–D COMPLETE (built, gate-green, fake-driver-tested).** Grounded in a verified
> deep-research pass (2026-07-16, 25 sources, 20 confirmed / 5 refuted claims). This is the
> "generate → agent-fills → human-reviews → human-submits" architecture, plus the reboot-durable
> service and card UI. Live browser fill is opt-in and un-run in CI — see "Live validation" below.
>
> **Decisions (locked 2026-07-16):** submit model = **agent fills → you review → you submit**;
> platforms = **both** (ATS as the reliable core; LinkedIn/Naukri best-effort with anti-bot
> caveats); LLM backend = **both a configurable OpenAI-compatible endpoint AND a coding-agent
> driver** (you'll use Claude Code) behind one adapter interface; build order = **A → B → C → D**.
>
> **Track A (durable service + notify + port): DONE.** `docker-compose.yml` (restart-on-boot,
> health-checked, named volume, non-8000 port `JOBAGG_PORT` default 8770); Telegram end-of-run
> summary (`notify_run` → `build_run_summary`) with a dashboard link driven by `JOBAGG_PUBLIC_URL`
> / `notify.dashboard_url`; startup catch-up (≥24h-since-success gate) already covered reboots.
>
> **Track B (card UI + detail modal): DONE.** `.jobs-grid` of clickable `.job-card`s → `#job-modal`
> from `GET /api/jobs/{uid}/detail`; original-posting link + Apply button + safe HTML→text
> description. Fixed `serve --db` (was silently ignored). Live-verified.
>
> **Track C (profile + résumé tailoring engine): DONE.** `profile/` + `apply/backends.py` (two
> backends) + `resume/{tailor,render}.py` (selection + merge-exclusion guard + preservation → LaTeX
> → PDF). Live-verified: real profile → tailored → 108 KB PDF via pdflatex.
>
> **Coverage pass: DONE.** `paginate_until_empty` (loop-until-empty + `max_pages` cap) on Adzuna
> (+query targeting), Jooble, Unstop. Live: raw fetch ~246 → ~1211.
>
> **Track D (browser apply agent): DONE (build plan below is now the as-built map).** Opt-in
> `[apply]` extra (browser-use / playwright / cryptography). Encrypted per-domain session store
> (`apply/session.py`, Fernet, `data/sessions/<domain>.enc`); `BrowserDriver` Protocol + `FakeDriver`
> + `PlaywrightDriver` (`apply/driver.py`); **Set-of-Marks DOM grounding** lifted from the Form
> Controller Agent (`apply/detector.py` JS field-detect → `apply/grounding.py` LLM value-mapping,
> code owns geometry, model owns semantics); deterministic ATS field maps
> (`apply/ats/{greenhouse,lever,ashby,smartrecruiters}.py` + `detect_ats`); orchestrator
> (`apply/agent.py`) that refuses when `apply.enabled` is false or `apply.auto_submit` is true, fills
> then **stops at Submit**. Dashboard wired: `POST /api/jobs/{uid}/apply` (guarded) spawns the CLI
> `apply` subcommand; detail-modal Apply button branches on `apply.enabled`. Config surfaced in the
> dashboard (Config → "Apply agent (Track D)": `apply.enabled` + `resume.backend` picker) and in
> `.env.example` (`JOBAGG_SESSION_KEY`). The review-gate pauses on `while browser.is_connected()`
> (not `input()`) so it works when dashboard-spawned. **Never auto-submits.**
>
> **State @ 2026-07-18:** 372 tests green, 90.16% cov, `ruff`/`mypy` clean. Tracks A–C + coverage
> committed (`219bc9b`, `294dbad`, `6c69bbc`); the Track D batch is in this same commit round.
> `profile.yaml` is git-ignored (personal).
>
> ### 👉 Handoff — start here next session
> 1. `git status` / `git log` — everything through Track D is committed; the tree should be clean.
> 2. Verify `profile.yaml` facts are correct (it's your ground truth; git-ignored).
> 3. **Track D is built but never run against a real browser** — do the "Live validation" below on
>    your machine (needs a display + `pip install -e '.[apply]'` + a `JOBAGG_SESSION_KEY`). Report
>    per-ATS fill reliability, which the fake-driver tests can't cover.
> 4. Everything else (résumé tailoring CLI + `POST /api/jobs/{uid}/tailor` + Tailor button, profile
>    editor at `/profile`) is live and testable today.

## Problem

Turn the aggregator (which finds + dedups + filters jobs) into a tool that also **helps apply**:
click a job → see full details in a modal → generate a role-tailored résumé from your real
profile → have an agent pre-fill the application form → you review and submit. Plus: run
reboot-durably in the background, on a non-8000 port, and Telegram-notify a summary + link when a
cycle finishes.

## Goals

- **G1** Card-based job list; click a card → detail modal with the full posting, a link to the
  original, and an **Apply** action.
- **G2** Per-role résumé tailoring from a **provided LaTeX template** + a structured **profile**
  (projects, skills, ambitions) — **truthful**, ATS-parseable.
- **G3** An agent that reads the application form and **pre-fills** it; **you** handle any auth
  (once, persisted) and **you** click submit.
- **G4** Configurable LLM backend (OpenAI-compatible endpoint / local) — reuse the existing
  swap-friendly client pattern.
- **G5** Reboot-durable background service (Docker), configurable port, Telegram run-summary +
  localhost link on completion; startup catch-up already gated on "no success in ~24h".

## Non-goals (deliberate, research-driven)

- **NG1 — No blind unattended auto-submit.** The durable legal exposure is *breach of contract*:
  once an agent logs in and click-accepts a ToS that bans automation, those terms are enforceable
  (hiQ v. LinkedIn — won on CFAA for *public* scraping, **lost $500K on contract**). Keeping the
  human as the one who accepts ToS and clicks **Submit** is the safe, durable pattern — and it's
  also what actually works, because self-hosted browser agents can't beat anti-bot/CAPTCHA.
- **NG2 — No résumé fabrication.** Tailoring = re-ordering/emphasizing **real** facts from your
  profile, never inventing employers, dates, or skills. Enforced structurally (below), not just by
  prompt.
- **NG3 — No mass LinkedIn/Naukri automation.** Those platforms actively detect+ban automation and
  gate behind CAPTCHA (we watched Naukri return `406 recaptcha` to jobspy). ATS forms first.

## Research-grounded decisions

| Decision | Choice | Why (cited) |
|---|---|---|
| Browser driver | **browser-use** (self-hosted) | MIT, Playwright-based (DOM-first), backend-agnostic: OpenAI-compatible `base_url`, any LiteLLM model, **local Ollama** — runs with *no* cloud LLM. Built-in Playwright-format session persistence. [docs.browser-use.com] |
| Not Skyvern/Stagehand (yet) | — | Skyvern's anti-bot/proxy/CAPTCHA are **cloud-only** (self-hosted just pauses 30s for manual CAPTCHA); Stagehand leans Browserbase cloud. Fine as future options. |
| Auth persistence | **Playwright `storageState`** (cookies + localStorage), **encrypted at rest** | Reused across runs so you log in once; `{indexedDB:true}` opt-in since v1.51; **sessionStorage is NOT persisted** and **server-side token expiry still forces periodic re-login**. Encrypt with Fernet/libsodium (keychain-derived key). |
| Submit model | **Agent fills → you review → you submit** | NG1. Also the only reliable pattern given anti-bot walls; browser-use itself notes "fully autonomous multi-step tasks still need human-in-the-loop checkpoints", and a 25-field form is where autonomous filling gets flaky. |
| Résumé pipeline | **ResumeFlow-style 3-step** (extract JD → extract profile facts → generate) **+ layered anti-fabrication** | Off-the-shelf, no fine-tuning (arXiv 2402.06221). Truthfulness needs deterministic layers, not just a prompt: **merge-exclusion** (LLM text may not enter real employer/experience sections), **content-preservation-vs-alignment scoring** (flag low-preservation/high-alignment = likely hallucination), prompt grounding as *one* layer. |
| Avoid | **Genetic/DE "fitness optimizer" résumé loops** (Synapse) | Anti-pattern: they optimize toward the tool's own match score with **no truthfulness constraint** — they game the scorer and *remove* facts. |
| LLM backend | **Two adapters behind one interface:** a configurable OpenAI-compatible endpoint *and* a local coding-agent driver (Claude Code / Codex / MCP) | User wants both; will personally use Claude Code. One `AgentBackend` protocol (`generate_resume`, `fill_form`) with two impls, chosen in `/config`. Reuses the project's swap-friendly client pattern. |
| Platforms | **Both** — ATS forms are the **reliable core**, LinkedIn Easy Apply / Naukri are **best-effort** | User chose both. ATS (Greenhouse/Lever/Ashby/SmartRecruiters) are mostly public/no-login, simplest, lowest ToS risk. LinkedIn/Naukri stay opt-in, headful-only, with explicit anti-bot/ToS caveats and no auto-submit — the agent *assists*, you accept ToS + submit. |

## Architecture (new components)

```
sources/ (unchanged: fetch → dedup → filter → upsert)
        │
        ▼
profile/           # ✅ DONE — ground truth: schema.py (Pydantic) + store.py (validated YAML loader)
resume/            # ✅ DONE — templates/base_resume.tex, tailor.py, render.py
  ├── tailor.py    #   ✅ JD-extract → select → guarded LLM rewrite (merge-exclusion) → preservation
  └── render.py    #   ✅ fill template (escaped) → .tex → compile_pdf (tectonic/pdflatex seam)
apply/             # the agent layer (opt-in, off by default)
  ├── backends.py  #   ✅ DONE — AgentBackend + OpenAICompatible + CodingAgent + build_backend
  ├── session.py   #   ⬜ Track D — encrypted Playwright storageState per domain (Fernet)
  ├── driver.py    #   ⬜ Track D — BrowserDriver Protocol + BrowserUseDriver (headful, no submit)
  ├── agent.py     #   ⬜ Track D — orchestrator: résumé + field-map → driver.fill_form → review
  └── ats/*        #   ⬜ Track D — per-ATS deterministic field maps (Greenhouse/Lever/…)
dashboard/         # ✅ cards + detail modal;  ⬜ Track D: wire Apply → agent + "Tailor" preview
notify/telegram    # ✅ DONE — run-summary digest with the localhost link
```

**Apply flow (per job, human-triggered):** click **Apply** on a card → backend picks/generates the
tailored résumé → launches a **headful** browser-use session (you can watch) → loads saved auth for
that domain (or prompts you to log in once, then persists it encrypted) → the agent locates and
**fills** the form fields from your profile + résumé → **stops at Submit** → you review the filled
form in the real browser and click **Submit** yourself. Nothing is sent without you.

## Reboot-durability + notify (Track A — decision-independent)

- **Dockerfile** already exists (single-process `serve`, auto-init). Add `restart: unless-stopped`
  via a `docker-compose.yml`, a configurable published port, and a named volume for `/data`.
- **Startup catch-up** already fires only when there's been no *success* in ~24h — so a reboot mid-day
  won't double-fetch, and a reboot after a long gap will catch up. No change needed there.
- **Telegram**: the runner's step-8 already builds notifiers; add a **run-summary** message
  (`new/updated/expired`, per-source ok/fail, + `http://<host>:<port>/`) posted when a cycle finishes,
  behind `notify.telegram.enabled` + `TELEGRAM_*` env. (Today Telegram only sends the new-jobs digest.)

## Phased build

- **Phase A — durable service + notify + port. ✅ DONE.** `docker-compose.yml`, `JOBAGG_PORT`,
  Telegram run-summary + dashboard link.
- **Phase B — card UI + detail modal. ✅ DONE.** Table → responsive `.jobs-grid` of clickable
  `.job-card`s; click → `#job-modal` filled from `GET /api/jobs/{uid}/detail` (facts + flattened
  description + **Open original posting** link + **Apply** button + quick-actions). Description is
  flattened HTML→text server-side (`html_to_text`, drops `<script>`/`<style>`) so no source markup
  renders. Apply (today) opens the posting + marks applied — Track D will hook the agent here.
  Also fixed a real bug: `serve --db X` was ignored (uvicorn factory got no kwargs) → always served
  the default DB; now passed via `JOBAGG_DB` env, which also unbreaks the throwaway-DB verify flow.
  Verified live against a throwaway DB (110 real jobs): grid + detail modal render correctly.
- **Phase C — profile + résumé tailoring. ✅ DONE.**
  - `profile/{schema,store}.py` (Pydantic `Profile`), validated YAML loader; **`profile.yaml`**
    (git-ignored, personal) + `config/profile.example.yaml` (committed placeholder). LaTeX template
    packaged at `resume/templates/base_resume.tex`.
  - `apply/backends.py` — `AgentBackend` protocol + **OpenAICompatibleBackend** (any base_url) +
    **CodingAgentBackend** (subprocess, e.g. `claude -p`) + `build_backend`. Config: `resume.*`.
  - `resume/tailor.py` — JD-keyword extract → deterministic project/skill **selection** → optional
    LLM bullet rewrite behind a **merge-exclusion guard** (rejects any rewrite that introduces a
    number absent from the source → keeps the truthful original) → **preservation scoring** + flags.
    `backend=None` = pure selection, zero fabrication/cost.
  - `resume/render.py` — fills the template macros (LaTeX-escaped) → `.tex` → `compile_pdf` (tectonic/
    pdflatex behind a seam). **Live-verified: real profile → tailored → 108 KB PDF via pdflatex.**
  - 32 tests (backends/tailor/render/profile). Fully offline-testable.
- **Phase D — apply agent (opt-in). ⬜ REMAINING (browser half).** Config scaffolding done
  (`apply.enabled`/`auto_submit=false`). Still to build: encrypted per-domain session store
  (Playwright `storageState` + Fernet); the browser-use orchestrator (headful, fill-then-review,
  behind a seam like jobspy's `_scrape_jobs`); ATS field maps first; dashboard Apply-button wiring +
  a "Tailor résumé" preview in the modal. **Honest caveat:** the live browser fill can't be
  end-to-end verified in CI (needs a display + real credentials + `pip install '.[apply]'`), so it
  will ship opt-in with the orchestration logic unit-tested via a fake driver.

## Risks & how we design around them

- **Anti-bot / CAPTCHA** — real and unbeatable self-hosted (Naukri already blocks jobspy). → Headful,
  human-present, ATS-first, no LinkedIn/Naukri auto-submit. The agent *assists*; it doesn't evade.
- **ToS / breach-of-contract** — the human accepts ToS and submits; the agent never click-accepts a ToS
  on your behalf. ATS public forms minimize logged-in-ToS surface.
- **Résumé fabrication** — structural merge-exclusion + preservation/alignment scores surfaced to you;
  you approve the PDF before it's attached. Never auto-generate claims not in your profile.
- **Auth secrets at rest** — encrypt the storageState blob (Fernet, key from OS keychain / a
  passphrase); never commit it; document that server-side expiry still needs occasional re-login.
- **Form-fill reliability** — deterministic field maps for known ATS; LLM only for the unknown; always
  human-review before submit.

## Track D — as-built map (was: build plan)

**Built and gate-green** (Steps 0–5 below are done; Step 6 is the remaining live check). Ships
**opt-in** (`apply.enabled`, default false) with the non-browser logic unit-tested via `FakeDriver`.
Live headful fill still needs a **real browser** (Chromium) + `pip install -e '.[apply]'` — a
background/CI run can't validate it. See **"Live validation"** (Step 6).

### Step 0 — intermediate win first (no browser)
The résumé engine (`resume/tailor.py` + `render.py`) already works but has **no user surface**.
Wire it before touching the browser — fully testable today:
- **CLI:** a `tailor` subcommand — `python -m job_aggregator tailor <job_uid> [--out resume.pdf]`
  → load profile → `tailor_resume(profile, job.description, backend=build_backend(cfg.resume))`
  → `render_latex` → `compile_pdf`. Print the preservation score + flags.
- **Dashboard:** `POST /api/jobs/{uid}/tailor` returning the tailored preview (selected projects,
  flags, preservation) + a **"Tailor résumé"** button in the detail modal (`job_detail.html`). The
  PDF download link points at the compiled artifact under `data/resumes/<uid>.pdf`.
- Keep the backend call behind the existing `AgentBackend` seam so tests inject a fake.

### Step 1 — optional dependencies
Add a `[project.optional-dependencies] apply = ["browser-use", "playwright", "cryptography"]`
extra so the core tool stays lean. Lazy-import `browser_use`/`playwright` behind the driver seam
(mirror `jobspy_source.py`'s lazy import) so `pytest`/`mypy` never require them. Document
`pip install -e '.[apply]' && playwright install chromium`.

### Step 2 — encrypted session store  (`apply/session.py`)
- Persist Playwright `storageState` (cookies + localStorage) **per domain**, encrypted at rest with
  **Fernet**. Key from env `JOBAGG_SESSION_KEY` (or derive from a passphrase via `scrypt`); never
  commit; store blobs under `data/sessions/<domain>.enc` (git-ignored — add to `.gitignore`).
- API: `load_state(domain) -> dict | None`, `save_state(domain, state) -> None`, `has_state(domain)`.
- Caveats to encode: `sessionStorage` is NOT persisted; server-side token expiry still forces
  periodic re-login (surface a clear "session expired, please log in again" path).
- **Testable now:** encrypt→decrypt round-trip, wrong-key fails, missing file → None.

### Step 3 — browser driver behind a seam  (`apply/driver.py`)
- Define `BrowserDriver` Protocol: `fill_form(url, fields, *, storage_state, headful=True) -> FillResult`
  where `FillResult` = {filled: dict, unfilled: list, screenshot_path, needs_login: bool}.
- `BrowserUseDriver` implements it via browser-use (headful; loads/saves storageState; **stops
  before Submit** — never clicks it). A `FakeDriver` in tests records calls and returns canned
  results. The orchestrator only ever talks to the Protocol.

### Step 4 — apply orchestrator  (`apply/agent.py`)
Per-job, human-triggered flow (matches the "Apply flow" section above):
1. Resolve/generate the tailored résumé (Step 0 engine) → PDF path.
2. Build the field map: `profile` + tailored résumé → `{name,email,phone,linkedin,github,resume_path,
   cover_note,…}`. For known ATS use **deterministic field maps** (`apply/ats/{greenhouse,lever,
   ashby,smartrecruiters}.py`); fall back to the LLM/driver for unknown forms.
3. `has_state(domain)?` → load it; else headful login, then `save_state`.
4. `driver.fill_form(...)` → **stop at Submit**. Return the fill result + screenshot to the UI.
5. **User reviews in the real browser and clicks Submit.** Nothing is auto-submitted
   (`apply.auto_submit` stays false — enforced, not just default).
- **Testable now:** the field-map builder + orchestration branching against `FakeDriver` +
  fake backend; ATS field maps are pure functions.

### Step 5 — dashboard wiring
- `POST /api/jobs/{uid}/apply` (guarded by `apply.enabled`) → kicks off Step 4 → streams status.
- Detail-modal **Apply** button: today it opens the posting + marks applied; when `apply.enabled`,
  it instead launches the agent and shows fill progress + the review prompt.
- **ATS first** (Greenhouse/Lever/Ashby/SmartRecruiters — public, no-login, simplest). LinkedIn
  Easy Apply / Naukri: keep behind an extra opt-in flag, headful-only, with the anti-bot/ToS
  warning in the UI. Never auto-submit anywhere.

### Step 6 — Live validation (REMAINING — the one un-done piece)
On your machine, with a display: `pip install -e '.[apply]'` → `playwright install chromium` → set
`JOBAGG_SESSION_KEY` in `.env` → turn on **apply.enabled** (Config page, or `default_config.yaml`) →
pick a Greenhouse/Lever posting → click **Apply** → watch it fill → **you** submit. Confirm session
persistence (second apply on the same domain skips login). Report what the fake-driver tests can't:
real form-fill reliability per ATS. Backend is `resume.backend` (default `coding_agent` = Claude
Code `claude -p`, no API key; switch to `openai_compatible` only if you want an HTTP endpoint).

## Open research questions (unresolved by the deep-research pass — design around them)

- Per-platform empirical block/ban reality (LinkedIn Easy Apply / Naukri / each ATS) — general law +
  frameworks are covered, platform-level ban rates are not. → headful, human-present, no auto-submit.
- Concrete encryption-at-rest scheme for the persisted session (Fernet vs OS keychain vs SQLCipher).
- Local-model form-fill reliability floor (documented action-schema issues on some Qwen models).
- Whether the tailored LaTeX actually passes real ATS parsers end-to-end.
