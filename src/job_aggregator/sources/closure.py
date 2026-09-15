"""Closed-application checks against a posting's original page (contract stub, 2026-09-15).

A source that keeps returning a closed posting (RemoteOK), or a windowed source that simply
stops returning it (LinkedIn), leaves it visible for weeks. These checks open the
original once and read the marker the site itself shows when applications close.

Frozen seam: the runner calls `sweep` after each run, and the dashboard calls `check_posting`
when the owner opens a posting. The markers were verified on live pages in the 2026-09-15 audit:
- jobspy_linkedin: GET /jobs/view/<id> without redirects. A 3xx whose Location holds
  "expired_jd_redirect", or a 200 whose page holds class="closed-job", means closed.
- internshala: the detail page <input id="status" value="closed|expired"> means closed.
- unstop: GET https://unstop.com/api/public/competition/<id>. regnRequirements.reg_status
  "FINISHED" means closed.
Any other answer (a block, a 429, a changed page) is "unknown", never "closed".

"open" also needs a positive marker the audit saw on every open page, so an authwall, a captcha
or a redesign that drops both markers reads "unknown" without a list of block pages to maintain.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import httpx

from job_aggregator.sources._http import make_client
from job_aggregator.sources.internshala import _is_internshala_detail_url

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

ClosureVerdict = Literal["closed", "open", "unknown"]

# Sources whose original page shows a reliable closed marker. Jooble (Cloudflare 403) and Indeed
# (401 bot detection) block scripted checks, and RemoteOK hides the origin behind a paywall.
# Adzuna is left out on purpose: its job pages answer this app's self-identifying User-Agent with
# 429 (live check 2026-09-15, twice), while its API is the route it offers to programs. The app does
# not disguise itself to get around that, so Adzuna rows keep windowed retirement by posting age.
CHECKABLE_SOURCES = frozenset({"jobspy_linkedin", "internshala", "unstop"})

# The sweep re-opens a posting at most this often. The audit saw closures 6 and 9 days after
# first sight, so 3 days catches those within a few runs while 25 checks a run still reach rows
# that were never checked.
SWEEP_RECHECK = timedelta(days=3)
# Politeness gap between two requests to the same site in one sweep. LinkedIn shares the owner's
# IP with the daily jobspy scrape, and a 429 there would starve that scrape, so it gets the wider
# gap (the audit's 7 s spacing drew no 429 in 20 requests). The other sites drew no block at
# 2.5-3 s in 25-30 requests each.
LINKEDIN_GAP_S = 5.0
DEFAULT_GAP_S = 2.5
# The dashboard runs a check inside the modal request. A slow site must not hold the modal for the
# 20 s fetch default, and a check that times out is only "unknown".
CHECK_TIMEOUT_S = 8.0

_HTML_ACCEPT = "text/html,application/xhtml+xml"
_DIGITS = re.compile(r"[0-9]+")
# The page to open per source, built from the numeric posting id on a fixed host, so a stored
# URL can never steer the request anywhere else. Internshala has no id route: it uses the
# stored detail URL after a host check.
_CHECK_URL = {
    "jobspy_linkedin": "https://www.linkedin.com/jobs/view/{id}",
    "unstop": "https://unstop.com/api/public/competition/{id}",
}
# Where the id sits in a stored URL, for rows without a numeric native id (every LinkedIn row
# stores NULL). Host-anchored, so a URL from another site never yields an id.
_URL_ID_RE = {
    "jobspy_linkedin": re.compile(r"linkedin\.com/jobs/view/([0-9]+)"),
    "unstop": re.compile(r"unstop\.com/[^?#]*-([0-9]+)(?:[/?#]|$)"),
}
# Positive "this is the real posting page" markers, seen on every open page in the audit
# (LinkedIn 10 of 10 pages).
_LINKEDIN_OPEN = "top-card-layout__title"
_LINKEDIN_CLOSED_RE = re.compile(r'class="closed-job[\s"]')
_INTERNSHALA_STATUS_RE = re.compile(r'<input\b[^>]*\bid="status"[^>]*>', re.IGNORECASE)
_VALUE_RE = re.compile(r'\bvalue="([^"]*)"')
_INTERNSHALA_CLOSED = frozenset({"closed", "expired"})


def _target_url(source: str, url: str, native_id: str | None) -> str | None:
    """The URL to open for this posting, or None when no safe URL can be built (no request)."""
    if source == "internshala":
        return url if _is_internshala_detail_url(url) else None
    template = _CHECK_URL.get(source)
    if template is None:
        return None
    if native_id and _DIGITS.fullmatch(native_id):
        return template.format(id=native_id)
    m = _URL_ID_RE[source].search(url)
    return template.format(id=m.group(1)) if m else None


def internshala_verdict(page: str) -> ClosureVerdict:
    """Read <input id="status"> from an Internshala detail page. Pure, so the dashboard reads it
    from the page it already fetched for the description."""
    tag = _INTERNSHALA_STATUS_RE.search(page)
    value = _VALUE_RE.search(tag.group(0)) if tag else None
    status = value.group(1).lower() if value else ""
    if status in _INTERNSHALA_CLOSED:
        return "closed"
    return "open" if status == "active" else "unknown"


def _read_linkedin(resp: httpx.Response) -> ClosureVerdict:
    if resp.is_redirect:
        # An authwall redirect can carry the same trk value in its query, so the target must also
        # be a /jobs/ search page, as on all 6 expired redirects the audit saw.
        loc = urlparse(resp.headers.get("location", ""))
        expired = loc.path.startswith("/jobs/") and "expired_jd_redirect" in loc.query
        return "closed" if expired else "unknown"
    if resp.status_code != 200:
        return "unknown"
    if _LINKEDIN_CLOSED_RE.search(resp.text):
        return "closed"
    return "open" if _LINKEDIN_OPEN in resp.text else "unknown"


def _read_internshala(resp: httpx.Response) -> ClosureVerdict:
    return internshala_verdict(resp.text) if resp.status_code == 200 else "unknown"


def _read_unstop(resp: httpx.Response) -> ClosureVerdict:
    try:
        reg = resp.json()["data"]["competition"]["regnRequirements"]["reg_status"]
    except (ValueError, KeyError, TypeError):  # not JSON (a block page) or a changed shape
        return "unknown"
    if resp.status_code != 200 or not isinstance(reg, str) or not reg:
        return "unknown"
    return "closed" if reg == "FINISHED" else "open"


# One reader per source. Each names the exact status and marker it trusts.
_READERS: dict[str, Callable[[httpx.Response], ClosureVerdict]] = {
    "jobspy_linkedin": _read_linkedin,
    "internshala": _read_internshala,
    "unstop": _read_unstop,
}


def check_posting(source: str, url: str, native_id: str | None) -> ClosureVerdict:
    """Open the posting's original page once and report what it says."""
    target = _target_url(source, url, native_id)
    if target is None:
        return "unknown"
    headers = {} if source == "unstop" else {"Accept": _HTML_ACCEPT}
    try:
        with make_client(timeout=CHECK_TIMEOUT_S) as client:
            # No redirect-follow for LinkedIn: the expired marker is the redirect itself.
            resp = client.get(target, headers=headers, follow_redirects=source != "jobspy_linkedin")
    except httpx.HTTPError:  # timeout, refused connection, too many redirects
        return "unknown"
    return _READERS[source](resp)


