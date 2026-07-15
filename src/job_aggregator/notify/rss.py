"""RSS/Atom feed writer (Phase 7). Renders data/feed.xml from recent active jobs."""

from __future__ import annotations

import sqlite3

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job
from job_aggregator.notify.base import Notifier


class RssNotifier(Notifier):
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def notify_new(self, jobs: list[Job], cfg: Config) -> None:
        """Regenerate the whole feed (latest active jobs, max_items) — not just new ones."""
        raise NotImplementedError("Phase 7: write feed.xml")
