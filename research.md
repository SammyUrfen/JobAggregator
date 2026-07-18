# Job-Aggregator Tool — Decision-Ready Build Plan

*For Bibek (SammyUrfen) — remote/India internships in backend / systems / distributed / ML / LLM / RL / Go. Free, self-hosted, cron every 8h (00:00 / 08:00 / 16:00). Research verified against live endpoints on 2026-07-14.*

---

## 1. TL;DR

**Recommended path (decisive):** Build your own tool with **three source tiers**, each a clean adapter behind one `Source` interface:

- **Tier A — India market (scraping):** `JobSpy` (`pip install -U python-jobspy`) for **Naukri + LinkedIn-guest + Indeed-India + Google Jobs**, plus **Unstop's public JSON endpoint** for India-native student/fresher internships. This is the only maintained OSS lib that natively parses Naukri, India's #1 board.
- **Tier B — Remote boards (free no-auth JSON APIs):** `RemoteOK`, `Himalayas` (has a real `country=IN` filter), `Jobicy` (first-class internship + salary fields), `Adzuna` (real India index, INR salary), `Jooble`. No scraping, no anti-bot, stable.
- **Tier C — Company careers via ATS JSON (the reliable backbone):** per-company `Greenhouse` / `Lever` / `Ashby` / `SmartRecruiters` endpoints — all no-auth, all confirmed live, zero anti-bot. Seed the company list from `Feashliaa/job-board-aggregator`'s ~95k-slug dataset, curated down to India + remote + your target companies.

On top of that, **your own SQLite state layer** does dedup (canonical URL + a `company|title|location` hash) and stale-deletion (per-cycle `last_seen` with a **per-source success guard**). This is the systems-interesting part and fits your from-scratch ethos — it's ~50–100 lines, not a library.

**Skip entirely:** X/Twitter (no free reliable path), authenticated LinkedIn (account ban in 3–7 days), Wellfound (DataDome+Cloudflare, paid-only). Route around them — you lose almost nothing.

**Fast fallback (5 minutes, no code):** Browse **hiring.cafe** in a browser — free, no login, 2.8M listings across 46 ATS platforms, with real remote + India + internship + salary filters. Its API is now Cloudflare-locked (see §5), so it's *browse-only*, but it's the single best manual surface today.

**Language note:** JobSpy is Python-only and load-bearing, so the scraper orchestration should be Python. If you want this to double as Go practice, Tiers B and C are pure HTTP+JSON — ideal territory for the exact Go primitives you haven't touched yet (structs, interfaces, `encoding/json`, goroutines for concurrent fetches). See §4 for the hybrid layout. But conclave is your Go learning centerpiece; don't let this utility fight you — Python end-to-end is the pragmatic default.

---

## 2. Ranked comparison

Verdicts weighted for *your* constraints (free, remote/India, internship, cron-able, dedup-friendly). "Confirmed" = a claim the adversarial verification pass checked against a live endpoint/repo on 2026-07-14.

