"""Email digest notifier (Phase 7). stdlib smtplib; defaults to a local opensmtpd relay.

NEW_ONLY: plain-text digest of this run's new jobs. Empty jobs or empty recipient -> dry-run.
STARTTLS + login only when SMTP creds are present (localhost:25 opensmtpd needs none). Any
failure is logged + swallowed. An injected SMTP (tests) is never quit()-ed.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import TYPE_CHECKING, Any

from job_aggregator.notify.base import FeedScope, Notifier, format_meta

if TYPE_CHECKING:
    from job_aggregator.config.schema import Config
    from job_aggregator.models.job import Job

log = logging.getLogger(__name__)

SMTP_TIMEOUT = 15.0
EMAIL_SUBJECT_PREFIX = "[JobAggregator]"


def build_email(jobs: list[Job]) -> tuple[str, str]:
    """Return (subject, plain-text body) for a digest of `jobs`."""
    n = len(jobs)
    plural = "s" if n != 1 else ""
    subject = f"{EMAIL_SUBJECT_PREFIX} {n} new job{plural}"
    lines: list[str] = []
    for i, job in enumerate(jobs, start=1):
        lines.append(f"{i}. {job.title} — {job.company}")
        meta = format_meta(job)
        if meta:
            lines.append(f"   {meta}")
        lines.append(f"   {job.url}")
        lines.append("")
    body = "\n".join(lines).rstrip() + "\n"
    return subject, body


class EmailNotifier(Notifier):
    name = "email"
    feed_scope = FeedScope.NEW_ONLY

    def __init__(self, smtp: Any = None) -> None:
        # `smtp` is an injected client for tests; production opens a real smtplib.SMTP.
        self._smtp = smtp

    def notify_new(self, jobs: list[Job], cfg: Config) -> None:
        to = cfg.notify.email.to
        if not jobs or not to:
            log.info("email dry-run (%d jobs, to=%r)", len(jobs), to)
            return
        try:
            host = os.environ.get("SMTP_HOST") or cfg.notify.email.smtp_host
            port_env = os.environ.get("SMTP_PORT")
            port = int(port_env) if port_env else cfg.notify.email.smtp_port
            user = os.environ.get("SMTP_USER")
            password = os.environ.get("SMTP_PASSWORD")

            subject, body = build_email(jobs)
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = user or f"jobaggregator@{host}"
            msg["To"] = to
            msg.set_content(body)

            client = self._smtp
            opened = client is None
            if opened:
                client = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
            if user and password:
                client.starttls()
                client.login(user, password)
            client.send_message(msg)
            if opened:  # only close a connection we opened; leave injected fakes alone
                client.quit()
        except Exception:
            log.exception("email send failed (ignored)")
