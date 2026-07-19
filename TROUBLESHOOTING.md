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
| Almost no internships in the feed | Pre-2026-07-18 behavior: Unstop ignored `search_terms` (generic firehose), Adzuna/Jooble queried only role phrases, jobspy never passed `job_type`, and intern stipends died on the full-time salary floor. | Fixed: Unstop sends `searchTerm`, Adzuna adds a `title_only=intern` walk, jobspy passes `job_type=internship` (+ LinkedIn descriptions), Internshala is a first-class source, and internships use `salary.min_internship` (default 0) + a score boost. Use the **Internships** filter chip on the dashboard. |
| A good job disappeared after a few days though it's still live | Old absence-based expiry: page-capped ("windowed") fetches can't see everything, and unseen used to mean stale→deleted. | Fixed: windowed sources (adzuna/jooble/jobspy + capped unstop/internshala walks) retire jobs by **posting age** (`schedule.windowed_retire_days`, default 30), not absence. Exhaustive sources (ATS boards) keep absence-based expiry. |
| A relevant posting was dropped as `experience:<N>y` | The description demands more years than `keywords.max_experience_years` (default 2). Rarely, a company blurb ("10+ years serving clients") false-matches. | Raise `max_experience_years` in `/config`, or set it to 0 to disable the gate. Internships are always exempt. |
| The tailored résumé / apply agent seems to know little about the job | Thin-description sources (Internshala/Unstop cards) give the pipeline only a title + a short blurb — so tailoring and form-fill have little to work with. | Open the job's detail modal and paste the **real posting text** (and notes like notice period / availability / screening-question answers) into **Extra context**, then **Save context** (or just hit Tailor/Apply — they send it too). It's folded into the JD for tailoring and handed to the apply agent to fill specific fields. Stored per-job, survives re-fetch. |
| Internshala descriptions are all the same short line ("Internshala listing: …") | The listing pages only carry a category slug — the real JD lives on the detail page, which the daily run doesn't crawl (robots is ambiguous about bulk detail crawling). | Fixed: opening an Internshala job in the modal fetches its real JD on demand (one page, human-triggered) and caches it (`full_description`, survives re-fetch), so subsequent opens are instant and tailoring/apply use the full text too. A failed fetch falls back to the slug. |
| Adzuna / Jooble descriptions cut off with "…" | **Data, not UI**: the Adzuna free API returns a 500-char preview and Jooble a ~300-char snippet — the full text only exists on the posting. The modal cap is 12000 chars, so nothing's truncated on render. | Nothing to fix — the modal shows a "this source only returns a short preview" note; click **Open original posting** for the full description. |
| The Tailor button just reorders projects, doesn't reword bullets | Pre-2026-07-19 the dashboard tailor was deterministic-only (keyword-ranked selection + skill reorder, bullets verbatim) — the LLM was never invoked. | Fixed: with `tailor_with_llm` on, the LLM (Claude Code by default) now **chooses which projects appear** from your whole portfolio AND rewords their bullets behind the anti-fabrication guard, in one call. Preview badge: **LLM-chosen + reworded** vs **deterministic**. |
| The résumé always shows the same projects regardless of the job | With `tailor_with_llm` off it's pure keyword ranking (deterministic). | Turn `tailor_with_llm` on in `/config` — the LLM then reads the JD + all your projects and selects the most relevant ones for that specific job (a keyword-poor but relevant project can now be chosen). Un-chosen projects don't appear. |
| Tailoring flags "LLM selection unusable — used keyword ranking" | The model's reply had no parseable `### <project>` headers, so it couldn't be attributed — it safely fell back to deterministic keyword selection with untouched bullets. | Retry (usually transient); persistent → switch `resume.agent_command` to a stronger model or set `tailor_with_llm: false`. |
| Tailoring takes ~1 minute / the button spins a while | The coding-agent backend spawns `claude -p`; the model reads your whole portfolio (all ~15 projects) to select the best ones and reword them in one call. `--model sonnet` (default) beats the inherited opus; the session runs lean (`--strict-mcp-config` — no serena/playwright MCP spawn — plus tools off). | Expected for LLM selection over the whole portfolio. Faster paths: an OpenAI-compatible endpoint (`resume.backend: openai_compatible` + key in `.env`), or `resume.tailor_with_llm: false` for instant deterministic keyword selection. Edit `resume.agent_command` to change the model. |

## Apply agent (Track D)