| Option | Type | Sources | India | Remote | Free? | Config/Cron? | Dedup/stale? | Verdict |
|---|---|---|---|---|---|---|---|---|
| **ATS JSON** (Greenhouse / Lever / Ashby / SmartRecruiters) | Per-company API | Company careers via ATS | Token-list dependent (SmartRecruiters has `country=in`) | Strong (Lever `workplaceType`, Ashby `location`) | **Fully free, no key** — confirmed live | Trivial GETs, per-8h; token list is the work | DIY, easy (stable `id`+`updated_at`) | **Backbone.** Most reliable, zero anti-bot. Build tool around this. |
| **JobSpy** (speedyapply) | Repo (Python) | Naukri, LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter, Bayt, BDJobs | **Best OSS** — native Naukri | Good (`is_remote`) | **Free, MIT** — confirmed active (commits to Feb 2026) | Very configurable; wrap in cron | DIY (DataFrame → your store) | **The engine** for the India market. |
| **Unstop public JSON** | First-party endpoint | Unstop internships/jobs/hackathons | **Excellent** (India-first) | Good (`region=online`) | **Free, no auth** — confirmed live | Query params, plain GET | DIY (`id`+`updated_at`) | **India-native win.** Must filter on `updated_at` (surfaces 2022 posts) and use `subtype` not `type`. |
| **Adzuna API** | Free API (key) | Adzuna India index | **Strong** (`in`, INR salary) — confirmed | Moderate (keyword) | **Free tier** (~250/day *community-reported*, not official) | Very configurable GET | DIY (`id`+`redirect_url`) | Best free API for **India + salary**. 12 countries (not 19). |
| **RemoteOK API** | Free API | RemoteOK board | Indirect (Worldwide roles) | **Excellent** | **Free, no auth** — confirmed | Simple GET, tag filter | DIY (`id`+`epoch`+`expires`) | Top remote-volume feed. Element[0] is a legal notice; **attribution required**. |
| **Himalayas API** | Free API | Himalayas board | **Filterable** (`country=IN`) — confirmed | **Excellent** | **Free, no auth** — confirmed | `q`/`country`/`seniority` params | DIY (`id`+expiry) | Best-filtered remote board. **Refreshes ~24h** — sub-daily cron adds no new data. |
| **Jobicy API** | Free API | Jobicy board | Weak (US/CA/AU) — confirmed | Good | **Free, no auth** — confirmed | `count`/`geo`/`tag` params | DIY | Only feed with **first-class `internship` type + salary** fields. |
| **Jooble API** | Free API (key) | Jooble aggregate | Good (India site) | Moderate (keyword) | Free key (approval) | POST JSON body | DIY (link/id) | India breadth complement to Adzuna. |
| **Remotive API** | Free API | Remotive board | Indirect | Excellent | Free, no auth | GET, **hard cap 4/day**, 2/min | DIY (`id`+date) | Usable but **24h-delayed** + tight cap. Secondary only. |
| **VincenzoImp/job-search-tool** | Repo (fork target) | JobSpy boards | Via JobSpy | Via JobSpy | Free, MIT — confirmed (v10.1.3, 2026-05-18) | APScheduler + YAML | Retention advertised, **dedup undocumented** | Closest turnkey fork. UI is **React** (not Streamlit); Naukri only via JobSpy. |
| **hiring.cafe** | Website | 46 ATS platforms, 2.8M jobs | Strong | Strong | Free to browse; **API Cloudflare-locked** | Browse-only (API refuted) | N/A | **Best manual browse.** Not a free cron source anymore. |
| LinkedIn guest endpoint | Endpoint | LinkedIn jobs (logged-out) | Good | Good (`f_WT=2`) | Free, no auth — confirmed | Params; **429 after ~10 pages/IP** | DIY (stable job IDs) | Use *via JobSpy*, throttled. Never logged-in. |

**Dead ends — do not build free automation on these:**

| Option | Why it's out |
|---|---|
| **X/Twitter** (twscrape / Scweet / snscrape / Nitter) | Anonymous stack dead since 2023. Survivors need burner accounts that get suspended. Job signal is thin/noisy. **Drop it.** |
| **Authenticated LinkedIn** (linkedin-api, StaffSpy) | Account restricted/banned in ~3–7 days. LinkedIn sued Proxycurl (Jan 2025 → shut down Jul 2025). Never scrape logged-in. |
| **Wellfound** | DataDome + Cloudflare; reliable extraction needs a stealth browser + residential proxies. Paid Apify only. Browse manually. |
| **ai-jobs.net API** | Rebranded to aijobs.net; documented endpoint `/api/list-jobs/` now **404s** (verified 2026-07-14). Don't build on it. |
| **JSearch** (RapidAPI) | Best *data* (Google-for-Jobs backed) but free tier = **200 requests/month** — confirmed. An 8h multi-keyword cron burns that in a day. Paid-only for real use. |
| **hiring.cafe internal API** | `POST /api/search-jobs` → 405, count endpoint → 404, `GET` → 401 (verified 2026-07-14). Now Cloudflare-gated. Browse-only. |
| **Instahyre / Cutshort** | Login/matching-gated, internship-light. (Instahyre is UA-gating, not a hard 403, but not worth the session/auth handling.) |
| **JobFunnel** | **Archived Dec 10, 2025.** Maintainer's own reason: boards moved to aggressive anti-bot; browser rebuild "too slow, fragile, operationally complex." Read its dedup design as prior art; do not fork. |

---

## 3. Best for zero effort today (5 minutes, no code)

**hiring.cafe** — open it in a browser right now:

- Free, no account, ~2.8M live listings pulled from **46 ATS platforms** and company career pages (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, …). Confirmed live and free-to-browse.
- Filters map 1:1 onto your needs: **keyword / job-title**, **location** (India / Bengaluru), **workplace type** (Remote / Hybrid / Onsite), **commitment = Internship**, **salary range**, seniority, date-posted.
- Links straight to the company's own apply page — no reposts.

