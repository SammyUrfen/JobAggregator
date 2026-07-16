"""Notifier contract + factory + shared digest formatters (Phase 7).

Notifiers run AFTER the cycle's data is committed, so the governing rule is: a notifier failure
must NEVER fail the run — every channel logs and swallows its own errors. `feed_scope` decides
which payload the runner hands a notifier: NEW_ONLY (Telegram/email digest of this run's new
jobs) or RECENT_ACTIVE (the RSS snapshot of recent active jobs).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.pipeline.runner import RunSummary

log = logging.getLogger(__name__)


class FeedScope(StrEnum):
    NEW_ONLY = "new_only"  # Telegram / email digest
    RECENT_ACTIVE = "recent_active"  # RSS snapshot


class Notifier(ABC):
    name: str
    feed_scope: FeedScope = FeedScope.NEW_ONLY

    @abstractmethod
    def notify_new(self, jobs: list[Job], cfg: Config) -> None:
        """Deliver `jobs`. MUST NOT raise; on failure log and return."""
        ...

    def notify_run(self, summary: RunSummary, cfg: Config) -> None:
        """Optional end-of-run summary (status + counts + dashboard link). Default: no-op.
        Only Telegram implements it; RSS/email ignore it. MUST NOT raise."""
        return


def format_remote_or_location(job: Job) -> str:
    return "Remote" if job.is_remote else (job.location or "")


def format_salary(job: Job) -> str:
    if not job.salary_parsed:
        return ""
    lo, hi = job.salary_min, job.salary_max
    if lo is None and hi is None:
        return ""
    currency = job.salary_currency or "INR"
    period = job.salary_period or "month"
    if lo is not None and hi is not None and lo != hi:
        amount = f"{lo:,}-{hi:,}"
    else:
        value = hi if hi is not None else lo
        assert value is not None  # exactly one bound set (both-None returned above)
        amount = f"{value:,}"
    return f"{currency} {amount}/{period}"


def format_meta(job: Job) -> str:
    parts = (format_remote_or_location(job), format_salary(job), job.source)
    return " · ".join(p for p in parts if p)


def build_notifiers(cfg: Config, clock: Clock | None = None) -> list[Notifier]:
    """Instantiate enabled notifiers from cfg.notify. All-disabled -> []. An enabled-but-
    unconfigured channel (missing token/recipient) is a safe dry-run at notify time."""
    from job_aggregator.clock import SystemClock
    from job_aggregator.notify.email import EmailNotifier
    from job_aggregator.notify.rss import RssNotifier
    from job_aggregator.notify.telegram import TelegramNotifier

    resolved_clock = clock or SystemClock()
    out: list[Notifier] = []
    if cfg.notify.telegram.enabled:
        out.append(TelegramNotifier())
    if cfg.notify.email.enabled:
        out.append(EmailNotifier())
    if cfg.notify.rss.enabled:
        out.append(RssNotifier(clock=resolved_clock))
    return out