| Symptom | Cause | Fix |
|---|---|---|
| The agent lands on the posting, not the form ("it has Apply / Easy Apply / Quick Apply") | Job URLs point at POSTINGS; the form is behind the apply button, sometimes a wizard/login. The old deterministic fill couldn't navigate that. | Fixed by the **agentic engine** (`apply.engine: agentic`, default): a Claude session drives the visible Chromium via the playwright MCP — clicks through to the form, fills it, attaches the résumé, and never submits. Watch progress live: `tail -f data/apply_agent.log`. |
| Easy/Quick Apply demands login though I'm logged in, in Zen | The agent's Chromium is a separate browser with an empty cookie jar. | Fixed: `apply.use_browser_cookies` (default on) imports the posting site's cookies from your Zen profile (read-only copy of `cookies.sqlite`, only that domain's rows). No Zen cookies for the site → the agent pauses at the login wall for you, then continues; that login is also saved (encrypted) for next time. |
| The agent reached the screening questions but left them blank | Pre-2026-07-19 the agent only had your contact fields, and its rule said "never invent an answer" — so an open-ended question ("why this role", "describe your experience", a short design question) it couldn't answer from contact data was left empty and reported unfilled. | Fixed: the agent now gets your full **background** (summary, projects, skills, education) and the rule tells it to **draft** a concise, truthful answer for screening/essay questions (grounded only in that background + your Extra context, no invented facts). You still review before submitting. For a specific answer you want, paste it into **Extra context** — it's highest priority. |
| A captcha appeared mid-apply | Sites challenge automated-looking sessions. | By design the agent **waits**: solve the captcha in the very window it is driving; it polls (~15s intervals, up to ~10 min) and continues. The session's hard bound is `apply.agent_timeout_s` (default 900s). |
| LinkedIn drops to a sign-in / "join now" wall after clicking Easy Apply | LinkedIn's anti-bot distrusts a fresh Chromium even with imported cookies and invalidates the session on the apply click — the documented best-effort caveat for LinkedIn/Naukri. | Log in inside that same window when the wall appears; the agent waits and continues once the form is back. ATS boards (Greenhouse/Lever/Ashby/Internshala/Unstop) don't do this and are the reliable path. If it keeps bouncing, hit **Stop** and apply manually. |
| An apply agent is stuck / went haywire / a window won't close / no window ever opened | Any of: a site login-loop, a hung page, or a launch that failed to paint a window. | Click **⏹ Stop apply** in the dashboard header (appears whenever an agent is live, polled every 4s — survives page reloads and closed modals). It SIGTERMs then SIGKILLs the whole agent tree (python + claude + MCP + Chromium) without touching the dashboard. CLI equivalent: `curl -X POST localhost:8770/api/apply/stop`. |
| Nothing happened but cookies were imported, then it errored | The Zen cookie DB stores expiry in **milliseconds** (stock Firefox uses seconds); passed to Playwright as seconds they landed in year ~58,000 and Playwright rejected the whole jar, killing the browser at startup. | Fixed — ms-scale expiry is normalized and a rejected cookie no longer sinks the session (you'd just start logged-out). |
| Apply session times out / takes very long | Big multi-step forms genuinely take minutes; a too-small `agent_timeout_s` kills it mid-way. Also `apply.agent_model: ""` inherits your claude default — a flagship model is slow for this. | Keep `agent_model: sonnet` (default), raise `agent_timeout_s` if needed, and check `data/apply_agent.log` — it streams while the agent works, so "slow but progressing" and "stuck" look different. On timeout the browser STAYS open; finish by hand. |
| Apply button says it cannot open a browser here | serve is running in a headless context (no DISPLAY/WAYLAND_DISPLAY). | Run the dashboard on your desktop (`./start.sh` or the systemd user unit — a login session has a display). The refusal message includes the exact fallback CLI command. |
| The agent opens Chromium, not my browser (Zen) | Expected — Playwright drives its own bundled Chromium. Your default browser is only used by the plain Apply↗ button (agent off). | Nothing to fix. With `use_browser_cookies` on you're already logged in inside that Chromium for the posting's site. |
| Apply finished but says `session not saved` | `JOBAGG_SESSION_KEY` missing/invalid — session persistence is optional and degrades gracefully (the fill itself is unaffected). | Generate once: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` → put it in `.env`, keep it stable. |

## Docker deployment (RETIRED 2026-07-18 — host-native `start.sh` is the deployment now)

| Symptom | Cause | Fix |
|---|---|---|
| After 2026-07-18 the container sees an empty/old DB | Deployment moved from the `jobagg-data` named volume to a bind mount of `./data` (so the host apply CLI and the dashboard share one DB). Compose no longer mounts the old volume. | The volume's DB was copied to `./data/jobs.db` during the migration (originals kept in `data/backups/` and in the untouched `jobagg-data` volume). To re-extract manually: `docker run --rm -v jobaggregator_jobagg-data:/data alpine cat /data/jobs.db > data/jobs.db`. |
| Container can't write `./data` / profile edits fail | The container runs as `JOBAGG_UID:JOBAGG_GID` (default 1000:1000) to match host file ownership; a different host uid breaks writes. | Set `JOBAGG_UID`/`JOBAGG_GID` in `.env` to your `id -u`/`id -g` and `docker compose up -d`. |
| Profile edits vanish after `up --build` (pre-fix) | `profile.yaml` used to be baked into the image (also a privacy leak — personal résumé data in image layers). | Fixed: `.dockerignore` excludes it and compose bind-mounts `./profile.yaml` into the container, so `/profile` edits land on the host file and survive rebuilds. |

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
