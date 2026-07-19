"""Internshala listing-page source: HTML parsing, stipend/ago normalization, fetch pipeline.

Pages are respx-mocked HTML built from the REAL card markup shape (classes verified live
2026-07-18: .individual_internship / .job-internship-name / .company-name / .stipend /
.locations / .status-info), so a selector rename in the source shows up as a red test here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.sources.internshala import (
    InternshalaSource,
    _parse_ago,
    _parse_stipend,
    parse_listing_page,
)

FIXED_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def _card(
    iid: str,
    title: str,
    company: str = "Acme Labs",
    stipend: str = "₹ 15,000 - 30,000 /month",
    location: str = "Work from home",
    ago: str = "4 days ago",
) -> str:
    return f"""
    <div class="container-fluid individual_internship" internshipid="{iid}"
         data-href="/internship/detail/{title.lower().replace(" ", "-")}-{iid}">
      <h3 class="job-internship-name"><a class="job-title-href">{title}</a></h3>
      <p class="company-name">{company}</p>
      <div class="detail-row-1">
        <span class="locations"><a>{location}</a></span>
        <span class="stipend">{stipend}</span>
      </div>
      <div class="status-inactive status-info"><span>{ago}</span></div>
    </div>"""


def _page(*cards: str) -> str:
    return f"<html><body><div id='list'>{''.join(cards)}</div></body></html>"


# ── pure parsers ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("₹ 15,000 - 30,000 /month", (15000, 30000)),
        ("₹ 20,000 /month", (20000, 20000)),
        ("₹ 1,000 /month", (1000, 1000)),
        ("Unpaid", (None, None)),
        (None, (None, None)),
        ("", (None, None)),
    ],
)
def test_parse_stipend(text: str | None, expected: tuple[int | None, int | None]) -> None:
    assert _parse_stipend(text) == expected


@pytest.mark.parametrize(
    ("text", "days_ago"),
    [
        ("Today", 0),
        ("Just now", 0),
        ("Few hours ago", 0),
        ("1 day ago", 1),
        ("4 days ago", 4),
        ("2 weeks ago", 14),
        ("1 month ago", 30),
    ],
)
def test_parse_ago(text: str, days_ago: int) -> None:
    assert _parse_ago(text, FIXED_NOW) == FIXED_NOW - timedelta(days=days_ago)


def test_parse_ago_unknown_is_none() -> None:
    assert _parse_ago("sometime", FIXED_NOW) is None
    assert _parse_ago(None, FIXED_NOW) is None


def test_parse_listing_page_extracts_cards() -> None:
    cards = parse_listing_page(_page(_card("11", "Python Development"), _card("12", "Backend")))
    assert len(cards) == 2
    first = cards[0]
    assert first["id"] == "11"
    assert first["title"] == "Python Development"
    assert first["company"] == "Acme Labs"
    assert first["stipend"] == "₹ 15,000 - 30,000 /month"
    assert first["location"] == "Work from home"
    assert first["ago"] == "4 days ago"
    assert first["href"].startswith("/internship/detail/")


def test_parse_listing_page_empty_html() -> None:
    assert parse_listing_page("<html><body>redesigned!</body></html>") == []


# ── fetch pipeline ───────────────────────────────────────────────────────────────────────


def _mock_slug(slug: str, *pages: str) -> None:
    respx.get(f"https://internshala.com/internships/{slug}/").mock(
        return_value=httpx.Response(200, text=pages[0])
    )
    for n, body in enumerate(pages[1:], start=2):
        respx.get(f"https://internshala.com/internships/{slug}/page-{n}/").mock(
            return_value=httpx.Response(200, text=body)
        )


@respx.mock
def test_fetch_maps_cards_to_jobs(cfg: Config) -> None:
    _mock_slug("work-from-home-backend-development-internships", _page(_card("1", "Backend")))
    src = InternshalaSource(slugs=["work-from-home-backend-development-internships"], max_pages=3)
    res = src.fetch(cfg, FixedClock(FIXED_NOW))
    assert res.succeeded is True
    assert res.exhaustive is True  # short page = the slug's complete inventory
    job = res.jobs[0]
    # bare category titles get the honest "Internship" suffix so the detector fires
    assert job.title == "Backend Internship"
    assert job.company == "Acme Labs"
    assert job.is_remote is True  # "Work from home"
    assert job.location is None
    assert job.salary_min == 15000
    assert job.salary_max == 30000
    assert job.salary_currency == "INR"
    assert job.salary_period == "month"
    assert job.url.startswith("https://internshala.com/internship/detail/")
    assert job.posted_at == FIXED_NOW - timedelta(days=4)
    # the slug is surfaced as description text so the must_have stack anchor can match
    assert job.description is not None
    assert "backend development" in job.description


@respx.mock
def test_fetch_dedupes_across_slugs_and_keeps_intern_suffix_idempotent(cfg: Config) -> None:
    shared = _card("7", "Backend Intern")  # same card listed under both slugs
    _mock_slug("work-from-home-backend-development-internships", _page(shared))
    _mock_slug("backend-development-internship-in-bangalore", _page(shared))
    src = InternshalaSource(
        slugs=[
            "work-from-home-backend-development-internships",
            "backend-development-internship-in-bangalore",
        ],
        max_pages=3,
    )
    res = src.fetch(cfg, FixedClock(FIXED_NOW))
    assert res.n_fetched == 1  # deduped by detail href
    assert res.jobs[0].title == "Backend Intern"  # already says intern — no double suffix


@respx.mock
def test_fetch_one_slug_failing_keeps_others(cfg: Config) -> None:
    respx.get("https://internshala.com/internships/broken-slug/").mock(
        return_value=httpx.Response(404)
    )
    _mock_slug("work-from-home-backend-development-internships", _page(_card("1", "Backend")))
    src = InternshalaSource(
        slugs=["broken-slug", "work-from-home-backend-development-internships"], max_pages=3
    )
    res = src.fetch(cfg, FixedClock(FIXED_NOW))
    assert res.succeeded is True
    assert res.n_fetched == 1
    assert res.exhaustive is False  # a failed slug means we did not see the full view


@respx.mock
def test_fetch_all_slugs_failing_reports_failure(cfg: Config) -> None:
    respx.get("https://internshala.com/internships/broken-slug/").mock(
        return_value=httpx.Response(404)
    )
    src = InternshalaSource(slugs=["broken-slug"], max_pages=3)
    res = src.fetch(cfg, FixedClock(FIXED_NOW))
    assert res.succeeded is False


@respx.mock
def test_fetch_redesigned_page_is_suspicious_empty(cfg: Config) -> None:
    # A site redesign that breaks the card selector must degrade to a FAILED (suspicious-empty)
    # source run — never a crash, and never a mass stale-expiry of previously seen jobs.
    _mock_slug("work-from-home-backend-development-internships", "<html><body>new UI</body></html>")
    src = InternshalaSource(slugs=["work-from-home-backend-development-internships"], max_pages=3)
    res = src.fetch(cfg, FixedClock(FIXED_NOW))
    assert res.succeeded is False
    assert res.error is not None and "0 items" in res.error


@respx.mock
def test_unpaid_and_onsite_cards_map_conservatively(cfg: Config) -> None:
    _mock_slug(
        "backend-development-internship-in-bangalore",
        _page(_card("9", "Java Development", stipend="Unpaid", location="Bangalore")),
    )
    src = InternshalaSource(slugs=["backend-development-internship-in-bangalore"], max_pages=3)
    job = src.fetch(cfg, FixedClock(FIXED_NOW)).jobs[0]
    assert job.salary_min is None and job.salary_parsed is False  # Unpaid -> UNKNOWN bucket
    assert job.is_remote is None
    assert job.location == "Bangalore"


# ── detail-page description (on-demand JD fetch) ─────────────────────────────────────────


def test_parse_detail_description_extracts_jd() -> None:
    from job_aggregator.sources.internshala import parse_detail_description

    html = (
        "<html><body><div class='top'></div>"
        "<div class='internship_details'><p>About: build backend APIs.</p>"
        "<span>Skills: Python, Django</span></div></body></html>"
    )
    out = parse_detail_description(html)
    assert out is not None
    assert "build backend APIs" in out
    assert "Python, Django" in out


def test_parse_detail_description_missing_selector_is_none() -> None:
    from job_aggregator.sources.internshala import parse_detail_description

    assert parse_detail_description("<html><body>redesigned, no details div</body></html>") is None


def test_fetch_detail_description_rejects_non_detail_url() -> None:
    from job_aggregator.sources.internshala import fetch_detail_description

    # a listing URL (not a /internship/detail/ page) -> None without any network call
    assert fetch_detail_description("https://internshala.com/internships/backend/") is None
    assert fetch_detail_description("https://example.com/x") is None


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://internshala.com/internship/detail/x-1", True),
        ("http://internshala.com/internship/detail/x", True),
        # SSRF vectors the HOST check (not a substring) must reject:
        ("http://evil.com/internshala.com/internship/detail/x", False),
        ("http://internshala.com.evil.com/internship/detail/x", False),
        ("file:///etc/passwd", False),
        ("http://169.254.169.254/internship/detail/x", False),
        ("https://internshala.com/internships/backend/", False),  # wrong path
    ],
)
def test_is_internshala_detail_url_host_checked(url: str, ok: bool) -> None:
    from job_aggregator.sources.internshala import _is_internshala_detail_url

    assert _is_internshala_detail_url(url) is ok