def record_verdict(
    conn: sqlite3.Connection, job_uid: str, verdict: ClosureVerdict, now: datetime
) -> None:
    """Stamp closure_checked_at and soft-delete a closed posting. A posting the owner applied to
    or bookmarked keeps its status: he acted on it, and the closed state must not hide it."""
    conn.execute(
        "UPDATE jobs SET closure_checked_at = ?, status = CASE "
        "WHEN ? = 'closed' AND applied = 0 AND bookmarked = 0 THEN 'deleted' ELSE status END "
        "WHERE job_uid = ?",
        (now.isoformat(), verdict, job_uid),
    )
    conn.commit()


def sweep(
    conn: sqlite3.Connection,
    cfg: Config,
    clock: Clock,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Check up to cfg.schedule.closure_checks_per_run visible postings and retire the closed
    ones. Returns counts by outcome ("closed", "open", "unknown", "skipped_blocked"). Never
    raises for a network problem: a blocked site is skipped for the rest of the sweep."""
    counts = {"closed": 0, "open": 0, "unknown": 0, "skipped_blocked": 0}
    limit = cfg.schedule.closure_checks_per_run
    if limit <= 0:
        return counts
    sources = sorted(CHECKABLE_SOURCES)
    cutoff = (clock.now() - SWEEP_RECHECK).isoformat()
    # Oldest knowledge first: a never-checked row counts as checked when first seen. A plain
    # NULL-first order spent the whole budget on rows the source listed as open minutes earlier,
    # and a posting that closed a week after its first check was never checked again.
    rows = conn.execute(
        "SELECT job_uid, source, url, source_native_id FROM jobs "
        f"WHERE status != 'deleted' AND hidden = 0 AND source IN ({', '.join('?' * len(sources))}) "
        "AND (closure_checked_at IS NULL OR closure_checked_at < ?) "
        "ORDER BY COALESCE(closure_checked_at, first_seen_at) ASC, match_score DESC",
        (*sources, cutoff),
    ).fetchall()
    blocked: set[str] = set()
    last_request: dict[str, datetime] = {}
    checks = 0
    for job_uid, source, url, native_id in rows:
        if checks >= limit or blocked.issuperset(sources):
            break
        if source in blocked:
            # Not stamped and not counted: it keeps its place, and the budget goes to other sites.
            counts["skipped_blocked"] += 1
            continue
        requests_site = _target_url(source, url, native_id) is not None
        checks += requests_site
        if requests_site and source in last_request:
            # Each source is one site, so the source name is the per-domain key.
            gap = LINKEDIN_GAP_S if source == "jobspy_linkedin" else DEFAULT_GAP_S
            wait = gap - (clock.now() - last_request[source]).total_seconds()
            if wait > 0:
                sleep(wait)
        verdict = check_posting(source, url, native_id)
        if requests_site:
            last_request[source] = clock.now()
            if verdict == "unknown":
                # An answer without a known marker is a block or a redesign. Either way the next
                # request to that site would be wasted or would deepen the block.
                blocked.add(source)
        counts[verdict] += 1
        record_verdict(conn, job_uid, verdict, clock.now())
    return counts
