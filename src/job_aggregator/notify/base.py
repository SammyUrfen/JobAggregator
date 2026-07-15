"""Notifier contract + factory (Phase 7). Notify on new-only; disabled notifiers no-op."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job

if TYPE_CHECKING:
    from job_aggregator.clock import Clock


class Notifier(ABC):
    @abstractmethod
    def notify_new(self, jobs: list[Job], cfg: Config) -> None:
        """Deliver a digest of NEW jobs. Must swallow/log its own errors (never break a run)."""
        raise NotImplementedError


def build_notifiers(cfg: Config, clock: Clock) -> list[Notifier]:
    """Instantiate enabled notifiers from cfg.notify (clock feeds RSS timestamps).

    No-op until Phase 7 wires the concrete notifiers (telegram/email/rss): returns an empty list
    so the runner's step-8 notify completes cleanly. The runner is already reachable here (Phase
    5/6), so this must NOT raise.
    """
    return []
