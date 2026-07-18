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
**v1 COMPLETE (Phases 0–9) + auto-apply Tracks A–D ALL done.** Full gate green:
`ruff check .`, `ruff format --check .`, `mypy src`, `pytest` (**372 passed** / 0 skipped; coverage
**90.16%**, hard gate 85%). Verified live: a real cycle writes+dedups jobs and emits a valid Atom
`feed.xml`; the dashboard serves all routes (incl. `/profile` YAML editor); résumé tailoring
produces a real PDF; a fresh Docker cycle kept ~129 relevant jobs (the earlier "only 1 job" was a
**stale image** — always `docker compose up --build`, not the filter). `profile.yaml` is git-ignored
(personal). **The ONLY un-done piece is running the Track D apply agent against a real browser** —
see `docs/auto_apply_design.md` → "Live validation" (Step 6): needs a display +
`pip install -e '.[apply]'` + `JOBAGG_SESSION_KEY`. **Next session: `git log` (tree should be clean),
then either do the live Track D check or move to new work.**

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