**The honest catch:** the keyless JSON API the research originally pitched as your cron primitive **no longer works** — it's now behind Cloudflare (405/404/401 to plain HTTP as of 2026-07-14). So use hiring.cafe as your **daily manual browse**, and build your automated pipeline on the tiers in §4. Don't waste time trying to script it without a headless browser + residential proxies.

Runner-ups for manual browsing: **Unstop → Internships** (India-first), **RemoteOK → /remote-internships**, **LinkedIn guest search** with `f_WT=2` (remote) + Bengaluru geo, **Wellfound → India + Remote** (startup interns; browse-only).

---

## 4. Best DIY tool to run on your cron every 8h

This is the real deliverable. It's a **three-tier fetch → normalize → dedup → expire → notify** pipeline. The scraping tiers are fragile by nature; the API + ATS tiers are the durable core. Lean on the durable core and treat scraping as a bonus.

### 4.1 Recommended stack

```
cron (00/08/16)  →  run.py / cmd/aggregate
                       │
      ┌────────────────┼─────────────────────────────┐
   Tier A            Tier B                        Tier C
  (scraping)      (free JSON APIs)             (ATS JSON, per-company)
   JobSpy:         RemoteOK                     Greenhouse  boards-api.greenhouse.io/v1/boards/{tok}/jobs
    Naukri         Himalayas (country=IN)       Lever       api.lever.co/v0/postings/{co}?mode=json
    LinkedIn       Jobicy                       Ashby       api.ashbyhq.com/posting-api/job-board/{org}
    Indeed-IN      Adzuna (India+INR)           SmartRec.   api.smartrecruiters.com/v1/companies/{id}/postings?country=in
    Google         Jooble
   Unstop JSON     (Remotive secondary)
                       │
                normalize → common Job struct (per-source adapter)
                       │
                SQLite state: upsert (dedup) + last_seen cycle (stale-delete)
                       │
                filter/score (keywords + salary gate + remote/internship)
                       │
                notify on status→'new' (Telegram / your opensmtpd mail / local RSS)
```

Why this shape:
- **Tier C is the backbone** because it has *no anti-bot and no rate limits worth worrying about* — every endpoint returned HTTP 200 no-auth in verification (Greenhouse `stripe`, Lever `plaid`/`leverdemo`, Ashby `Notion`, SmartRecruiters `Bosch?country=in`). The only cost is maintaining a company token list.
- **Tier B never scrapes** — stable official JSON, immune to the LinkedIn/Naukri fragility.
- **Tier A is where India volume lives** but is the fragile part — JobSpy's LinkedIn 429s after ~10 pages/IP; Naukri parsing can break between releases. Keep `results_wanted` modest and `hours_old` small.

### 4.2 Normalized schema (SQLite)

```sql
CREATE TABLE jobs (
  job_uid        TEXT PRIMARY KEY,     -- cross-source dedup hash (see 4.3)
  source         TEXT NOT NULL,        -- 'greenhouse','jobspy_naukri','unstop','remoteok',...
  source_native_id TEXT,               -- board's own id (fast secondary key)
  title          TEXT NOT NULL,
  company        TEXT NOT NULL,
  location       TEXT,
  is_remote      INTEGER,              -- 0/1, best-effort
  url            TEXT NOT NULL,        -- canonicalized (UTM/query stripped)
  description    TEXT,
  salary_min     INTEGER,             -- normalized to INR/month where derivable
  salary_max     INTEGER,
  salary_currency TEXT,
  salary_period  TEXT,                 -- 'month'/'year'/'hour'
  salary_parsed  INTEGER,              -- 1 if we trust the number, 0 if inferred/missing
  posted_at      TEXT,
  first_seen_at  TEXT NOT NULL,
  last_seen_at   TEXT NOT NULL,
  seen_cycle     INTEGER NOT NULL,     -- epoch of the run that last saw it
  status         TEXT NOT NULL,        -- 'new' | 'active' | 'stale' | 'deleted'
  -- user fields that must SURVIVE updates:
  applied        INTEGER DEFAULT 0,
  bookmarked     INTEGER DEFAULT 0,
  notes          TEXT
);
CREATE INDEX idx_jobs_source ON jobs(source);
CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_seen   ON jobs(seen_cycle);

-- per-source success gate, written each run BEFORE any expiry step
CREATE TABLE source_runs (
  cycle_id   INTEGER NOT NULL,
  source     TEXT NOT NULL,
  succeeded  INTEGER NOT NULL,         -- 1 = fetch OK (safe to expire its jobs), 0 = errored/429/empty-suspicious
  n_fetched  INTEGER,
  PRIMARY KEY (cycle_id, source)
);
```

