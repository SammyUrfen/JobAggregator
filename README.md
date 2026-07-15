# JobAggregator

A self-hosted job/internship aggregator that pulls from many sources at once, **deduplicates
across them**, **expires** postings that disappear, and serves it all from a themed **FastAPI
dashboard** — where your filters and sources live and take effect on the next run. Tuned for
**remote / India** roles in backend, systems, distributed systems, ML/AI/LLM/RL, and Go.
Runs **once a day** on your own machine. No paid APIs required.

> Status: **scaffold**. The architecture and build plan are complete and the skeleton runs;
> the phases (0–9) are being implemented. See `PLAN.md`.

## Why it exists
No free tool does *all* of: multi-source **and** configurable **and** scheduled **and**
deduplicated **and** self-expiring **and** India/remote/salary-filtered. So this assembles
one from the parts that actually work (see `research.md` for the full evaluation).

## Sources (three tiers)
- **Tier A — India market (scraping):** JobSpy (Naukri, LinkedIn-guest, Indeed-IN, Google) +
  Unstop public JSON.
- **Tier B — remote boards (free no-auth APIs):** RemoteOK, Himalayas (`country=IN`), Jobicy,
  Adzuna (INR salary), Jooble.
- **Tier C — company careers (ATS JSON, the reliable backbone):** Greenhouse, Lever, Ashby,
  SmartRecruiters — per-company, no anti-bot.

## Quickstart
```bash
conda activate job-aggregator          # Python 3.11 env
pip install -e ".[dev]"                 # runtime + dev deps
cp .env.example .env                    # fill in optional keys (Adzuna/Jooble/Telegram/SMTP)
python -m job_aggregator initdb         # create data/jobs.db, seed config
python -m job_aggregator run            # one aggregation cycle now
python -m job_aggregator serve          # dashboard at http://127.0.0.1:8000
```
Config is edited **in the dashboard** (`/config`) and applies on the next run. The daily run
is scheduled in-process (see `PLAN.md` §6); a `systemd` timer alternative is documented in
Phase 9.

## How it stays smart
- **Dedup**: cross-source content hash `sha256(company|title|location)` + canonical-URL +
  fuzzy title match, so the same role on Naukri + LinkedIn + a Greenhouse board is one row.
- **Stale-deletion**: a per-cycle `last_seen` with a **per-source success guard** — a blocked
  source (e.g. a LinkedIn 429) never wrongly expires its jobs.
- **Salary**: normalized to INR/month; missing salary is **kept and flagged** (most Indian
  internships list none) rather than dropped.

## Honest limitations
- **X/Twitter, authenticated LinkedIn, Wellfound, hiring.cafe's API, JSearch** are *not* free
  or reliable to automate — deliberately excluded (see `research.md`). LinkedIn is used only
  via JobSpy's throttled guest endpoint.
- Tier C (ATS) has no global search — you curate a company token list (`docs/ats_token_lists.md`).
- Scraping (Naukri/LinkedIn) is inherently fragile and may break between JobSpy releases; the
  durable core is the free APIs + ATS endpoints. Personal, low-volume use only.

## License
MIT.
