"""Email digest notifier (Phase 7). stdlib smtplib; defaults to local opensmtpd relay."""

from __future__ import annotations

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job
from job_aggregator.notify.base import Notifier


class EmailNotifier(Notifier):
    def notify_new(self, jobs: list[Job], cfg: Config) -> None:
        raise NotImplementedError("Phase 7: email digest")
