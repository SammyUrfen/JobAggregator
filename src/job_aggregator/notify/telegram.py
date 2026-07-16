"""Telegram digest notifier (Phase 7). Bot API sendMessage via httpx; env TELEGRAM_*.

NEW_ONLY: renders an HTML digest of this run's new jobs and POSTs it. All dynamic text is
HTML-escaped. Missing token/chat_id -> dry-run log (no I/O). Any failure is logged + swallowed.
"""

from __future__ import annotations

import html
import logging
import os
from typing import TYPE_CHECKING

import httpx

from job_aggregator.notify.base import FeedScope, Notifier, format_meta

if TYPE_CHECKING:
    from job_aggregator.config.schema import Config
    from job_aggregator.models.job import Job

log = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_TIMEOUT = 10.0
TELEGRAM_MESSAGE_LIMIT = 4096  # Telegram hard cap on message text length
MAX_TELEGRAM_JOBS = 20  # keep a digest readable; the rest become "…and N more"


def _esc(text: str) -> str:
    """Escape HTML text content (Telegram parse_mode=HTML)."""
    return html.escape(text, quote=False)


def _esc_attr(text: str) -> str:
    """Escape a value going into an HTML attribute (e.g. href)."""
    return html.escape(text, quote=True)


def build_digest(jobs: list[Job], *, max_jobs: int = MAX_TELEGRAM_JOBS) -> str:
    """Render the HTML digest. Singular/plural header, tappable title links, capped list with a
    '…and N more' footer, truncated to the Telegram message limit."""
    n = len(jobs)
    plural = "s" if n != 1 else ""
    lines = [f"<b>{n} new job{plural}</b>", ""]
    for i, job in enumerate(jobs[:max_jobs], start=1):
        lines.append(
            f'{i}. <a href="{_esc_attr(job.url)}">{_esc(job.title)}</a> — {_esc(job.company)}'
        )
        meta = _esc(format_meta(job))
        if meta:
            lines.append(f"   {meta}")
    remaining = n - max_jobs
    if remaining > 0:
        lines.append("")
        lines.append(f"…and {remaining} more.")
    text = "\n".join(lines)
    if len(text) > TELEGRAM_MESSAGE_LIMIT:
        text = text[: TELEGRAM_MESSAGE_LIMIT - 1] + "…"
    return text


class TelegramNotifier(Notifier):
    name = "telegram"
    feed_scope = FeedScope.NEW_ONLY

    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self._token = token
        self._chat_id = chat_id

    def notify_new(self, jobs: list[Job], cfg: Config) -> None:
        if not jobs:
            return  # NEW_ONLY: no new jobs -> no message (no spam)
        token = self._token or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = self._chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            log.info("telegram enabled but token/chat_id missing; dry-run (%d jobs)", len(jobs))
            return
        try:
            resp = httpx.post(
                f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
                timeout=TELEGRAM_TIMEOUT,
                json={
                    "chat_id": chat_id,
                    "text": build_digest(jobs),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            resp.raise_for_status()
        except Exception:
            log.exception("telegram send failed (ignored)")
