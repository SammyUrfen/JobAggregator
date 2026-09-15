"""Internshala listing-page source — the #1 Indian internship site (HTML, BeautifulSoup).

No JSON API exists, but the server-rendered listing pages under /internships/<filter-slug>/ are
plain HTML behind no Cloudflare wall, robots.txt-allowed, and carry everything the pipeline
needs per card: title, company, stipend (native INR/month), location, duration, posted-ago, a
short about text, skills, detail link.
(research.md's 2026-07-14 "filter URLs redirect" dead-end note went stale — verified live
2026-07-18: every configured slug returns 200 with correctly filtered results.)

Selector fragility is the known trade-off of any HTML source: a site redesign breaks parsing.
The mapper is defensive (every field optional except title/company/url) and a page that yields
ZERO cards is treated as the end of pagination, so a redesign degrades to a failed/empty source
run — never a crash, and (per the stale guard) never a mass-expiry of previously seen jobs.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from job_aggregator.errors import SourceError
from job_aggregator.sources._http import get_text, make_client, paginate_until_empty
from job_aggregator.sources.base import (
    RawPosting,
    Source,
    SourceResult,
    build_result,
    elapsed_ms,
    from_epoch_seconds,
)

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

log = logging.getLogger(__name__)

_BASE = "https://internshala.com"
_CARDS_PER_PAGE = 50  # observed page size; a short page ends pagination
# The full job description lives on the DETAIL page (the listing card carries only an about text
# and a skills list). We fetch it ON DEMAND when the user opens a job — a human action, one page
# at a time — not in the automated run (robots is ambiguous about bulk detail crawling).
# `.internship_details` holds the "About the internship" + skills + who-can-apply blocks
# (selector verified live).
_DETAIL_SELECTOR = ".internship_details"
# Detail HTML can be large; cap what we store (the modal renderer caps display anyway).
_MAX_DETAIL_CHARS = 12000
# Stipend text is native INR/month: "₹ 15,000 - 30,000 /month" | "₹ 20,000 /month" | "Unpaid".
_STIPEND_RE = re.compile(r"₹\s*([\d,]+)(?:\s*-\s*([\d,]+))?\s*/month")
# The card's exact word for a zero stipend (10 of 50 cards on the 2026-09-15 audit page). Other
# non-numeric text ("Not provided", "$ 200 - 300 /month") stays unknown: silence is not unpaid.
_UNPAID_STIPEND = "unpaid"
# "4 days ago" / "2 weeks ago" / "1 month ago"; "Today"/"Just now"/"Few hours ago" -> now.
_AGO_RE = re.compile(r"(\d+)\s*(minute|hour|day|week|month)s?\s+ago", re.IGNORECASE)
_AGO_UNIT_DAYS = {"minute": 0.0, "hour": 0.0, "day": 1.0, "week": 7.0, "month": 30.0}
# The ago label moves between classes as a card ages: status-success (today), status-info (days),
# status-inactive (1 week+). Reading only the first two lost posted_at on 24 of 50 live cards.
_AGO_SELECTOR = ".status-success, .status-info, .status-inactive"
# The card duration is the span after the calendar icon: "6 Months", "1 Month", "2 Weeks" (the
# only shapes on 214 audited cards). Anything else is left out, so the duration stays unknown.
_DURATION_SELECTOR = "i.ic-16-calendar + span"
_DURATION_RE = re.compile(r"(\d+)\s*(weeks?|months?)", re.IGNORECASE)
# Detail URLs end in the posting's creation time as 10-digit epoch seconds ("...ambill1788633027").
# It backs up the ago label; the audit matched it to APPLY BY minus 30 days on 18 of 29 pages.
_URL_EPOCH_RE = re.compile(r"(\d{10})/?$")


def _parse_stipend(text: str | None) -> tuple[int | None, int | None]:
    """(min, max) INR/month from the stipend text: (0, 0) for "Unpaid", (None, None) when the
    text is absent or states no INR amount (the job then buckets UNKNOWN and is kept)."""
    if not text:
        return None, None
    if text.strip().lower() == _UNPAID_STIPEND:
        return 0, 0
    m = _STIPEND_RE.search(text)
    if not m:
        return None, None
    lo = int(m.group(1).replace(",", ""))
    hi = int(m.group(2).replace(",", "")) if m.group(2) else None
    return lo, hi if hi is not None else lo


def _parse_ago(text: str | None, now: datetime) -> datetime | None:
    """Posted-ago label -> approximate aware datetime (day-resolution is enough for recency
    sorting and the windowed-retire age check)."""
    if not text:
        return None
    t = text.strip().lower()
    if t in ("today", "just now") or ("hour" in t and "ago" in t and not t[0].isdigit()):
        return now  # "Today" / "Few hours ago"
    m = _AGO_RE.search(t)
    if not m:
        return None
    return now - timedelta(days=float(m.group(1)) * _AGO_UNIT_DAYS[m.group(2).lower()])


def _url_created_at(href: str, now: datetime) -> datetime | None:
    """Creation time from the epoch suffix of a detail URL, or None when there is none. A time
    after `now` cannot be a creation time (a slug that merely ends in digits), so it is None."""
    m = _URL_EPOCH_RE.search(urlparse(href).path)
    created = from_epoch_seconds(m.group(1)) if m else None
    return created if created is not None and created <= now else None


def _duration_line(text: str | None) -> str | None:
    """The contract's "Duration: N months" / "N weeks" line from the card duration text."""
    m = _DURATION_RE.fullmatch((text or "").strip())
    return f"Duration: {m.group(1)} {m.group(2).lower()}" if m else None


