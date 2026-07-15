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

## Current status
**Phases 0–6 implemented + verified** (full gate green: `ruff check`/`ruff format`/`mypy src`
all clean; `pytest` 156 passed / 3 skipped; one **live end-to-end cycle** writes+dedups jobs). Done:
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
  `.env` load post-parse; error-hierarchy → `{code,message}` envelope). `notify.base.build_notifiers`
  is a no-op `[]` until Phase 7 (was raising and crashing `run` once new jobs appeared).

**Remaining: Phases 7–9** — stubs still raise `NotImplementedError("Phase N: ...")`. Build in
order per PLAN.md Part II. Next: Phase 7 (notifications: telegram/email/rss + runner step-8 finalize).

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
