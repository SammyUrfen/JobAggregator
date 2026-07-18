-- JobAggregator SQLite schema (authoritative; see PLAN.md §2.3).
-- Applied idempotently by storage.db.init_db(). Hand-written SQL, no ORM.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- One row per aggregation cycle.
CREATE TABLE IF NOT EXISTS runs (
  run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at    TEXT NOT NULL,           -- ISO8601 UTC
  finished_at   TEXT,
  status        TEXT NOT NULL,           -- 'running' | 'success' | 'partial' | 'failed'
  trigger       TEXT NOT NULL,           -- 'schedule' | 'manual' | 'startup_catchup'
  n_sources_ok  INTEGER DEFAULT 0,
  n_sources_err INTEGER DEFAULT 0,
  n_new         INTEGER DEFAULT 0,
  n_updated     INTEGER DEFAULT 0,
  n_expired     INTEGER DEFAULT 0,
  error         TEXT
);

-- Per-source outcome within a cycle. `succeeded` is the STALE-DELETE GUARD: a source that
-- failed (429, network error, suspicious-empty) must NOT have its jobs expired (PLAN §4.5).
CREATE TABLE IF NOT EXISTS source_runs (
  run_id      INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  source      TEXT NOT NULL,             -- e.g. 'jobspy_naukri', 'greenhouse', 'remoteok'
  succeeded   INTEGER NOT NULL,          -- 1 = safe to expire this source's jobs this cycle
  n_fetched   INTEGER,
  duration_ms INTEGER,
  error       TEXT,
  PRIMARY KEY (run_id, source)
);

-- One row per (deduplicated) job posting.
CREATE TABLE IF NOT EXISTS jobs (
  job_uid          TEXT PRIMARY KEY,     -- sha256(norm(company)|norm(title)|norm(location))
  source           TEXT NOT NULL,
  source_native_id TEXT,
  title            TEXT NOT NULL,
  company          TEXT NOT NULL,
  location         TEXT,
  is_remote        INTEGER,              -- 0 | 1 | NULL
  url              TEXT NOT NULL,        -- canonicalized (tracking params stripped)
  description      TEXT,
  salary_min       INTEGER,              -- normalized to INR/month
  salary_max       INTEGER,
  salary_currency  TEXT,
  salary_period    TEXT,                 -- 'month' | 'year' | 'hour'
  salary_raw       TEXT,                 -- original string, for auditing
  salary_parsed    INTEGER NOT NULL DEFAULT 0,
  salary_bucket    TEXT,                 -- 'pass' | 'unknown' | 'fail'
  match_score      REAL,
  is_internship    INTEGER NOT NULL DEFAULT 0,  -- title-detected internship/trainee flag
  posted_at        TEXT,                 -- ISO8601
  first_seen_at    TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  last_seen_cycle  INTEGER NOT NULL REFERENCES runs(run_id),
  status           TEXT NOT NULL,        -- 'new' | 'active' | 'stale' | 'deleted'
  -- USER FIELDS: must survive upserts (never overwritten by a re-fetch).
  applied          INTEGER NOT NULL DEFAULT 0,
  bookmarked       INTEGER NOT NULL DEFAULT 0,
  hidden           INTEGER NOT NULL DEFAULT 0,
  notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status     ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_source     ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_last_cycle ON jobs(last_seen_cycle);
CREATE INDEX IF NOT EXISTS idx_jobs_score      ON jobs(match_score);

-- Single-row effective configuration (JSON). The dashboard writes it; the runner reads it
-- at the start of each cycle, so edits take effect on the NEXT run.
CREATE TABLE IF NOT EXISTS config (
  id         INTEGER PRIMARY KEY CHECK (id = 1),
  data       TEXT NOT NULL,             -- Config serialized as JSON
  updated_at TEXT NOT NULL
);
