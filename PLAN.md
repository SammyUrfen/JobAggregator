# JobAggregator — Build Plan

> This document is the authoritative build plan. **Part I** is the frozen architecture
> spec (contracts: DB schema, Source interface, config schema, dedup/salary/stale
> algorithms, dashboard + theme tokens). **Part II** is the phase-by-phase build guide
> (Phases 0–9), authored per-phase from Part I and reconciled by an adversarial
> consistency + buildability critique pass.
>
> **Precedence:** where Part II's *"Canonical contracts"* section refines or narrows Part I
> (e.g. exact signatures, `ClassVar` names, `last_successful_run` semantics), **Part II
> wins** — it is the reconciled source of truth. Build phases strictly in order 0→9.
>
> Environment: conda env **`job-aggregator`** (Python 3.11). Repo path has a space —
> quote it. Import package is `job_aggregator` (src layout). See CLAUDE.md for the index.


---

# Part I — Architecture & Canonical Spec (FROZEN)

> This is the single source of truth. Every phase guide, every stub, every test must
> conform to the names, signatures, schema, and behaviours defined here. If a phase
> guide needs to deviate, it must call the deviation out explicitly. Do not silently
> rename modules, columns, or functions.

Project codename: **JobAggregator** (package `job_aggregator`). Repo lives at
`/home/SammyUrfen/Codes/job aggregator/` (note the space — always quote the path in shells).
Runs on a personal Fedora laptop. Python 3.11, conda env **`job-aggregator`**.
Job runs **once per 24 h** (configurable hour). Dashboard is a **FastAPI** web app whose
config edits take effect on the *next* run.

---

## 0. Non-negotiable decisions (with rationale)

