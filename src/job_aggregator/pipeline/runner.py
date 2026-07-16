"""run_cycle — the heart of the pipeline (Phase 5). Exact ordering in PLAN §4.1.

A thin dispatcher: sources fetch CONCURRENTLY in a ThreadPoolExecutor (I/O bound), but every DB
write happens on the main thread, so one sqlite connection is safe (no check_same_thread=False).
Results are processed in INPUT order for deterministic first-seen provenance (Tier A before C).
The stale-delete guard only expires sources that succeeded this cycle.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from job_aggregator.config.schema import Config
from job_aggregator.errors import RunInProgressError
from job_aggregator.pipeline.filters import score_and_filter
from job_aggregator.pipeline.salary import salary_bucket
from job_aggregator.pipeline.stale import expire_stale
from job_aggregator.sources.base import Source, SourceResult
from job_aggregator.storage import jobs_repo, runs_repo

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.models.job import Job

logger = logging.getLogger(__name__)

# Bounded pool: source fetches are I/O bound. Capped so a huge source list can't spawn unbounded
# threads on a laptop.
MAX_FETCH_WORKERS = 8


class SupportsNotifyNew(Protocol):
    """Structural notifier interface (so tests can pass a duck-typed recorder without importing
    notify.base, and the real Notifier satisfies it too)."""

    def notify_new(self, jobs: list[Job], cfg: Config) -> None: ...


@dataclass
class RunSummary:
    run_id: int
    status: str  # 'success' | 'partial' | 'failed'
    n_sources_ok: int
    n_sources_err: int
    n_new: int
    n_updated: int
    n_expired: int
    trigger: str = "manual"
    n_filtered_out: int = 0
    duration_ms: int = 0
    source_errors: dict[str, str] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"run #{self.run_id} [{self.status}] trigger={self.trigger} "
            f"sources ok={self.n_sources_ok} err={self.n_sources_err} | "
            f"new={self.n_new} updated={self.n_updated} filtered={self.n_filtered_out} "
            f"expired={self.n_expired} ({self.duration_ms} ms)"
        )


def run_cycle(
    conn: sqlite3.Connection,
    cfg: Config,
    clock: Clock,
    trigger: str,
    *,
    sources: Sequence[Source] | None = None,
    notifiers: Sequence[SupportsNotifyNew] | None = None,
) -> RunSummary:
    """Run one aggregation cycle (PLAN §4.1 steps 1-9). Raises RunInProgressError if another
    cycle is already 'running'. Returns a RunSummary; a fatal error finalizes the run 'failed'
    and re-raises."""
    started = time.perf_counter()
    if runs_repo.current_run(conn) is not None:
        raise RunInProgressError("another cycle is already running")
    run_id = runs_repo.start_run(conn, trigger, clock)
    conn.commit()
    n_ok = n_err = n_new = n_updated = n_filtered = n_expired = 0
    source_errors: dict[str, str] = {}
    try:
        resolved = list(sources) if sources is not None else _build_sources(cfg)
        results = _fetch_all(resolved, cfg, clock)
        succeeded, n_ok, n_err, source_errors = _record_source_runs(conn, run_id, results)
        conn.commit()
        n_new, n_updated, n_filtered = _filter_and_upsert(conn, run_id, results, cfg, clock)
        conn.commit()
        n_expired = expire_stale(conn, run_id, succeeded, cfg, clock)
        _notify(conn, run_id, cfg, clock, notifiers)  # step 8
        status = _run_status(n_ok, n_err)
        runs_repo.finish_run(
            conn,
            run_id,
            status,
            n_sources_ok=n_ok,
            n_sources_err=n_err,
            n_new=n_new,
            n_updated=n_updated,
            n_expired=n_expired,
            clock=clock,
        )
        conn.commit()
        return RunSummary(
            run_id,
            status,
            n_ok,
            n_err,
            n_new,
            n_updated,
            n_expired,
            trigger=trigger,
            n_filtered_out=n_filtered,
            duration_ms=int((time.perf_counter() - started) * 1000),
            source_errors=source_errors,
        )
    except Exception as exc:
        logger.exception("cycle #%d failed fatally", run_id)
        try:
            runs_repo.finish_run(
                conn,
                run_id,
                "failed",
                n_sources_ok=n_ok,
                n_sources_err=n_err,
                n_new=n_new,
                n_updated=n_updated,
                n_expired=n_expired,
                clock=clock,
                error=f"{type(exc).__name__}: {exc}",
            )
            conn.commit()
        except Exception:
            logger.exception("could not finalize failed run #%d", run_id)
        raise


def _fetch_all(sources: Sequence[Source], cfg: Config, clock: Clock) -> list[SourceResult]:
    if not sources:
        return []
    results: list[SourceResult | None] = [None] * len(sources)
    workers = min(MAX_FETCH_WORKERS, len(sources))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="src") as pool:
        futures = {pool.submit(_fetch_one, s, cfg, clock): i for i, s in enumerate(sources)}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    return [r for r in results if r is not None]  # input order -> deterministic first-seen


def _fetch_one(source: Source, cfg: Config, clock: Clock) -> SourceResult:
    t0 = time.perf_counter()
    try:
        return source.fetch(cfg, clock)
    except Exception as exc:  # belt-and-suspenders: fetch() is contractually no-raise
        logger.warning("source %s raised despite no-raise contract: %s", source.name, exc)
        return SourceResult(
            source=source.name,
            succeeded=False,
            jobs=[],
            n_fetched=0,
            duration_ms=int((time.perf_counter() - t0) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )


def _record_source_runs(
    conn: sqlite3.Connection, run_id: int, results: list[SourceResult]
) -> tuple[set[str], int, int, dict[str, str]]:
    """One source_runs row per sub-result (else one keyed on res.source). Returns the set of
    succeeded (sub-)source names that feed the stale guard, plus ok/err counts and errors."""
    succeeded: set[str] = set()
    n_ok = n_err = 0
    errors: dict[str, str] = {}
    for res in results:
        rows = res.sub_results or [(res.source, res.succeeded, res.n_fetched)]
        for name, ok, n in rows:
            runs_repo.record_source_run(
                conn,
                run_id,
                name,
                succeeded=ok,
                n_fetched=n,
                duration_ms=res.duration_ms,
                error=None if ok else res.error,
            )
            if ok:
                n_ok += 1
                succeeded.add(name)
            else:
                n_err += 1
                if res.error:
                    errors[name] = res.error
    return succeeded, n_ok, n_err, errors


def _filter_and_upsert(
    conn: sqlite3.Connection,
    run_id: int,
    results: list[SourceResult],
    cfg: Config,
    clock: Clock,
) -> tuple[int, int, int]:
    n_new = n_updated = n_filtered = 0
    for res in results:
        if not res.succeeded:
            continue  # never ingest a source we couldn't see
        for job in res.jobs:
            job.salary_bucket = salary_bucket(job, cfg)  # uniform bucket for ALL sources
            verdict = score_and_filter(job, cfg)
            if not verdict.keep:
                n_filtered += 1
                continue
            job.match_score = verdict.score
            if jobs_repo.upsert_job(conn, job, run_id, clock) == "new":
                n_new += 1
            else:
                n_updated += 1
    return n_new, n_updated, n_filtered


def _notify(
    conn: sqlite3.Connection,
    run_id: int,
    cfg: Config,
    clock: Clock,
    notifiers: Sequence[SupportsNotifyNew] | None,
) -> None:
    """Step 8 (final): hand each notifier its feed-scoped payload, straight from the DB.

    NEW_ONLY channels get this run's new jobs (`jobs_new_in_run` — never a stuck-'new' from a
    failed source); RECENT_ACTIVE (RSS) gets the recent-active snapshot. A notifier failure
    NEVER fails the run. Notify does not mutate run counts.
    """
    from job_aggregator.notify.base import FeedScope, build_notifiers

    new_jobs = jobs_repo.jobs_new_in_run(conn, run_id)
    recent = jobs_repo.recent_active_jobs(conn, cfg.notify.rss.max_items)
    resolved = list(notifiers) if notifiers is not None else build_notifiers(cfg, clock)
    for notifier in resolved:
        scope = getattr(notifier, "feed_scope", FeedScope.NEW_ONLY)
        payload = recent if scope is FeedScope.RECENT_ACTIVE else new_jobs
        try:
            notifier.notify_new(payload, cfg)
        except Exception:
            name = getattr(notifier, "name", type(notifier).__name__)
            logger.exception("notifier %s raised (ignored)", name)


def _run_status(n_ok: int, n_err: int) -> str:
    if n_ok == 0 and n_err == 0:
        return "success"  # no sources enabled = a legitimate no-op
    if n_ok == 0:
        return "failed"
    if n_err > 0:
        return "partial"
    return "success"


def _build_sources(cfg: Config) -> list[Source]:
    from job_aggregator.sources.registry import build_enabled_sources

    return build_enabled_sources(cfg)
