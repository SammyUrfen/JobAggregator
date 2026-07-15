"""Telegram digest notifier (Phase 7). Bot API sendMessage via httpx; env TELEGRAM_*."""

from __future__ import annotations

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job
from job_aggregator.notify.base import Notifier


class TelegramNotifier(Notifier):
    def notify_new(self, jobs: list[Job], cfg: Config) -> None:
        raise NotImplementedError("Phase 7: telegram digest")