### 4.3 Dedup key design (the interesting part)

Two-level, because the same job appears on Naukri + LinkedIn + a company's Greenhouse board simultaneously:

1. **Fast path — per-source identity.** Key on `(source, source_native_id)`; if absent, use the **canonical URL** (lowercase host, strip `utm_*`, `gh_src`, `?ref=`, fragments). Prevents re-inserting the same posting from the same source.
2. **Cross-source identity — content hash.** Compute
   ```
   job_uid = sha256( norm(company) + '|' + norm(title) + '|' + norm(location) )
   norm(x) = lowercase, strip punctuation/whitespace, collapse company suffixes
             (inc, ltd, pvt, private limited, llp, corp, technologies)
   ```
   This collapses the same role seen on three boards into one row.
3. **Near-duplicate second pass (optional, high-value).** Within the same `norm(company)`, run `rapidfuzz.token_sort_ratio` on titles; if `> 90`, treat as the same job (catches "Backend Engineer Intern" vs "Backend Engineering Intern"). This is the pattern `dome317/job-search-pipeline` uses.

**On conflict (job already exists):** update `last_seen_at`, `seen_cycle`, and refresh mutable fields (salary, description) — but **never overwrite `applied` / `bookmarked` / `notes`**. (That's `BjornMelin/ai-job-scraper`'s content-hash-preserves-user-data idea.)

### 4.4 Stale-deletion — the `last_seen` cycle pattern (with the guard everyone gets wrong)

```
cycle_id = int(time.time())   # one id per run

for each source:
    try:
        rows = fetch(source)
        for r in rows: upsert(r, cycle_id)          # dedup + set last_seen/seen_cycle
        record_source_run(cycle_id, source, succeeded=1, n=len(rows))
    except (RateLimited, NetworkError, SuspiciousEmpty):
        record_source_run(cycle_id, source, succeeded=0)   # DO NOT let this expire jobs

# expiry — only for sources that SUCCEEDED this cycle
for source in sources_that_succeeded(cycle_id):
    # soft-delete first: a one-off blip shouldn't nuke live jobs
    UPDATE jobs SET status='stale'
      WHERE source=? AND seen_cycle < cycle_id AND status IN ('new','active')
    # hard-hide only after a grace window (e.g. 6–9 missed cycles ≈ 2–3 days)
    UPDATE jobs SET status='deleted'
      WHERE source=? AND status='stale'
        AND last_seen_at < now - GRACE
```

The **per-source success guard** is the correctness crux and matches your `Correctness > Reliability` priority: if LinkedIn 429s, its jobs *did not disappear* — you just couldn't see them. Expiring on a blocked run would wrongly delete live postings. `Jobzy` (a repo studied) has no TTL/stale-deletion at all; JobFunnel got the dedup right but died on anti-bot. You're building the piece the ecosystem consistently botches.

Note also: treat an unexpected **empty** result from a normally-populated source as `succeeded=0` (suspicious), not "everything expired." And note the Unstop endpoint surfaces stale postings (a record dated 2022 appeared in testing) — so recency-filter on `updated_at` is **mandatory** for Unstop, and read the opportunity type from `subtype`, not `type`.

### 4.5 Salary gate (>=30k remote / >=80k in-office) — the null problem

Salary is **usually null for Indian internships** across all these sources (confirmed for JobSpy, Unstop, Internshala, most ATS). A hard threshold silently drops legitimate postings. Do this instead — three buckets, a deliberate config choice:

- `salary_parsed=1 AND salary >= threshold` → **pass** (threshold = 30k INR/mo if `is_remote`, 80k INR/mo if in-office).
- `salary_parsed=1 AND salary < threshold` → **drop**.
- `salary_parsed=0` (missing/unparseable) → **keep + flag** `salary:"unknown"` so you judge manually. Do *not* drop these — you'd lose most of the real internships.

For in-office roles specifically, be stricter: if not remote **and** salary unknown, you can optionally demote (not drop) to a low-priority bucket, since your 80k bar only makes sense with a confirmed number.

### 4.6 Notifications