| Decision | Choice | Why |
|---|---|---|
| Language | Python 3.11 only | user directive |
| Web framework | **FastAPI + Uvicorn**, server-rendered **Jinja2** | user's deepest framework; full control over the hand-rolled theme (Streamlit would fight the custom CSS design tokens) |
| Frontend JS | **Minimal vanilla JS** (fetch), no SPA, no CDN | server-first, robust, matches from-scratch ethos; filters are GET query params (bookmarkable), only run-now polling + row actions use fetch |
| DB | **SQLite via stdlib `sqlite3`**, WAL mode, hand-written SQL, no ORM | he hand-writes SQL parsers; ORM magic is anti-ethos; one file, zero infra |
| Config validation | **Pydantic v2** models | structured, typed, matches his structured-error taste |
| HTTP client | **httpx** (sync client) | modern, timeouts/retries; JobSpy is sync so runner stays sync |
| Scraping engine (Tier A) | **python-jobspy** | only maintained OSS lib that natively parses Naukri (India #1) |
| Fuzzy dedup | **rapidfuzz** | fast token_sort_ratio |
| Scheduler | **APScheduler** `BackgroundScheduler`, daily cron + startup catch-up + run-lock | laptop sleeps → catch-up-on-startup is mandatory |
| Concurrency | **`concurrent.futures.ThreadPoolExecutor`** for I/O-bound source fetches | sources are I/O bound; "new syntax for a problem he's solved with Java ConcurrentHashMap" |
| Lint/format | **ruff** (lint + format) | his C++ `-Wall -Wextra -Wpedantic` discipline → ruff strict |
| Types | **mypy** strict | |
| Tests | **pytest** + **pytest-cov**, **respx** (httpx mocking), injected **clock** for time | table-driven, deterministic; "never say done without verification" |
| Layout | **src layout, package-by-feature** | mirrors his Spring package-by-feature (`sources/`, `pipeline/`, not `handlers/`) |
| Error handling | custom exception hierarchy → centralized translation | his cross-project pattern (`ApiException`+`ErrorCode`) |

**Explicit priority order (his):** Correctness > Reliability > UX > Maintainability > Performance.
The per-source success guard in stale-deletion is the concrete embodiment of Correctness>Reliability.

---

## 1. Directory tree (authoritative)

```
job aggregator/                       # repo root (has a space)
├── pyproject.toml                    # deps + ruff + mypy + pytest config; console script
├── README.md
├── CLAUDE.md                         # project index for future Claude sessions
├── PLAN.md                           # THIS spec (Part I) + phase-by-phase guide (Part II)
├── research.md                       # moved from ../job-aggregator-research.md
├── .gitignore
├── .env.example                      # secrets: TELEGRAM_*, ADZUNA_*, JOOBLE_*, SMTP_*
├── blood_orange_theme_detail.html    # the theme reference (already present)
├── config/
│   └── default_config.yaml           # seed config; loaded into DB on first init
├── data/                             # runtime, GITIGNORED: jobs.db, feed.xml, logs/
│   └── .gitkeep
├── docs/
│   ├── ats_token_lists.md            # how to seed Greenhouse/Lever/Ashby/SmartRecruiters tokens
│   └── testing.md
├── src/job_aggregator/
│   ├── __init__.py                   # __version__
│   ├── __main__.py                   # `python -m job_aggregator` -> cli.main()
│   ├── cli.py                        # argparse: run | serve | initdb | show-config
│   ├── logging_setup.py              # configure_logging()
│   ├── errors.py                     # exception hierarchy + ErrorCode
│   ├── clock.py                      # Clock protocol + SystemClock + FixedClock (tests)
│   ├── paths.py                      # resolves DATA_DIR, DB_PATH, etc. (respects env)
│   ├── config/
│   │   ├── __init__.py
│   │   ├── schema.py                 # Pydantic: Config, Keywords, SalaryConfig, ScheduleConfig, SourcesConfig, AtsConfig, NotifyConfig
│   │   └── store.py                  # load_effective_config(conn), save_config(conn, cfg), seed_from_yaml()
│   ├── models/
│   │   ├── __init__.py
│   │   └── job.py                    # Job (pydantic), JobStatus enum, SalaryBucket enum
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── schema.sql                # DDL (jobs, runs, source_runs, config)
│   │   ├── db.py                     # connect(), init_db(), migrate(), pragmas
│   │   ├── jobs_repo.py              # upsert_job, get_jobs (filtered), set_user_flag, mark_stale, mark_deleted
│   │   └── runs_repo.py              # start_run, finish_run, record_source_run, current_run, recent_runs
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── base.py                   # Source ABC, SourceResult, RawPosting
│   │   ├── registry.py               # build_enabled_sources(cfg) -> list[Source]
│   │   ├── _http.py                  # shared httpx client + get_json() with retry/backoff
│   │   ├── jobspy_source.py          # Tier A
│   │   ├── unstop.py                 # Tier A
│   │   ├── remoteok.py               # Tier B
│   │   ├── himalayas.py              # Tier B
│   │   ├── jobicy.py                 # Tier B
│   │   ├── adzuna.py                 # Tier B (needs key)
│   │   ├── jooble.py                 # Tier B (needs key)
│   │   ├── ats_greenhouse.py         # Tier C
│   │   ├── ats_lever.py              # Tier C
│   │   ├── ats_ashby.py              # Tier C
│   │   └── ats_smartrecruiters.py    # Tier C
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── normalize.py              # raw dict -> Job (per-source adapters live in each source; shared helpers here)
│   │   ├── dedup.py                  # canonical_url, content_hash (job_uid), norm_company/title/location, fuzzy_is_dup
│   │   ├── salary.py                 # parse_salary, to_inr_month, salary_bucket
│   │   ├── filters.py                # score_and_filter(job, cfg) -> FilterVerdict
│   │   ├── stale.py                  # expire_stale(conn, run_id, succeeded_sources, cfg, clock)
│   │   └── runner.py                 # run_cycle(conn, cfg, clock, trigger) -> RunSummary  (THE HEART)
│   ├── notify/
│   │   ├── __init__.py
│   │   ├── base.py                   # Notifier ABC
│   │   ├── telegram.py
│   │   ├── email.py
│   │   └── rss.py                    # writes data/feed.xml via Jinja template
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── scheduler.py              # JobScheduler: start(), trigger_now(), catch_up_on_startup(), _run_locked()
│   └── dashboard/
│       ├── __init__.py
│       ├── app.py                    # create_app() -> FastAPI; lifespan starts scheduler
│       ├── routes_jobs.py            # GET /  (+ /api/jobs/{uid}/action)
│       ├── routes_config.py          # GET /config, PUT /api/config
│       ├── routes_runs.py            # GET /runs, POST /api/runs, GET /api/runs/current
│       ├── deps.py                   # get_conn, get_config
│       ├── templates/
│       │   ├── base.html
│       │   ├── jobs.html
│       │   ├── config.html
│       │   ├── runs.html
│       │   └── partials/{job_row.html, run_status.html}
│       └── static/
│           ├── css/theme.css         # design tokens from blood orange (light + dark)
│           ├── css/app.css           # layout/components
│           ├── js/app.js             # run-now polling, row actions, theme toggle
│           └── favicon.svg
└── tests/
    ├── conftest.py                   # in-memory/tmp db fixture, FixedClock, sample config
    ├── fixtures/                     # recorded JSON responses per source
    ├── test_dedup.py
    ├── test_salary.py
    ├── test_filters.py
    ├── test_normalize.py
    ├── test_stale.py
    ├── test_jobs_repo.py
    ├── test_runner.py
    ├── test_sources_ats.py
    ├── test_sources_apis.py
    └── test_dashboard.py
```

---

## 2. Data model

### 2.1 Enums (`models/job.py`)
```python
class JobStatus(str, Enum):
    NEW = "new"          # inserted this cycle, not yet notified-cleared
    ACTIVE = "active"    # seen in a recent successful cycle
    STALE = "stale"      # not seen last cycle (soft), within grace window
    DELETED = "deleted"  # missing beyond grace window (soft-deleted, hidden from default view)

class SalaryBucket(str, Enum):
    PASS = "pass"        # parsed AND meets threshold
    UNKNOWN = "unknown"  # not parseable -> KEEP + flag (most Indian internships)
    FAIL = "fail"        # parsed AND below threshold -> dropped before insert
```

### 2.2 `Job` (pydantic model)
Fields (all typed; `Optional` where nullable): `job_uid: str` (PK, content hash),
`source: str`, `source_native_id: str | None`, `title: str`, `company: str`,
`location: str | None`, `is_remote: bool | None`, `url: str` (canonicalized),
`description: str | None`, `salary_min: int | None`, `salary_max: int | None`,
`salary_currency: str | None`, `salary_period: str | None` (`"month"|"year"|"hour"`),
`salary_raw: str | None`, `salary_parsed: bool`, `salary_bucket: SalaryBucket | None`,
`posted_at: datetime | None`, `match_score: float | None`.

The Job model represents a *normalized* posting produced by a source adapter, BEFORE it
touches the DB. Persistence-only columns (`first_seen_at`, `last_seen_at`, `last_seen_cycle`,
`status`, user flags) are added by `jobs_repo`, not carried on `Job`.

### 2.3 SQLite schema (`storage/schema.sql`) — authoritative DDL
```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at    TEXT NOT NULL,           -- ISO8601 UTC
  finished_at   TEXT,
  status        TEXT NOT NULL,           -- 'running'|'success'|'partial'|'failed'
  trigger       TEXT NOT NULL,           -- 'schedule'|'manual'|'startup_catchup'
  n_sources_ok  INTEGER DEFAULT 0,
  n_sources_err INTEGER DEFAULT 0,
  n_new         INTEGER DEFAULT 0,
  n_updated     INTEGER DEFAULT 0,
  n_expired     INTEGER DEFAULT 0,
  error         TEXT
);

CREATE TABLE IF NOT EXISTS source_runs (
  run_id      INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  source      TEXT NOT NULL,
  succeeded   INTEGER NOT NULL,          -- 1 = safe to expire this source's jobs
  n_fetched   INTEGER,
  duration_ms INTEGER,
  error       TEXT,
  PRIMARY KEY (run_id, source)
);

CREATE TABLE IF NOT EXISTS jobs (
  job_uid          TEXT PRIMARY KEY,     -- sha256(norm(company)|norm(title)|norm(location))
  source           TEXT NOT NULL,
  source_native_id TEXT,
  title            TEXT NOT NULL,
  company          TEXT NOT NULL,
  location         TEXT,
  is_remote        INTEGER,              -- 0|1|NULL
  url              TEXT NOT NULL,        -- canonicalized
  description      TEXT,
  salary_min       INTEGER,              -- normalized INR/month
  salary_max       INTEGER,
  salary_currency  TEXT,
  salary_period    TEXT,
  salary_raw       TEXT,
  salary_parsed    INTEGER NOT NULL DEFAULT 0,
  salary_bucket    TEXT,                 -- 'pass'|'unknown'|'fail'
  match_score      REAL,
  posted_at        TEXT,
  first_seen_at    TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  last_seen_cycle  INTEGER NOT NULL REFERENCES runs(run_id),
  status           TEXT NOT NULL,        -- JobStatus
  -- user fields: MUST survive upserts
  applied          INTEGER NOT NULL DEFAULT 0,
  bookmarked       INTEGER NOT NULL DEFAULT 0,
  hidden           INTEGER NOT NULL DEFAULT 0,
  notes            TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status      ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_source      ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_last_cycle  ON jobs(last_seen_cycle);
CREATE INDEX IF NOT EXISTS idx_jobs_score       ON jobs(match_score);

CREATE TABLE IF NOT EXISTS config (
  id         INTEGER PRIMARY KEY CHECK (id = 1),  -- single row
  data       TEXT NOT NULL,             -- config serialized as JSON
  updated_at TEXT NOT NULL
);
```

---

## 3. Source contract (`sources/base.py`)

```python
@dataclass
class RawPosting:
    """Whatever a source yields, before normalization. Kept as a dict-ish payload."""
    payload: dict

@dataclass
class SourceResult:
    source: str
    succeeded: bool          # False on error OR suspicious-empty -> stale-delete SKIPPED
    jobs: list[Job]          # already normalized by the source adapter
    n_fetched: int
    duration_ms: int
    error: str | None = None

class Source(ABC):
    name: str                # stable id, e.g. "greenhouse", "jobspy_naukri", "remoteok"
    @abstractmethod
    def fetch(self, cfg: Config, clock: Clock) -> SourceResult: ...
```

**Rules every source obeys:**
- A source **normalizes its own rows** into `Job` objects (calls shared helpers in
  `pipeline/normalize.py`, `pipeline/dedup.py`, `pipeline/salary.py`).
- On any exception, network error, non-2xx, or **suspicious empty** (0 results from a
  source that is normally populated), return `succeeded=False`. **Never raise out of fetch.**
- `succeeded=False` means "we couldn't see this source's jobs" → its jobs are NOT expired.
- Tier C ATS sources are **per-company**: one `Source` instance may loop over a token list
  and aggregate; if *any* company fetch fails, that failure is noted but the source only
  reports `succeeded=False` if it got zero usable data overall (document the exact rule in
  the phase guide — partial success should still count as succeeded with the fetched subset).

**Source `name` values (stable, used as `jobs.source` and in `source_runs`):**
`jobspy_naukri`, `jobspy_linkedin`, `jobspy_indeed`, `jobspy_google` (or a single
`jobspy` source that tags each Job's `source` per-site — DECISION: one `JobSpySource`
instance, but each produced Job's `.source` is `jobspy_<site>`, and `source_runs` records
one row per site so the success guard is per-site). `unstop`, `remoteok`, `himalayas`,
`jobicy`, `adzuna`, `jooble`, `greenhouse`, `lever`, `ashby`, `smartrecruiters`.

---

## 4. Pipeline (the correctness core)

### 4.1 `run_cycle(conn, cfg, clock, trigger) -> RunSummary` (`pipeline/runner.py`)
The heart. Exact ordering:
1. `run_id = runs_repo.start_run(conn, trigger, clock)` (status='running').
2. Build enabled sources via `registry.build_enabled_sources(cfg)`.
3. Fetch all sources **concurrently** via `ThreadPoolExecutor` (bounded, e.g. max_workers=8).
   Each returns a `SourceResult`. Never let one source's exception kill the cycle.
4. For each `SourceResult`: `runs_repo.record_source_run(...)` with its `succeeded` flag.
5. Collect all Jobs from succeeded sources. Apply `filters.score_and_filter` → drop hard
   fails (exclude keyword, wrong level, salary FAIL, wrong location). Keep pass + unknown.
6. **Dedup + upsert**: for each surviving Job, `jobs_repo.upsert_job(conn, job, run_id, clock)`.
   Upsert sets `last_seen_at`/`last_seen_cycle`; INSERT → status='new'; existing → status
   promoted to 'active', mutable fields refreshed, **user flags preserved**.
7. **Stale-delete**: `stale.expire_stale(conn, run_id, succeeded_sources, cfg, clock)`.
   ONLY sources that succeeded this cycle may expire. Soft 'stale' first; hard 'deleted'
   after `grace_days`.
8. **Notify**: gather jobs with status='new' from this run; `Notifier.notify_new(...)` for
   each enabled notifier (digest, not per-job spam). Regenerate RSS.
9. `runs_repo.finish_run(...)` with counts + status ('success' if all sources ok, 'partial'
   if some failed, 'failed' if all failed / fatal). Return `RunSummary`.

Concurrency guard: only one cycle runs at a time (scheduler holds the lock; runner also
checks no other run is 'running' as a belt-and-suspenders).

### 4.2 Dedup (`pipeline/dedup.py`)
```python
def norm_company(s: str) -> str   # lowercase, strip punctuation/space, strip suffixes:
    #   inc, llc, ltd, pvt, private limited, llp, corp, corporation, technologies, labs, inc.
def norm_title(s: str) -> str     # lowercase, collapse whitespace, strip punctuation
def norm_location(s: str) -> str  # lowercase; map remote synonyms -> "remote"; else city/country
def content_hash(company, title, location) -> str   # sha256 of "c|t|l" of the norms -> job_uid
def canonical_url(url: str) -> str  # lowercase host, drop fragment + tracking params
    #   (utm_*, gh_src, ref, source, src) ; keep the path + meaningful query
def fuzzy_is_dup(title_a, title_b, threshold=90) -> bool   # rapidfuzz.token_sort_ratio
```
- Primary identity: `job_uid = content_hash(...)`. Cross-source: same role on Naukri +
  LinkedIn + a Greenhouse board collapses to ONE row.
- Fuzzy second pass: within the same `norm_company`, if two candidate uids differ but
  `fuzzy_is_dup(title)` > threshold, treat as the same (prefer the existing row).
- **Known trade-off (document it):** two genuinely distinct openings with identical
  company+title+location collapse to one row. Acceptable for a personal feed; keep
  first-seen URL, refresh mutable fields.

### 4.3 Salary (`pipeline/salary.py`)
```python
def parse_salary(raw: str | None, currency: str | None, period: str | None,
                 min_v: int | None, max_v: int | None) -> ParsedSalary
def to_inr_month(amount: int, currency: str, period: str, rates: dict[str,float]) -> int
def salary_bucket(job: Job, cfg: Config) -> SalaryBucket
```
- Normalize everything to **INR / month**. Year→/12. Foreign currency→INR via a small
  **configurable static rate table** (`cfg.salary.fx_rates`, default `{USD:83, EUR:90, GBP:105}`)
  — named constants with a comment that these are approximate and user-updatable.
- `salary_parsed=True` only if a real number + currency + period were convertible.
- Threshold: `30000` if `is_remote` else `80000` INR/month (from `cfg.salary`).
- Compare using `salary_max` if present else `salary_min`.
- Bucket: parsed & >=threshold → PASS; parsed & <threshold → FAIL; else UNKNOWN.
- **UNKNOWN is KEPT** (`on_missing: keep_and_flag`). In-office + UNKNOWN → optionally
  demote score (`demote_in_office_if_unknown`) but still keep.

### 4.4 Filters / scoring (`pipeline/filters.py`)
```python
@dataclass
class FilterVerdict:
    keep: bool
    score: float
    reasons: list[str]   # human-readable, shown nowhere critical but useful for tests/logs

def score_and_filter(job: Job, cfg: Config) -> FilterVerdict
```
Hard drops (`keep=False`): contains any `exclude` keyword (title); matches NO
`level_required` token (title/description) when `require_level=True`; salary bucket FAIL;
location not in configured locations AND not remote. Score: +role match in title (weight),
+bonus keywords, +remote boost if `remote_preferred`, small recency boost. Score drives the
dashboard default sort.

### 4.5 Stale-deletion (`pipeline/stale.py`) — the crux
```python
def expire_stale(conn, run_id: int, succeeded_sources: set[str], cfg: Config, clock: Clock) -> int
```
```
for source in succeeded_sources:      # NEVER expire a source that failed/blocked this run
    # soft: jobs from this source not seen this cycle -> 'stale'
    UPDATE jobs SET status='stale'
      WHERE source=? AND last_seen_cycle < :run_id AND status IN ('new','active')
    # hard: stale jobs older than grace window -> 'deleted' (soft-hide, not row delete)
    UPDATE jobs SET status='deleted'
      WHERE source=? AND status='stale' AND last_seen_at < :now - grace_days
```
- A `jobspy_linkedin` 429 → its `source_runs.succeeded=0` → its jobs are untouched (they
  did NOT disappear, we just couldn't see them). This is THE bug the ecosystem gets wrong.
- `status='deleted'` is a soft-hide (row kept for history + to preserve user flags); default
  views exclude it. A separate optional hard-purge can `DELETE` very old deleted rows.
- Grace: `cfg.schedule.grace_days` (default 3). With daily runs, 3 missed days = deleted.

---

## 5. Config schema (`config/schema.py` + `config/default_config.yaml`)

Pydantic v2 models. The full config is persisted as JSON in `config.data` and edited from
the dashboard; the runner loads the effective config at the START of each cycle → edits take
effect next run.

```yaml
# config/default_config.yaml  (seed; mirror in Pydantic Config)
keywords:
  roles: [backend engineer, systems software, distributed systems, infrastructure engineer,
          platform engineer, site reliability, database engineer, ml engineer,
          machine learning engineer, ai engineer, llm engineer, reinforcement learning, mlops]
  bonus: [Go, Golang, Rust, C++, PyTorch, RAG, LLM, GRPO, LoRA, inference, storage engine,
          consistent hashing, kubernetes, kafka]
  level_required: [intern, internship, trainee, new grad, graduate engineer, junior]
  exclude: [senior, staff, principal, lead, manager, director, "5+ years", "clearance required"]
  require_level: true

locations: ["Bengaluru, India", Bangalore, India, Remote, "Remote - India", Worldwide]
remote_preferred: true

salary:
  currency: INR
  period: month
  min_remote: 30000
  min_in_office: 80000
  on_missing: keep_and_flag          # keep_and_flag | drop
  demote_in_office_if_unknown: true
  fx_rates: {USD: 83.0, EUR: 90.0, GBP: 105.0}   # approximate, user-updatable

schedule:
  run_hour_local: 3                  # daily at 03:00 local
  hours_old: 48                      # incremental fetch window (>= 24h + slack)
  grace_days: 3                      # stale -> deleted after this many missed days
  catch_up_on_startup: true

sources:
  jobspy:
    enabled: true
    sites: [naukri, linkedin, indeed, google]
    search_terms: [backend intern, systems intern, ml intern, "golang intern"]
    location: "Bengaluru, India"
    country_indeed: india
    is_remote: true
    results_wanted: 40
    hours_old: 48
    proxies: []                      # add ONLY for linkedin at volume
  unstop: {enabled: true, opportunities: [internships, jobs], search_terms: [backend, "machine learning"], max_age_days: 30}
  remoteok: {enabled: true}          # attribution required (element[0] is a legal notice)
  himalayas: {enabled: true, country: IN}   # ~24h refresh; coverage not freshness
  jobicy: {enabled: true, job_type: internship}
  adzuna: {enabled: true, country: in}       # keys via env ADZUNA_APP_ID / ADZUNA_APP_KEY
  jooble: {enabled: true}                    # key via env JOOBLE_API_KEY
  remotive: {enabled: false}                 # 24h delayed + 4 calls/day cap
  ats:
    greenhouse: {enabled: true, tokens: []}       # e.g. [razorpay, postman]
    lever: {enabled: true, slugs: []}
    ashby: {enabled: true, orgs: []}              # case-sensitive
    smartrecruiters: {enabled: true, company_ids: []}   # supports ?country=in
  # explicitly unsupported (documented dead ends): twitter, wellfound, hiring_cafe, jsearch

notify:
  on: new_only
  telegram: {enabled: false}         # bot_token/chat_id via env
  email: {enabled: false, smtp_host: "localhost", smtp_port: 25, to: ""}
  rss: {enabled: true, path: "data/feed.xml", max_items: 100}
```

Secrets NEVER live in the config row — they come from env (`.env` / `.env.example`):
`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOOBLE_API_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`.

---

## 6. Scheduler (`scheduler/scheduler.py`)
- `JobScheduler(conn_factory, cfg_factory, clock)` wraps APScheduler `BackgroundScheduler`.
- `start()`: add a daily `CronTrigger(hour=cfg.schedule.run_hour_local)` +
  `misfire_grace_time` generous; then `catch_up_on_startup()`.
- `catch_up_on_startup()`: if `catch_up_on_startup` and no successful run in the last ~24h
  (query `runs`), submit an immediate run. Handles the sleeping-laptop reality.
- `trigger_now(trigger='manual')`: submit a run if none in progress (returns run_id or a
  409-ish "already running").
- `_run_locked()`: a `threading.Lock` + DB `status='running'` check so manual + scheduled
  never overlap. Each run opens its OWN sqlite connection (sqlite connections are not
  thread-safe to share).

## 7. Dashboard (`dashboard/`) — where the theme lives
- `create_app()` builds FastAPI, mounts `/static`, sets Jinja2 templates, starts the
  scheduler in the lifespan context, stops it on shutdown.
- **Pages (server-rendered, filters = GET query params, bookmarkable):**
  - `GET /` — Jobs table. Columns: score, title (→ url), company, location, remote badge,
    salary (bucket-colored), posted, source, actions. Query filters: `q`, `source`,
    `remote` (bool), `bucket`, `status` (default excludes 'deleted' + 'hidden'), `applied`,
    `bookmarked`, `sort` (score|posted|company), `page`. Row actions POST via fetch.
  - `GET /config` — form bound to the Pydantic Config; `PUT /api/config` validates + saves
    → toast "applies on next run". Show validation errors inline (his structured-error taste).
  - `GET /runs` — run history table + per-source breakdown + errors; "Run now" button
    (`POST /api/runs` → 202 + run_id) with live status polling (`GET /api/runs/current`).
- **JSON API:** `POST /api/runs`, `GET /api/runs/current`, `POST /api/jobs/{uid}/action`
  (body: `{field: applied|bookmarked|hidden|notes, value}`), `PUT /api/config`.
- **Header:** title, last-run summary, next-run time, Run-now, light/dark toggle.

### 7.1 Theme — Blood Orange design tokens (from `blood_orange_theme_detail.html`)
`theme.css` defines CSS custom properties; light is default, dark via
`@media (prefers-color-scheme: dark)` AND a manual `:root[data-theme="dark"]` override
(toggle stamps `data-theme`, wins both directions). Radii: card 12px, inner 10px, control
8px, button 6px. Borders 0.5px (fall back 1px). Weight 500 for emphasis. Mono
(`ui-monospace, "Fira Code", monospace`) for salary/counts/ids; sans (`system-ui`) for text.

| token | light | dark |
|---|---|---|
| `--bg` | `#FBF3EA` | `#241713` |
| `--surface-1` | `#FFF9F3` | `#2E1D17` |
| `--surface-2` | `#F3E7DA` (derived) | `#3A251E` (derived) |
| `--accent` | `#E23F3F` | `#FF6B5B` |
| `--accent-contrast` | `#FFF9F3` | `#2E1613` |
| `--text` | `#361F1C` | `#F7E1DA` |
| `--text-secondary` | `#8A6A62` | `#B18A80` |
| `--border` | `#E7D6C8` | `#3D2A22` |
| `--ok` | `#2E7D5B` (derived green) | `#5FD6A0` |
| `--warn` | `#C77D2E` (derived amber, for UNKNOWN salary) | `#E9A85C` |

Salary bucket colors: PASS→`--ok`, UNKNOWN→`--warn`, FAIL→(not shown, filtered out).
Remote badge uses `--accent`. Keep the warm, calm feel of the reference card.

---

## 8. Conventions (his)
- `gofmt`-equivalent discipline: **ruff format** + **ruff check** + **mypy** clean before "done".
- Comments explain **WHY**, not what; doc comments on every exported (public) function/class.
- Named constants with justifying comments; never bare magic numbers (grace_days, thresholds,
  fx_rates, fuzzy threshold, max_workers all named + commented).
- Custom exception hierarchy in `errors.py` (`JobAggregatorError` → `SourceError`,
  `ConfigError`, `StorageError`) with an `ErrorCode` enum; one place translates to HTTP in
  the dashboard.
- Tests: table-driven, deterministic; inject `FixedClock`; mock HTTP with `respx`; record
  real sample responses in `tests/fixtures/`. Target the correctness core (dedup, salary,
  stale, filters, runner) hardest.
- Every phase ends with an explicit **acceptance check** (a command that must pass).

## 9. Verified external facts to honor (from prior research, 2026-07-14)
- `pip install -U python-jobspy`; import `from jobspy import scrape_jobs`; returns a pandas
  DataFrame; supports `site_name=[...]` incl. `naukri`, `linkedin`, `indeed`, `google`;
  params `search_term`, `location`, `results_wanted`, `hours_old`, `country_indeed`,
  `is_remote`, `linkedin_fetch_description`, `proxies`. LinkedIn 429s after ~10 pages/IP.
- ATS endpoints (no auth): Greenhouse `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`;
  Lever `https://api.lever.co/v0/postings/{slug}?mode=json`; Ashby
  `https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true`;
  SmartRecruiters `https://api.smartrecruiters.com/v1/companies/{id}/postings?country=in`.
- Remote APIs (no auth): RemoteOK `https://remoteok.com/api` (element[0] is a legal notice —
  SKIP it; attribution required); Himalayas `https://himalayas.app/jobs/api/search?country=IN`;
  Jobicy `https://jobicy.com/api/v2/remote-jobs?count=50&tag=...`.
- Adzuna (key): `https://api.adzuna.com/v1/api/jobs/in/search/1?app_id=..&app_key=..&what=..`;
  free tier ~250/day (community-reported — confirm on dashboard). Jooble (key): POST JSON.
- Unstop (no auth, undocumented): `https://unstop.com/api/public/opportunity/search-result?opportunity=internships&searchTerm=..`;
  filter on `updated_at` recency (surfaces 2022 posts); read type from `subtype` not `type`.
- Skip entirely: X/Twitter (no free path), authenticated LinkedIn (ban 3–7d), Wellfound
  (Cloudflare+DataDome), hiring.cafe API (Cloudflare-locked; browse-only), JSearch
  (200 req/month free tier).
```

---

# Part II — Build guide

This is the phase-by-phase build guide for JobAggregator. Part I (the frozen spec) is prepended separately; this part tells a medium-effort model exactly what to build, in what order, with which signatures, and how to prove each phase is done. Build phases strictly in order 0→9: each phase's acceptance gate depends only on itself plus earlier phases.

**How to read this.** Before any phase, read **Canonical contracts** below — it is the single source of truth for every shared type, signature, column name, and enum value. Where an individual phase's prose ever seems to disagree with Canonical contracts, Canonical contracts wins (the phases were reconciled to it). Every phase ends in a **### Acceptance check** you must run and pass — "never say done without verification."

### Phase index

| Phase | Title | Key deliverables | Acceptance (headline) |
|---|---|---|---|
| 0 | Foundation & environment | `pyproject.toml`, `cli.py` stubs, `errors.py`, `clock.py`, `paths.py`, `logging_setup.py`, seed `config/default_config.yaml`, package skeleton, Phase-0 tests | `python -m job_aggregator --help` stdlib-only; `ruff`/`mypy`/`pytest` clean |
| 1 | Storage core (SQLite) | `storage/schema.sql`, `storage/db.py`, `storage/jobs_repo.py`, `storage/runs_repo.py`, `models/job.py`, `config/schema.py`, `config/store.py` | `initdb` creates 4 tables; `pytest tests/test_jobs_repo.py` green |
| 2 | Domain pipeline (pure) | `pipeline/dedup.py`, `pipeline/salary.py`, `pipeline/filters.py`, `pipeline/normalize.py` | pure-fn tests green; `mypy src` clean |
| 3 | Sources: base + Tier B/C | `sources/_http.py`, `sources/base.py`, Tier-B + ATS adapters, `sources/registry.py` | `pytest tests/test_sources_apis.py tests/test_sources_ats.py` green |
| 4 | Sources: Tier A (JobSpy) | `sources/jobspy_source.py` | `pytest tests/test_jobspy_source.py` green (mocked scrape) |
| 5 | Pipeline runner + stale-delete | `pipeline/stale.py`, `pipeline/runner.py` | `pytest tests/test_runner.py tests/test_stale.py` green |
| 6 | Scheduler + CLI wiring | `scheduler/scheduler.py`, finalize `cli.py` | `pytest tests/test_scheduler.py tests/test_cli.py`; one live cycle writes jobs |
| 7 | Notifications | `notify/base.py`, `notify/telegram.py`, `notify/email.py`, `notify/rss.py`, jobs_repo notify helpers, runner step-8 final | `pytest tests/test_notify.py` green |
| 8 | Dashboard (FastAPI) | `dashboard/app.py`, `deps.py`, `routes_*.py`, templates, static (Blood Orange) | `pytest tests/test_dashboard.py`; `serve` boots, routes 200 |
| 9 | Polish, hardening, docs | coverage gate, `README.md`, `docs/*`, `TROUBLESHOOTING.md`, optional extras | `ruff`/`ruff format`/`mypy src`/`pytest` all green, coverage ≥85% |

---

### Canonical contracts (read first — resolves all cross-phase names)

Every shared symbol below is authoritative. Phases reproduce/import these exactly.

**`models/job.py` — owned by Phase 1, imported by 2/3/4/5/7/8. Phase 2 must NOT redefine it.**
- `class JobStatus(str, Enum)`: `NEW="new"`, `ACTIVE="active"`, `STALE="stale"`, `DELETED="deleted"`.
- `class SalaryBucket(str, Enum)`: `PASS="pass"`, `UNKNOWN="unknown"`, `FAIL="fail"` (threshold-evaluation buckets; `PASS`=parsed & meets floor, `FAIL`=parsed & below floor → dropped before insert, `UNKNOWN`=unparseable → keep+flag).
- `class Job(BaseModel)` fields: `job_uid: str`, `source: str`, `source_native_id: str|None=None`, `title: str`, `company: str`, `location: str|None=None`, `is_remote: bool|None=None`, `url: str`, `description: str|None=None`, `salary_min: int|None=None` (normalized INR/month), `salary_max: int|None=None`, `salary_currency: str|None=None`, `salary_period: str|None=None`, `salary_raw: str|None=None`, `salary_parsed: bool=False`, `salary_bucket: SalaryBucket|None=None`, `posted_at: datetime|None=None`, `match_score: float|None=None`. No `extra="forbid"`. **Persistence-only columns** (`first_seen_at`, `last_seen_at`, `last_seen_cycle`, `status`, `applied`, `bookmarked`, `hidden`, `notes`) are DB columns set by `jobs_repo`, NOT model fields.

**`sources/base.py` — owned by Phase 3.**
- `@dataclass class SourceResult`: `source: str`, `succeeded: bool`, `jobs: list[Job]=field(default_factory=list)`, `n_fetched: int=0`, `duration_ms: int=0`, `error: str|None=None`, `sub_results: list[tuple[str,bool,int]]=field(default_factory=list)`.
- `class Source(ABC)`: `name: ClassVar[str]`; `def fetch(self, cfg: Config, clock: Clock) -> SourceResult` — **never raises**; converts every failure to `succeeded=False`.

**`pipeline/*` — owned by Phase 2 (single source of truth for dedup/salary/filter).**
- `dedup.content_hash(company: str, title: str, location: str|None) -> str` — RAW inputs, normalizes internally, returns the **full 64-char** sha256 hexdigest.
- `dedup.canonical_url(url: str) -> str`; `dedup.norm_company/norm_title/norm_location`; `dedup.fuzzy_is_dup(title_a: str, title_b: str, *, threshold: int = FUZZY_TITLE_THRESHOLD) -> bool`.
- `salary.to_inr_month(amount: float, currency: str, period: str, rates: Mapping[str,float]) -> int`; `salary.salary_bucket(job: Job, cfg: Config) -> SalaryBucket`.
- `filters.score_and_filter(job: Job, cfg: Config) -> FilterVerdict`; `@dataclass(frozen=True) FilterVerdict(keep: bool, score: float, reasons: list[str])`. **No `clock` arg, no `salary_flagged`, no int score.**
- `normalize.clean_text(value: str|None) -> str|None`; `normalize.parse_date(value: object) -> datetime|None`; `normalize.build_job(cfg: Config, **fields: object) -> Job` (convenience constructor).

**`storage/jobs_repo.py` + `runs_repo.py` — owned by Phase 1.**
- `upsert_job(conn, job: Job, run_id: int, clock: Clock) -> Literal["new","updated"]` — returns `"new"` on INSERT, `"updated"` on conflict.
- `get_jobs(conn, *, q=None, source=None, remote=None, bucket=None, status: list[str]|None=None, include_hidden=False, applied=None, bookmarked=None, sort="score", limit=50, offset=0) -> list[sqlite3.Row]`; `count_jobs(...) -> int`; `count_by_status(conn) -> dict[str,int]`; `set_user_flag(conn, job_uid: str, field: str, value: bool|str|None) -> bool`.
- `_row_to_job(row: sqlite3.Row) -> Job`; `jobs_new_in_run(conn, run_id) -> list[Job]`; `recent_active_jobs(conn, limit) -> list[Job]`.
- `start_run(conn, trigger: str, clock) -> int`; `finish_run(conn, run_id, status, *, n_sources_ok=0, n_sources_err=0, n_new=0, n_updated=0, n_expired=0, clock, error=None) -> None` (status **positional**); `record_source_run(conn, run_id, source, *, succeeded, n_fetched=None, duration_ms=None, error=None) -> None` (source **positional**); `current_run(conn) -> sqlite3.Row|None`; `recent_runs(conn, limit=20) -> list[sqlite3.Row]`; `last_successful_run(conn) -> sqlite3.Row|None` (**`status='success'` only**).

**`config/schema.py` + `config/store.py` — owned by Phase 1.** `Config` (Pydantic v2, mirrors `default_config.yaml`, all nested models default-constructible so `Config()` works). `SalaryConfig.min_remote/min_in_office: int` with `ge=0`; `on_missing: Literal["keep_and_flag","drop"]`. `ScheduleConfig.run_hour_local: int` with `ge=0, le=23`. `store.seed_from_yaml(conn)`, `store.load_effective_config(conn) -> Config`, `store.save_config(conn, cfg: Config) -> None`.

**`storage/schema.sql` column names — the hard contract.** `jobs(job_uid, source, source_native_id, title, company, location, is_remote, url, description, salary_min, salary_max, salary_currency, salary_period, salary_raw, salary_parsed, salary_bucket, match_score, posted_at, first_seen_at, last_seen_at, last_seen_cycle, status, applied, bookmarked, hidden, notes)`. `runs(run_id, started_at, finished_at, status[running|success|partial|failed], trigger[schedule|manual|startup_catchup], n_sources_ok, n_sources_err, n_new, n_updated, n_expired, error)`. `source_runs(run_id, source, succeeded, n_fetched, duration_ms, error)` PK `(run_id, source)`. `config(id CHECK(id=1), data, updated_at)`.

**`errors.py` — owned by Phase 0.** `ErrorCode(StrEnum)` + `JobAggregatorError(message, *, details=None)` with `.code/.message/.details`; subclasses `ConfigError`, `StorageError`, `SourceError`, `NotifyError`, `RunInProgressError`, `NotFoundError`.

**`scheduler.JobScheduler` — owned by Phase 6.** `__init__(connect_fn: Callable[[], object], clock: Clock)`; `.start()`, `.stop()`, `.catch_up_on_startup()`, `.trigger_now(trigger="manual") -> int|None` (None when busy), and property `.next_run_at -> datetime|None`.

---

## Phase 0 — Foundation & environment

**Goal.** Stand up an installable, lint-clean, type-clean package skeleton. Nothing fetches jobs, touches a DB, or serves HTTP. It establishes the shape: `src/` layout, console entry point, injectable primitives (`Clock`, error hierarchy, paths, logging), the tooling gate (`ruff` + `mypy --strict` + `pytest`), and the seed config/secret files.

**Exit criterion.** `conda run -n job-aggregator python -m job_aggregator --help` prints subcommand help with only the stdlib importable (heavy deps imported lazily), and `ruff check .`, `ruff format --check .`, `mypy src tests`, `pytest` are all clean.

### File manifest

| Path | Purpose |
|---|---|
| `pyproject.toml` | deps, ruff/mypy/pytest config, `job-aggregator` entry point, src-layout |
| `README.md` | referenced by `pyproject readme` |
| `.gitignore` | ignore `__pycache__`, `data/*`, `.env`, caches |
| `.env.example` | template for all secrets (never in the DB config row) |
| `config/default_config.yaml` | seed config; schema-of-record for Phase 1 Pydantic models |
| `data/.gitkeep`, `docs/.gitkeep`, `docs/ats_token_lists.md` (stub) | runtime + doc scaffolding |
| `src/job_aggregator/__init__.py` | `__version__` + layout docstring |
| `src/job_aggregator/__main__.py` | `python -m job_aggregator` → `cli.main` |
| `src/job_aggregator/cli.py` | argparse skeleton; `initdb\|run\|serve\|show-config` **stub** handlers |
| `src/job_aggregator/logging_setup.py` | `configure_logging(level)` |
| `src/job_aggregator/errors.py` | `ErrorCode` + `JobAggregatorError` hierarchy |
| `src/job_aggregator/clock.py` | `Clock` Protocol, `SystemClock`, `FixedClock` |
| `src/job_aggregator/paths.py` | env-resolved data paths + package-resource paths |
| `src/job_aggregator/{config,models,storage,sources,pipeline,notify,scheduler,dashboard}/__init__.py` | empty package markers |
| `tests/conftest.py` | shared fixtures (`fixed_clock`, `reset_logging`, env isolation) — **grown additively by later phases** |
| `tests/test_*.py` | Phase 0 unit tests |
| `tests/fixtures/.gitkeep` | dir for recorded JSON payloads |

### 1. Environment

```bash
conda create -n job-aggregator python=3.11 -y
conda run -n job-aggregator python -m pip install -e ".[dev]"
```
Phase-0 code imports **no** heavy deps at module top. `apscheduler>=3.10,<4` is a **hard pin** (4.x reorganized the API; Phase 6 uses the 3.x `BackgroundScheduler`+`CronTrigger`).

### 2. `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "job-aggregator"
version = "0.1.0"
description = "Self-hosted job/internship aggregator: multi-source fetch, dedup, stale-deletion, and a FastAPI dashboard. Tuned for remote/India systems + AI roles."
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "Bibek Jyoti Charah", email = "bibekcharah@gmail.com" }]
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
    "pydantic>=2.7",
    "pyyaml>=6.0",
    "httpx>=0.27",
    "python-jobspy>=1.1.79",
    "rapidfuzz>=3.9",
    "apscheduler>=3.10,<4",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2", "pytest-cov>=5.0", "respx>=0.21", "freezegun>=1.5",
    "ruff>=0.5", "mypy>=1.10", "types-PyYAML",
]

[project.scripts]
job-aggregator = "job_aggregator.cli:main"

[tool.setuptools]
package-dir = { "" = "src" }
[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src", "tests"]
[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "SIM", "RUF", "PL"]
ignore = ["PLR0913", "PLR2004", "PLC0415"]   # PLC0415: lazy in-function imports are INTENTIONAL
[tool.ruff.lint.per-file-ignores]
"tests/*" = ["PLR2004", "S101"]
[tool.ruff.format]
quote-style = "double"

[tool.mypy]
python_version = "3.11"
strict = true
warn_unused_ignores = true
warn_redundant_casts = true
namespace_packages = true
mypy_path = "src"
[[tool.mypy.overrides]]
module = ["jobspy.*", "apscheduler.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"
pythonpath = ["src"]
```

**Lint decisions (verified):** `PLC0415` ignored so lazy heavy imports keep `--help` stdlib-only. `errors.py` uses `enum.StrEnum` (ruff `UP042` under py311). `clock.py` uses `datetime.UTC` (ruff `UP017`). Bare `dict` is rejected by `disallow_any_generics` → use `dict[str, object]`.

### 3–7. Static seed files

- `README.md`: one short page (title, one-liner, quickstart snippet). Referenced by `pyproject`.
- `.gitignore`: `__pycache__/`, `*.py[cod]`, `*.egg-info/`, `build/`, `dist/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`, `.coverage`, `htmlcov/`, `.venv/`, `venv/`, `.env`, `data/*` (with `!data/.gitkeep`), editor/OS junk.
- `.env.example` — secrets live in `.env` (loaded by python-dotenv), **never** in the DB config row. Exact key names (Phase 3/5 read these): `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOOBLE_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`. Override knobs: `JOBAGG_DATA_DIR`, `JOBAGG_DB_PATH`, `JOBAGG_LOG_LEVEL`. Phase 9 adds `JOBAGG_DISABLE_SCHEDULER` (documented there).
- `config/default_config.yaml` — the **schema-of-record**. Frozen contract; Phase 1's Pydantic models mirror it 1:1. Reproduce verbatim:

```yaml
keywords:
  roles: [backend engineer, systems software, distributed systems, infrastructure engineer,
    platform engineer, site reliability, database engineer, ml engineer,
    machine learning engineer, ai engineer, llm engineer, reinforcement learning, mlops]
  bonus: [Go, Golang, Rust, C++, PyTorch, RAG, LLM, GRPO, LoRA, inference,
    storage engine, consistent hashing, kubernetes, kafka]
  level_required: [intern, internship, trainee, new grad, graduate engineer, junior]
  exclude: [senior, staff, principal, lead, manager, director, "5+ years", "clearance required"]
  require_level: true
locations: ["Bengaluru, India", Bangalore, India, Remote, "Remote - India", Worldwide]
remote_preferred: true
salary:
  currency: INR
  period: month
  min_remote: 30000
  min_in_office: 80000
  on_missing: keep_and_flag        # keep_and_flag | drop
  demote_in_office_if_unknown: true
  fx_rates: {USD: 83.0, EUR: 90.0, GBP: 105.0}
schedule:
  run_hour_local: 3
  hours_old: 48
  grace_days: 3
  catch_up_on_startup: true
sources:
  jobspy:
    enabled: true
    sites: [naukri, linkedin, indeed, google]
    search_terms: ["backend intern", "systems intern", "ml intern", "golang intern"]
    location: "Bengaluru, India"
    country_indeed: india
    is_remote: true
    results_wanted: 40
    hours_old: 48
    proxies: []
  unstop: {enabled: true, opportunities: [internships, jobs], search_terms: [backend, "machine learning"], max_age_days: 30}
  remoteok: {enabled: true}
  himalayas: {enabled: true, country: IN}
  jobicy: {enabled: true, job_type: internship}
  adzuna: {enabled: true, country: in}
  jooble: {enabled: true}
  remotive: {enabled: false}
  ats:
    greenhouse: {enabled: true, tokens: []}
    lever: {enabled: true, slugs: []}
    ashby: {enabled: true, orgs: []}
    smartrecruiters: {enabled: true, company_ids: []}
notify:
  on: new_only
  telegram: {enabled: false}
  email: {enabled: false, smtp_host: localhost, smtp_port: 25, to: ""}
  rss: {enabled: true, path: "data/feed.xml", max_items: 100}
```
JobSpy slugs must be exact (`naukri`, `linkedin`, `indeed`, `google`); `country_indeed: india` required for Indeed/Glassdoor (LinkedIn ignores it). `docs/ats_token_lists.md` is a one-line stub (populated in Phase 9).

### 8–9. `__init__.py` / `__main__.py`

`__init__.py`: `__version__ = "0.1.0"` (must equal `pyproject` version; a test asserts via `importlib.metadata`) + a package-layout docstring. `__main__.py`:
```python
from job_aggregator.cli import main
if __name__ == "__main__":
    raise SystemExit(main())
```

### 10. `cli.py` (Phase 0 = stubs)

Stable command surface: global `--version`, `--db`, `--log-level`; subcommands `initdb | run | serve | show-config`. **Do not import Phase 1+ modules or heavy deps.** Public signatures (stable across all phases): `cmd_initdb/cmd_run/cmd_serve/cmd_show_config(args) -> int`, `build_parser() -> ArgumentParser`, `main(argv: list[str]|None=None) -> int`.

```python
from __future__ import annotations
import argparse
from job_aggregator import __version__
from job_aggregator.logging_setup import configure_logging

def cmd_initdb(args: argparse.Namespace) -> int:
    configure_logging(args.log_level); print("initdb: not implemented until Phase 1"); return 0
def cmd_run(args: argparse.Namespace) -> int:
    configure_logging(args.log_level); print("run: not implemented until Phase 5"); return 0
def cmd_serve(args: argparse.Namespace) -> int:
    configure_logging(args.log_level); print("serve: not implemented until Phase 8"); return 0
def cmd_show_config(args: argparse.Namespace) -> int:
    configure_logging(args.log_level); print("show-config: not implemented until Phase 1"); return 0

def build_parser() -> argparse.ArgumentParser:
    from job_aggregator.paths import default_db_path
    parser = argparse.ArgumentParser(prog="job-aggregator",
        description="Self-hosted multi-source job/internship aggregator.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", default=str(default_db_path()), help="path to the SQLite DB")
    parser.add_argument("--log-level", default="INFO", help="logging level (default: INFO)")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("initdb", help="create and seed the database"); p.set_defaults(func=cmd_initdb)
    p = sub.add_parser("run", help="execute one aggregation cycle now"); p.set_defaults(func=cmd_run)
    p = sub.add_parser("serve", help="launch the dashboard web app")
    p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true"); p.set_defaults(func=cmd_serve)
    p = sub.add_parser("show-config", help="print the effective config"); p.set_defaults(func=cmd_show_config)
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
```
`add_subparsers(required=True)` → no subcommand raises `SystemExit(2)`. `--version`/`--help` print and `SystemExit(0)` before dispatch (stdlib-only). Later phases replace bodies with **lazy** heavy imports inside handlers.

### 11. `logging_setup.py`

`configure_logging(level: str|None=None) -> None`. Precedence: arg > `JOBAGG_LOG_LEVEL` env > `"INFO"`, uppercased; `logging.basicConfig(...)`; if not DEBUG, pin `httpx`/`apscheduler` loggers to WARNING. Constants `_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s: %(message)s"`, `_DATEFMT = "%H:%M:%S"`. `basicConfig` is a no-op if root already has handlers → tests use a `reset_logging` fixture.

### 12. `errors.py`

```python
from __future__ import annotations
from enum import StrEnum

class ErrorCode(StrEnum):
    CONFIG_INVALID = "config_invalid"
    STORAGE_ERROR = "storage_error"
    SOURCE_FETCH_FAILED = "source_fetch_failed"
    SOURCE_PARSE_FAILED = "source_parse_failed"
    NOTIFY_FAILED = "notify_failed"
    RUN_IN_PROGRESS = "run_in_progress"
    NOT_FOUND = "not_found"
    INTERNAL = "internal"

class JobAggregatorError(Exception):
    code: ErrorCode = ErrorCode.INTERNAL
    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] = details or {}

class ConfigError(JobAggregatorError): code = ErrorCode.CONFIG_INVALID
class StorageError(JobAggregatorError): code = ErrorCode.STORAGE_ERROR
class SourceError(JobAggregatorError): code = ErrorCode.SOURCE_FETCH_FAILED
class NotifyError(JobAggregatorError): code = ErrorCode.NOTIFY_FAILED
class RunInProgressError(JobAggregatorError): code = ErrorCode.RUN_IN_PROGRESS
class NotFoundError(JobAggregatorError): code = ErrorCode.NOT_FOUND
```
`StrEnum` (not `(str, Enum)`), `dict[str, object]` (not bare `dict`). `NotFoundError` is shipped here (Phase 8 depends on it). Sources must never let these escape `fetch()`.

### 13. `clock.py`

```python
from __future__ import annotations
from datetime import UTC, datetime, timedelta
from typing import Protocol

class Clock(Protocol):
    def now(self) -> datetime: ...

class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

class FixedClock:
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        self._instant = instant
    def now(self) -> datetime:
        return self._instant
    def advance(self, *, seconds: float = 0, days: float = 0) -> None:
        self._instant = self._instant + timedelta(seconds=seconds, days=days)
```
`from datetime import UTC` + `datetime.now(UTC)` (not `timezone.utc`). `timedelta` at module top. `FixedClock.now()` is stable until `advance()`.

### 14. `paths.py`

Module constants: `PACKAGE_DIR = Path(__file__).resolve().parent`; `SCHEMA_SQL_PATH = PACKAGE_DIR/"storage"/"schema.sql"`; `TEMPLATES_DIR = PACKAGE_DIR/"dashboard"/"templates"`; `STATIC_DIR = PACKAGE_DIR/"dashboard"/"static"`; `REPO_ROOT = PACKAGE_DIR.parent.parent`; `DEFAULT_CONFIG_YAML = REPO_ROOT/"config"/"default_config.yaml"`. Functions (return absolute `Path`, resolve env at call time): `data_dir()` = `Path(os.environ.get("JOBAGG_DATA_DIR","data")).resolve()`; `default_db_path()` = `JOBAGG_DB_PATH` if set else `data_dir()/"jobs.db"`; `feed_path()` = `data_dir()/"feed.xml"`; `log_dir()` = `data_dir()/"logs"`.

### 15–16. Subpackage markers + tests

Empty `__init__.py` in each subpackage; `tests/fixtures/.gitkeep`.

**`tests/conftest.py` (Phase 0 base — later phases ADD to this file, never replace it).** Canonical instant used project-wide: `FIXED_INSTANT = datetime(2026, 7, 15, 3, 0, 0, tzinfo=UTC)`. Fixtures: `fixed_clock` → `FixedClock(FIXED_INSTANT)`; `reset_logging` → clears root handlers + resets `httpx`/`apscheduler` to `NOTSET` around each test.

Phase-0 tests:
- `test_version.py`: `__version__ == "0.1.0"`; `importlib.metadata.version("job-aggregator") == __version__`.
- `test_errors.py`: table over every `ErrorCode` member → string; base defaults (`code is INTERNAL`, `message`, `details == {}`, `str(e)=="boom"`); details carried + isolated per-instance; subclass codes table incl. `NotFoundError → NOT_FOUND`.
- `test_clock.py`: fixed returns instant; `now()` stable; naive coerced to UTC; `advance` table; `SystemClock().now().tzinfo is UTC`; both satisfy `Clock`.
- `test_paths.py` (monkeypatch): default/env-override for `data_dir`/`default_db_path`/`feed_path`/`log_dir`; resource paths under `PACKAGE_DIR`; `DEFAULT_CONFIG_YAML.parts[-2:] == ("config","default_config.yaml")`.
- `test_logging_setup.py` (reset_logging): default INFO; arg overrides env; env used when no arg (lowercase→upper); noisy loggers quieted when not DEBUG; untouched at DEBUG.
- `test_cli.py` (capsys): `--help`/`--version` exit 0; no subcommand exit 2; unknown exit 2; each subcommand dispatches to stub (exit 0, "not implemented"); `build_parser` wires each `func`.

### Acceptance check

```bash
conda create -n job-aggregator python=3.11 -y
conda run -n job-aggregator python -m pip install -e ".[dev]"
conda run -n job-aggregator python -m job_aggregator --help      # usage + {initdb,run,serve,show-config}
conda run -n job-aggregator job-aggregator --version             # -> "job-aggregator 0.1.0"
conda run -n job-aggregator ruff check .                         # All checks passed!
conda run -n job-aggregator ruff format --check .                # already formatted
conda run -n job-aggregator mypy src tests                       # Success
conda run -n job-aggregator pytest                               # all Phase-0 tests pass
conda run -n job-aggregator python -m job_aggregator initdb      # "initdb: not implemented until Phase 1", exit 0
conda run -n job-aggregator python -m job_aggregator; echo $?    # argparse error, exit 2
```

---

## Phase 1 — Storage core (SQLite)

Builds the persistence layer plus the shared domain/config models everything else depends on: `models/job.py`, `config/schema.py`, `config/store.py`, `storage/schema.sql`, `storage/db.py`, `storage/jobs_repo.py`, `storage/runs_repo.py`. Plain parameterized SQL, no ORM.

**Guarantees at exit:** `init_db` creates all 4 tables + indexes; `upsert_job` is idempotent, returns `"new"|"updated"`, preserves user flags across re-scrapes; `get_jobs`/`count_jobs`/`count_by_status`/`_row_to_job`/`jobs_new_in_run`/`recent_active_jobs` answer downstream reads; run-bookkeeping records run + per-source outcomes. `pytest tests/test_jobs_repo.py` green.

### Two invariants (state in module docstrings)

1. **All bookkeeping timestamps are UTC ISO-8601 from `clock.now().isoformat()`** (`first_seen_at`, `last_seen_at`, `started_at`, `finished_at`), so lexicographic == chronological. `posted_at` is display-only (may carry source tz).
2. **One `sqlite3.Connection` per thread** (`check_same_thread=True`). The scheduler opens its own connection inside each run; the dashboard opens one per request. WAL makes concurrent readers + single writer safe across separate connections.

### `models/job.py`

Reproduce the Canonical-contract `Job`/`JobStatus`/`SalaryBucket` verbatim (Phase 2 imports, never redefines):

```python
from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel

class JobStatus(str, Enum):
    NEW = "new"; ACTIVE = "active"; STALE = "stale"; DELETED = "deleted"

class SalaryBucket(str, Enum):
    PASS = "pass"; UNKNOWN = "unknown"; FAIL = "fail"

class Job(BaseModel):
    job_uid: str
    source: str
    source_native_id: str | None = None
    title: str
    company: str
    location: str | None = None
    is_remote: bool | None = None
    url: str
    description: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_raw: str | None = None
    salary_parsed: bool = False
    salary_bucket: SalaryBucket | None = None
    posted_at: datetime | None = None
    match_score: float | None = None
```

### `config/schema.py`

Pydantic v2 models mirroring `default_config.yaml`. All nested models default-constructible so `Config()` works and matches the seed. Key validation bounds are load-bearing (Phase 8 config-save 422 test depends on them):

```python
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

class Keywords(BaseModel):
    roles: list[str] = []
    bonus: list[str] = []
    level_required: list[str] = []
    exclude: list[str] = []
    require_level: bool = True

class SalaryConfig(BaseModel):
    currency: str = "INR"
    period: str = "month"
    min_remote: int = Field(default=30000, ge=0)          # ge=0 -> negatives rejected (Phase 8 test)
    min_in_office: int = Field(default=80000, ge=0)
    on_missing: Literal["keep_and_flag", "drop"] = "keep_and_flag"
    demote_in_office_if_unknown: bool = True
    fx_rates: dict[str, float] = {"USD": 83.0, "EUR": 90.0, "GBP": 105.0}

class ScheduleConfig(BaseModel):
    run_hour_local: int = Field(default=3, ge=0, le=23)
    hours_old: int = 48
    grace_days: int = 3
    catch_up_on_startup: bool = True

class JobSpyConfig(BaseModel):
    enabled: bool = True
    sites: list[str] = ["naukri", "linkedin", "indeed", "google"]
    search_terms: list[str] = ["backend intern", "systems intern", "ml intern", "golang intern"]
    location: str = "Bengaluru, India"
    country_indeed: str = "india"
    is_remote: bool = True
    results_wanted: int = 40
    hours_old: int = 48
    proxies: list[str] = []

# ... UnstopConfig, RemoteOkConfig, HimalayasConfig(country="IN"), JobicyConfig(job_type="internship"),
#     AdzunaConfig(country="in"), JoobleConfig, RemotiveConfig(enabled=False),
#     each ATS model {enabled, tokens/slugs/orgs/company_ids}, AtsConfig, SourcesConfig,
#     TelegramNotify/EmailNotify(smtp_host/smtp_port/to)/RssNotify(path,max_items), NotifyConfig(on="new_only")
# each with default_factory so Config() reproduces default_config.yaml exactly.

class Config(BaseModel):
    keywords: Keywords = Keywords()
    locations: list[str] = ["Bengaluru, India", "Bangalore", "India", "Remote", "Remote - India", "Worldwide"]
    remote_preferred: bool = True
    salary: SalaryConfig = SalaryConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    sources: "SourcesConfig" = ...          # default_factory
    notify: "NotifyConfig" = ...            # default_factory
```
Round-trip contract: `Config.model_validate(yaml.safe_load(DEFAULT_CONFIG_YAML.read_text()))` succeeds, and `Config()` == the seed.

### `config/store.py`

- `seed_from_yaml(conn, yaml_path: Path | None = None) -> None` — idempotent: writes the single `config` row (id=1) only if absent, from `DEFAULT_CONFIG_YAML` (or override). Serializes `Config.model_dump(mode="json")`.
- `load_effective_config(conn) -> Config` — reads the row, `Config.model_validate(json.loads(data))`; raises `ConfigError` if the row is missing/invalid.
- `save_config(conn, cfg: Config) -> None` — `UPDATE config SET data=?, updated_at=? WHERE id=1` (writer for the dashboard, which is the source of truth).

### `storage/schema.sql` (copy verbatim)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
  run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  status        TEXT NOT NULL,           -- 'running'|'success'|'partial'|'failed'
  trigger       TEXT NOT NULL,           -- 'schedule'|'manual'|'startup_catchup'
  n_sources_ok  INTEGER DEFAULT 0,
  n_sources_err INTEGER DEFAULT 0,
  n_new         INTEGER DEFAULT 0,
  n_updated     INTEGER DEFAULT 0,
  n_expired     INTEGER DEFAULT 0,
  error         TEXT
);
CREATE TABLE IF NOT EXISTS source_runs (
  run_id      INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  source      TEXT NOT NULL,
  succeeded   INTEGER NOT NULL,
  n_fetched   INTEGER,
  duration_ms INTEGER,
  error       TEXT,
  PRIMARY KEY (run_id, source)
);
CREATE TABLE IF NOT EXISTS jobs (
  job_uid          TEXT PRIMARY KEY,     -- sha256(norm(company)|norm(title)|norm(location)), full 64 hex
  source           TEXT NOT NULL,
  source_native_id TEXT,
  title            TEXT NOT NULL,
  company          TEXT NOT NULL,
  location         TEXT,
  is_remote        INTEGER,              -- 0|1|NULL
  url              TEXT NOT NULL,
  description      TEXT,
  salary_min       INTEGER,              -- normalized INR/month
  salary_max       INTEGER,
  salary_currency  TEXT,
  salary_period    TEXT,
  salary_raw       TEXT,
  salary_parsed    INTEGER NOT NULL DEFAULT 0,
  salary_bucket    TEXT,                 -- 'pass'|'unknown'|'fail'
  match_score      REAL,
  posted_at        TEXT,
  first_seen_at    TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  last_seen_cycle  INTEGER NOT NULL REFERENCES runs(run_id),
  status           TEXT NOT NULL,        -- JobStatus
  applied          INTEGER NOT NULL DEFAULT 0,
  bookmarked       INTEGER NOT NULL DEFAULT 0,
  hidden           INTEGER NOT NULL DEFAULT 0,
  notes            TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status      ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_source      ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_last_cycle  ON jobs(last_seen_cycle);
CREATE INDEX IF NOT EXISTS idx_jobs_score       ON jobs(match_score);
CREATE TABLE IF NOT EXISTS config (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  data       TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```
`foreign_keys` is per-connection (not persisted) → `connect()` must re-issue it.

### `storage/db.py`

Constants: `BUSY_TIMEOUT_MS = 5000`, `SCHEMA_VERSION = 1`. `connect(db_path) -> sqlite3.Connection`: mkdir parent (unless `:memory:`); `sqlite3.connect(str(db_path))` (default `check_same_thread=True`); `row_factory = sqlite3.Row`; pragmas `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout={BUSY_TIMEOUT_MS}`. `init_db(conn)`: `executescript(SCHEMA_SQL_PATH.read_text())`; `commit()`; `migrate(conn)`. `migrate(conn)`: forward-only on `PRAGMA user_version`; v0→v1 just stamps the version. Assign `fetchone()`/`fetchall()` to annotated locals before returning (mypy `warn_return_any`).

### `storage/jobs_repo.py`

Constants/types: `UpsertOutcome = Literal["new", "updated"]`; `_USER_FLAG_FIELDS = frozenset({"applied","bookmarked","hidden","notes"})`; `_BOOL_FLAG_FIELDS = frozenset({"applied","bookmarked","hidden"})`; `DEFAULT_PAGE_LIMIT = 50`; `MAX_PAGE_LIMIT = 200`; `_SORT_SQL = {"score":"match_score DESC, posted_at DESC","posted":"posted_at DESC","company":"company COLLATE NOCASE ASC, title COLLATE NOCASE ASC"}`.

**`upsert_job(conn, job, run_id, clock) -> UpsertOutcome`** — existence probe then `ON CONFLICT DO UPDATE`; **returns `"new"` on INSERT, `"updated"` on conflict** (Phase 5 counts `n_new` via `== "new"`):

```python
_UPSERT_SQL = """
INSERT INTO jobs (
    job_uid, source, source_native_id, title, company, location, is_remote, url,
    description, salary_min, salary_max, salary_currency, salary_period, salary_raw,
    salary_parsed, salary_bucket, match_score, posted_at,
    first_seen_at, last_seen_at, last_seen_cycle, status
) VALUES (
    :job_uid, :source, :source_native_id, :title, :company, :location, :is_remote, :url,
    :description, :salary_min, :salary_max, :salary_currency, :salary_period, :salary_raw,
    :salary_parsed, :salary_bucket, :match_score, :posted_at,
    :now, :now, :run_id, :status_new
)
ON CONFLICT(job_uid) DO UPDATE SET
    last_seen_at=excluded.last_seen_at, last_seen_cycle=excluded.last_seen_cycle,
    status=:status_active,
    is_remote=excluded.is_remote, description=excluded.description,
    salary_min=excluded.salary_min, salary_max=excluded.salary_max,
    salary_currency=excluded.salary_currency, salary_period=excluded.salary_period,
    salary_raw=excluded.salary_raw, salary_parsed=excluded.salary_parsed,
    salary_bucket=excluded.salary_bucket, match_score=excluded.match_score,
    posted_at=excluded.posted_at
    -- NOT updated: source, source_native_id, url, first_seen_at (first-seen provenance),
    --             applied, bookmarked, hidden, notes (USER FLAGS MUST SURVIVE UPSERTS)
"""
```
Body: probe `existed`; build params (`is_remote` → 0/1/None; `salary_parsed` → int; `salary_bucket.value` or None; `posted_at.isoformat()` or None; `status_new=JobStatus.NEW.value`, `status_active=JobStatus.ACTIVE.value`); execute; commit; `return "updated" if existed else "new"`.

**`get_jobs`** — frozen signature (status is a list):
```python
def get_jobs(conn, *, q=None, source=None, remote=None, bucket=None,
             status: list[str] | None = None, include_hidden=False,
             applied=None, bookmarked=None, sort="score",
             limit=DEFAULT_PAGE_LIMIT, offset=0) -> list[sqlite3.Row]: ...
```
Shared `_build_where(...)`: `q` → `(title LIKE :q OR company LIKE :q)`; equality binds for `source`/`bucket`; `remote`/`applied`/`bookmarked` → `int(...)`; `status` list → `status IN (...)`; when `status is None` → `status != 'deleted'`; when `not include_hidden` → `hidden = 0`. `order_by = _SORT_SQL.get(sort, _SORT_SQL["score"])`; `limit = max(1, min(limit, MAX_PAGE_LIMIT))`. `count_jobs(conn, *, ...same filters...) -> int`. `count_by_status(conn) -> dict[str,int]` via `GROUP BY status`.

**`set_user_flag(conn, job_uid, field, value) -> bool`** — whitelist `field` (else `ValueError`); bool flags → `int(bool(value))`, notes → `str|None`; `UPDATE jobs SET {field}=? WHERE job_uid=?`; commit; return `cur.rowcount > 0` (unknown uid → False; the frozen stub's return is tightened to `bool` deliberately). Only `{order_by}` and `{field}` are interpolated, both whitelisted.

**Notify/read helpers (frozen deliverables for Phase 7):**
```python
def _row_to_job(row: sqlite3.Row) -> Job:
    """Map a jobs row to a Job (is_remote int->bool|None, salary_parsed int->bool,
    salary_bucket str->SalaryBucket|None, posted_at ISO str->datetime|None)."""
def jobs_new_in_run(conn, run_id: int) -> list[Job]:
    rows = conn.execute("SELECT * FROM jobs WHERE status='new' AND last_seen_cycle=? "
                        "ORDER BY match_score DESC", (run_id,)).fetchall()
    return [_row_to_job(r) for r in rows]
def recent_active_jobs(conn, limit: int) -> list[Job]:
    rows = conn.execute("SELECT * FROM jobs WHERE status IN ('new','active') AND hidden=0 "
                        "ORDER BY COALESCE(posted_at,'') DESC, last_seen_at DESC LIMIT ?",
                        (limit,)).fetchall()
    return [_row_to_job(r) for r in rows]
```
`jobs_new_in_run` is exactly "notify once, never again": a job stuck `'new'` from an earlier run (its source failed) keeps its older `last_seen_cycle` and is excluded. `mark_stale`/`mark_deleted` are **not** here — Phase 5 owns those UPDATEs inline.

### `storage/runs_repo.py`

Constants: `_VALID_TRIGGERS = frozenset({"schedule","manual","startup_catchup"})`, `_VALID_RUN_STATUSES = frozenset({"running","success","partial","failed"})`, `RECENT_RUNS_DEFAULT_LIMIT = 20`.

Signatures (source/status **positional**, per Canonical contracts):
```python
def start_run(conn, trigger: str, clock) -> int          # INSERT status='running', return lastrowid
def finish_run(conn, run_id: int, status: str, *, n_sources_ok=0, n_sources_err=0,
               n_new=0, n_updated=0, n_expired=0, clock, error=None) -> None
def record_source_run(conn, run_id: int, source: str, *, succeeded: bool,
                      n_fetched=None, duration_ms=None, error=None) -> None
def current_run(conn) -> sqlite3.Row | None              # status='running' ORDER BY run_id DESC LIMIT 1
def recent_runs(conn, limit=RECENT_RUNS_DEFAULT_LIMIT) -> list[sqlite3.Row]
def last_successful_run(conn) -> sqlite3.Row | None       # status='success' ONLY ORDER BY run_id DESC LIMIT 1
```
`start_run`/`finish_run` validate trigger/status (`ValueError` otherwise). `record_source_run` uses `ON CONFLICT(run_id, source) DO UPDATE` (idempotent). `last_successful_run` matches **`status='success'` only** (a partial run means a source failed → catch-up should re-attempt; Phase 6's contract is aligned to this strict definition).

### Tests (`tests/conftest.py` additions + `tests/test_jobs_repo.py`)

`conftest.py` (ADD, don't replace Phase 0): `clock()`→`FixedClock(FIXED_INSTANT)`; `conn(tmp_path)` → real-file DB (`connect` + `init_db`, `yield`, close); `run_id(conn, clock)` → `start_run(conn,"manual",clock)`; `make_job()` builder (frozen fields, unique `job_uid`).

`test_jobs_repo.py` (24 tests):
1. all 4 tables created; 2. `init_db` idempotent; 3. pragmas (`foreign_keys=1`, `journal_mode='wal'`, Row factory); 4. unknown `run_id` → `IntegrityError` (FK live); 5. insert → outcome `"new"`, `status="new"`, `first_seen_at==last_seen_at`, flags default; 6. re-upsert → `"updated"`, one row, `status="active"`, `last_seen_cycle` bumped, `first_seen_at` unchanged; 7. user flags preserved across upsert; 8. first-seen provenance (source/url) preserved, mutable fields refreshed; 9. `get_jobs` filters (parametrized: source/remote/bucket/q/none); 10. default excludes deleted+hidden, `status=["deleted"]`/`include_hidden=True` include; 11. sort + `limit/offset` pagination + `count_jobs`; 12. score sort NULLs last; 13. `count_by_status`; 14. `set_user_flag` rejects unknown field (`ValueError`); 15. `set_user_flag` unknown uid → `False`; 16. clear notes to None; 17. `start_run` sets running + `current_run`; 18. bad trigger `ValueError`; 19. `finish_run` records counts + clears current; 20. bad status `ValueError`; 21. `record_source_run` persists `succeeded=0`/`error`; 22. `record_source_run` upsert (one row, latest wins); 23. `recent_runs` desc; 24. `last_successful_run` returns only `success` (a later `partial` is NOT returned — pins strict definition). Plus: `test_row_to_job_roundtrip`, `test_jobs_new_in_run_only_this_run`, `test_recent_active_excludes_hidden_deleted`.

### Acceptance check

```bash
cd "/home/SammyUrfen/Codes/job aggregator"
pip install -e ".[dev]"
python - <<'PY'
from job_aggregator.storage.db import connect, init_db
c = connect("data/jobs.db"); init_db(c)
names = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
assert {"jobs","runs","source_runs","config"} <= names, names
print("initdb OK:", sorted(names))
PY
pytest tests/test_jobs_repo.py -q
ruff check src/job_aggregator/storage src/job_aggregator/models src/job_aggregator/config tests/test_jobs_repo.py tests/conftest.py
ruff format --check src/job_aggregator/storage src/job_aggregator/models src/job_aggregator/config
mypy src/job_aggregator/storage src/job_aggregator/models src/job_aggregator/config
```
Gates scoped to `storage/`+`models/`+`config/` (`mypy src` still fails on unresolved Phase 2–8 imports; the full-tree gate is Phase 9).

---

## Phase 2 — Domain pipeline (pure)

The correctness core: four **pure** modules under `pipeline/`. No network, no DB, no `datetime.now()`. **This phase does NOT define `Job`/`JobStatus`/`SalaryBucket`/`RawJob`** — those are owned by Phase 1 (`job_aggregator.models.job`); import them. Everything here conforms to the frozen signatures in Canonical contracts.

Conventions: `from __future__ import annotations`; doc-comments explain WHY; named constants; `ruff` + `mypy --strict` clean; table-driven tests. Reference `Config` under `TYPE_CHECKING` in `filters.py` to avoid an import cycle.

### `pipeline/dedup.py`

Identity: `job_uid = sha256(norm_company · norm_title · norm_location)`, URL-independent, **full 64-char digest** (frozen — Phase 4 asserts `len==64`). Takes RAW inputs and normalizes internally (never pre-normalized, never truncated).

```python
import hashlib, re, unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from rapidfuzz import fuzz

_COMPANY_SUFFIXES = frozenset({"inc","incorporated","llc","llp","ltd","limited","pvt","private",
  "plc","corp","corporation","co","gmbh","ag","sa","srl","bv","group","holdings",
  "technologies","technology","labs","software","systems","solutions","india","global","worldwide"})
_LOCATION_ALIASES = {"bangalore":"bengaluru","blr":"bengaluru","anywhere":"remote",
  "worldwide":"remote","wfh":"remote","distributed":"remote"}
_TRACKING_PARAMS = frozenset({"gclid","fbclid","mc_cid","mc_eid","ref","referrer","source","src",
  "trk","trackingid","refid","originalsubdomain","position","pagenum","utm_id"})
FUZZY_TITLE_THRESHOLD = 88
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

def _ascii_fold(t): return unicodedata.normalize("NFKD", t).encode("ascii","ignore").decode("ascii")
def _tokens(t): return _NON_ALNUM.sub(" ", _ascii_fold(t).lower()).split()

def norm_company(name: str) -> str:
    toks = _tokens(name); stripped = list(toks)
    while stripped and stripped[-1] in _COMPANY_SUFFIXES: stripped.pop()
    return " ".join(stripped) if stripped else " ".join(toks)   # all-suffix guard
def norm_title(title: str) -> str: return " ".join(_tokens(title))
def norm_location(loc: str | None) -> str:
    if not loc: return ""
    return " ".join(_LOCATION_ALIASES.get(t, t) for t in _tokens(loc))

def content_hash(company: str, title: str, location: str | None) -> str:
    key = f"{norm_company(company)}|{norm_title(title)}|{norm_location(location)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()        # full 64 hex

def canonical_url(url: str) -> str:
    url = (url or "").strip()
    if not url: return ""
    p = urlsplit(url)
    if not p.scheme and not p.netloc: return url
    kept = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=False)
            if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")]
    kept.sort()
    path = p.path.rstrip("/") if len(p.path) > 1 else p.path       # preserve path case
    return urlunsplit(((p.scheme or "https").lower(), p.netloc.lower(), path, urlencode(kept), ""))

def fuzzy_is_dup(title_a: str, title_b: str, *, threshold: int = FUZZY_TITLE_THRESHOLD) -> bool:
    """Near-duplicate title check (rapidfuzz token_sort_ratio). Second-layer only; runtime
    dedup is exact-hash on job_uid (Phase 5)."""
    return fuzz.token_sort_ratio(title_a, title_b) >= threshold
```

### `pipeline/salary.py`

Frozen public API: `to_inr_month(amount, currency, period, rates) -> int` and `salary_bucket(job, cfg) -> SalaryBucket`. Internal parse helpers (period/currency normalization, best-effort text parse) support `normalize.build_job` but are not the public surface.

```python
from collections.abc import Mapping
from job_aggregator.models.job import Job, SalaryBucket

_PERIOD_TO_MONTH = {"month":1.0, "year":1/12, "week":52/12, "day":260/12, "hour":2080/12}

def representative_inr(mn: int | None, mx: int | None) -> int | None:
    if mn is not None and mx is not None: return (mn + mx) // 2
    return mx if mx is not None else mn

def to_inr_month(amount: float, currency: str, period: str, rates: Mapping[str, float]) -> int:
    """Convert one amount to INR/month. Caller guarantees currency is 'INR' or present in rates,
    and period is one of month/year/week/day/hour (unknown period -> assumed month)."""
    cur = currency.upper()
    fx = 1.0 if cur == "INR" else float(rates[cur])
    pmonth = _PERIOD_TO_MONTH.get(period, 1.0)
    return int(round(amount * fx * pmonth))

def salary_bucket(job: Job, cfg: Config) -> SalaryBucket:
    """PASS if parsed INR/month meets the applicable floor, FAIL if below, UNKNOWN if unparsed."""
    if not job.salary_parsed or (job.salary_min is None and job.salary_max is None):
        return SalaryBucket.UNKNOWN
    rep = representative_inr(job.salary_min, job.salary_max)
    if rep is None: return SalaryBucket.UNKNOWN
    floor = cfg.salary.min_remote if job.is_remote else cfg.salary.min_in_office
    return SalaryBucket.PASS if rep >= floor else SalaryBucket.FAIL
```
`to_inr_month(600000, "INR", "year", rates) == 50000`; `to_inr_month(60000, "USD", "year", rates) == 415000` (60000·83/12). Unknown currency is the caller's responsibility to guard (Phase 4 checks `ccy in known_ccy`).

### `pipeline/filters.py`

Frozen: `score_and_filter(job, cfg) -> FilterVerdict(keep, score: float, reasons: list[str])`. **No clock** (pure; recency scoring is dropped — a pure function may not read wall-clock time). Reads `job.salary_bucket` (set uniformly by the runner before filtering).

```python
from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING
from job_aggregator.models.job import Job, SalaryBucket
from job_aggregator.pipeline.dedup import norm_location
if TYPE_CHECKING:
    from job_aggregator.config.schema import Config

ROLE_TITLE_WEIGHT = 10; ROLE_DESC_WEIGHT = 3; BONUS_WEIGHT = 4
REMOTE_BONUS = 5; SALARY_PASS_BONUS = 6
IN_OFFICE_UNKNOWN_SALARY_PENALTY = 5
ROLE_MATCH_CAP = 3; BONUS_MATCH_CAP = 5

@dataclass(frozen=True)
class FilterVerdict:
    keep: bool
    score: float
    reasons: list[str] = field(default_factory=list)

def _matches(text_lc: str, keyword: str) -> bool:
    kw = keyword.strip().lower()
    if not kw: return False
    return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text_lc) is not None

def _location_ok(job: Job, cfg: "Config") -> bool:
    if job.is_remote and cfg.remote_preferred: return True
    jl = norm_location(job.location)
    if not jl: return True
    for loc in cfg.locations:
        nl = norm_location(loc)
        if not nl or nl == "remote": continue
        if nl in jl or jl in nl or set(nl.split()) & set(jl.split()): return True
    return False

def score_and_filter(job: Job, cfg: "Config") -> FilterVerdict:
    title_lc = job.title.lower(); desc_lc = (job.description or "").lower()
    hay = f"{title_lc}\n{desc_lc}"; kw = cfg.keywords
    for ex in kw.exclude:                                    # 1. hard excludes (title only)
        if _matches(title_lc, ex): return FilterVerdict(False, 0.0, [f"excluded:{ex}"])
    if kw.require_level and not any(_matches(hay, lv) for lv in kw.level_required):
        return FilterVerdict(False, 0.0, ["no_level"])       # 2. level
    title_roles = [r for r in kw.roles if _matches(title_lc, r)]
    desc_roles = [r for r in kw.roles if r not in title_roles and _matches(desc_lc, r)]
    if not title_roles and not desc_roles:
        return FilterVerdict(False, 0.0, ["no_role_match"])  # 3. role
    if not _location_ok(job, cfg):
        return FilterVerdict(False, 0.0, ["location_mismatch"])  # 4. location
    bucket = job.salary_bucket                                # 5. salary gate (reads bucket)
    salary_flagged = False
    if bucket is SalaryBucket.FAIL:
        return FilterVerdict(False, 0.0, ["salary_below_floor"])
    if bucket is None or bucket is SalaryBucket.UNKNOWN:
        if cfg.salary.on_missing == "drop":
            return FilterVerdict(False, 0.0, ["salary_missing"])
        salary_flagged = True
    score = ROLE_TITLE_WEIGHT * min(len(title_roles), ROLE_MATCH_CAP)          # 6. score
    score += ROLE_DESC_WEIGHT * min(len(desc_roles), ROLE_MATCH_CAP)
    score += BONUS_WEIGHT * min(sum(1 for b in kw.bonus if _matches(hay, b)), BONUS_MATCH_CAP)
    reasons: list[str] = []
    if job.is_remote and cfg.remote_preferred: score += REMOTE_BONUS
    if bucket is SalaryBucket.PASS: score += SALARY_PASS_BONUS
    if salary_flagged and not job.is_remote and cfg.salary.demote_in_office_if_unknown:
        score -= IN_OFFICE_UNKNOWN_SALARY_PENALTY; reasons.append("salary_unknown_flagged")
    reasons.extend(f"role_title:{r}" for r in title_roles)
    reasons.extend(f"role_desc:{r}" for r in desc_roles)
    return FilterVerdict(True, float(max(score, 0)), reasons)
```

### `pipeline/normalize.py`

Shared cleaners + `build_job` convenience (frozen sigs; `parse_date` takes no clock — sources that need relative-date handling parse dates themselves via epoch/ISO helpers in Phase 3's `base.py`).

```python
def clean_text(value: str | None) -> str | None:
    """NFKC-normalize, strip zero-width, collapse whitespace, trim. None/empty -> None."""
def parse_date(value: object) -> datetime | None:
    """datetime/date passthrough (to UTC); epoch seconds OR ms (auto-detect >=1e11 = ms);
    ISO-8601 incl. trailing Z; digit-string epochs; else None. Never raises."""
def build_job(cfg: Config, **fields: object) -> Job:
    """Convenience constructor: clean title/company/location, canonical_url(url), compute
    content_hash uid, convert salary via to_inr_month, set salary_bucket, return Job(status
    handled by storage). Optional helper — Tier-B/C adapters use base.to_job, Tier A builds Job
    directly, so build_job is not on the hot path."""
```

### Tests (`tests/conftest.py` additions + 4 modules)

`conftest` adds: `fx_rates` → `{"USD":83.0,"EUR":90.0,"GBP":105.0}`; `make_raw`/`make_job` builders (frozen `Job` fields); `cfg` → `Config.model_validate(yaml.safe_load(DEFAULT_CONFIG_YAML.read_text()))`.

- `test_dedup.py`: `norm_company` (`"Stripe, Inc."→"stripe"`, all-suffix guard); `norm_title`; `norm_location` (`"Bangalore"→"bengaluru"`, `None→""`, `"Worldwide"→"remote"`); `canonical_url` (utm dropped, `gh_jid` kept, host lowercased, path case preserved, `""→""`); `content_hash` (**`len==64`**, hex, deterministic, location-sensitive); `fuzzy_is_dup("backend engineer intern","backend engineer internship")` True, vs `"graphic designer intern"` False.
- `test_salary.py`: `to_inr_month(600000,"INR","year",rates)==50000`; `to_inr_month(60000,"USD","year",rates)==415000`; `to_inr_month(40000,"INR","month",rates)==40000`; `salary_bucket` (unparsed→UNKNOWN; remote 50k→PASS at 30k floor; in-office 45k→FAIL at 80k floor).
- `test_filters.py` (uses `cfg`, `make_job`): (1) `title="Senior Backend Engineer"` → `keep False, reasons==["excluded:senior"]`; (2) no_level; (3) no_role; (4) `title="Backend Engineer Intern", is_remote=True, description="Go and Kubernetes, distributed systems", salary_bucket=SalaryBucket.PASS` → `keep True`, **`score==32.0`** (10 role-title + 3 role-desc[distributed systems] + 8 bonus[Go,Kubernetes] + 5 remote + 6 PASS); (5) `is_remote=False, salary_bucket=SalaryBucket.FAIL` → `keep False, reasons[0]=="salary_below_floor"`; (6) `is_remote=False, description="backend systems platform", salary_bucket=UNKNOWN` → `keep True`, **`score==5.0`** (10 role-title − 5 demote); (7) `cfg.model_copy` with `salary.on_missing="drop"`, unknown salary → `keep False, reasons==["salary_missing"]`.
- `test_normalize.py`: `clean_text` (None→None, whitespace collapse, ZWSP/nbsp); `parse_date` (ISO, offset applied, epoch s vs ms same instant, garbage→None); `build_job` smoke (valid `Job`, `job_uid==content_hash(company,title,location)`).

### Acceptance check

```bash
ruff check src tests
ruff format --check src tests
mypy src                                                     # strict; type-checks filters against Config
pytest tests/test_dedup.py tests/test_salary.py tests/test_filters.py tests/test_normalize.py -q
```

---

## Phase 3 — Sources: base + Tier B/C adapters

Builds the source layer: `sources/_http.py` (retry/backoff JSON fetch), `sources/base.py` (the `Source` contract + `RawPosting` + shared normalization), Tier-B adapters (RemoteOK, Himalayas, Jobicy, Adzuna, Jooble), Unstop, Tier-C ATS (Greenhouse, Lever, Ashby, SmartRecruiters), and `sources/registry.py`. No orchestration. JobSpy is Phase 4.

**Contract conformance (Canonical):** `Source.fetch(self, cfg: Config, clock: Clock) -> SourceResult`; `SourceResult(source, succeeded, jobs, n_fetched, duration_ms, error, sub_results)`. Adapters build their httpx client **internally** via `_http.make_client()` (no `client` param on `fetch`). Dedup identity is **imported from `pipeline.dedup`** (`content_hash`, `canonical_url`) — `base.py` does NOT re-implement it. The runner writes one `source_runs` row per `SourceResult` keyed on `run_id`.

### `sources/_http.py`

One HTTP transport policy: browser UA (RemoteOK/Himalayas/Unstop 403 without one), timeouts, and a **manual** retry/backoff loop on 429/5xx (`httpx` transport-level `retries=` only retries connection errors, never status codes).

```python
import time
from collections.abc import Callable, Mapping
from typing import Any
import httpx
from job_aggregator.errors import SourceError

BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/126.0.0.0 Safari/537.36 JobAggregator/0.1 (+self-hosted; contact bibekcharah@gmail.com)")
DEFAULT_TIMEOUT_S = 20.0; DEFAULT_CONNECT_S = 10.0; DEFAULT_MAX_RETRIES = 3
BASE_BACKOFF_S = 0.5; MAX_RETRY_AFTER_S = 30.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

def make_client(*, timeout: float | None = None) -> httpx.Client:
    t = httpx.Timeout(timeout or DEFAULT_TIMEOUT_S, connect=DEFAULT_CONNECT_S)
    return httpx.Client(timeout=t, follow_redirects=True,
                        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"})

def get_json(client, url, *, params=None, method="GET", json_body=None,
             max_retries=DEFAULT_MAX_RETRIES, sleep: Callable[[float], None] = time.sleep) -> Any:
    """Fetch + JSON-decode with retry on ConnectError/ConnectTimeout/ReadTimeout and 429/5xx
    (honoring numeric Retry-After capped at MAX_RETRY_AFTER_S). Raises SourceError on give-up,
    non-retryable status (400/401/403/404), or JSON decode failure. Adapters catch SourceError."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.request(method, url, params=params, json=json_body)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            last_exc = exc
            if attempt == max_retries:
                raise SourceError(f"network error for {url}", details={"url": url, "error": str(exc)}) from exc
            sleep(BASE_BACKOFF_S * (2 ** attempt)); continue
        status = resp.status_code
        if status in RETRYABLE_STATUS:
            if attempt == max_retries:
                raise SourceError(f"HTTP {status} (exhausted) for {url}", details={"url": url, "status": status})
            sleep(_retry_delay(resp, attempt)); continue
        if 200 <= status < 300:
            try: return resp.json()
            except ValueError as exc:
                raise SourceError(f"invalid JSON from {url}", details={"url": url, "error": str(exc)}) from exc
        raise SourceError(f"HTTP {status} for {url}", details={"url": url, "status": status})
    raise SourceError(f"request failed for {url}", details={"url": url}) from last_exc

def _retry_delay(resp, attempt):
    ra = resp.headers.get("Retry-After")
    if ra and ra.strip().isdigit(): return min(float(ra), MAX_RETRY_AFTER_S)
    return BASE_BACKOFF_S * (2 ** attempt)
```
404 (invalid ATS slug) is terminal (not in `RETRYABLE_STATUS`). Non-integer `Retry-After` → exponential backoff.

### `sources/base.py`

```python
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar
from job_aggregator.clock import Clock
from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job
from job_aggregator.pipeline.dedup import canonical_url, content_hash   # single source of truth
from job_aggregator.pipeline.normalize import clean_text

@dataclass(slots=True)
class RawPosting:
    source: str; title: str; company: str; url: str
    source_native_id: str | None = None
    location: str | None = None
    is_remote: bool | None = None
    description: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None       # 'year'|'month'|'hour'|'week'|None
    posted_at: datetime | None = None      # parsed aware-datetime

@dataclass
class SourceResult:
    source: str
    succeeded: bool
    jobs: list[Job] = field(default_factory=list)
    n_fetched: int = 0
    duration_ms: int = 0
    error: str | None = None
    sub_results: list[tuple[str, bool, int]] = field(default_factory=list)
    @classmethod
    def ok(cls, source, jobs, *, duration_ms=0):
        return cls(source=source, succeeded=True, jobs=jobs, n_fetched=len(jobs), duration_ms=duration_ms)
    @classmethod
    def failed(cls, source, error, *, jobs=None, duration_ms=0):
        j = jobs or []
        return cls(source=source, succeeded=False, jobs=j, n_fetched=len(j), duration_ms=duration_ms, error=error)

class Source(ABC):
    name: ClassVar[str]
    @abstractmethod
    def fetch(self, cfg: Config, clock: Clock) -> SourceResult: ...

def to_job(raw: RawPosting) -> Job:
    return Job(
        job_uid=content_hash(raw.company, raw.title, raw.location),
        source=raw.source, source_native_id=raw.source_native_id,
        title=clean_text(raw.title) or raw.title.strip(),
        company=clean_text(raw.company) or raw.company.strip(),
        location=raw.location, is_remote=raw.is_remote, url=canonical_url(raw.url),
        description=raw.description, salary_min=raw.salary_min, salary_max=raw.salary_max,
        salary_currency=raw.salary_currency, salary_period=raw.salary_period,
        salary_parsed=(raw.salary_min is not None or raw.salary_max is not None),
        posted_at=raw.posted_at)   # datetime, not ISO string; salary_bucket set by runner

# date/salary parse helpers adapters call:
def from_epoch_seconds(v): ...   # -> datetime|None (UTC)
def from_epoch_millis(v): ...
def parse_iso(v): ...            # datetime.fromisoformat incl. 'Z'/'+05:30'
def pos_int_or_none(v):          # 0/'0'/None/non-numeric -> None; positive int otherwise
    ...

def build_result(source: str, raw_items: list[Any],
                 mapper: Callable[[Any], RawPosting | None], *, duration_ms: int = 0) -> SourceResult:
    """Tier-B builder encoding the suspicious-empty rule. `raw_items` = REAL postings (structural
    noise pre-stripped). Empty -> succeeded=False (a populated feed returning nothing is suspicious,
    so Phase 5 won't expire its jobs). mapper -> None drops a legitimately-filtered posting."""
    if not raw_items:
        return SourceResult.failed(source, "suspicious empty: source returned 0 items", duration_ms=duration_ms)
    jobs = [to_job(r) for item in raw_items if (r := mapper(item)) is not None]
    return SourceResult.ok(source, jobs, duration_ms=duration_ms)
```
`is_remote` stays tri-state (`bool|None`) on `Job`; `jobs_repo` converts to 0/1/NULL at persist. `posted_at` is a `datetime`. `to_job` imports `content_hash`/`canonical_url` from `pipeline.dedup` (deleted from `base.py`; the earlier "Phase 2 imports from base" note is dropped).

### Tier B adapters (pattern)

Each `Source` subclass: `def fetch(self, cfg, clock): with make_client() as client: try: data = get_json(...) except SourceError as exc: return SourceResult.failed(self.name, str(exc)) ...` then locate item list → map to `RawPosting` → `build_result`. Field maps (verified live names):

- **`remoteok.py` `RemoteOKSource`** `name="remoteok"`: `GET https://remoteok.com/api` (array; **strip element[0] legal notice** — keep only dicts with both `id`+`position`). `source_native_id=str(id)`, `title=position`, `company`, `url` (host lowercased by `canonical_url`), `location=location or "Remote"`, `is_remote=True`, `description`, `salary_min/max=pos_int_or_none(salary_min/max)` (`"0"`→None), `salary_currency="USD"`, `posted_at=parse_iso(date) or from_epoch_seconds(epoch)`.
- **`himalayas.py` `HimalayasSource(country="IN")`**: `GET https://himalayas.app/jobs/api/search`, params `{country: UPPERCASE, limit: 100}`; items `data["jobs"]`; `source_native_id=str(guid)`, `title`, `company=companyName`, `url=applicationLink`, `location=(locationRestrictions or ["Remote"])[0]`, `is_remote=True`, `salary_min/max=pos_int_or_none(minSalary/maxSalary)`, `salary_currency=currency`, `salary_period=_HIMA_PERIOD.get(salaryPeriod)` (`annual→year` etc.), `posted_at=from_epoch_seconds(pubDate)`.
- **`jobicy.py` `JobicySource(job_type=None)`**: `GET https://jobicy.com/api/v2/remote-jobs`, params `{count:50}`; items `data["jobs"]`; client-side `job_type` filter (mapper returns None if set and no `jobType` element case-insensitively contains it — a legit drop). `source_native_id=str(id)`, `title=jobTitle`, `company=companyName`, `url`, `location=jobGeo or "Remote"`, `is_remote=True`, `salary_min/max=pos_int_or_none(salaryMin/salaryMax)`, `salary_currency=salaryCurrency`, `salary_period=_JOBICY_PERIOD.get(salaryPeriod)`, `posted_at=parse_iso(pubDate)`.
- **`adzuna.py` `AdzunaSource(country, app_id, app_key)`**: `GET https://api.adzuna.com/v1/api/jobs/{country}/search/1`, params `{app_id, app_key, results_per_page:50, "content-type":"application/json", sort_by:"date"}`; keys **injected by registry from env**; items `data["results"]`; `source_native_id=str(id)`, `company=company.display_name`, `location=location.display_name`, `url=redirect_url`, `salary_min/max=pos_int_or_none(salary_min/max)`, `salary_currency=_ADZUNA_CCY.get(country.lower())`, `salary_period="year"`, `is_remote=None`, `posted_at=parse_iso(created)`.
- **`jooble.py` `JoobleSource(api_key, keywords, location)`**: `POST https://jooble.org/api/{api_key}`, `json_body={keywords, location}`, called `get_json(..., method="POST", json_body=...)`; items `data["jobs"]`; `source_native_id=str(id)`, `title`, `company=company or keywords`, `url=link`, `location`, `description=snippet`, salary left `None`/`salary_parsed=0`, `posted_at=parse_iso(updated)`. (Confirm field names against a live response before shipping.)
- **`unstop.py` `UnstopSource(opportunities, search_terms, max_age_days)`**: `GET https://unstop.com/api/public/opportunity/search-result`, **loop opportunities** with `{opportunity, per_page:30, page:1}`; jobs at `data["data"]["data"]`. **Recency filter** (mandatory): drop postings older than `clock.now() - timedelta(days=max_age_days)` (unparseable date → keep). Key logic on `subtype`, never `type`. Accumulate across opportunity calls; if none succeeded → `SourceResult.failed`; else `build_result` with a recency-filtering mapper. `salary_min/max` only when `jobDetail.show_salary==1 and not not_disclosed`; `salary_currency=_UNSTOP_CCY.get(jobDetail.currency)` (icon tokens like `fa-rupee→INR`), `salary_period=_UNSTOP_PERIOD.get(pay_in)`.

`build_result` distinguishes **structural empty** (0 real items → suspicious → `succeeded=False`) from **filtered-to-zero** (`succeeded=True, jobs=[]`). Strip RemoteOK's legal notice BEFORE `build_result`.

### Tier C — ATS adapters

Partial-success rule constant:
```python
ATS_REQUIRE_ALL_COMPANIES = False   # False: succeeded if >=1 company fetched OK (coverage);
                                    # True: succeeded only if EVERY company OK.
def run_ats(source, companies, fetch_one, client) -> SourceResult:
    jobs, failed, ok = [], [], 0
    for company in companies:
        try: raws = fetch_one(client, company)          # raises SourceError on HTTP/network fail
        except SourceError as exc: failed.append(f"{company}: {exc}"); continue
        ok += 1; jobs.extend(to_job(r) for r in raws)
    if ATS_REQUIRE_ALL_COMPANIES and failed:
        return SourceResult.failed(source, f"strict: {len(failed)} failed: {failed}")
    if ok == 0:
        return SourceResult.failed(source, f"all {len(companies)} failed: {failed}")
    return SourceResult.ok(source, jobs)
```
Per-company **empty is legitimate** (no openings) — only an ERROR marks a company failed. Each ATS adapter's `fetch(self, cfg, clock)` opens a client and calls `run_ats`.

- **`ats_greenhouse.py` `GreenhouseSource(tokens)`** `name="greenhouse"`: per token `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`; items `data["jobs"]`; **no salary**; `is_remote = True if "remote" in location.name.lower() else None`; `source_native_id=str(id)`, `url=absolute_url`, `location=location.name`, `company=company_name or token`, `description=content`, `posted_at=parse_iso(updated_at or first_published)`.
- **`ats_lever.py` `LeverSource(slugs)`** `name="lever"`: per slug `GET https://api.lever.co/v0/postings/{slug}?mode=json`; **bare array**; invalid slug returns `{"ok":false,...}` → raise `SourceError` (failed company); `createdAt` is **epoch ms** → `from_epoch_millis`; `company=slug`, `title=text`, `url=hostedUrl`, `is_remote` from `workplaceType` (remote→True, onsite/hybrid→False, missing→None), salary from `salaryRange` (usually null), `salary_period=_LEVER_PERIOD.get(interval)` (`per-year-salary→year`).
- **`ats_ashby.py` `AshbySource(orgs)`** `name="ashby"`: per org `GET https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true` (**org case-sensitive**); items `data["jobs"]`; keep only `isListed is not False`; `is_remote=bool(isRemote)`; salary from `compensation.summaryComponents` where `compensationType=="Salary"` (`minValue/maxValue/currencyCode`, period from `_ashby_period("1 YEAR"→"year")`); `posted_at=parse_iso(publishedAt)`.
- **`ats_smartrecruiters.py` `SmartRecruitersSource(company_ids, country="in")`** `name="smartrecruiters"`: per id `GET https://api.smartrecruiters.com/v1/companies/{id}/postings`, params `{country: lowercase ISO, limit:100}`; items `data["content"]`; no salary/description in listing; `company=company.name or id`, `url=ref`, `location=_sr_location(...)`, `is_remote=bool(location.remote)`, `posted_at=parse_iso(releasedDate)`.

### `sources/registry.py`

```python
def build_enabled_sources(cfg: Config) -> list[Source]:
    s = cfg.sources; out: list[Source] = []
    if s.remoteok.enabled: out.append(RemoteOKSource())
    if s.himalayas.enabled: out.append(HimalayasSource(country=s.himalayas.country))
    if s.jobicy.enabled: out.append(JobicySource(job_type=s.jobicy.job_type))
    if s.adzuna.enabled:
        aid, akey = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
        if aid and akey: out.append(AdzunaSource(country=s.adzuna.country, app_id=aid, app_key=akey))
        else: log.warning("adzuna enabled but keys unset; skipping")
    if s.jooble.enabled:
        key = os.environ.get("JOOBLE_API_KEY")
        if key:
            kw = cfg.keywords.roles[0] if cfg.keywords.roles else "backend intern"
            out.append(JoobleSource(api_key=key, keywords=kw, location=cfg.locations[0] if cfg.locations else ""))
        else: log.warning("jooble enabled but JOOBLE_API_KEY unset; skipping")
    if s.unstop.enabled:
        out.append(UnstopSource(opportunities=s.unstop.opportunities,
                                search_terms=s.unstop.search_terms, max_age_days=s.unstop.max_age_days))
    ats = s.ats
    if ats.greenhouse.enabled and ats.greenhouse.tokens: out.append(GreenhouseSource(tokens=ats.greenhouse.tokens))
    if ats.lever.enabled and ats.lever.slugs: out.append(LeverSource(slugs=ats.lever.slugs))
    if ats.ashby.enabled and ats.ashby.orgs: out.append(AshbySource(orgs=ats.ashby.orgs))
    if ats.smartrecruiters.enabled and ats.smartrecruiters.company_ids:
        out.append(SmartRecruitersSource(company_ids=ats.smartrecruiters.company_ids))
    # Tier A (JobSpy) appended in Phase 4: if s.jobspy.enabled: out.append(JobSpySource())
    return out
```

### Tests (`tests/test_sources_apis.py`, `tests/test_sources_ats.py`)

`conftest` adds `FIXED_NOW = datetime(2026,7,15,12,0,tzinfo=UTC)` and `load_fixture(name)`. Every test is `@respx.mock`, drives adapters with recorded fixtures under `tests/fixtures/`, calls `src.fetch(cfg, FixedClock(FIXED_NOW))` (respx intercepts the internally-built client), and monkeypatches `get_json`'s `sleep` (no test sleeps). Fixtures (concrete field values, no guessing) include `remoteok.json`, `himalayas.json`, `jobicy.json` (**id 501 `jobTitle="Machine Learning Intern"`, `jobType:["Internship"]`, salaryMin/Max 0; id 502 `jobTitle="Senior Data Engineer"`, `jobType:["Full-Time"]`, 120000/160000** — internally consistent with the assertion below), `adzuna.json`, `jooble.json`, `unstop.json` (recent id "30111" + 2022 old id "20777"), `greenhouse.json` (job 402 has no `company_name` → token fallback), `lever.json`, `lever_notfound.json`, `ashby.json`, `smartrecruiters.json`.

`test_sources_apis.py`: remoteok skips legal notice + maps (`n_fetched==1`, `is_remote==True`, host lowercased, `salary_min is None`); remoteok suspicious-empty when only legal (`succeeded is False`); himalayas sends country + epoch-seconds date + `salary_period=="year"`; **jobicy filters by job_type** (`JobicySource("internship")` keeps only 501, `postings[0].title=="Machine Learning Intern"`; `JobicySource(None)` keeps both); jobicy suspicious-empty; adzuna injected keys + INR currency + utm stripped; jooble POSTs json body; unstop drops stale keeps recent (`n_fetched==1`, `salary_currency=="INR"`, `salary_period=="month"`); unstop loops opportunities; unstop all-fail → failed. Plus `get_json` retry tests (5xx-then-200; Retry-After capped to 30; give-up→SourceError; 404 no-retry; ConnectError retry). Plus registry tests (builds Tier B+C from `default_config.yaml` with env keys + non-empty ATS lists; skips adzuna without keys; omits disabled).

`test_sources_ats.py`: greenhouse maps + company fallback (`is_remote` True for "Remote - India", None for job 402); greenhouse partial success (good→jobs, bad 404→`succeeded True`); greenhouse all-fail→failed; lever array + epoch ms (`posted_at==from_epoch_millis(...).isoformat()` at persist, `is_remote==True`); lever not-found slug fails that company (two-slug → `succeeded True`); ashby compensation + is_remote; smartrecruiters country param + remote.

Note: adapters produce `Job` objects; a `Job.posted_at` is a `datetime`, so assertions compare `.posted_at` to a `datetime` (persistence to ISO happens in `jobs_repo`).

### Acceptance check

```bash
ruff check src/job_aggregator/sources tests
ruff format --check src/job_aggregator/sources tests
mypy src/job_aggregator/sources
pytest tests/test_sources_apis.py tests/test_sources_ats.py -q
```
No source raises out of `fetch()`; suspicious-empty (Tier B) + partial-success (ATS) rules verified; `build_enabled_sources` produces the right set from `default_config.yaml`.

---

## Phase 4 — Sources: Tier A (JobSpy)

Builds `sources/jobspy_source.py`: the `JobSpySource(Source)` adapter driving `python-jobspy` (Naukri/LinkedIn-guest/Indeed/Google), turning the returned `pandas.DataFrame` into normalized `Job`s. `fetch()` never raises; per-site failure/empty → that site `succeeded=False` so stale-delete leaves its jobs untouched.

Dependencies (correct owners): `Source`/`SourceResult` from `sources/base.py` (Phase 3); `Job`/`SalaryBucket` from `models/job.py` (Phase 1); `content_hash`/`canonical_url` from **`pipeline/dedup.py` (Phase 2)**; `to_inr_month`/`salary_bucket` from **`pipeline/salary.py` (Phase 2)**; `Config`/`JobSpyConfig` from **`config/schema.py` (Phase 1)**; `Clock` from `clock.py`.

Design decisions: `JobSpySource.name="jobspy"`; every `Job.source` is `jobspy_<site>`; `sub_results=[("jobspy_<site>", succeeded, n)]` so the runner records one `source_runs` row per site and the guard is per-site (a LinkedIn 429 must not zero out Naukri). Call `scrape_jobs` once per `(site, search_term)` with `site_name=[site]` (failure isolation + exact per-site counts). Suspicious-empty ⇒ site `succeeded=False`. Indeed: pass `hours_old`, **omit `is_remote`** (Indeed silently drops filters when combined). `linkedin_fetch_description` stays False. Salary normalized to INR/month via `to_inr_month`; original in `salary_raw`; `salary_bucket = salary_bucket(job, cfg)`.

```python
from __future__ import annotations
import logging, time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any
from job_aggregator.models.job import Job
from job_aggregator.pipeline.dedup import canonical_url, content_hash
from job_aggregator.pipeline.salary import salary_bucket, to_inr_month
from job_aggregator.sources.base import Source, SourceResult
if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config, JobSpyConfig

log = logging.getLogger(__name__)
_SITES_REQUIRING_COUNTRY = frozenset({"indeed", "glassdoor"})
_SITES_NO_IS_REMOTE = frozenset({"indeed"})
_VERBOSE = 1; _DESCRIPTION_FORMAT = "markdown"
_INTERVAL_TO_PERIOD = {"yearly":"year","annual":"year","monthly":"month","hourly":"hour"}

def _is_missing(v): return v is None or v != v   # NaN/NaT self-inequality; no pandas import
def _clean_str(v): ...      # -> str|None
def _clean_float(v): ...    # -> float|None
def _clean_bool(v): ...     # -> bool|None
def _clean_dt(v): ...       # Timestamp|date|ISO str -> tz-aware UTC datetime|None
def _salary_raw_repr(mn, mx, ccy, interval): ...   # "USD 60000-90000/yearly" or None

def _scrape_jobs(**kwargs: Any) -> Any:            # the ONE seam tests monkeypatch
    from jobspy import scrape_jobs
    return scrape_jobs(**kwargs)

def _build_scrape_kwargs(site: str, term: str, jc: "JobSpyConfig") -> dict[str, Any]:
    k = {"site_name":[site], "search_term":term, "location":jc.location,
         "results_wanted":jc.results_wanted, "hours_old":jc.hours_old,
         "description_format":_DESCRIPTION_FORMAT, "verbose":_VERBOSE}
    if site in _SITES_REQUIRING_COUNTRY: k["country_indeed"] = jc.country_indeed
    if jc.is_remote and site not in _SITES_NO_IS_REMOTE: k["is_remote"] = True
    if jc.proxies: k["proxies"] = jc.proxies
    return k

def _map_salary(row, cfg) -> dict[str, Any]:
    interval = _clean_str(row.get("interval")); currency = _clean_str(row.get("currency"))
    min_raw = _clean_float(row.get("min_amount")); max_raw = _clean_float(row.get("max_amount"))
    raw_repr = _salary_raw_repr(min_raw, max_raw, currency, interval)
    period = _INTERVAL_TO_PERIOD.get(interval.lower()) if interval else None
    ccy = currency.upper() if currency else None
    base = cfg.salary.currency.upper()
    known = {base, *(k.upper() for k in cfg.salary.fx_rates)}
    if period and ccy in known and (min_raw is not None or max_raw is not None):
        rates = cfg.salary.fx_rates
        s_min = to_inr_month(int(round(min_raw)), ccy, period, rates) if min_raw is not None else None
        s_max = to_inr_month(int(round(max_raw)), ccy, period, rates) if max_raw is not None else None
        return {"salary_min":s_min, "salary_max":s_max, "salary_currency":base,
                "salary_period":"month", "salary_raw":raw_repr, "salary_parsed":True}
    return {"salary_raw":raw_repr, "salary_parsed":False}

@dataclass
class _SiteStat:
    calls=0; errors=0; rows=0; jobs=0; last_error: str | None = None
    @property
    def succeeded(self) -> bool: return self.jobs > 0

class JobSpySource(Source):
    name = "jobspy"
    def fetch(self, cfg: "Config", clock: "Clock") -> SourceResult:   # clock unused (jobspy uses hours_old)
        jc = cfg.sources.jobspy; started = time.monotonic()
        if not jc.sites or not jc.search_terms:
            return SourceResult(source=self.name, succeeded=True, jobs=[], n_fetched=0, duration_ms=0)
        stats = {s: _SiteStat() for s in jc.sites}
        seen: set[tuple[str, str]] = set(); all_jobs: list[Job] = []
        for site in jc.sites:
            st = stats[site]
            for term in jc.search_terms:
                st.calls += 1
                try: df = _scrape_jobs(**_build_scrape_kwargs(site, term, jc))
                except Exception as exc:
                    st.errors += 1; st.last_error = f"{type(exc).__name__}: {exc}"
                    log.warning("jobspy %s/%r failed: %s", site, term, exc); continue
                rows = [] if df is None else df.to_dict(orient="records")
                st.rows += len(rows)
                for row in rows:
                    try: job = self._row_to_job(row, site, cfg)
                    except Exception as exc: log.warning("jobspy %s row failed: %s", site, exc); continue
                    if job is None: continue
                    key = (site, job.job_uid)
                    if key in seen: continue
                    seen.add(key); all_jobs.append(job); st.jobs += 1
        elapsed = int((time.monotonic() - started) * 1000)
        subs = [(f"jobspy_{s}", stats[s].succeeded, stats[s].jobs) for s in jc.sites]
        failed = [f"jobspy_{s}: {stats[s].last_error or 'empty'}" for s in jc.sites if not stats[s].succeeded]
        return SourceResult(source=self.name, succeeded=any(v.succeeded for v in stats.values()),
                            jobs=all_jobs, n_fetched=len(all_jobs), duration_ms=elapsed,
                            error="; ".join(failed) or None, sub_results=subs)
    def _row_to_job(self, row, site, cfg) -> Job | None:
        title = _clean_str(row.get("title")); company = _clean_str(row.get("company"))
        url = _clean_str(row.get("job_url"))
        if not title or not company or not url: return None
        location = _clean_str(row.get("location"))
        job = Job(job_uid=content_hash(company, title, location or ""), source=f"jobspy_{site}",
                  source_native_id=None, title=title, company=company, location=location,
                  is_remote=_clean_bool(row.get("is_remote")), url=canonical_url(url),
                  description=_clean_str(row.get("description")),
                  posted_at=_clean_dt(row.get("date_posted")), **_map_salary(row, cfg))
        job.salary_bucket = salary_bucket(job, cfg)     # Job is mutable
        return job
```

Column → field map: `title→title` (required), `company→company` (required, not `company_name`), `job_url→url` (via `canonical_url`; `job_url_direct` ignored), `location→location`, `is_remote→is_remote` (board's flag, NaN→None), `description→description`, `date_posted→posted_at`, `min_amount/max_amount/currency/interval→salary_*` (INR/month via `to_inr_month`), synthesized `source=jobspy_<site>`, `source_native_id=None`, `job_uid=content_hash(company,title,location or "")`, `match_score` left None (runner sets it).

Runner contract (Phase 5): for each `SourceResult`, one `record_source_run(conn, run_id, sub_name, succeeded=..., n_fetched=..., duration_ms=res.duration_ms)` per `sub_results` entry (else one keyed on `res.source`); succeeded sub-names feed the stale guard. Registry: `if cfg.sources.jobspy.enabled: sources.append(JobSpySource())`.

### Tests (`tests/test_jobspy_source.py`)

Deterministic, no network: `monkeypatch.setattr(js, "_scrape_jobs", fake)` where `fake(**kwargs)` returns a `pd.DataFrame` (dispatch on `kwargs["site_name"][0]`, or `raise` to simulate 429). `_cfg(sites, terms)` from `Config()`, `_row(**over)`. Tests: normalizes basic row (1 job, `source=="jobspy_naukri"`, utm stripped, `len(job_uid)==64`, `posted_at.tzinfo is not None`, `sub_results==[("jobspy_naukri",True,1)]`, `res.succeeded True`); per-site tagging + sub_results; **LinkedIn 429 tolerated** (no raise; `subs["jobspy_naukri"]==(True,1)`, `subs["jobspy_linkedin"]==(False,0)`, `succeeded True`, "linkedin" in `res.error`); empty DataFrame marks site failed; rows missing required fields dropped; dedup within site across terms; **salary yearly INR normalized** (`min_amount=600000,currency="INR",interval="yearly"` → `salary_min==50000`, `salary_max==75000`, `salary_currency=="INR"`, `salary_period=="month"`, `salary_bucket in set(SalaryBucket)`); salary missing → `salary_parsed False`, `salary_bucket==UNKNOWN`; `_build_scrape_kwargs` (indeed → no is_remote, country present; others → is_remote present, country absent; `hours_old==48`, `results_wanted==40`); proxies passthrough; no-sites → empty, seam never called.

### Acceptance check

```bash
cd "/home/SammyUrfen/Codes/job aggregator"
ruff check src/job_aggregator/sources/jobspy_source.py tests/test_jobspy_source.py
ruff format --check src/job_aggregator/sources/jobspy_source.py tests/test_jobspy_source.py
mypy src/job_aggregator/sources/jobspy_source.py
pytest tests/test_jobspy_source.py -q
```

---

## Phase 5 — Pipeline runner + stale-deletion

Two files at the correctness core: `pipeline/stale.py` (`expire_stale`, the per-source success guard) and `pipeline/runner.py` (`run_cycle`, the 9-step orchestrator). Keeps `run_cycle` a thin dispatcher; DB writes happen only on the runner's main thread; sources fetch concurrently in an executor.

Dependencies (exact): `runs_repo.start_run/record_source_run/finish_run/current_run`; `jobs_repo.upsert_job(...) -> "new"|"updated"`, `jobs_repo.set_user_flag`; `db.connect/init_db`; `config.load_effective_config`; `models.Job/JobStatus/SalaryBucket`; `filters.score_and_filter(job, cfg)` → `FilterVerdict(keep, score, reasons)`; `salary.salary_bucket(job, cfg)`; `sources.base.Source/SourceResult`, `sources.registry.build_enabled_sources`; `notify.base.build_notifiers`/`Notifier` (lazy — Phase 7).

### `pipeline/stale.py`

```python
from __future__ import annotations
import logging, sqlite3
from datetime import timedelta
from job_aggregator.clock import Clock
from job_aggregator.config.schema import Config
logger = logging.getLogger(__name__)

def expire_stale(conn: sqlite3.Connection, run_id: int, succeeded_sources: set[str],
                 cfg: Config, clock: Clock) -> int:
    """For each SUCCEEDED source: mark jobs not seen this cycle 'stale', then 'deleted' past
    grace_days. Returns newly-stale + newly-deleted. A source absent from succeeded_sources is
    never iterated -> its jobs are physically unreachable by both UPDATEs (the whole point)."""
    grace_days = cfg.schedule.grace_days
    cutoff_iso = (clock.now() - timedelta(days=grace_days)).isoformat()
    cur = conn.cursor(); n = 0
    for source in sorted(succeeded_sources):                    # sorted -> reproducible
        cur.execute("UPDATE jobs SET status='stale' WHERE source=? AND last_seen_cycle<? "
                    "AND status IN ('new','active')", (source, run_id))
        n += cur.rowcount
        cur.execute("UPDATE jobs SET status='deleted' WHERE source=? AND status='stale' "
                    "AND julianday(last_seen_at) < julianday(?)", (source, cutoff_iso))
        n += cur.rowcount
    conn.commit()
    logger.debug("expire_stale run=%d sources=%d expired=%d", run_id, len(succeeded_sources), n)
    return n
```
Soft: `status IN ('new','active') AND last_seen_cycle < run_id` → `'stale'` (seen-this-cycle rows have `last_seen_cycle == run_id`). Hard: `status='stale' AND julianday(last_seen_at) < julianday(cutoff)` → `'deleted'`. Empty `succeeded_sources` → no-op returns 0. `'deleted'` rows are never re-touched (idempotent); resurrection is `upsert_job`'s job.

### `pipeline/runner.py`

`MAX_FETCH_WORKERS = 8`. `RunSummary` extends the frozen 7 core fields with defaulted extras.

```python
from __future__ import annotations
import logging, sqlite3, time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from job_aggregator.clock import Clock
from job_aggregator.config.schema import Config
from job_aggregator.errors import RunInProgressError
from job_aggregator.pipeline.filters import score_and_filter
from job_aggregator.pipeline.salary import salary_bucket
from job_aggregator.pipeline.stale import expire_stale
from job_aggregator.sources.base import Source, SourceResult
from job_aggregator.storage import jobs_repo, runs_repo
if TYPE_CHECKING:
    from job_aggregator.models.job import Job
    from job_aggregator.notify.base import Notifier
logger = logging.getLogger(__name__)
MAX_FETCH_WORKERS = 8

@dataclass
class RunSummary:
    run_id: int; status: str
    n_sources_ok: int; n_sources_err: int
    n_new: int; n_updated: int; n_expired: int
    trigger: str = "manual"; n_filtered_out: int = 0; duration_ms: int = 0
    source_errors: dict[str, str] = field(default_factory=dict)
    def __str__(self) -> str:
        return (f"run #{self.run_id} [{self.status}] trigger={self.trigger} "
                f"sources ok={self.n_sources_ok} err={self.n_sources_err} | "
                f"new={self.n_new} updated={self.n_updated} filtered={self.n_filtered_out} "
                f"expired={self.n_expired} ({self.duration_ms} ms)")

def run_cycle(conn, cfg: Config, clock: Clock, trigger: str, *,
              sources: Sequence[Source] | None = None,
              notifiers: Sequence["Notifier"] | None = None) -> RunSummary:
    started = time.perf_counter()
    if runs_repo.current_run(conn) is not None:
        raise RunInProgressError("another cycle is already running")
    run_id = runs_repo.start_run(conn, trigger, clock); conn.commit()
    n_ok = n_err = n_new = n_updated = n_filtered = n_expired = 0
    source_errors: dict[str, str] = {}
    try:
        resolved = list(sources) if sources is not None else _build_sources(cfg)
        results = _fetch_all(resolved, cfg, clock)
        succeeded, n_ok, n_err, source_errors = _record_source_runs(conn, run_id, results)
        conn.commit()
        new_jobs, n_new, n_updated, n_filtered = _filter_and_upsert(conn, run_id, results, cfg, clock)
        conn.commit()
        n_expired = expire_stale(conn, run_id, succeeded, cfg, clock)
        _notify(conn, run_id, cfg, clock, new_jobs, notifiers)     # step 8 (Phase 7 finalizes)
        status = _run_status(n_ok, n_err)
        runs_repo.finish_run(conn, run_id, status, n_sources_ok=n_ok, n_sources_err=n_err,
                             n_new=n_new, n_updated=n_updated, n_expired=n_expired, clock=clock)
        conn.commit()
        return RunSummary(run_id, status, n_ok, n_err, n_new, n_updated, n_expired,
                          trigger=trigger, n_filtered_out=n_filtered,
                          duration_ms=int((time.perf_counter()-started)*1000), source_errors=source_errors)
    except Exception as exc:
        logger.exception("cycle #%d failed fatally", run_id)
        try:
            runs_repo.finish_run(conn, run_id, "failed", n_sources_ok=n_ok, n_sources_err=n_err,
                                 n_new=n_new, n_updated=n_updated, n_expired=n_expired,
                                 clock=clock, error=f"{type(exc).__name__}: {exc}")
            conn.commit()
        except Exception: logger.exception("could not finalize failed run #%d", run_id)
        raise

def _fetch_all(sources, cfg, clock) -> list[SourceResult]:
    if not sources: return []
    results: list[SourceResult | None] = [None] * len(sources)
    with ThreadPoolExecutor(max_workers=min(MAX_FETCH_WORKERS, len(sources)), thread_name_prefix="src") as pool:
        fut = {pool.submit(_fetch_one, s, cfg, clock): i for i, s in enumerate(sources)}
        for f in as_completed(fut): results[fut[f]] = f.result()
    return [r for r in results if r is not None]     # input order -> deterministic first-seen

def _fetch_one(source, cfg, clock) -> SourceResult:
    t0 = time.perf_counter()
    try: return source.fetch(cfg, clock)
    except Exception as exc:
        logger.warning("source %s raised despite no-raise contract: %s", source.name, exc)
        return SourceResult(source=source.name, succeeded=False, jobs=[], n_fetched=0,
                            duration_ms=int((time.perf_counter()-t0)*1000), error=f"{type(exc).__name__}: {exc}")

def _record_source_runs(conn, run_id, results) -> tuple[set[str], int, int, dict[str, str]]:
    succeeded: set[str] = set(); n_ok = n_err = 0; errors: dict[str, str] = {}
    for res in results:
        rows = res.sub_results or [(res.source, res.succeeded, res.n_fetched)]
        for name, ok, n in rows:
            runs_repo.record_source_run(conn, run_id, name, succeeded=ok, n_fetched=n,
                                        duration_ms=res.duration_ms, error=None if ok else res.error)
            if ok: n_ok += 1; succeeded.add(name)
            else:
                n_err += 1
                if res.error: errors[name] = res.error
    return succeeded, n_ok, n_err, errors

def _filter_and_upsert(conn, run_id, results, cfg, clock) -> tuple[list["Job"], int, int, int]:
    new_jobs: list[Job] = []; n_new = n_updated = n_filtered = 0
    for res in results:
        if not res.succeeded: continue                       # never ingest an unseen source
        for job in res.jobs:
            job.salary_bucket = salary_bucket(job, cfg)      # uniform bucket for ALL sources
            verdict = score_and_filter(job, cfg)
            if not verdict.keep: n_filtered += 1; continue
            job.match_score = verdict.score
            if jobs_repo.upsert_job(conn, job, run_id, clock) == "new":
                n_new += 1; new_jobs.append(job)
            else: n_updated += 1
    return new_jobs, n_new, n_updated, n_filtered

def _notify(conn, run_id, cfg, clock, new_jobs, notifiers) -> None:
    """PROVISIONAL step 8 — Phase 7 REPLACES this body with FeedScope routing
    (jobs_new_in_run + recent_active_jobs). Until then: deliver the in-memory new_jobs to each
    notifier, best-effort. A notifier failure NEVER fails the run."""
    if notifiers is None:
        if not new_jobs: return
        notifiers = _build_notifiers(cfg, clock)
    for n in notifiers:
        try: n.notify_new(new_jobs, cfg)
        except Exception: logger.exception("notifier %s failed", type(n).__name__)

def _run_status(n_ok, n_err) -> str:
    if n_ok == 0 and n_err == 0: return "success"    # no sources enabled = legit no-op
    if n_ok == 0: return "failed"
    if n_err > 0: return "partial"
    return "success"

def _build_sources(cfg):
    from job_aggregator.sources.registry import build_enabled_sources
    return build_enabled_sources(cfg)
def _build_notifiers(cfg, clock):
    from job_aggregator.notify.base import build_notifiers   # lazy: Phase 7
    return build_notifiers(cfg, clock)
```
Concurrency: executor threads only fetch/normalize in-memory `Job`s; **all DB writes on the main thread**, so sharing one connection is safe and `check_same_thread=False` is not needed. Determinism: results processed in **input order** (stable first-seen; Tier A before Tier C). Suspicious-empty is the source's responsibility to report as `succeeded=False`; the runner treats `succeeded=True, jobs=[]` as a legitimate empty (so its old jobs stale). `salary_bucket` is computed uniformly here for every source (Tier-B `to_job` left it None; the runner fixes it before filtering).

### Test infra (`tests/conftest.py` additions + `tests/_fakes.py`)

`conftest` adds `sample_config` (permissive: `require_level=False`, `exclude=[senior,...]`, `on_missing="keep_and_flag"`, `grace_days=3`) and a `:memory:` `conn` fixture for runner tests. `tests/_fakes.py`: `make_job(uid, source, ...)` (frozen `Job`, crafted to pass `sample_config`); `FakeSource(name, jobs, *, succeeded=True, error=None, duration_ms=1, sub_results=None)`; `RaisingSource`; **`RecordingNotifier`** — a standalone duck-typed class (`def notify_new(self, jobs, cfg)`), NOT importing `notify.base` (Phase 5 must not depend forward on Phase 7). To force a dedup collapse give two `make_job(...)` the SAME uid; to keep distinct give different uids AND dissimilar title+company. Always `start_run` before `upsert_job` (FK).

### `tests/test_stale.py`

Seed via `upsert_job`, call `expire_stale` with hand-chosen `run_id`s. Tests: soft-stale from succeeded source (`run_id=2, {"src"}` → `'stale'`, returns 1); failed-source jobs untouched (`succeeded_sources=set()` → `'new'`, returns 0); only-succeeded expire; within-grace stays stale; grace-window `stale→deleted` (advance `grace_days+1`); seen-this-cycle not staled (`upsert` at run_id=2 then `expire_stale(run_id=2)` → unchanged); deleted idempotent; empty succeeded set no-op.

### `tests/test_runner.py`

Full-cycle over fakes + `FixedClock` + real DB/repos/filters/stale, `notifiers=[]` unless targeting notify. Tests: cross-source dedup collapses (same uid → 1 row, `n_new==1, n_updated==1`, surviving `source` is first in input order); stale-delete only touches succeeded sources (`partial`, `(n_ok,n_err,n_expired)==(1,1,1)`); failed source leaves its jobs untouched across many post-grace cycles (stays `'active'`); grace-window stale→deleted; user flags preserved across cycle (`bookmarked/applied==1`, `notes` intact, `status=='active'`); all-sources-fail → `'failed'`, 0 rows; no-sources → `'success'`, all zero; **new jobs notified new-only** (`RecordingNotifier`; `recorder.calls == [["a"],["c"]]`); source-that-raises caught (`'failed'`, `source_runs.succeeded==0` for `boom`); filtered job not inserted (`title="Senior Backend Engineer"` → 0 rows, `n_filtered_out==1`); subsource guard (`sub_results=[("jobspy_naukri",True,1),("jobspy_linkedin",False,0)]` → two `source_runs` rows; a pre-seeded `jobspy_linkedin` job untouched, `jobspy_naukri` stales).

### Acceptance check

```bash
cd "/home/SammyUrfen/Codes/job aggregator"
pytest tests/test_runner.py tests/test_stale.py -q
ruff check src/job_aggregator/pipeline/runner.py src/job_aggregator/pipeline/stale.py tests/test_runner.py tests/test_stale.py tests/_fakes.py
ruff format --check src/job_aggregator/pipeline/runner.py src/job_aggregator/pipeline/stale.py tests/test_runner.py tests/test_stale.py tests/_fakes.py
mypy src/job_aggregator/pipeline/runner.py src/job_aggregator/pipeline/stale.py
```

---

## Phase 6 — Scheduler + CLI wiring

Makes the aggregator self-driving. Replaces the `scheduler/scheduler.py` stub with a real in-process daily scheduler (APScheduler `BackgroundScheduler`, 3.x) with startup catch-up and a run-lock; finalizes the four CLI subcommands.

Trigger vocabulary (frozen by `schema.sql`): exactly `'schedule' | 'manual' | 'startup_catchup'`.

### 6.1 `scheduler/scheduler.py`

Why `BackgroundScheduler` (a cycle is blocking sqlite/httpx work → own pool thread, never the event loop). Fresh sqlite connection per run (opened inside the job body on the executing thread; `connect_fn` is a factory). One lock funnel: process-local `threading.Lock` (non-blocking acquire) + a `runs_repo.current_run` DB check for cross-process runs. `trigger_now` is synchronous and returns `run_id` (or None if busy); catch-up submits to the executor (must not block startup).

Frozen public API + additive `next_run_at`:
```python
class JobScheduler:
    def __init__(self, connect_fn: Callable[[], object], clock: Clock) -> None: ...
    def start(self) -> None: ...            # register daily cron, start, then catch_up_on_startup()
    def stop(self) -> None: ...             # shutdown(wait=False); safe if never started
    def catch_up_on_startup(self) -> None: ...
    def trigger_now(self, trigger: str = "manual") -> int | None: ...   # run_id, or None if busy
    @property
    def next_run_at(self) -> datetime | None: ...   # from the daily job's next_run_time
```

Constants: `TRIGGER_SCHEDULE="schedule"`, `TRIGGER_MANUAL="manual"`, `TRIGGER_CATCHUP="startup_catchup"`; `DAILY_JOB_ID="daily_cycle"`, `IMMEDIATE_JOB_ID="immediate_cycle"`; `MISFIRE_GRACE_SECONDS=3600`; `MAX_INSTANCES=1`; `CATCH_UP_THRESHOLD=timedelta(hours=24)`.

```python
from __future__ import annotations
import logging, sqlite3, threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast
from job_aggregator.clock import Clock
if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler
log = logging.getLogger(__name__)
TRIGGER_SCHEDULE="schedule"; TRIGGER_MANUAL="manual"; TRIGGER_CATCHUP="startup_catchup"
DAILY_JOB_ID="daily_cycle"; IMMEDIATE_JOB_ID="immediate_cycle"
MISFIRE_GRACE_SECONDS=3600; MAX_INSTANCES=1; CATCH_UP_THRESHOLD=timedelta(hours=24)

class JobScheduler:
    def __init__(self, connect_fn: Callable[[], object], clock: Clock) -> None:
        self._connect_fn = connect_fn; self._clock = clock
        self._lock = threading.Lock(); self._scheduler: BackgroundScheduler | None = None

    def start(self) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        from job_aggregator.config.store import load_effective_config
        conn = cast("sqlite3.Connection", self._connect_fn())
        try: run_hour = load_effective_config(conn).schedule.run_hour_local
        finally: conn.close()
        if self._scheduler is None: self._scheduler = BackgroundScheduler()
        self._scheduler.add_job(self._run_locked, trigger=CronTrigger(hour=run_hour),
            args=[TRIGGER_SCHEDULE], id=DAILY_JOB_ID, replace_existing=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS, coalesce=True, max_instances=MAX_INSTANCES)
        self._scheduler.start()
        log.info("scheduler started; daily run at %02d:00 local", run_hour)
        self.catch_up_on_startup()

    def stop(self) -> None:
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=False); log.info("scheduler stopped")

    @property
    def next_run_at(self) -> datetime | None:
        if self._scheduler is None: return None
        job = self._scheduler.get_job(DAILY_JOB_ID)
        return getattr(job, "next_run_time", None) if job else None

    def catch_up_on_startup(self) -> None:
        from job_aggregator.config.store import load_effective_config
        from job_aggregator.storage import runs_repo
        conn = cast("sqlite3.Connection", self._connect_fn())
        try:
            if not load_effective_config(conn).schedule.catch_up_on_startup:
                log.info("startup catch-up disabled; skipping"); return
            last_row = runs_repo.last_successful_run(conn)
        finally: conn.close()
        last = _run_finished_at(last_row)
        if self._should_catch_up(last, self._clock.now(), CATCH_UP_THRESHOLD):
            self._submit_async(TRIGGER_CATCHUP)
        else: log.info("recent success at %s; catch-up not needed", last)

    @staticmethod
    def _should_catch_up(last_success, now, threshold) -> bool:
        if last_success is None: return True
        return (now - last_success) >= threshold

    def trigger_now(self, trigger: str = TRIGGER_MANUAL) -> int | None:
        return self._run_locked(trigger)

    def _submit_async(self, trigger: str) -> None:
        if self._scheduler is None or not self._scheduler.running:
            raise RuntimeError("scheduler not started; call start() first")
        self._scheduler.add_job(self._run_locked, args=[trigger], id=f"{IMMEDIATE_JOB_ID}:{trigger}",
            replace_existing=True, coalesce=True, max_instances=MAX_INSTANCES, misfire_grace_time=None)

    def _run_locked(self, trigger: str) -> int | None:
        from job_aggregator.config.store import load_effective_config
        from job_aggregator.pipeline.runner import run_cycle
        from job_aggregator.storage import runs_repo
        if not self._lock.acquire(blocking=False):
            log.warning("a run is already in progress in this process; skipping %s", trigger); return None
        conn: sqlite3.Connection | None = None
        try:
            conn = cast("sqlite3.Connection", self._connect_fn())
            if runs_repo.current_run(conn) is not None:
                log.warning("an active run exists in the DB; skipping %s", trigger); return None
            cfg = load_effective_config(conn)
            summary = run_cycle(conn, cfg, self._clock, trigger=trigger)
            log.info("run finished (%s): %s", trigger, summary)
            return summary.run_id
        except Exception:
            log.exception("run cycle raised for trigger=%s", trigger); return None
        finally:
            if conn is not None: conn.close()
            self._lock.release()

def _run_finished_at(row) -> datetime | None:
    if row is None: return None
    raw = row["finished_at"] or row["started_at"]
    if raw is None: return None
    dt = datetime.fromisoformat(raw)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
```
Catch-up uses `last_successful_run` (**`status='success'` only**, per Canonical contracts): a day where every run was `partial` looks like "not fully succeeded" → catch-up re-attempts the missed sources. Cron fires on local wall-clock (`run_hour_local`); all bookkeeping is UTC via `self._clock`. `run_hour_local` change needs a `serve` restart. Dangling `'running'` row after a crash is reconciled upstream (Phase 5 startup reconciler / `TROUBLESHOOTING.md`).

### 6.2 `cli.py` finalization

**`main()`** loads `.env` after `parse_args` (so `--help`/`--version` stay stdlib-only) and maps the error hierarchy to a terse envelope:
```python
def _load_env() -> None:
    try: from dotenv import load_dotenv
    except ModuleNotFoundError: return
    load_dotenv()

def main(argv: list[str] | None = None) -> int:
    parser = build_parser(); args = parser.parse_args(argv); _load_env()
    try: return int(args.func(args))
    except JobAggregatorError as exc:
        print(f"error [{exc.code.value}]: {exc.message}", file=sys.stderr)
        if exc.details: print(f"  details: {exc.details}", file=sys.stderr)
        return 1
```
Add `import sys` + `from job_aggregator.errors import JobAggregatorError`. Unexpected exceptions propagate with a traceback (honest bug failure).

**`cmd_initdb`** mkdir parent first:
```python
def cmd_initdb(args) -> int:
    from pathlib import Path
    from job_aggregator.config.store import seed_from_yaml
    from job_aggregator.logging_setup import configure_logging
    from job_aggregator.storage.db import connect, init_db
    configure_logging(args.log_level)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(args.db); init_db(conn); seed_from_yaml(conn)
    print(f"initialized database at {args.db}"); return 0
```
**`cmd_run`**: `summary = run_cycle(conn, cfg, SystemClock(), trigger="manual"); print(summary); return 0` (partial source failures aren't errors; only infra errors reach the envelope; a concurrent run raises `RunInProgressError` → envelope). **`cmd_serve`**: `uvicorn.run(..., factory=True)`, single process — WHY comment: never `--workers N` (each worker spins its own scheduler → N daily fires). **`cmd_show_config`**: prints `cfg.model_dump_json(indent=2)`.

### 6.3 Phase 8 hand-off (contract)

Dashboard lifespan wires it: `scheduler = JobScheduler(connect_fn=lambda: connect(db_path), clock=SystemClock()); scheduler.start(); ...; scheduler.stop()`. "Run now" route offloads the blocking `trigger_now` off the event loop (`await run_in_threadpool(scheduler.trigger_now, "manual")`) and maps `None → 409`.

### 6.4 Tests

`tests/test_scheduler.py` (deterministic; monkeypatch `run_cycle`/`load_effective_config`/`runs_repo.*` at source modules; `_DummyConn`, `_FakeScheduler` recording `add_job`, `_ok_summary()`, `_cfg(catch_up)` via `SimpleNamespace`): `test_should_catch_up` (parametrized `>=24h` boundary, never-ran→True, recent→False, future-skew→False); catch-up submits when no prior success (one job, `args==[TRIGGER_CATCHUP]`); skips when recent; disabled never submits; `test_lock_prevents_overlap` (a blocking `run_cycle` on one thread → second `trigger_now` returns None, body entered once); `test_db_active_run_skips_before_run_cycle` (truthy `current_run` → run_cycle never called); `test_trigger_now_returns_run_id`.

`tests/test_cli.py` (smoke): `--version` exits 0; `run` prints the summary (monkeypatched); a `ConfigError` → `error [config_invalid]` on stderr, exit 1.

### Acceptance check

```bash
pip install -e '.[dev]'
ruff check src/job_aggregator/scheduler/scheduler.py src/job_aggregator/cli.py tests/test_scheduler.py tests/test_cli.py
mypy src/job_aggregator/scheduler/scheduler.py src/job_aggregator/cli.py
pytest tests/test_scheduler.py tests/test_cli.py -q
# One real end-to-end cycle (Phases 1-5; needs network). Set only remoteok enabled for speed:
export JOBAGG_DB_PATH="$(mktemp -d)/jobs.db"
python -m job_aggregator initdb --db "$JOBAGG_DB_PATH"
python -m job_aggregator run --db "$JOBAGG_DB_PATH"
python -c "import os,sqlite3,sys; c=sqlite3.connect(os.environ['JOBAGG_DB_PATH']); \
n=c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]; print('jobs written:', n); sys.exit(0 if n else 1)"
```

---

## Phase 7 — Notifications

Builds `notify/`: a uniform `Notifier` abstraction plus three channels (Telegram digest, email digest, RSS/Atom feed), and **finalizes** `run_cycle` step 8 with `new_only` semantics. Notifiers run after the data is committed, so the governing rule is **a notifier failure must never fail the run** — notifiers log and swallow.

**This phase supersedes Phase 5's provisional step 8.** The final wiring lives here (single new-jobs source: `jobs_repo.jobs_new_in_run` for NEW_ONLY channels, `recent_active_jobs` for RSS). Two additive extensions to the frozen surface: `Notifier.feed_scope: FeedScope` (class attribute; does not change `notify_new`), and `build_notifiers(cfg, clock=None)` (optional trailing `clock` for deterministic RSS `<updated>`).

RSS is a **snapshot** (most-recent active jobs, `not hidden`, newest first, capped at `max_items`, regenerated every run even when 0 new). Telegram/email are event-driven and **skip when the new set is empty** (no spam).

### `notify/base.py`

```python
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING
from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job
if TYPE_CHECKING:
    from job_aggregator.clock import Clock
log = logging.getLogger(__name__)

class FeedScope(str, Enum):
    NEW_ONLY = "new_only"          # Telegram / email digest
    RECENT_ACTIVE = "recent_active"  # RSS snapshot

class Notifier(ABC):
    name: str
    feed_scope: FeedScope = FeedScope.NEW_ONLY
    @abstractmethod
    def notify_new(self, jobs: list[Job], cfg: Config) -> None:
        """Deliver `jobs`. MUST NOT raise; on failure log and return."""
        ...

def format_remote_or_location(job: Job) -> str:
    return "Remote" if job.is_remote else (job.location or "")

def format_salary(job: Job) -> str:
    if not job.salary_parsed: return ""
    lo, hi = job.salary_min, job.salary_max
    if lo is None and hi is None: return ""
    currency = job.salary_currency or "INR"; period = job.salary_period or "month"
    amount = f"{lo:,}-{hi:,}" if (lo is not None and hi is not None and lo != hi) else f"{(hi if hi is not None else lo):,}"
    return f"{currency} {amount}/{period}"

def format_meta(job: Job) -> str:
    return " · ".join(p for p in (format_remote_or_location(job), format_salary(job), job.source) if p)

def build_notifiers(cfg: Config, clock: "Clock | None" = None) -> list[Notifier]:
    from job_aggregator.clock import SystemClock
    from job_aggregator.notify.email import EmailNotifier
    from job_aggregator.notify.rss import RssNotifier
    from job_aggregator.notify.telegram import TelegramNotifier
    rc = clock or SystemClock(); out: list[Notifier] = []
    if cfg.notify.telegram.enabled: out.append(TelegramNotifier())
    if cfg.notify.email.enabled: out.append(EmailNotifier())
    if cfg.notify.rss.enabled: out.append(RssNotifier(clock=rc))
    return out
```
Never raises; all-disabled → `[]`. An enabled-but-unconfigured channel (missing token/recipient) is a safe dry-run (logs INFO, no I/O). Formatters read frozen `Job` fields (`salary_min/salary_max/salary_currency/salary_period/salary_parsed`).

### `notify/telegram.py`

Constants: `TELEGRAM_API_BASE="https://api.telegram.org"`, `TELEGRAM_TIMEOUT=10.0`, `TELEGRAM_MESSAGE_LIMIT=4096`, `MAX_TELEGRAM_JOBS=20`. Pure `build_digest(jobs, *, max_jobs=MAX_TELEGRAM_JOBS) -> str` renders HTML (`parse_mode="HTML"`) escaping all dynamic text via `_esc`/`_esc_attr`, singular/plural header, tappable title links, "…and N more" footer, truncates to `TELEGRAM_MESSAGE_LIMIT`. `TelegramNotifier(name="telegram", feed_scope=NEW_ONLY)`: `notify_new` returns early on empty; resolves `token`/`chat_id` from ctor or `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` env (missing → dry-run log); POSTs `sendMessage` (`chat_id/text/parse_mode=HTML/disable_web_page_preview=True`); wraps in `try/except Exception` (logged, swallowed).

### `notify/email.py`

Constants: `SMTP_TIMEOUT=15.0`, `EMAIL_SUBJECT_PREFIX="[JobAggregator]"`. Pure `build_email(jobs) -> tuple[str, str]` (subject + plain-text body). `EmailNotifier(name="email", feed_scope=NEW_ONLY, __init__(smtp=None))`: empty jobs / empty `cfg.notify.email.to` → dry-run; env `SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD` override config; only `starttls()`+`login()` when creds present (localhost:25 opensmtpd needs none); `send_message`; injected fake SMTP is never `quit()`-ed; all wrapped/swallowed.

### `notify/rss.py`

Atom 1.0 (RFC 3339 dates = `datetime.isoformat()`), rendered through an inline Jinja2 `Template(..., autoescape=True)`. Constants: `FEED_ID="urn:jobaggregator:feed"`, `FEED_TITLE`, `FEED_AUTHOR`, `FEED_GENERATOR`, `FEED_SITE_URL="http://localhost:8000"`. Pure `render_feed(jobs, cfg, clock) -> str`: caps at `cfg.notify.rss.max_items`; per-entry `<id>=urn:jobuid:<job_uid>`, `<title>=f"{title} — {company}"`, `<updated>=_rfc3339(posted_at or now)`, optional `<published>`, `<link href=url>`, `categories` (`source`, `remote`, `salary:<bucket>`), `<summary>=format_meta(job)`; empty list → still-valid feed. `RssNotifier(name="rss", feed_scope=RECENT_ACTIVE, __init__(clock=None, out_path=None))`: writes to a `.tmp` sibling then `replace()` (atomic); wrapped/swallowed. `_rfc3339` assumes naive → UTC.

### `storage/jobs_repo.py` reads (defined in Phase 1, used here)

`jobs_new_in_run(conn, run_id) -> list[Job]` (`status='new' AND last_seen_cycle=run_id`) and `recent_active_jobs(conn, limit) -> list[Job]` (`status IN ('new','active') AND hidden=0`, newest first), both via `_row_to_job`. `jobs_new_in_run` never re-notifies a stuck-`new` job from a failed source.

### Runner step 8 — FINAL (replaces Phase 5's provisional `_notify` body)

```python
# pipeline/runner.py — final step 8 (supersedes the provisional version)
def _notify(conn, run_id, cfg, clock, new_jobs_unused, notifiers) -> None:
    from job_aggregator.notify.base import FeedScope, build_notifiers
    new_jobs = jobs_repo.jobs_new_in_run(conn, run_id)
    recent = jobs_repo.recent_active_jobs(conn, cfg.notify.rss.max_items)
    resolved = list(notifiers) if notifiers is not None else build_notifiers(cfg, clock)
    for n in resolved:
        payload = recent if getattr(n, "feed_scope", FeedScope.NEW_ONLY) is FeedScope.RECENT_ACTIVE else new_jobs
        try: n.notify_new(payload, cfg)
        except Exception: logger.exception("notifier %s raised (ignored)", getattr(n, "name", type(n).__name__))
```
`cfg.notify.on == "new_only"` is the only supported mode; this wiring is that mode. Notify does not mutate run counts. (Phase 5's `RecordingNotifier` — NEW_ONLY — still receives `jobs_new_in_run(run_id)`, so its `test_new_jobs_notified_new_only` assertion `[["a"],["c"]]` holds under this final version too.)

### Tests (`tests/test_notify.py`)

Local `make_job` (frozen fields). Telegram `build_digest`: singular grammar; plural + cap (25 jobs, `max_jobs=20` → "25 new jobs", line "20." present, "21." absent, "…and 5 more."); HTML escape; truncation to limit. Telegram send (`@respx.mock` + env): posts message (assert `chat_id/parse_mode/disable_web_page_preview` in body); dry-run when token missing; empty jobs no send; swallows HTTP error (logged, no raise). Email `build_email`: subject/body, singular. Email send (injected `_FakeSMTP`): sends with `To`, no TLS/login without creds, not quit-ed; STARTTLS+login when creds present; dry-run when no recipient; swallows send error. RSS `render_feed` (parse back with `ElementTree`, NS Atom): well-formed (3 entries, feed `<updated>=="2026-07-15T03:00:00+00:00"`); escape round-trip (`"C & D <x> — Acme"`); caps at `max_items`; empty still valid. `RssNotifier` writes atomically (`out.exists()`, parses, no `.tmp` left). Factory: only-enabled (rss on → one notifier, `feed_scope RECENT_ACTIVE`; all on → `{telegram,email,rss}`); all-disabled → `[]`. new_only filtering (`conn` + repos): `jobs_new_in_run` selects only this run's new jobs (A re-seen→active excluded, B new included); excludes stuck-`new` from failed source; `recent_active_jobs` excludes hidden + deleted.

### Acceptance check

```bash
cd "/home/SammyUrfen/Codes/job aggregator"
ruff check src/job_aggregator/notify tests/test_notify.py
ruff format --check src/job_aggregator/notify tests/test_notify.py
mypy src/job_aggregator/notify
pytest tests/test_notify.py -q
```

---

## Phase 8 — Dashboard (FastAPI + Blood Orange theme)

The human control plane and the **source of truth for config** (edits take effect on the next run). Server-rendered FastAPI: sync `def` handlers (blocking `sqlite3` in Starlette's threadpool), Jinja2 templates, one hand-rolled CSS design-token system (Blood Orange), ~120 lines of vanilla JS (no CDN). It owns the scheduler via the lifespan.

**All SQL/templates use the frozen `schema.sql` column names** (per Canonical contracts): jobs `job_uid/posted_at/match_score/first_seen_at/last_seen_at`, the separate `hidden` 0/1 column, statuses `new/active/stale/deleted`; runs `run_id/n_new/n_updated/n_expired`, status `{running,success,partial,failed}`, trigger `{schedule,manual,startup_catchup}`; source_runs `run_id/source/succeeded/n_fetched/duration_ms/error`. Salary buckets are `pass/unknown/fail`.

### 8.1 File map

```
dashboard/
├── app.py  deps.py  routes_jobs.py  routes_config.py  routes_runs.py
├── templates/{base,jobs,config,runs}.html + partials/{job_row,run_status}.html
└── static/css/{theme,app}.css  static/js/app.js  static/favicon.svg
tests/test_dashboard.py
```

### 8.2 `app.py`

```python
def create_app(*, db_path=None, clock=None, scheduler=None) -> FastAPI:
    """Zero-arg call must fully work (uvicorn factory=True). Tests inject db_path/clock/scheduler."""
```
Flow: resolve defaults (`db_path = str(db_path or default_db_path())`, `clock = clock or SystemClock()`, `scheduler = scheduler or JobScheduler(connect_fn=lambda: connect(db_path), clock=clock)` — lazy-import `JobScheduler`/`connect`); lifespan starts/stops the scheduler; stash `db_path/clock/scheduler/templates` on `app.state`; mount `/static`; include routers; register exception handlers.

```python
@asynccontextmanager
async def lifespan(app):
    if not os.environ.get("JOBAGG_DISABLE_SCHEDULER"):   # OS-timer alternative (Phase 9)
        app.state.scheduler.start()
    try: yield
    finally:
        if not os.environ.get("JOBAGG_DISABLE_SCHEDULER"):
            app.state.scheduler.stop()
```
Error envelope: `_STATUS_BY_CODE = {CONFIG_INVALID:422, NOT_FOUND:404, RUN_IN_PROGRESS:409, STORAGE_ERROR:500, NOTIFY_FAILED:500, SOURCE_FETCH_FAILED:502, SOURCE_PARSE_FAILED:502, INTERNAL:500}`. Handlers: `JobAggregatorError` → `{"error":{"code","message","details}}` at the mapped status; `RequestValidationError` → 422 `code="validation_error"` with normalized `details.errors`; bare `Exception` → logged 500 `code="internal"`. Single `error_envelope(code, message, status, details=None)` helper in `deps.py`. `create_app()` must not touch the DB at build time.

### 8.3 `deps.py`

`SchedulerProtocol` (structural): `start()->None`, `stop()->None`, `trigger_now(trigger:str=...)->int|None`, property `next_run_at->datetime|None`. Per-request `get_conn` opens `connect(db_path)` and closes in `finally` (threadpool-safe). `get_config` = `load_effective_config(conn)`. `header_context` reads last run from DB (`SELECT run_id, status, trigger, started_at, finished_at, n_new, n_updated, n_expired, error FROM runs ORDER BY started_at DESC LIMIT 1`) and next run from `scheduler.next_run_at`.

### 8.4 `routes_jobs.py`

Constants: `PAGE_SIZE=50`, `MAX_Q_LEN=200`, `BUCKET_KEYS=("pass","unknown","fail")`, `_STATUS_VALUES=("new","active","stale","deleted")`, `_SORT_OPTIONS=("score","date","salary")`.
```python
_ORDER_BY = {
    "score":  "match_score DESC, posted_at DESC",
    "date":   "posted_at IS NULL, posted_at DESC, match_score DESC",
    "salary": "salary_min IS NULL, salary_min DESC, match_score DESC",
}
_ACTIONS = {
    "apply":("applied",1), "unapply":("applied",0),
    "bookmark":("bookmarked",1), "unbookmark":("bookmarked",0),
    "hide":("hidden",1), "unhide":("hidden",0),   # hide/unhide route through the hidden column
}
```
`JobQuery` (frozen dataclass): `q, source, remote("yes"/"no"/None), bucket, status(one of _STATUS_VALUES or "all" or None), show_hidden(bool), applied, bookmarked, sort, page`. `_parse_job_query` whitelists everything (unknown → safe default). `_query_jobs`: dynamic WHERE (all user values bound `?`); default view (`status is None`) → `status != 'deleted'`; `status=="all"` → no status filter; else `status = ?`; unless `show_hidden` → `hidden = 0`; remote/bucket/applied/bookmarked binds; `ORDER BY {_ORDER_BY[sort]} LIMIT ? OFFSET ?`. Only `_ORDER_BY[...]` (whitelisted) and `{column}` in `job_action` (from `_ACTIONS`) are interpolated.

`GET /` renders `jobs.html` with rows, `total`, pagination, distinct sources, `BUCKET_KEYS`, `_STATUS_VALUES`. Empty state (`rows==[]`) renders `data-testid="empty"`, not an error.

`POST /api/jobs/{uid}/action` (Pydantic `JobAction` with `Literal["apply","unapply","bookmark","unbookmark","hide","unhide"]`): `column, value = _ACTIONS[body.action]`; `UPDATE jobs SET {column}=? WHERE job_uid=?`; commit; `rowcount==0` → `NotFoundError` (404); else re-render `partials/job_row.html` for the row (client swaps `outerHTML`). Invalid action → `RequestValidationError` → 422.

### 8.5 `routes_config.py`

Merge submitted flat form fields onto the current config dict, then `Config.model_validate` (single validation authority) — preserves nested keys the form doesn't expose (`salary.fx_rates`, `jobspy.sites/search_terms`, ATS token lists). `ConfigForm` (Pydantic, `extra="ignore"`) covers schedule/salary/keywords/locations/notify/source-toggles. `_apply_form(current, f)` overlays editable fields onto a deep copy. `GET /config` renders the form pre-filled from `cfg.model_dump(mode="json")`. `PUT /api/config` (`Form()`): merge → validate; on `ValidationError` raise `ConfigError("config is invalid", details={"errors":[{"field":_dotted(loc),"message":msg}]})` → 422; else `save_config(conn, cfg)` + commit → `{"ok":True,"message":"Saved. Applies on the next run."}`. Because `SalaryConfig.min_remote/min_in_office` carry `ge=0` and `run_hour_local` carries `ge=0,le=23` and `on_missing` is a `Literal`, the invalid-config test cases actually reject.

### 8.6 `routes_runs.py`

`RUNS_HISTORY_LIMIT=50`. Reads: `_list_runs` (`SELECT run_id, trigger, status, started_at, finished_at, n_new, n_updated, n_expired, error FROM runs ORDER BY started_at DESC LIMIT ?`); `_source_runs` (`SELECT source, succeeded, n_fetched, duration_ms, error FROM source_runs WHERE run_id=? ORDER BY source` — template derives a display status from `succeeded`); `_current_run` (running row else most recent).
```python
@router.get("/runs", response_class=HTMLResponse) ...          # history + per-source breakdown
@router.post("/api/runs", status_code=202)
async def run_now(scheduler = Depends(get_scheduler)):
    run_id = await run_in_threadpool(scheduler.trigger_now, "manual")  # off the event loop
    if run_id is None: raise RunInProgressError("a run is already in progress")  # -> 409 envelope
    return JSONResponse(status_code=202, content={"run_id": run_id, "status": "running"})
@router.get("/api/runs/current") ...   # {run_id,status,trigger,counts:{new,updated,expired},sources,next_run_at} or idle
```
`GET /api/runs/current` returns `n_new/n_updated/n_expired` under `counts`, `sources` from `_source_runs`, and `status` in `{running,success,partial,failed}` (render `partial` as a first-class pill). The poller stops on `status != "running"`.

### 8.7 Templates

Jinja2 autoescape on; never `| safe` on job data. `base.html` is a real HTML document with viewport meta, favicon, `theme.css`+`app.css`, anti-FOUC inline script (reads `localStorage.theme`, sets `data-theme` before paint), header (nav Jobs/Config/Runs, run-status pill, Run-now button, theme toggle, "Last run … · Next …"). `jobs.html` — GET filter form (`q/source/remote/bucket/status/show_hidden/applied/bookmarked/sort`), table over `partials/job_row.html`, empty-state block, pagination. `config.html` — one form (JS PUTs), fieldsets, field names matching `ConfigForm`, inline `.field-error[data-field-error="salary.min_remote"]` slots, success banner, "applies on the next run" note. `runs.html` — current-run card, history table (Started links to `/runs?run_id=`), source breakdown. `partials/job_row.html` — `<tr data-uid="{{ job.job_uid }}">` with title link (`rel="noopener"`), badges (source, Remote/On-site, salary bucket `bucket-{{ job.salary_bucket }}` showing `₹{{ job.salary_min }}–{{ job.salary_max }}/{{ job.salary_period }}` or `—`), `match_score`, `posted_at or '—'`, action buttons carrying `data-action`+`data-uid` reflecting `applied/bookmarked/hidden`. `partials/run_status.html` — status pill `pill-{{ current.status }}` (supports `partial`) with stable ids (`#rs-pill`, `#rs-new`, …).

### 8.8 Static

`theme.css` — Blood Orange tokens, exact hex (light default + `@media (prefers-color-scheme: dark)` + `:root[data-theme=...]` overrides that win both ways). Core: `--bg:#FBF3EA`, `--surface-1:#FFF9F3`, `--accent:#E23F3F`, `--text:#361F1C`, `--border:#E7D6C8`, `--on-accent:#FFF9F3`; dark `--bg:#241713`, `--accent:#FF6B5B`, `--text:#F7E1DA`; status `--ok:#2F7A4F`(dark `#7CC79A`), `--warn:#B23A2A`(dark `#FF9B8B`), `--run:#E23F3F`(dark `#FF6B5B`). Salary buckets map to the three states: `bucket-pass`→`--ok`, `bucket-fail`→`--warn`, `bucket-unknown`→neutral surface. `app.css` — layout/table/badges/pills (incl. `.pill-partial{background:var(--warn)}`), tokens only, wide tables in `.table-wrap{overflow-x:auto}`. `app.js` — `POLL_INTERVAL_MS=2000`; theme toggle (flip `data-theme` + `localStorage`); Run-now (`POST /api/runs`; on 202 disable + poll `/api/runs/current` updating `#rs-pill`/counts, stop on non-`running`; on 409 show "already in progress"); delegated row actions (`POST /api/jobs/{uid}/action` JSON, swap `tr.outerHTML`, alert `error.message` on failure); config submit (`PUT /api/config` FormData, clear `.field-error`, on 422 fill each `[data-field-error]`). `favicon.svg` — self-contained disc.

### 8.10 Tests (`tests/test_dashboard.py`)

`TestClient` as a context manager (runs lifespan). `FakeScheduler` (implements `SchedulerProtocol`: `start/stop`, `next_run_at`, `trigger_now(trigger="manual")` inserts a `'running'` `runs` row and returns its `run_id`; `busy=True` → returns `None`). `db_path` fixture seeds frozen columns. `_seed_jobs` covers the matrix (e.g. `j1` remoteok remote bucket=pass applied=1 status=active score=9 salary 250000; `j2` naukri onsite bucket=unknown bookmarked=1 status=new salary NULL; `j3` linkedin remote bucket=pass hidden=1 status=active). `_seed_runs` inserts one `success` run (`run_id=1`, `n_new/n_updated/n_expired`) + 2 `source_runs`.

Tests: index renders jobs + header; default hides `hidden` (`j3` absent unless `show_hidden`); filter source/remote/bucket(`pass`/`unknown`)/applied/bookmarked/q; sort table-driven (score/date/salary null-last via `data-uid="jX"` order); pagination (page 1 = `PAGE_SIZE`, page 2 remainder + prev); empty state (`data-testid="empty"`); action apply persists (`applied=1`) and returns `<tr data-uid="j2"`; hide→`hidden=1`, unhide→`hidden=0`; unknown uid → 404 `not_found`; invalid action → 422 `validation_error`; config page 200 (shows `run_hour_local=3`); config PUT valid saves + preserves `salary.fx_rates`; config PUT invalid → 422 `config_invalid` with dotted `details.errors[0].field` (parametrized `run_hour_local=99`, `salary_min_remote=-5`, `salary_on_missing="bogus"` — all now genuinely rejected by the schema bounds); runs page 200 (shows run + source names); `POST /api/runs` → 202 integer `run_id`, follow-up `/api/runs/current` shows `running`; conflict (`busy=True`) → 409 `run_in_progress`; current idle when empty; theme.css exact tokens (`#E23F3F`, `#FF6B5B`, `#FBF3EA`, `#241713`, `[data-theme="dark"]`); favicon served (`image/svg`); lifespan starts+stops scheduler.

### Acceptance check

```bash
pip install -e ".[dev]"
ruff check src/job_aggregator/dashboard tests/test_dashboard.py
ruff format --check src/job_aggregator/dashboard
mypy src/job_aggregator/dashboard
pytest tests/test_dashboard.py -q
python -m job_aggregator initdb
python -m job_aggregator serve --port 8000 & sleep 2
curl -fsS http://127.0.0.1:8000/       >/dev/null
curl -fsS http://127.0.0.1:8000/config >/dev/null
curl -fsS http://127.0.0.1:8000/runs   >/dev/null
curl -fsS http://127.0.0.1:8000/static/css/theme.css | grep -q '#E23F3F'
curl -fsS -X POST http://127.0.0.1:8000/api/runs | grep -q run_id
kill %1
```
Manual/Playwright (throwaway port + temp `JOBAGG_DB_PATH`): theme toggle survives reload and beats OS preference, no FOUC; empty state; row actions swap+persist; config inline error → fix → "Saved. Applies on the next run."; Run-now pill running→success/partial/failed; second click while running shows "already in progress"; console/network clean, no external requests.

---

## Phase 9 — Polish, hardening, docs

Hand-off grade: make the whole toolchain gate green, enforce a coverage target on the correctness core, write the four docs a stranger needs, and add three clearly-optional extras (Internshala BS4 adapter, Dockerfile, systemd `.service`+`.timer`).

**zsh gotcha:** `pip install -e .[dev]` fails in zsh (`no matches found`). Every command quotes it: `pip install -e '.[dev]'`.

### 9.0 The "all green" gate + coverage

Four commands must exit 0: `ruff check .`, `ruff format --check .`, `mypy src`, `pytest`. Additive `pyproject.toml` edits:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers --cov=job_aggregator --cov-report=term-missing --cov-fail-under=85"
pythonpath = ["src"]
markers = [
    "network: real network I/O; deselected by default. Run with `-m network`.",
    "slow: slower integration test (e2e, full run_cycle).",
]
[tool.coverage.run]
branch = true
source = ["job_aggregator"]
omit = ["*/__main__.py", "*/cli.py", "*/dashboard/*", "*/logging_setup.py"]
[tool.coverage.report]
show_missing = true
exclude_lines = ["pragma: no cover", "if TYPE_CHECKING:", "raise NotImplementedError", "\\.\\.\\."]
```
Coverage: **overall hard gate 85%**; **correctness core (`storage`,`pipeline`,`config`,`models`) ≥90%**, checked on demand:
```bash
pytest --cov=job_aggregator --cov-report=
coverage report --include="*/storage/*,*/pipeline/*,*/config/*,*/models/*"
```
Document (honesty): the network-bound sources/notify adapters pull the overall number down and are respx-mocked only — the number does not prove the live boards still return the captured shapes. If building the Internshala adapter, add `"bs4.*"` to the mypy `ignore_missing_imports` override (no scattered `# type: ignore`).

### 9.1 `README.md`

Sections in order: title + one-liner (verbatim `pyproject` description); What & Why; Architecture at a glance (package tree + one-line data flow `fetch → normalize → dedup → salary → filter → upsert → stale-delete → notify`); Quickstart (below); config-in-dashboard; scheduling (in-process vs cron/systemd); Sources table; **Limitations** (mandatory); Development (the four-command gate + links); License MIT.

Quickstart:
```bash
conda create -n job-aggregator python=3.11 -y && conda activate job-aggregator
pip install -e '.[dev]'
cp .env.example .env
python -m job_aggregator initdb
python -m job_aggregator run
python -m job_aggregator serve            # http://127.0.0.1:8000
```
Config-in-dashboard wording: on first `initdb`, `default_config.yaml` seeds the single-row `config` table; thereafter the dashboard is the source of truth; edits validate against the Pydantic schema and apply on the **next run**; secrets never live in the config row. Scheduling: default in-process `BackgroundScheduler` (single process only — never `--workers N`); alternative OS-owned via `JOBAGG_DISABLE_SCHEDULER=1` + a systemd `.timer` or cron driving `python -m job_aggregator run`. Sources table (Tier A jobspy; Tier B Unstop/RemoteOK/Himalayas/Jobicy/Remotive-off; keyed Adzuna/Jooble; ATS ×4). Limitations: dead-end sources (twitter/wellfound/hiring.cafe/jsearch); no-salary (Greenhouse/SmartRecruiters), no-is_remote (Greenhouse/Adzuna, inferred); LinkedIn rate-limited (blocked board = partial, not crash); single-process scheduler; approximate hand-maintained FX rates; Internshala adapter HTML-scraped/off by default.

### 9.2 `docs/ats_token_lists.md`

Why tokens; per-ATS careers-URL pattern + no-auth verify `curl` (Greenhouse `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true`; Lever `api.lever.co/v0/postings/<slug>?mode=json` — bare array, bad slug `{"ok":false}`; Ashby `api.ashbyhq.com/posting-api/job-board/<Org>?includeCompensation=true` — case-sensitive; SmartRecruiters `.../companies/<CompanyId>/postings?country=in`). Confirmed-live test tokens (`stripe`, `gopuff`, `Ramp`, `Visa`). Curated India/remote candidates marked **candidate — verify** (razorpay/postman/zerodha/groww/cred/meesho/hasura on Greenhouse, etc.). Optional `scripts/verify_ats.sh`.

### 9.3 `docs/testing.md`

Layout (`tests/` mirrors `src/`; golden captures in `tests/fixtures/`). Run commands (`pytest`, `-m network`, `--cov-report=html`). Three house rules with snippets: injected clock never `datetime.now()`; respx for all HTTP; golden fixtures for adapters. Shared conftest spec (real tmp-file DB, `FixedClock`, `load_fixture`/`load_json_fixture`). Table-driven convention. The deterministic run-cycle harness (FakeSource + FixedClock; advance past `grace_days` to pin stale-delete). Coverage target restated.

### 9.4 `TROUBLESHOOTING.md`

Symptom→cause→fix table (living doc). Rows include: `no matches found: .[dev]` (quote it); wrong-CWD `initdb`; Indeed/Glassdoor 0 rows (`country_indeed`); LinkedIn 429 (partial not fatal); `linkedin_fetch_description=True` slower; jobspy silently-ignored filters; `SQLite objects created in a thread...` (open connection inside the job, never `check_same_thread=False`); daily job fires N times (`--workers`); daily run skipped (misfire grace default 1s → Phase 6 sets it high); config save `422 extra_forbidden`; `python-multipart` missing; RemoteOK 403 (browser UA + skip element[0]); Adzuna 400 (keys/country); Unstop 2022 posts (`max_age_days` mandatory); mypy stub-missing (`ignore_missing_imports` list); `ruff format --check` fails (commit formatting); undeclared pytest marker; `run` 0 new but rows returned (filters too strict); Internshala 0 rows (selectors drifted). Expanded notes for the sqlite-thread rule, multi-scheduler double-fire, and misfire-grace default.

### 9.5 Optional extras (not required for acceptance)

- **`sources/internshala.py` `InternshalaSource`** — optional (`[project.optional-dependencies].internshala = ["beautifulsoup4>=4.12","lxml>=5.2"]`), off by default (`config.sources.internshala.enabled=false`). Conforms to the frozen `Source` contract: `fetch(self, cfg, clock) -> SourceResult`, never raises. Pure helpers `_listing_url`, `_parse_listing(html, clock)`, `_parse_stipend`, `_parse_posted`. Browser UA mandatory. Selectors are best-effort (no API) and pinned by `tests/fixtures/internshala_listing.html`. Tests: `_parse_stipend` (fixed/range/Unpaid/perf/empty), `_parse_posted` (FixedClock), golden `_parse_listing`, empty listing, `fetch_success`, dedup across search terms, `503`/`403` → failed `SourceResult` (no crash).
- **`Dockerfile`** — `python:3.11-slim`, non-root, `/data` volume, `ENV JOBAGG_DATA_DIR=/data`, seeds on first boot then `serve --host 0.0.0.0`; single process only (no `--workers`). `.dockerignore` mirrors `.gitignore`.
- **`deploy/job-aggregator.{service,timer}`** — `Type=oneshot` service running `python -m job_aggregator run`; `OnCalendar=*-*-* 03:00:00`, `Persistent=true`, `RandomizedDelaySec=300`. If used, run `serve` with `JOBAGG_DISABLE_SCHEDULER=1` (or don't run `serve`) to avoid double-fetching.

### 9.6 Phase-9 hardening tests (not optional)

`tests/test_cli.py`: all subcommands parse; `parse_args([])` → `SystemExit(2)`; `--help` exit 0; `--version` prints `__version__`; `test_cli_import_is_stdlib_only` (subprocess: importing `job_aggregator.cli` must not pull `{fastapi, jobspy, uvicorn, apscheduler, pandas, httpx}` into `sys.modules`). `tests/test_docs.py`: required docs exist + non-empty; README quickstart uses real subcommands and the quoted `pip install -e '.[dev]'`. `tests/test_e2e_offline.py` (`@pytest.mark.slow`): monkeypatch `build_enabled_sources` to a single `FakeSource` (3 RawPostings, two near-duplicate at same URL); `run_cycle` → `n_new==2` (dedup collapse), rows persisted, re-run → `n_new==0, n_updated==3` (idempotent upsert); then source returns `[]`, advance `grace_days-1` → still present, advance past `grace_days` → `n_expired>0` and row gone.

### Acceptance check

```bash
conda activate job-aggregator
pip install -e '.[dev]'
ruff check .
ruff format --check .
mypy src
pytest                       # all tests pass AND coverage >= 85%
pytest --cov=job_aggregator --cov-report=
coverage report --include="*/storage/*,*/pipeline/*,*/config/*,*/models/*"   # each >= 90%
```
Manual new-user end-to-end: `conda create` → `pip install -e '.[dev]'` → `cp .env.example .env` → `initdb` → `run` (prints a RunSummary) → `serve` (jobs listed). Docs present: `README.md`, `docs/ats_token_lists.md`, `docs/testing.md`, `TROUBLESHOOTING.md`. Optional extras, if built, independently green (`pytest tests/sources/test_internshala.py`; `docker build .`; `systemd-analyze verify deploy/*`).

---

## Definition of done (whole project)

- [ ] **Toolchain gate is green from the repo root:** `ruff check .`, `ruff format --check .`, `mypy src`, `pytest` all exit 0.
- [ ] **Coverage:** overall ≥85% (`--cov-fail-under=85`); correctness core (`storage`,`pipeline`,`config`,`models`) ≥90% via the scoped `coverage report`.
- [ ] **Every phase's Acceptance check (0–9) passes** in order, each in isolation given its predecessors.
- [ ] **Contracts hold end-to-end:** the frozen `Job` model + `SalaryBucket(pass/unknown/fail)` + `JobStatus(new/active/stale/deleted)`; `SourceResult(source,succeeded,jobs,n_fetched,duration_ms,error,sub_results)` + `Source.fetch(cfg, clock)`; `content_hash` full 64-char; `upsert_job(...) -> "new"|"updated"`; `record_source_run`/`finish_run` source/status positional; `last_successful_run` = `status='success'` only; all `schema.sql` column names used verbatim across storage/notify/dashboard.
- [ ] **CLI works:** `python -m job_aggregator --help` is stdlib-only; `initdb`, `run`, `serve`, `show-config` all function; the console script and `python -m` forms are equivalent.
- [ ] **A real cycle persists jobs:** `initdb` then `run` writes rows; re-running is idempotent (`n_new==0, n_updated>0`); user flags survive re-scrapes.
- [ ] **Correctness crux verified:** a failed/blocked source never expires its jobs; a succeeded source's vanished jobs go `stale → deleted` at the grace boundary; cross-source duplicates collapse to one row (first-seen wins).
- [ ] **Notifications:** `jobs_new_in_run` never re-notifies; digest channels skip empty sets; RSS regenerates every run and is well-formed Atom; a notifier failure never fails the run; disabled/unconfigured channels are safe no-ops.
- [ ] **Dashboard:** `serve` boots single-process; `/`, `/config`, `/runs` return 200; config edits validate (bad values 422 inline) and apply on the next run; "Run now" → poll → final status (incl. `partial`); light/dark theme survives reload and beats the OS preference; no external requests, no console errors.
- [ ] **Scheduler:** in-process daily run at `run_hour_local`; startup catch-up fires only when no `success` in ~24h; run-lock prevents overlap in- and cross-process; `JOBAGG_DISABLE_SCHEDULER=1` cleanly hands scheduling to an OS timer.
- [ ] **Docs present and guarded:** `README.md`, `docs/ats_token_lists.md`, `docs/testing.md`, `TROUBLESHOOTING.md` exist, are non-empty, and use real subcommands + the zsh-quoted install.
- [ ] **Hygiene:** `__pycache__`, `data/*`, `.env`, build artifacts are gitignored; no committed binaries; commits (if any) are terse and trailer-free.
