"""RSS/Atom feed writer (Phase 7). RECENT_ACTIVE snapshot of recent active jobs.

Atom 1.0 with RFC-3339 dates, rendered through an autoescaping Jinja2 template. The feed is a
snapshot regenerated every run (even with 0 new jobs), written atomically (.tmp then replace).
Any failure is logged + swallowed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Template

from job_aggregator.clock import SystemClock
from job_aggregator.notify.base import FeedScope, Notifier, format_meta
from job_aggregator.paths import feed_path

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config
    from job_aggregator.models.job import Job

log = logging.getLogger(__name__)

FEED_ID = "urn:jobaggregator:feed"
FEED_TITLE = "JobAggregator — new roles"
FEED_AUTHOR = "JobAggregator"
FEED_GENERATOR = "JobAggregator"
FEED_SITE_URL = "http://localhost:8000"

_ATOM_TEMPLATE = Template(
    """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <id>{{ feed_id }}</id>
  <title>{{ feed_title }}</title>
  <updated>{{ updated }}</updated>
  <generator>{{ feed_generator }}</generator>
  <author><name>{{ feed_author }}</name></author>
  <link rel="alternate" href="{{ site_url }}"/>
{% for e in entries %}  <entry>
    <id>{{ e.id }}</id>
    <title>{{ e.title }}</title>
    <updated>{{ e.updated }}</updated>
{% if e.published %}    <published>{{ e.published }}</published>
{% endif %}    <link rel="alternate" href="{{ e.url }}"/>
{% for c in e.categories %}    <category term="{{ c }}"/>
{% endfor %}    <summary>{{ e.summary }}</summary>
  </entry>
{% endfor %}</feed>
""",
    autoescape=True,
)


def _rfc3339(dt: datetime) -> str:
    """RFC-3339 timestamp; a naive datetime is assumed to be UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def render_feed(jobs: list[Job], cfg: Config, clock: Clock) -> str:
    """Render the Atom feed XML. Caps at cfg.notify.rss.max_items; empty list -> valid feed."""
    now = clock.now()
    entries: list[dict[str, Any]] = []
    for job in jobs[: cfg.notify.rss.max_items]:
        categories = [job.source]
        if job.is_remote:
            categories.append("remote")
        if job.salary_bucket is not None:
            categories.append(f"salary:{job.salary_bucket.value}")
        entries.append(
            {
                "id": f"urn:jobuid:{job.job_uid}",
                "title": f"{job.title} — {job.company}",
                "updated": _rfc3339(job.posted_at or now),
                "published": _rfc3339(job.posted_at) if job.posted_at else None,
                "url": job.url,
                "categories": categories,
                "summary": format_meta(job),
            }
        )
    xml: str = _ATOM_TEMPLATE.render(
        feed_id=FEED_ID,
        feed_title=FEED_TITLE,
        feed_author=FEED_AUTHOR,
        feed_generator=FEED_GENERATOR,
        site_url=FEED_SITE_URL,
        updated=_rfc3339(now),
        entries=entries,
    )
    return xml


class RssNotifier(Notifier):
    name = "rss"
    feed_scope = FeedScope.RECENT_ACTIVE

    def __init__(self, clock: Clock | None = None, out_path: Path | None = None) -> None:
        self._clock: Clock = clock or SystemClock()
        self._out_path = out_path or feed_path()

    def notify_new(self, jobs: list[Job], cfg: Config) -> None:
        try:
            xml = render_feed(jobs, cfg, self._clock)
            self._out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._out_path.with_name(self._out_path.name + ".tmp")
            tmp.write_text(xml, encoding="utf-8")
            tmp.replace(self._out_path)  # atomic swap into place
        except Exception:
            log.exception("rss write failed (ignored)")