- **Telegram bot** — free, trivial, best for a solo dev on a laptop. Notify only on `status → 'new'`; never re-notify on re-sighting.
- **Your own opensmtpd + DKIM mail server** — you already run one; a daily digest email fits "own the whole stack."
- **Local RSS/Atom file** — generate `feed.xml` in the working dir; point any reader at it. Zero infra, fully local (aligns with your no-cloud-artifacts rule).

### 4.7 Effort estimate + phased build order

| Phase | Scope | Effort |
|---|---|---|
| **0 — Prove the engine** | `pip install -U python-jobspy`; one `scrape_jobs()` for Naukri+LinkedIn+Indeed+Google, `location='Bengaluru, India'`, `is_remote=True`, `hours_old=24`, `search_term='backend intern'`; dump CSV; eyeball. Curl one Greenhouse/Lever/Ashby board. | **1 evening** |
| **1 — State core** | SQLite schema; per-source adapter → common `Job`; dedup upsert (URL + content hash); wire one cron entry at 00/08/16. JobSpy tier only. | **1 weekend** |
| **2 — Correctness** | Stale-deletion with per-source success guard + grace window; add Tier B APIs (RemoteOK, Himalayas `country=IN`, Jobicy, Adzuna, Jooble). Salary bucketing. | **~1 week of evenings** |
| **3 — Backbone** | Tier C ATS: Greenhouse/Lever/Ashby/SmartRecruiters adapters; seed token list from `Feashliaa` (~95k slugs, CC BY-NC — fine for personal use), curate to India+remote+target cos; add Unstop JSON. | **2–3 evenings** |
| **4 — Polish** | Keyword/salary scoring; Telegram/mail/RSS notify on `new`; optional Internshala BS4 scraper for more India internship volume. | **2 evenings** |

**Fork-instead-of-scratch option:** `VincenzoImp/job-search-tool` (v10.1.3, 2026-05-18, MIT) already wires JobSpy + SQLite + APScheduler + YAML + Telegram + Docker. Fork it, then **harden its dedup/retention** (undocumented) and **add Tiers B/C**. Caveats: UI is **React** (not Streamlit), and Naukri comes only via JobSpy, not natively. Study `adgramigna/job-board-scraper` (MIT) for its idempotency/state pattern and `dome317/job-search-pipeline` for fuzzy cross-source dedup.

**Go-hybrid option (if you want the practice):** write Tiers B + C in Go — they're pure `net/http` + `encoding/json`. A `type Source interface { Fetch(ctx) ([]Job, error) }` with one impl per ATS/API is exactly your "thin dispatcher + dedicated modules / package-by-feature" style (`internal/greenhouse`, `internal/remoteok`, …), and fetching all sources concurrently with a goroutine-per-source + `errgroup` is the "new syntax for a problem you've already solved" framing. Shell out to a tiny Python JobSpy helper for Tier A (Naukri/LinkedIn). This hits every Go primitive on your not-yet-touched list. Cost: two languages and a subprocess boundary — worth it only if learning Go here is a goal, otherwise stay all-Python.

---

## 5. Source-by-source reality check (brutally honest)

**LinkedIn — worth it, but only the guest path.** The unauthenticated `jobs-guest/seeMoreJobPostings` endpoint that JobSpy uses returned 200 with 30 parseable job cards (stable `urn:li:jobPosting` IDs, `f_WT=2` remote filter accepted) — *no login, so no account to ban*. But it **429s after ~10 pages from one IP** (JobSpy README: "proxies are a must basically"), and salary is usually missing. **Authenticated** scraping (`linkedin-api`, `StaffSpy`) gets real *or* burner accounts restricted in ~3–7 days — LinkedIn sued Proxycurl (Jan 2025) which shut down entirely (Jul 2025). ToS/legal posture: `hiQ v. LinkedIn` says scraping *public* data isn't a CFAA crime, but hiQ still lost on state-law contract/trespass claims ($500k, injunction). For a personal, low-volume, non-redistributed tool the realistic risk is an **IP block, not a lawsuit** — but stay on guest endpoints, throttle, never log in, don't republish.

**X/Twitter — not viable free. Drop it.** The anonymous stack (snscrape/Nitter/ntscraper) has been dead since X killed guest access in 2023. The living tools (`twscrape` v0.19.1 Jun 2026, `Scweet`) *require authorized X accounts* (auth_token/ct0 cookies) that violate ToS and get suspended under repeated cron use — you'd perpetually re-seed burners. On top of that, job signal on X is unstructured free-text with poor India/remote coverage and heavy spam. Not worth an LLM parsing layer for the yield.

