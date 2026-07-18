# CLAUDE.md — JobAggregator

Index for any Claude Code session working in this repo. Read **PLAN.md** before writing code.

## What this is
A self-hosted, multi-source job/internship aggregator for **remote/India** roles in
backend / systems / distributed / ML / AI / LLM / RL / Go. It fetches from many sources,
**deduplicates** across them, **expires** postings that vanish, and serves everything from a
**FastAPI dashboard** (config edits there take effect on the next run). Runs **once/24h** on
a personal laptop. Fully Python. Learning/portfolio project — built from scratch on purpose.

## Environment (MUST use)
- conda env **`job-aggregator`** (Python 3.11). Interpreter:
  `/home/SammyUrfen/miniconda3/envs/job-aggregator/bin/python`.
- Activate: `conda activate job-aggregator`. Install: `pip install -e ".[dev]"`.
- Repo path has a **space** (`job aggregator/`) — always quote it in shells. The import
  package is `job_aggregator` (underscore), src layout.

## Where things are
- **PLAN.md** — Part I: the FROZEN architecture spec (contracts: DB schema, Source interface,
  config schema, dedup/salary/stale algorithms, dashboard + theme tokens). Part II: the
  phase-by-phase build guide (Phases 0–9), each with an Acceptance check. **This is the
  contract — do not silently rename modules/columns/functions.**
- **research.md** — the upstream research (why these sources, what's a dead end, verified
  endpoints as of 2026-07-14).
- **config/default_config.yaml** — seed config; mirrors `config/schema.py`.
- **blood_orange_theme_detail.html** — the theme reference; tokens live in
  `dashboard/static/css/theme.css`.
- **docs/auto_apply_design.md** — research-grounded design for the post-v1 **auto-apply
  extension** (durable Docker + card UI + truthful résumé tailoring + fill-then-review apply
  agent). Read it before touching Track A–D work.