def _card_text(card: Any, selector: str) -> str | None:
    el = card.select_one(selector)
    return el.get_text(" ", strip=True) if el else None


def parse_detail_description(html: str) -> str | None:
    """Extract the real JD HTML (`.internship_details`) from a detail page, or None if the
    selector isn't present (a redesign / a non-detail page). Pure + testable against a fixture."""
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one(_DETAIL_SELECTOR)
    if el is None:
        return None
    inner = el.decode_contents().strip()
    return inner[:_MAX_DETAIL_CHARS] or None


def _is_internshala_detail_url(url: str) -> bool:
    """True only for a real Internshala detail page. Checks the parsed HOST (not a substring) so a
    URL like http://evil.com/internshala.com/internship/detail/x can't fool the fetch into an
    SSRF — and the scheme must be http(s), never file://. Belt-and-suspenders: stored URLs come
    from the source, but the fetcher validates its own input."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme in ("http", "https")
        and (host == "internshala.com" or host.endswith(".internshala.com"))
        and parsed.path.startswith("/internship/detail/")
    )


def fetch_detail_description(url: str) -> str | None:
    """The real Internshala JD for one posting URL, fetched live (best-effort). Any failure —
    non-Internshala URL, network error, selector gone — returns None so the caller keeps the
    listing slug. Human-triggered (called when the user opens the job), never in the daily run."""
    if not _is_internshala_detail_url(url):
        return None
    try:
        with make_client() as client:
            return parse_detail_description(get_text(client, url))
    except SourceError as exc:
        log.info("internshala detail fetch failed for %s: %s", url, exc)
        return None


def parse_listing_page(html: str) -> list[dict[str, Any]]:
    """Extract the per-card fields from one listing page. Pure + separately testable against a
    saved fixture, so a selector break shows up as a red test, not a silent empty run."""
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for card in soup.select(".individual_internship"):
        cards.append(
            {
                "id": card.get("internshipid"),
                "href": card.get("data-href"),
                "title": _card_text(card, ".job-internship-name"),
                "company": _card_text(card, ".company-name"),
                "stipend": _card_text(card, ".stipend"),
                "location": _card_text(card, ".locations"),
                "ago": _card_text(card, _AGO_SELECTOR),
                "duration": _card_text(card, _DURATION_SELECTOR),
                "about": _card_text(card, ".about_job .text"),
                "skills": [el.get_text(" ", strip=True) for el in card.select(".job_skill")],
            }
        )
    return cards


class InternshalaSource(Source):
    name = "internshala"

    def __init__(self, slugs: list[str], max_pages: int = 3) -> None:
        self.slugs = slugs
        self.max_pages = max_pages

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        now = clock.now()
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        errors: list[str] = []
        ok = 0
        exhausted = True
        with make_client() as client:

            def fetch_page(page: int, slug: str) -> list[dict[str, Any]]:
                path = f"/internships/{slug}/" if page == 1 else f"/internships/{slug}/page-{page}/"
                return parse_listing_page(get_text(client, f"{_BASE}{path}"))

            for slug in self.slugs:
                try:
                    # partial binds THIS slug (avoids the late-binding loop-closure trap).
                    cards, walk_done = paginate_until_empty(
                        partial(fetch_page, slug=slug),
                        max_pages=self.max_pages,
                        page_size=_CARDS_PER_PAGE,
                    )
                except SourceError as exc:
                    errors.append(f"{slug}: {exc}")
                    exhausted = False
                    continue
                ok += 1
                exhausted = exhausted and walk_done
                for card in cards:  # slugs overlap (backend ⊂ software dev) — dedupe by link/id
                    key = str(card.get("href") or card.get("id") or "")
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                    card["slug"] = slug
                    items.append(card)
        if ok == 0:
            return SourceResult.failed(
                self.name, f"all slugs failed: {errors}", duration_ms=elapsed_ms(start)
            )
        return build_result(
            self.name,
            items,
            lambda card: self._map(card, now),
            duration_ms=elapsed_ms(start),
            exhaustive=exhausted,
        )

    @staticmethod
    def _map(card: dict[str, Any], now: datetime) -> RawPosting | None:
        title = (card.get("title") or "").strip()
        company = (card.get("company") or "").strip()
        href = (card.get("href") or "").strip()
        if not title or not company or not href:
            return None  # selector drift or a malformed card — skip, never crash
        # Card titles are bare category names ("Python Development"); suffix "Internship" so the
        # title says what the posting IS (matching how Internshala renders the detail page) and
        # the pipeline-wide internship detector fires on it.
        if "intern" not in title.lower():
            title = f"{title} Internship"
        location = (card.get("location") or "").strip()
        is_wfh = location.lower() == "work from home"
        # Hybrid cards append "(Hybrid)" to the cities ("Chennai, Bangalore (Hybrid)"); a plain city
        # card states no mode, so it stays None. The location text is kept as is: it is part of
        # the job_uid, and changing it would re-key every stored row.
        is_remote = True if is_wfh else (False if "(hybrid)" in location.lower() else None)
        # An "Unpaid" card gives (0, 0), and 0 is not None, so INR/month is set with it too.
        s_min, s_max = _parse_stipend(card.get("stipend"))
        slug_words = str(card.get("slug") or "").replace("-", " ")
        skills = ", ".join(card.get("skills") or [])
        # The slug line stays first so every posting keeps the must_have/role match it had when the
        # slug was the whole description (the gates only gain matches from more text). The card's
        # about text, skills and duration line feed the unpaid, duration and work-mode signals and
        # the modal.
        description_parts = [
            f"Internshala listing: {slug_words}." if slug_words else None,
            card.get("about"),
            f"Skills: {skills}." if skills else None,
            _duration_line(card.get("duration")),
        ]
        return RawPosting(
            source="internshala",
            source_native_id=str(card.get("id") or "") or None,
            title=title,
            company=company,
            url=f"{_BASE}{href}" if href.startswith("/") else href,
            location=None if is_wfh else (location or None),
            is_remote=is_remote,
            description="\n".join(p for p in description_parts if p) or None,
            salary_min=s_min,
            salary_max=s_max,
            salary_currency="INR" if s_min is not None else None,
            salary_period="month" if s_min is not None else None,
            # The ago label first, as before; the URL epoch covers a card whose label is missing
            # or unreadable, so the upsert does not overwrite posted_at with NULL.
            posted_at=_parse_ago(card.get("ago"), now) or _url_created_at(href, now),
        )