**Wellfound — dead end for free automation.** DataDome + Cloudflare; reliable extraction needs a stealth browser (Camoufox) + residential-proxy ladder. Only paid Apify actors survive. Good startup-intern content — **browse manually**, don't automate.

**Naukri — the India crown jewel, only via JobSpy.** JobSpy is the *only* maintained OSS lib that parses Naukri, with India-specific fields (skills, experience_range, company_rating, vacancy_count, work_from_home_type). Confirmed in the README and schema. It's still scraping, so expect occasional breakage; a one-line ToS disclaimer in your README fits your calibrated-claims style. This is your single highest-leverage India source.

**Unstop — the best India-native find, works today.** `GET unstop.com/api/public/opportunity/search-result?opportunity=internships&searchTerm=backend` returned 200, clean paginated JSON, **no auth/key/cookie** (verified). `region` distinguishes `india` / `online`. Two gotchas from verification: it **surfaces stale postings** (saw `updated_at` 2022) so filter on recency is mandatory; and the `type` field is unreliable — key on **`subtype`**. Undocumented internal endpoint → could add auth/rate-limiting without notice; be polite, version-guard the parser.

**Internshala — scrapeable, but no clean filter URLs.** *(STALE as of 2026-07-18: filter slugs like `/internships/work-from-home-backend-development-internships/` now return 200 directly with correctly filtered results and paginate via `/page-N/`; robots.txt allows them. Implemented as a first-class source — see `sources/internshala.py`.)* Listing pages return 200 to a normal UA and parse fine with BeautifulSoup (200 internship cards on `/internships/`). **But** the advertised filter URL (`/internships/computer-science,work-from-home-internships/`) **301-redirects and drops the filters** — you must harvest the current filter URL scheme from the site's own links, and selectors break periodically. Existing community scrapers are toy-grade. Treat as an optional DIY BS4 tier for extra India internship volume, not a reliable primary.

**ATS boards (Greenhouse / Lever / Ashby / SmartRecruiters) — the reliable backbone.** All confirmed live, no-auth, 200 responses. Greenhouse: stable `id`+`internal_job_id`+`updated_at`+`location.name`. Lever: distinctive structured `workplaceType` (remote/on-site/hybrid) — the cleanest remote signal of any source. Ashby: uuid `id` + structured `location`/`secondaryLocations` + optional `includeCompensation`. SmartRecruiters: **the only one with a server-side `country=in` filter** (still per-companyId, but disproportionately valuable for India). **The catch:** all are *per-company, no global search* — coverage = quality of your token list, and curating it is the real ongoing maintenance. Skews to funded product startups + remote-first global cos — which happens to overlap well with your AI/systems/backend target, but **under-covers Indian mass-market listings**, so ATS alone is *not* sufficient; combine with Tier A (Naukri/Unstop).

**Remote boards (RemoteOK / Himalayas / Jobicy / Adzuna / Remotive / Jooble) — free, stable, no anti-bot.** All confirmed live and free. Nuances: **RemoteOK** — element[0] is a legal notice, **attribution required** or they suspend access. **Himalayas** — real `country=IN` + `seniority` filters (great), but data **refreshes only ~24h**, so a sub-daily cron gets identical data 2 of 3 runs; it's a coverage source, not a freshness edge. **Jobicy** — only feed with first-class `internship` type + salary, but geo skews US/CA/AU (weak India). **Adzuna** — real India index, INR salary, **12 countries (not 19)**; free-tier quota (~250/day) is *community-reported, not official* — confirm on your dashboard. **Remotive** — listings **delayed 24h by design** + **hard cap 4 calls/day** → one cached call per cycle, secondary only. **Jooble** — India breadth, needs a free approval-gated key.

**Refuted / stale — don't build on these:** **hiring.cafe's** keyless `POST /api/search-jobs` now returns 405/404/401 (Cloudflare-gated as of 2026-07-14) — browse-only. **ai-jobs.net's** documented API 404s (rebranded to aijobs.net). **JSearch** free tier is 200 req/**month** — too small for cron. All three were pitched by earlier research and the verification pass caught them.

---

## 6. Ready-to-paste config sketch

`config.yaml` — matches your constraints (remote-preferred, `>=30k` INR/mo remote, `>=80k` in-office, internship-level, your skills):

