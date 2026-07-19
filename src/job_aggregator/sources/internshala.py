"""Internshala listing-page source — the #1 Indian internship site (HTML, BeautifulSoup).

No JSON API exists, but the server-rendered listing pages under /internships/<filter-slug>/ are
plain HTML behind no Cloudflare wall, robots.txt-allowed, and carry everything the pipeline
needs per card: title, company, stipend (native INR/month), location, posted-ago, detail link.
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
)

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

log = logging.getLogger(__name__)

_BASE = "https://internshala.com"
_CARDS_PER_PAGE = 50  # observed page size; a short page ends pagination
# The real job description lives on the DETAIL page (the listing card only carries the category
# slug). We fetch it ON DEMAND when the user opens a job — a human action, one page at a time —
# not in the automated run (robots is ambiguous about bulk detail crawling). `.internship_details`
# holds the "About the internship" + skills + who-can-apply blocks (selector verified live).
_DETAIL_SELECTOR = ".internship_details"
# Detail HTML can be large; cap what we store (the modal renderer caps display anyway).
_MAX_DETAIL_CHARS = 12000
# Stipend text is native INR/month: "₹ 15,000 - 30,000 /month" | "₹ 20,000 /month" | "Unpaid".
_STIPEND_RE = re.compile(r"₹\s*([\d,]+)(?:\s*-\s*([\d,]+))?\s*/month")
# "4 days ago" / "2 weeks ago" / "1 month ago"; "Today"/"Just now"/"Few hours ago" -> now.
_AGO_RE = re.compile(r"(\d+)\s*(minute|hour|day|week|month)s?\s+ago", re.IGNORECASE)
_AGO_UNIT_DAYS = {"minute": 0.0, "hour": 0.0, "day": 1.0, "week": 7.0, "month": 30.0}


def _parse_stipend(text: str | None) -> tuple[int | None, int | None]:
    """(min, max) INR/month from the stipend text; (None, None) for Unpaid/absent/unparseable
    (the job then buckets UNKNOWN, which keep_and_flag retains)."""
    if not text:
        return None, None
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
                "ago": _card_text(card, ".status-info") or _card_text(card, ".status-success"),
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
        s_min, s_max = _parse_stipend(card.get("stipend"))
        slug_words = str(card.get("slug") or "").replace("-", " ")
        return RawPosting(
            source="internshala",
            source_native_id=str(card.get("id") or "") or None,
            title=title,
            company=company,
            url=f"{_BASE}{href}" if href.startswith("/") else href,
            location=None if is_wfh else (location or None),
            is_remote=True if is_wfh else None,
            # The filter slug IS the listing's category — surfacing it gives the must_have
            # stack-anchor gate honest text to match (cards carry no description).
            description=f"Internshala listing: {slug_words}." if slug_words else None,
            salary_min=s_min,
            salary_max=s_max,
            salary_currency="INR" if s_min is not None else None,
            salary_period="month" if s_min is not None else None,
            posted_at=_parse_ago(card.get("ago"), now),
        )
