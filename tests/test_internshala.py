"""Internshala listing-page source: HTML parsing, stipend/ago normalization, fetch pipeline.

Pages are respx-mocked HTML built from the REAL card markup shape (classes verified live
2026-09-15: .individual_internship / .job-internship-name / .company-name / .stipend /
.locations / i.ic-16-calendar + span / .about_job .text / .job_skill / ONE of .status-success,
.status-info, .status-inactive), so a selector rename in the source shows up as a red test here.
tests/fixtures/internshala_listing.html holds three cards copied from the live audit page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.pipeline.signals import internship_months
from job_aggregator.sources.base import RawPosting
from job_aggregator.sources.internshala import (
    InternshalaSource,
    _duration_line,
    _parse_ago,
    _parse_stipend,
    _url_created_at,
    parse_listing_page,
)

REAL_LISTING_HTML = (Path(__file__).parent / "fixtures" / "internshala_listing.html").read_text()
FIXED_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
# The audit saved the fixture listing page at this time; its ago labels are relative to it.
CAPTURE_NOW = datetime(2026, 9, 15, 8, 6, tzinfo=UTC)
WFH_BACKEND_SLUG = "work-from-home-backend-development-internships"


def _card(
    iid: str,
    title: str,
    company: str = "Acme Labs",
    stipend: str = "₹ 15,000 - 30,000 /month",
    location: str = "Work from home",
    ago: str | None = "4 days ago",
    ago_class: str = "status-info",
    duration: str = "3 Months",
    about: str = "Build REST APIs with FastAPI.",
    skills: tuple[str, ...] = ("Python", "FastAPI"),
    href: str | None = None,
) -> str:
    href = href or f"/internship/detail/{title.lower().replace(' ', '-')}-{iid}"
    skill_html = "".join(f'<div class="job_skill">{s}</div>' for s in skills)
    ago_html = f'<div class="{ago_class}"><i></i><span>{ago}</span></div>' if ago else ""
    return f"""
    <div class="container-fluid individual_internship" internshipid="{iid}" data-href="{href}">
      <h2 class="job-internship-name"><a class="job-title-href">{title}</a></h2>
      <p class="company-name">{company}</p>
      <div class="detail-row-1">
        <div class="row-1-item locations">
          <i class="ic-16-home"></i><span><a>{location}</a></span>
        </div>
        <div class="row-1-item">
          <i class="ic-16-money"></i><span class="stipend">{stipend}</span>
        </div>
        <div class="row-1-item"><i class="ic-16-calendar"></i><span>{duration}</span></div>
      </div>
      <div class="about_job"><div class="text">{about}</div></div>
      <div class="job_skills">{skill_html}</div>
      <div class="detail-row-2"><div class="color-labels">{ago_html}</div></div>
    </div>"""


def _page(*cards: str) -> str:
    return f"<html><body><div id='list'>{''.join(cards)}</div></body></html>"


def _raw(card_html: str, now: datetime = FIXED_NOW, slug: str = WFH_BACKEND_SLUG) -> RawPosting:
    """One card through the real parse + map path, without HTTP."""
    card = parse_listing_page(_page(card_html))[0]
    card["slug"] = slug
    raw = InternshalaSource._map(card, now)
    assert raw is not None
    return raw


# ── pure parsers ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("₹ 15,000 - 30,000 /month", (15000, 30000)),
        ("₹ 20,000 /month", (20000, 20000)),
        ("₹ 1,000 /month", (1000, 1000)),
        # Unpaid is a stated zero; every other non-INR text stays unknown (silence is not unpaid).
        ("Unpaid", (0, 0)),
        ("  unpaid ", (0, 0)),
        ("Not provided", (None, None)),
        ("$ 200 - 300 /month", (None, None)),
        (None, (None, None)),
        ("", (None, None)),
    ],
)
def test_parse_stipend(text: str | None, expected: tuple[int | None, int | None]) -> None:
    assert _parse_stipend(text) == expected


@pytest.mark.parametrize(
    ("text", "line", "months"),
    [
        ("6 Months", "Duration: 6 months", 6.0),
        ("1 Month", "Duration: 1 month", 1.0),
        ("2 Weeks", "Duration: 2 weeks", 0.5),
        ("Flexible", None, None),
        ("", None, None),
        (None, None, None),
    ],
)
def test_duration_line_feeds_the_signal(
    text: str | None, line: str | None, months: float | None
) -> None:
    assert _duration_line(text) == line
    assert internship_months(line) == months  # the contract line is what the filter reads


@pytest.mark.parametrize(
    ("href", "now", "expected"),
    [
        (
            "/internship/detail/work-from-home-backend-development-internship-at-ambill1788633027",
            CAPTURE_NOW,
            datetime(2026, 9, 5, 18, 30, 27, tzinfo=UTC),
        ),
        # a company slug ending in digits: the LAST 10 digits are the epoch
        (
            "/internship/detail/x-internship-at-acme3601788633027",
            CAPTURE_NOW,
            datetime(2026, 9, 5, 18, 30, 27, tzinfo=UTC),
        ),
        # an epoch after now cannot be a creation time
        ("/internship/detail/x-at-ambill1788633027", FIXED_NOW, None),
        ("/internship/detail/backend-11", CAPTURE_NOW, None),
        ("", CAPTURE_NOW, None),
    ],
)
def test_url_created_at(href: str, now: datetime, expected: datetime | None) -> None:
    assert _url_created_at(href, now) == expected


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
    assert first["duration"] == "3 Months"
    assert first["about"] == "Build REST APIs with FastAPI."
    assert first["skills"] == ["Python", "FastAPI"]
    assert first["href"].startswith("/internship/detail/")


@pytest.mark.parametrize(
    ("index", "iid", "stipend", "duration", "ago", "skills"),
    [
        (0, "3286172", "Unpaid", "1 Month", "Today", ["HTML", "Python"]),
        (1, "3282110", "Unpaid", "2 Weeks", "3 days ago", ["Python"]),
        # a card 1 week+ old carries its label ONLY in .status-inactive (the class the old
        # parser missed on 24 of 50 live cards)
        (2, "3270781", "₹ 15,000 /month", "6 Months", "1 week ago",
         ["JavaScript", "Node.js", "PostgreSQL", "Problem Solving", "Express.js", "React",
          "Generative AI Development"]),
    ],
)  # fmt: skip
def test_parse_listing_page_real_markup(
    index: int, iid: str, stipend: str, duration: str, ago: str, skills: list[str]
) -> None:
    cards = parse_listing_page(REAL_LISTING_HTML)
    assert len(cards) == 3
    card = cards[index]
    assert (card["id"], card["stipend"], card["duration"], card["ago"], card["skills"]) == (
        iid,
        stipend,
        duration,
        ago,
        skills,
    )
    assert card["location"] == "Work from home"
    assert card["about"] and len(card["about"]) > 500  # the card's about text, not empty


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


@pytest.mark.parametrize(
    ("stipend", "salary", "currency", "period"),
    [
        # UNPAID contract: 0/0 INR/month, so the stipend floor can FAIL it
        ("Unpaid", (0, 0), "INR", "month"),
        ("₹ 5,000 /month", (5000, 5000), "INR", "month"),
        ("Not provided", (None, None), None, None),  # unknown is kept, never read as unpaid
    ],
)
def test_stipend_maps_to_salary_fields(
    stipend: str, salary: tuple[int | None, int | None], currency: str | None, period: str | None
) -> None:
    raw = _raw(_card("9", "Java Development", stipend=stipend))
    assert (raw.salary_min, raw.salary_max) == salary
    assert (raw.salary_currency, raw.salary_period) == (currency, period)


@pytest.mark.parametrize(
    ("location", "is_remote", "stored_location"),
    [
        ("Work from home", True, None),
        ("Bangalore", None, "Bangalore"),  # a plain city states no mode
        ("Chennai, Bangalore (Hybrid)", False, "Chennai, Bangalore (Hybrid)"),
        ("Bangalore (Hybrid)", False, "Bangalore (Hybrid)"),
    ],
)
def test_work_mode_comes_from_the_card_location(
    location: str, is_remote: bool | None, stored_location: str | None
) -> None:
    raw = _raw(_card("9", "Java Development", location=location))
    assert raw.is_remote is is_remote
    assert raw.location == stored_location  # unchanged text: it is part of the job_uid


_EPOCH_HREF = "/internship/detail/work-from-home-backend-development-internship-at-ambill1788633027"


@pytest.mark.parametrize(
    ("ago", "ago_class", "href", "expected"),
    [
        ("2 days ago", "status-success", None, CAPTURE_NOW - timedelta(days=2)),
        ("5 days ago", "status-info", None, CAPTURE_NOW - timedelta(days=5)),
        ("2 weeks ago", "status-inactive", None, CAPTURE_NOW - timedelta(days=14)),
        # no label: the URL epoch (creation time) fills posted_at instead of NULL
        (None, "status-info", _EPOCH_HREF, datetime(2026, 9, 5, 18, 30, 27, tzinfo=UTC)),
        ("Be an early applicant", "status-success", _EPOCH_HREF,
         datetime(2026, 9, 5, 18, 30, 27, tzinfo=UTC)),
        (None, "status-info", None, None),  # neither: unknown
    ],
)  # fmt: skip
def test_posted_at_reads_every_label_class_then_the_url_epoch(
    ago: str | None, ago_class: str, href: str | None, expected: datetime | None
) -> None:
    raw = _raw(_card("9", "Backend", ago=ago, ago_class=ago_class, href=href), now=CAPTURE_NOW)
    assert raw.posted_at == expected


def test_description_keeps_slug_and_adds_card_text() -> None:
    raw = _raw(
        _card("9", "Backend", about="Ship Go services.", skills=("Go", "gRPC"), duration="2 Weeks")
    )
    assert raw.description == (
        "Internshala listing: work from home backend development internships.\n"
        "Ship Go services.\n"
        "Skills: Go, gRPC.\n"
        "Duration: 2 weeks"
    )


def test_description_without_card_text_is_the_slug_line() -> None:
    raw = _raw(_card("9", "Backend", about="", skills=(), duration=""))
    assert raw.description == "Internshala listing: work from home backend development internships."


@respx.mock
def test_fetch_real_markup_maps_unpaid_duration_and_age(cfg: Config) -> None:
    _mock_slug(WFH_BACKEND_SLUG, REAL_LISTING_HTML)
    res = InternshalaSource(slugs=[WFH_BACKEND_SLUG], max_pages=3).fetch(
        cfg, FixedClock(CAPTURE_NOW)
    )
    assert res.succeeded is True
    by_id = {job.source_native_id: job for job in res.jobs}
    expected = {
        # id: (salary_min, salary_max, salary_parsed, months, days since posted)
        "3286172": (0, 0, True, 1.0, 0),
        "3282110": (0, 0, True, 0.5, 3),
        "3270781": (15000, 15000, True, 6.0, 7),
    }
    for iid, (s_min, s_max, parsed, months, days) in expected.items():
        job = by_id[iid]
        assert (job.salary_min, job.salary_max, job.salary_parsed) == (s_min, s_max, parsed)
        assert (job.salary_currency, job.salary_period, job.is_remote) == ("INR", "month", True)
        assert internship_months(job.description) == months
        assert job.posted_at == CAPTURE_NOW - timedelta(days=days)
        assert job.description is not None
        assert job.description.startswith("Internshala listing: work from home backend")
    assert "Skills: HTML, Python." in (by_id["3286172"].description or "")


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
