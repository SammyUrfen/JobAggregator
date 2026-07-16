# JobAggregator

Self-hosted job/internship aggregator: multi-source fetch, dedup, stale-deletion, and a FastAPI dashboard. Tuned for remote/India systems + AI roles.

## What & Why

Every free job board does one of these things. JobAggregator does the *combination*, for a
single self-hosted user, and that combination is the point:

- **Multi-source** — pulls from job-board scrapers, remote-work APIs, and company ATS boards in
  one pass, so you read one list instead of ten tabs.
- **Configurable** — roles, locations, keyword allow/deny, salary floor, and which sources are
  on all live in the dashboard, not in code.
- **Scheduled** — runs once every 24h on your own laptop; no cron babysitting required (though
  you can hand scheduling to the OS — see below).
- **Deduplicated** — the same posting cross-listed on Naukri, LinkedIn, and a company board
  collapses to one row via a content hash.
- **Self-expiring** — a posting that vanishes from its source is deleted on the next run, so the
  list reflects what is *actually still open* rather than growing forever.
- **India / remote / salary-filtered** — normalizes pay to a single comparable unit (INR/month),
  filters to the roles and locations you care about, and keeps (flags, not drops) postings with
  no stated salary.

No free product bundles all six for a personal, single-user deployment. This is a
learning/portfolio project, built from scratch on purpose.

## Architecture at a glance

```
src/job_aggregator/
├── config/      # Pydantic schema + effective-config loader (dashboard is source of truth)
├── models/      # domain types (Job, JobStatus, SalaryBucket)
├── storage/     # SQLite: schema, upsert, stale-deletion, run bookkeeping
├── pipeline/    # the correctness core: normalize · dedup · salary · filter · runner
├── sources/     # Tier A/B/C adapters behind one Source interface + registry
├── notify/      # Telegram + email digests + an Atom/RSS feed
├── scheduler/   # in-process daily BackgroundScheduler
└── dashboard/   # FastAPI UI: job list, config editor, run history
```

One-line data flow:

```
fetch → normalize → dedup → salary (→ INR/month) → filter → upsert → stale-delete → notify
```

## Quickstart

```bash
conda create -n job-aggregator python=3.11 -y && conda activate job-aggregator
pip install -e '.[dev]'
cp .env.example .env
python -m job_aggregator initdb
python -m job_aggregator run
python -m job_aggregator serve            # http://127.0.0.1:8000
```

`run` and `serve` auto-initialize the DB (idempotent), so the explicit `initdb` above is
optional. Shared flags come *after* the subcommand: `python -m job_aggregator serve --db /tmp/x.db`.
Inspect the effective config from the terminal with `python -m job_aggregator show-config`.

The four subcommands: **`initdb`** (create + seed the DB), **`run`** (one aggregation cycle now),
**`serve`** (dashboard + in-process scheduler), **`show-config`** (print the effective config as JSON).

## Config-in-dashboard

On the first `initdb`, config is seeded from `config/default_config.yaml`. After that, **the
dashboard (`/config`) is the source of truth** — the YAML file is only the seed. Edits are
validated against the Pydantic schema (`config/schema.py`) on save and **apply on the next run**,
not mid-cycle. Secrets (Adzuna/Jooble keys, Telegram/SMTP credentials) are **never** stored in
the DB config row — they come from `.env` only. See `.env.example` for the full set.

## Scheduling

By default `serve` starts an **in-process `BackgroundScheduler`** that fires one aggregation
cycle daily at `schedule.run_hour_local` (default hour 3, local time). Because that scheduler and
the SQLite writes live in one process, run **exactly one** process — **never `uvicorn --workers N`**;
multiple workers would each schedule the job and race on the DB.

If you would rather the OS own scheduling, disable the in-process scheduler and drive `run`
externally:

```bash
JOBAGG_DISABLE_SCHEDULER=1 python -m job_aggregator serve   # dashboard only, no scheduler
```

Then point a systemd `.timer` (or a cron line) at `python -m job_aggregator run` once a day.

## Sources

| Tier | Source | Access | Notes |
|---|---|---|---|
| A | Naukri, LinkedIn (guest), Indeed-IN, Google | via `python-jobspy` | scraped; India-focused query |
| A | Unstop | HTTP API | internships/opportunities |
| B | RemoteOK, Himalayas, Jobicy | free HTTP APIs | remote-first boards |
| B | Adzuna | HTTP API + key | `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` |
| B | Jooble | HTTP API + key | `JOOBLE_API_KEY` |
| B | Remotive | — | evaluated dead end; left **off** |
| C | Greenhouse, Lever, Ashby, SmartRecruiters | per-company ATS | you curate company tokens; no global search |

A source that is enabled but missing its API key (or, for ATS, its company list) is skipped with
a warning rather than failing the whole run.

## Limitations

This is honest about what it does *not* do.

- **Deliberately excluded dead ends.** X/Twitter job posts, **authenticated** LinkedIn scraping,
  Wellfound, the `hiring.cafe` API, and JSearch were evaluated and left out — each was either a
  ToS/anti-bot wall or not worth the fragility. See `research.md` for the reasoning.
- **ATS has no global search.** Greenhouse/Lever/Ashby/SmartRecruiters only expose *per-company*
  boards, so you must curate the company tokens yourself (`docs/ats_token_lists.md`). Each ATS
  company is **stale-isolated**: one company's fetch failing does not expire another's postings.
- **Near-duplicates can still show twice.** Runtime dedup is an **exact content-hash** match; the
  same-ish title posted by two *different* companies will appear as two rows. A fuzzy
  second-pass (rapidfuzz) exists in the codebase but is **not wired into the runtime path**.
- **Salary normalization is approximate.** Pay is converted to **INR/month** using
  **hand-maintained FX rates**, which drift from the market. Postings with **no stated salary are
  kept and flagged**, never dropped — a filter floor only excludes postings whose *known* salary
  is below it.
- **No authentication on the dashboard.** There is no login. **Bind it to localhost**, treat it
  as single-user, personal, low-volume use only. Do not expose it to a network.
- **Scraping is fragile.** The jobspy-backed sources (Naukri/LinkedIn especially) break between
  upstream releases when the target sites change; expect occasional zero-result runs there.
- **Coverage does not prove liveness.** Test coverage is dominated by **respx-mocked** source
  adapters. It verifies the parsing/normalization logic against *captured* response shapes; it
  does **not** prove the live boards still return those shapes today.

## Development

Four-command gate — all must be green before "done":

```bash
ruff check .              # lint
ruff format --check .     # formatting
mypy src                  # strict type-check
pytest                    # runs the suite with coverage; --cov-fail-under=85
```

Further reading:

- [`docs/testing.md`](docs/testing.md) — test strategy (table-driven, injected `FixedClock`, respx).
- [`docs/ats_token_lists.md`](docs/ats_token_lists.md) — how to curate ATS company tokens.
- [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) — common failure modes and fixes.
- [`PLAN.md`](PLAN.md) — the frozen architecture contract (Part I) + phase-by-phase build guide (Part II).

## License

MIT.