```yaml
# ── keywords (tailored to your profile) ─────────────────────────────
keywords:
  roles:
    - backend engineer
    - systems software
    - distributed systems
    - infrastructure engineer
    - platform engineer
    - site reliability
    - database engineer
    - ml engineer
    - machine learning engineer
    - ai engineer
    - llm engineer
    - reinforcement learning
    - mlops
  bonus:                     # +score, not required
    - Go
    - Golang
    - Rust
    - C++
    - PyTorch
    - RAG
    - LLM
    - GRPO
    - LoRA
    - inference
    - storage engine
    - "consistent hashing"
  level_required:            # must match at least one (title or job_type)
    - intern
    - internship
    - trainee
    - "new grad"
    - "graduate engineer"
    - junior
  exclude:                   # hard drop
    - senior
    - staff
    - principal
    - manager
    - "5+ years"
    - "clearance required"

# ── location / remote ───────────────────────────────────────────────
locations:
  - "Bengaluru, India"
  - "Bangalore"
  - "India"
  - "Remote"
  - "Remote - India"
  - "Worldwide"
remote_preferred: true       # boost remote; in-office allowed only if salary gate met

# ── salary gate (INR / month) ───────────────────────────────────────
salary:
  currency: INR
  period: month
  min_remote: 30000
  min_in_office: 80000
  on_missing: keep_and_flag  # do NOT drop null-salary interns
  demote_in_office_if_unknown: true

# ── recency / cron ──────────────────────────────────────────────────
schedule:
  cron: "0 0,8,16 * * *"     # 00:00 / 08:00 / 16:00 local
  hours_old: 24              # incremental window (>= cycle gap; catches slow feeds)
grace_cycles: 8              # ~2.5 days before hard-expiring a vanished posting

# ── sources ─────────────────────────────────────────────────────────
sources:
  # Tier A — India market (scraping, fragile)
  jobspy:
    enabled: true
    sites: [naukri, linkedin, indeed, google]   # + glassdoor optional
    country_indeed: india
    results_wanted: 40                            # keep modest → fewer 429s
    proxies: []                                   # add rotating proxies ONLY for linkedin at volume
  unstop:
    enabled: true
    opportunity: [internships, jobs]
    filter_field: subtype                         # NOT 'type'
    max_age_days: 30                              # endpoint surfaces stale posts

  # Tier B — free remote APIs (stable, no anti-bot)
  remoteok:   { enabled: true, attribution: true }   # required
  himalayas:  { enabled: true, country: IN, seniority: [Entry-level] }  # 24h refresh
  jobicy:     { enabled: true, jobType: internship }
  adzuna:     { enabled: true, country: in, app_id: "REGISTER_FREE", app_key: "REGISTER_FREE" }
  jooble:     { enabled: true, api_key: "REQUEST_FREE_KEY" }
  remotive:   { enabled: false }                     # 24h-delayed + 4 calls/day; enable only if you need it

  # Tier C — ATS JSON (reliable backbone; per-company token lists)
  ats:
    enabled: true
    greenhouse_tokens:   [razorpay, postman, ...]     # seed + curate from Feashliaa dataset
    lever_slugs:         [...]
    ashby_orgs:          [Notion, ...]                # case-sensitive
    smartrecruiters_ids: [...]                        # supports ?country=in
    filter: { locations: ["India","Bengaluru","Remote"], internship_regex: "intern|internship|new grad|trainee" }

  # explicitly disabled — not free/viable
  twitter:    { enabled: false }   # dead end
  wellfound:  { enabled: false }   # Cloudflare+DataDome, paid-only
  hiring_cafe: { enabled: false }  # API Cloudflare-locked; browse manually

# ── notifications (all local/free) ──────────────────────────────────
notify:
  on: new_only                     # never re-notify on re-sighting
  telegram: { enabled: true, bot_token: "...", chat_id: "..." }
  email:    { enabled: false, smtp: "localhost:25" }   # your opensmtpd+DKIM box
  rss:      { enabled: true, path: "./feed.xml" }
```

---

*Verification provenance: every "confirmed" claim above was checked against a live endpoint or repo on 2026-07-14. Two originally-recommended sources were **refuted** (hiring.cafe keyless API, ai-jobs.net API) and are excluded from the automation path. Figures given as "community-reported" (Adzuna 250/day) are not in official docs — confirm on your own dashboard.*