## Current status
**v1 COMPLETE (Phases 0–9) + auto-apply Tracks A–D done + INTERNSHIP-FIRST OVERHAUL (2026-07-18).**
Full gate green: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest` (**445 passed** / 0
skipped; coverage **90.6%**, hard gate 85%). Live-verified A/B the same day: the old config kept
**107 jobs / 5 internships**; the overhauled pipeline kept **344 / 262 internships** (internshala
147, adzuna 60, unstop 48) with zero off-domain leaks in spot checks.

**Internship-first overhaul (this session; diagnosed by a 5-agent workflow replaying the real
pipeline live):**
- **Root causes found & fixed:** Unstop NEVER sent `search_terms` to its API (un-targeted
  firehose: 300 fetched → 2 kept) → now sends `searchTerm` per (opportunity × term), maps
  `details`/`required_skills`/`workfunction` as description, dedupes by id. Adzuna/Jooble only
  queried role phrases (0 intern targeting) → Adzuna adds a `title_only=intern & max_days_old=35`
  walk (266 fresh IT internships verified), Jooble rides `keywords.intern_queries`. jobspy never
  passed `job_type` → now `job_type=internship` (Indeed drops `hours_old` — jobspy can't combine
  them; LinkedIn composes) + `linkedin_fetch_description=true` (rows had NO description → gates
  ran title-only). `is_remote` seed → false (it strangled LinkedIn intern yield). Jobicy disabled
  (0 internships + no India geo server-side, verified). Himalayas now maps its description.
- **NEW source: Internshala** (`sources/internshala.py`, bs4 explicit dep) — listing-page HTML
  per filter slug (7 seeded; research.md's dead-end note was stale, pages return 200), stipend
  native INR/month, posted-ago→date, slug surfaced as description for the must_have gate; a
  redesign degrades to suspicious-empty, never a crash.
- **Pipeline:** `Job.is_internship` (title regex; schema **v2 migration** + backfill — bump
  `SCHEMA_VERSION` pattern in storage/db.py) drives: +25 score boost, relaxed role gate (stack
  anchor alone qualifies an intern posting), exemption from the new years-of-experience drop
  (`keywords.max_experience_years`, default 2, token+proximity regex, 0=off), and its own stipend
  floor `salary.min_internship` (default 0 — the 30k remote floor was dropping real SDE-intern
  posts). Title excludes extended (sr/ii/iii/iv/sde-2/frontend/sap/…); must_have gained
  java/spring/sde/software development. Dashboard: **Internships** chip (`?intern=1`) + green
  `Intern` badge; config UI exposes the new knobs plus bonus/level_required (were unrendered).
- **Stale-deletion correctness fix (audit-found):** windowed fetches (page caps/results_wanted —
  adzuna/jooble/jobspy/capped walks) were silently deleting live jobs that drifted past page N.
  `paginate_until_empty` now returns `(items, exhausted)`; `SourceResult.exhaustive` flows to
  `expire_stale`, where windowed sources retire jobs by POSTING AGE
  (`schedule.windowed_retire_days`, default 30) instead of absence.

**Apply-button fix (the "said it will open a browser but never did" bug):** proven chain — the
dashboard ran in Docker, the route spawned the agent INSIDE the container (first wall: no LaTeX;
then no playwright/chromium/display) while unconditionally claiming "a browser window is opening"
and marking the job applied. Now: `_apply_preflight` (display/[apply]-extra/LaTeX checks) returns
honest, actionable refusals incl. the exact host command; the spawn is watched ~1.5s for instant
death (stderr tail surfaced from `data/apply_last.log`); **`applied` is set by the CLI after the
fill completes**, not on launch; `save_state` degrades gracefully without `JOBAGG_SESSION_KEY`
(key now generated in `.env`). The user's Zen browser was never relevant — Playwright drives its
own Chromium. **Deployment reshaped so the flow actually works:** docker-compose now bind-mounts
`./data:/data:z` (named volume `jobagg-data` retired but untouched; DB migrated with backups in
`data/backups/`) and `./profile.yaml` (was BAKED into the image — privacy leak, now
.dockerignore'd), runs as `JOBAGG_UID:GID` (default 1000). Dashboard (Docker) and host apply CLI
share one DB: click Apply → honest message → run the printed command on the host.

**Other audit fixes:** crashed runs no longer masquerade as "run in progress" (trigger_now
returns run_id | "busy" | "failed"); résumé rewrite numbering-strip no longer amputates bullets
that LEAD with a metric ("91 Catch2 tests…" → "Catch2 tests…") + new flag when a rewrite DROPS a
metric; profile save is atomic (tmp+replace, bind-mount fallback); adzuna/jooble log which roles
beyond the first 6 are not queried (UI hint added).

**EVENING ROUND (same day): Docker retired + agentic apply (Track D v2).** Gate: **472 passed**,
90.67% cov, ruff+mypy clean.
- **Deployment is now HOST-NATIVE:** `./start.sh` (env-python absolute path, PATH for
  claude/npx/pdflatex, display fallback) run by systemd USER unit `job-aggregator-serve`
  (enabled + linger → starts at boot; `systemctl --user restart job-aggregator-serve` after code
  changes). Docker compose/image retired (files kept as fetch-only reference; jobagg-data volume
  untouched). Dashboard on :8770.
- **Telegram link fix:** Telegram strips anchors for localhost/LAN hosts — `build_run_summary`
  now renders local dashboard URLs as VISIBLE plain text (`_is_local_url`); public hosts keep the
  anchor. Stored config dashboard_url → :8770; verified with a real send.
- **Agentic apply (`apply/agentic.py` + `apply/cookies.py`):** `AgenticSession` satisfies the
  BrowserDriver seam, so `apply_to_job` is untouched. Flow: headful Chromium launched with a CDP
  port + the posting domain's cookies imported from the user's Zen profile
  (`~/.config/zen/*/cookies.sqlite`, Firefox format, copy-then-read, values never logged) →
  `claude -p` with @playwright/mcp@0.0.78 attached via `--cdp-endpoint` drives THAT window:
  reaches the form from the posting (Apply/Easy Apply/wizards), fills from ApplicationFields,
  uploads the résumé, NEVER submits (prompt HARD RULES + human review pause), and on a
  captcha/login wall it POLLS while the human solves it in the same window. Verified claude flags
  (v2.1.214): `--mcp-config` (needs `"type":"stdio"`) + `--strict-mcp-config` + `--allowedTools
  mcp__browser__*` + `--disallowedTools Bash,…` (least privilege); NO `--max-turns` (doesn't
  exist); `--output-format stream-json --verbose` STREAMED to `data/apply_agent.log` (tail -f
  while it runs — plain text buffered until exit and made slow look hung);
  `extract_result_text` pulls the final RESULT line. `apply.agent_model` default **sonnet**
  (inheriting the user's opus-1M default was slow/quota-hungry). Claude subprocess runs with
  **cwd=data_dir()** — @playwright/mcp restricts `browser_file_upload` to its workspace root
  (cwd), and the résumé lives under data/resumes (smoke-proven: outside-cwd = denied).
  **LIVE-VERIFIED end-to-end**: real GitLab Greenhouse posting → agent reached the form, filled
  contact fields, refused to invent answers for required questions, did not submit; upload smoke
  green after the cwd fix. Config: `apply.{engine,use_browser_cookies,browser_cookie_db,
  agent_timeout_s,agent_model}` + config-UI picker; deterministic engine kept as fallback (auto
  when claude/npx missing). Zen cookie jars verified present: linkedin 25, naukri 9, unstop 5.
- **Unstop closed-application fix:** `status` stays "LIVE"/`regn_open` stays 1 on CLOSED posts —
  the truthful field is `regnRequirements.reg_status` ("FINISHED" = the site's "Application
  Closed"; page-verified 5/5). Fetch now sends `oppstatus=open` (server-side equivalent,
  ~12× fewer pages) + a reg_status guard in `_map` (fail-open). Measured: only ~1-7% of unstop
  internships are open. One-off cleanup soft-deleted 49 closed stored rows (12 confirmed open
  kept). Internshala never lists closed posts (verified); Adzuna has no such field (documented).

**Apply hardening round (2026-07-19, from a real user run):** three fixes, gate 485 passed.
- **Zen cookie crash (`3ea250d`):** apply died at `ctx.add_cookies` — Zen stores cookie expiry
  in MILLISECONDS (stock Firefox uses seconds), so passed as seconds it was year ~58,000 and
  Playwright rejected the jar, killing the browser before any window painted. Fix:
  `cookies._expires_seconds` magnitude-detects ms and divides; `add_cookies` is wrapped
  try/except (a bad cookie degrades to logged-out, never fatal); the apply-alert log tail now
  greps bottom-up for the real error line (a harmless startup print sat below the traceback).
- **Kill switch (`1e06842`):** there was NO way to stop a stuck agent — and the review-pause
  loop keeps the process alive, so haywire runs piled up as orphans (found 3 live + chromium
  windows in the user's session; they shared serve's process group because the old spawn used a
  bare Popen, so group-kill was unsafe). Fix: `apply/procs.py` (register/live/stop_all with a
  /proc cmdline identity guard against PID reuse); `_launch_apply` now spawns with
  `start_new_session=True` (own process group) + registers the PID; `GET /api/apply/status`
  (drives visibility) + `POST /api/apply/stop` (SIGTERM→grace→SIGKILL the whole tree); header
  **⏹ Stop apply** button polled every 4s (survives reloads/closed modal). LIVE-VERIFIED: real
  internshala apply → status running:1 → Stop → 7-proc tree (python+claude+npx+chromium) all
  gone, service survived, status running:0.
- **LinkedIn logout (known limitation, prompt-hardened):** LinkedIn's anti-bot invalidates the
  imported session on the Easy-Apply click and bounces to a sign-in wall; the agent now STOPS
  and waits for a manual login instead of thrashing (thrashing logged it out further). ATS
  boards + Internshala/Unstop don't do this and are the reliable path.

**Remaining known-undone:** the agentic apply still hasn't completed a REAL end-to-end submission
by the user (headless smokes + a real internshala launch that was Stop-tested); LinkedIn Easy
Apply remains best-effort (anti-bot); dashboard auth / cross-process run-lock / fuzzy-dedup remain
documented limitations.

**Auto-apply extension (post-v1) — in progress.** Design + verified research in
`docs/auto_apply_design.md`. Locked decisions: fill→**you review→you submit** (never blind
auto-submit); **both** platform families (ATS = reliable core, LinkedIn/Naukri = best-effort,
headful, no auto-submit); **two agent backends** behind one adapter (OpenAI-compatible endpoint +
coding-agent/Claude Code). Build order A→B→C→D.
- **Track A (durable service + notify + port): DONE.** `docker-compose.yml` (restart-on-boot,
  health-checked, named volume, non-8000 port via `JOBAGG_PORT`, default 8770); Telegram
  end-of-run summary (`TelegramNotifier.notify_run` → `build_run_summary`, runner step-8b) with a
  dashboard link from `JOBAGG_PUBLIC_URL` / `notify.dashboard_url`; startup catch-up (≥24h gate)
  already handled reboots.
- **Track B (card UI + detail modal): DONE.** Table → `.jobs-grid` of clickable `.job-card`
  (`partials/job_card.html` + `_macros.html`); click → `#job-modal` filled by
  `GET /api/jobs/{uid}/detail` (`partials/job_detail.html`): facts + **flattened HTML→text**
  description (`routes_jobs.html_to_text`, drops `<script>`/`<style>`) + original-posting link +
  **Apply** (opens posting + marks applied; Track-D agent hooks here) + quick-actions. Action
  endpoint now returns the card partial. **Bug fixed:** `serve --db X` was ignored (uvicorn
  factory took no kwargs) → now via `JOBAGG_DB` env (`create_app` reads it); this also unbreaks the
  throwaway-`--db` verify workflow. 267 tests green, 89% cov; live-verified on a 110-job throwaway DB.
- **Track C (profile + LaTeX résumé tailoring): DONE.** `profile/` (Pydantic `Profile` +
  validated YAML loader). **`profile.yaml` is git-ignored** (personal — real résumé content);
  committed placeholder is `config/profile.example.yaml`. `apply/backends.py` = `AgentBackend`
  protocol + `OpenAICompatibleBackend` + `CodingAgentBackend` (subprocess) + `build_backend`.
  `resume/tailor.py` = JD-keyword extract → deterministic project/skill selection → optional LLM
  rewrite behind a **merge-exclusion guard** (rejects rewrites that add a number absent from source)
  → preservation scoring + flags (`backend=None` = pure selection, no LLM). `resume/render.py` =
  fill template (LaTeX-escaped) → `.tex` → `compile_pdf` (tectonic/pdflatex seam). Config: `resume.*`,
  `apply.*`. **Live-verified: real profile → tailored → 108 KB PDF.** 304 tests green, 89.8% cov.
- **Coverage pass (this session):** `paginate_until_empty` in `sources/_http.py` (loop until empty/
  short page/`max_pages` cap; first-page error fails, later-page error keeps earlier). Applied to
  Adzuna (+`what_or` query targeting), Jooble (+multi-role query), Unstop (per-opportunity). Live:
  adzuna 50→500, unstop 60→300, jooble 0→200. `max_pages` knobs in config.
- **Round-1 relevance/source fixes (committed `6c69bbc`):** broadened `roles` + added a `must_have`
  stack-anchor gate (`filters._hard_drop_reason`) + wider `exclude` so off-stack roles (Rust/embedded/
  teacher) drop while backend/systems/ML stay; per-role Adzuna `what` (+`category=it-jobs`) and Jooble
  queries; Unstop `_opportunity_url` (prefer `seo_url`, fixes 404s); jobspy `html` descriptions +
  backfill; catch-up now gates on `last_completed_run` (was firing every `serve`). Dropped Naukri from
  jobspy (`406 recaptcha`). Résumé-tailoring UI (Tailor button + preview) also landed here.
- **Track D (browser apply agent): DONE (opt-in, fake-driver-tested; live check pending).** `[apply]`
  extra (browser-use/playwright/cryptography, lazy-imported behind seams). `apply/session.py` =
  Fernet-encrypted per-domain Playwright `storageState` (`data/sessions/<domain>.enc`, key
  `JOBAGG_SESSION_KEY`). `apply/driver.py` = `BrowserDriver` Protocol + `FakeDriver` +
  `PlaywrightDriver`; deterministic ATS selectors → **Set-of-Marks grounding** → generic fallback +
  résumé upload; review-gate pauses on `while browser.is_connected()` (not `input()`, so it works
  dashboard-spawned). `apply/detector.py` (JS field-detect, lifted from the Form Controller Agent) +
  `apply/grounding.py` (`plan_fills`: LLM maps profile values to empty fields, code owns geometry).
  `apply/ats/{greenhouse,lever,ashby,smartrecruiters}.py` + `detect_ats`. `apply/agent.py` orchestrator
  refuses if `apply.enabled` is false or `auto_submit` is true; fills then **stops at Submit**.
  Dashboard: `POST /api/jobs/{uid}/apply` (guarded) spawns `python -m job_aggregator apply <uid>`;
  Apply button branches on `apply.enabled` (`data-apply-mode`). Surfaced in Config → "Apply agent
  (Track D)" (`apply.enabled` checkbox + `resume.backend` picker) and `.env.example`
  (`JOBAGG_SESSION_KEY`). **Never auto-submits.** `resume.backend` default is now `coding_agent`
  (Claude Code `claude -p`, **no API key**).
- **Profile editor (this batch): DONE.** `/profile` route + `profile.html` YAML editor;
  `profile/store.py` `load_profile_text`/`save_profile_text` (validate-before-write so a typo can't
  corrupt tailoring); nav link in `base.html`. `profile.yaml` is no longer write-once.

A multi-agent adversarial audit (ultracode) found **13 real defects**, all now fixed or documented:
Tier-B salaries now normalized to INR/month in the runner (were bucketed raw → good jobs dropped);
dashboard config checkboxes can be turned OFF (JS sends explicit true/false); crashed 'running' runs
self-heal via `runs_repo.reconcile_orphan_runs` (were a permanent wedge); `javascript:`/`data:` job
URLs stripped in `canonical_url` (stored XSS); salary gate uses max-of-range per §4.3; ATS is now
per-company stale-isolated; `run`/`serve` auto-init + friendly errors; location matching is
whole-token. Cross-process run-lock, fuzzy-dedup gap, and no-auth dashboard are documented limitations.

Docs: `README.md`, `docs/{ats_token_lists,testing}.md`, `TROUBLESHOOTING.md`; deploy extras
`Dockerfile`, `.dockerignore`, `deploy/job-aggregator.{service,timer}`.

Phase-by-phase history (each shipped green, `mypy`/`ruff`/`pytest` clean):
- **Phase 0** — foundation: package skeleton, `errors`/`clock`/`paths`/`logging`, tooling gate.
- **Phase 1** — storage core: `storage/{db,jobs_repo,runs_repo,schema.sql}`, `models/job.py`,
  `config/{schema,store}` (idempotent upsert w/ user-flag preservation; run bookkeeping).
- **Phase 2** — pure pipeline: `pipeline/{dedup,salary,filters,normalize}` (content-hash uid,
  INR/month salary, keyword scoring, `build_job`).
- **Phase 3** — sources: `sources/_http.py` (retry/backoff), `base.py` (`to_job`/`build_result`/
  `run_ats`), Tier-B (remoteok/himalayas/jobicy/adzuna/jooble/unstop) + Tier-C ATS
  (greenhouse/lever/ashby/smartrecruiters) + `registry.py`; respx-mocked tests + JSON fixtures.
- **Phase 4** — Tier A: `sources/jobspy_source.py` (`JobSpySource`, per-site `sub_results` guard,
  lazy `jobspy`/`pandas` behind the `_scrape_jobs` seam, salary→INR/month); wired into registry
  (Tier-A-first). Tests monkeypatch the seam (no network).
- **Phase 5** — correctness core: `pipeline/stale.py` (per-source success guard) + `pipeline/runner.py`
  (`run_cycle`: concurrent fetch → per-source record → filter → dedup-upsert → guarded stale-delete
  → provisional notify). All DB writes on the main thread; input-order determinism. Tests over
  `tests/_fakes.py` (`FakeSource`/`RaisingSource`/`RecordingNotifier`).
- **Phase 6** — self-driving: `scheduler/scheduler.py` (`JobScheduler`: APScheduler `BackgroundScheduler`
  daily cron + startup catch-up on last-**success** + non-blocking lock funnel) and finalized `cli.py`
  (`initdb`/`run`/`serve`/`show-config`; shared-parent `--db`/`--log-level` after the subcommand;
  `.env` load post-parse; error-hierarchy → `{code,message}` envelope).
- **Phase 7** — notifications: `notify/{base,telegram,email,rss}.py`. `FeedScope` routing;
  Telegram HTML digest (httpx), email digest (stdlib smtplib), atomic Atom/RSS snapshot (Jinja2).
  Notifiers log+swallow (never fail a run); missing creds → safe dry-run. Runner **step-8 finalized**:
  NEW_ONLY channels get `jobs_new_in_run`, RSS gets `recent_active_jobs` (both from the DB).
- **Phase 8** — dashboard: `dashboard/{app,deps,routes_jobs,routes_config,routes_runs}.py` +
  Jinja templates + Blood Orange theme (theme.css tokens, app.css, ~150-line vanilla app.js).
  `create_app(*, db_path, clock, scheduler)` (zero-arg works; lifespan owns the scheduler);
  GET / (filter/sort/paginate, whitelisted ORDER BY), row actions → swap `<tr>`, GET/PUT config
  (merge-onto-current + `Config.model_validate`, 422 dotted errors), runs history + Run-now +
  `/api/runs/current` poll; error hierarchy → JSON envelope. Verified live via curl (Playwright
  MCP not connected this session).

- **Phase 9** — polish/hardening: multi-agent adversarial audit (13 defects fixed, above),
  coverage gate ≥85%, README/docs/TROUBLESHOOTING, deploy extras. v1 done.

## Conventions (the user's — honor them)
- **ruff** (lint+format) + **mypy** strict + **pytest** must be green before "done".
- Comments explain **WHY**; doc comments on public identifiers; **named constants** with a
  justifying comment (no bare magic numbers).
- Custom error hierarchy (`errors.py`) → centralized HTTP translation in the dashboard.
- Tests: **table-driven, deterministic**, injected `FixedClock`, **respx** for HTTP. Test the
  correctness core (dedup, salary, filters, stale, runner) hardest. **Never say done without
  running it.**
- Priority order: **Correctness > Reliability > UX > Maintainability > Performance.** The
  per-source success guard in stale-deletion (PLAN §4.5) is the concrete embodiment.
- Commits: terse, single-sentence, imperative, **no co-author trailer**. Commit only when asked.

## Safety when verifying the dashboard
- Use a throwaway DB (`--db /tmp/...`) and a non-default port; don't clobber `data/jobs.db`.
- Drive the UI with Playwright MCP to check loading/empty states, light/dark, console/network
  before calling the dashboard done.
