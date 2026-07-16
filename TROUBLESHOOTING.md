# Troubleshooting

A living **Symptom → Cause → Fix** reference for JobAggregator. Entries are accurate to the
current codebase; add new rows as you hit new failure modes. Repo path has a space —
always quote it in shells.

## Install & environment

| Symptom | Cause | Fix |
|---|---|---|
| `zsh: no matches found: .[dev]` | zsh tries to glob the `[dev]` extras spec before pip sees it. | Quote it: `pip install -e '.[dev]'`. |
| `ModuleNotFoundError` / form posts 500 on `python-multipart` | The `/config` editor submits an HTML form; FastAPI needs `python-multipart` to parse it. It's a declared dependency. | Install extras: `pip install -e '.[dev]'` (pins `python-multipart>=0.0.9`). |
| mypy: `library stubs not installed` for a new dep | Third-party package ships no type stubs. | Add the module to the `[[tool.mypy.overrides]]` block with `ignore_missing_imports = true` in `pyproject.toml`. Don't scatter `# type: ignore` at call sites. |
| CI fails on `ruff format --check` | A file isn't formatted to ruff's style. | Run `ruff format .`, then commit the result. |

## CLI & database

| Symptom | Cause | Fix |
|---|---|---|
| `run`/`serve` complains you didn't `initdb` | Not normally a problem — `run` and `serve` auto-initialize the DB idempotently. The message `database not initialized — run initdb first` (raised in `config/store.py`) only appears on a path that is read-only or points somewhere the tables were never created. | Point `--db` at a writable path, or run `python -m job_aggregator initdb --db <path>` explicitly. |
| `SQLite objects created in a thread can only be used in that same thread` | A single `sqlite3.Connection` was shared across threads (scheduler job body vs. request handlers). SQLite connections default to `check_same_thread=True`. | Open a **fresh** connection inside each run/request via the `connect_fn` factory (as the scheduler and dashboard do). Never share one connection across threads, and don't paper over it with `check_same_thread=False`. |
| "A run is already in progress" on every trigger; a run seems wedged forever | A crash left a `runs` row in status `running`, so the run-lock never clears. | Auto-healed: `reconcile_orphan_runs` reaps orphaned `running` rows at startup (restart `serve`) and the next `run` self-heals too. In a multi-process deployment, don't run the systemd timer **and** the in-process scheduler at once — set `JOBAGG_DISABLE_SCHEDULER=1` for `serve`. |

## Scheduling

| Symptom | Cause | Fix |
|---|---|---|
| The daily job fires N times in one cycle | You launched uvicorn with `--workers N`; each worker starts its own in-process scheduler. | Run `serve` as a **single** process (one worker). If you need multiple workers, disable the in-process scheduler (`JOBAGG_DISABLE_SCHEDULER=1`) and drive runs from one external OS timer. |
| A daily run looks skipped after the laptop slept through its slot | APScheduler misfire grace is deliberately high (`MISFIRE_GRACE_SECONDS = 3600`) plus a startup catch-up, so a missed slot still fires. | Nothing to fix. Catch-up is suppressed only when a **successful** run happened in the last ~24h — `last_successful_run` is status=`success` only, so a partial run still triggers catch-up. |

## Config editor

| Symptom | Cause | Fix |
|---|---|---|
| Saving config returns **422** | A field failed the Pydantic schema (e.g. `run_hour_local` outside 0–23, a negative salary floor, an invalid `on_missing`). | Read the inline error — the dashboard renders the dotted field path next to the offending input. Correct that field and re-save. |
| A config checkbox won't turn **OFF** | Historic HTML-form quirk: an unchecked checkbox sends nothing, so the field looked "unchanged" instead of `false`. | Fixed — the JS now sends an explicit `true`/`false`. If you POST the config API directly, send the field as the string `"false"`; do **not** omit it. |

## Sources returning nothing

| Symptom | Cause | Fix |
|---|---|---|
| Indeed / Glassdoor return 0 rows | These sites require `country_indeed`, and jobspy silently drops filters when they're combined — Indeed drops `is_remote` if it's also passed. | Set `sources.jobspy.country_indeed` (default `india`). The adapter already omits `is_remote` for Indeed by design (`_SITES_NO_IS_REMOTE`), so leave that alone. |
| LinkedIn returns HTTP **429** | Expected — LinkedIn rate-limits scraping. | No fix needed. The per-site success guard turns it into a **PARTIAL** run (that site is skipped for stale-deletion), never a crash. Other sites in the same cycle are unaffected. |
| RemoteOK returns HTTP **403** | Needs a browser `User-Agent`; also its API's `element[0]` is a legal/attribution notice, not a job. | Already handled — the shared HTTP client sends a browser UA (`_http.BROWSER_UA`) and the adapter strips `element[0]`. If you still see 403, verify the UA header isn't being overridden. |
| Adzuna returns **400** / no results | Missing or wrong credentials. | Set `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` in `.env` and confirm the configured country code is valid. Without them the source logs a warning and is skipped. |
| Unstop shows postings from 2022 | Unstop's feed isn't recency-sorted on its own. | The `max_age_days` recency filter is mandatory and applied in the adapter (`cutoff = now - max_age_days`). Tighten `sources.unstop.max_age_days` in `/config` if stale posts still slip through. |

## Results look wrong

| Symptom | Cause | Fix |
|---|---|---|
| `run` prints `0 new` but the dashboard shows rows | Nothing is broken — your filters rejected every fetched posting before upsert. | Loosen the filters in `/config`: `require_level`/`roles`, `locations`, and the salary floors are strict by default. Existing rows in the dashboard are from earlier, laxer runs. |

## Security posture

| Symptom | Cause | Fix |
|---|---|---|
| The dashboard has no login | By design — this is a personal, single-user tool. | Bind to `127.0.0.1` only. Do **not** expose the dashboard to a network or the public internet; there is no auth layer to protect it. |

## Limitations

- These rows cover the failure modes seen so far; they are **not** exhaustive. Novel upstream
  changes (a source altering its API, a new rate-limit response) may surface symptoms not listed
  here — add them as you find them.
- Source-specific behavior (Indeed/LinkedIn/RemoteOK/Adzuna/Unstop quirks) reflects those
  services **as observed**; scrapers are inherently brittle and third parties can change without
  notice, so a fix here can go stale.
- Fixes assume the standard local setup (conda env `job-aggregator`, SQLite, single-process
  `serve`). Multi-process or externally-orchestrated deployments (systemd timer + workers) have
  extra failure modes only partially covered above.
- This is a living document, not a guarantee: verify a fix against your actual run before trusting
  it.
