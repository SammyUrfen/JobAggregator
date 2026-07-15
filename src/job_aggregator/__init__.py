"""JobAggregator — self-hosted, multi-source job/internship aggregator.

See PLAN.md for the frozen architecture spec (Part I) and the phase-by-phase build
guide (Part II). This package is laid out package-by-feature:

    config/    — Pydantic config schema + DB-backed store
    models/    — the normalized Job domain model
    storage/   — SQLite (hand-written SQL, no ORM): jobs, runs, source_runs, config
    sources/   — one adapter per source, behind a single Source contract
    pipeline/  — normalize, dedup, salary, filters, stale-deletion, and the run_cycle heart
    notify/    — Telegram / email / RSS notifiers (new-only)
    scheduler/ — daily in-process APScheduler run + startup catch-up
    dashboard/ — FastAPI app + Jinja templates + the Blood Orange theme
"""

__version__ = "0.1.0"
