"""Feed-quality rules in the Adzuna, RemoteOK and Himalayas adapters (audit of 2026-09-15).

Every case is table-driven over respx-mocked API pages, with text copied from the live audit
captures. The rules act only on what a posting states: silence keeps a posting unchanged.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx

from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job, SalaryBucket
from job_aggregator.pipeline.dedup import content_hash
from job_aggregator.pipeline.runner import _normalize_salary
from job_aggregator.pipeline.salary import salary_bucket
from job_aggregator.sources.adzuna import AdzunaSource
from job_aggregator.sources.himalayas import HimalayasSource
from job_aggregator.sources.remoteok import RemoteOkSource

# ── Adzuna ──────────────────────────────────────────────────────────────────────────────


def _web3_repost(company: str, setup: str) -> str:
    """The Hashtag Web3 repost preview, verbatim apart from company, title and setup."""
    return (
        f"About {company}: {company} is actively recruiting for the position of Intern. "
        f"Location & Setup: {setup} Overview: Contribute directly to {company}'s Web3, smart "
        "contract, and blockchain infrastructure. Complete candidate qualifications and "
        "application instructions are available on Hashtag Web3."
    )


# id, title, description, structured (salary_min, salary_max),
# expected (location, is_remote, salary_min, salary_max, salary_period)
_ADZUNA_CASES: list[tuple[str, str, str | None, tuple[Any, Any], tuple[Any, ...]]] = [
    (
        "hybrid-ny",
        "Internal Audit Intern",
        _web3_repost("Coinbase", "Hybrid - New York, NY"),
        (None, None),
        ("Hybrid - New York, NY", False, None, None, "month"),
    ),
    (
        "setup-remote",
        "Backend Engineer",
        _web3_repost("Bybit", "Remote"),
        (None, None),
        ("Remote", True, None, None, "month"),
    ),
    (
        "setup-apac",
        "Intern",
        _web3_repost("Bybit", "APAC - Remote; Hong Kong SAR"),
        (None, None),
        ("APAC - Remote; Hong Kong SAR", None, None, None, "month"),
    ),
    (
        "setup-silent",
        "ML Intern",
        _web3_repost("Crypto.com", "Bangalore"),
        (None, None),
        ("Bangalore", None, None, None, "month"),
    ),
    (
        "unpaid",
        "Web Development Intern (Remote)",
        "HopeMeals, an initiative of A.S. Foundation Internship Type: Unpaid Duration: 2 Months",
        (None, None),
        ("India", True, 0, 0, "month"),
    ),
    (
        "stipend-range",
        "Software Engineer Intern",
        "Location: Remote. Duration: Upto 3 Months Stipend: ₹20,000- ₹30,000 /month",
        (None, None),
        ("India", True, None, 30000, "month"),
    ),
    (
        "onsite-stipend",
        "AI Product Design Intern",
        "Location: Pune (5 days a week, onsite) Stipend: ₹25,000 per month",
        (None, None),
        ("India", False, None, 25000, "month"),
    ),
    (
        "lone-monthly-min",
        "PL/SQL and Java Developer INTERN TO HIRE",
        None,
        (15000.0, None),
        ("India", None, 15000, None, "month"),
    ),
    (
        "annual",
        "Platform Engineer",
        "Build the platform.",
        (300000.0, 600000.0),
        ("India", None, 300000, 600000, "year"),
    ),
    # Structured pay wins over a text statement: the text is a 500-character preview. A range
    # is annual pay, even under the monthly ceiling (live: 36000-48000 for "Stipend: 3-5k").
    (
        "structured-wins",
        "Intern",
        "This will be an unpaid internship position.",
        (36000, 48000),
        ("India", None, 36000, 48000, "year"),
    ),
    (
        "silent",
        "Backend Developer",
        "Python and Go services.",
        (None, None),
        ("India", None, None, None, "month"),
    ),
]


def _adzuna_jobs(cfg: Config, now_clock: FixedClock) -> dict[str, Job]:
    one_role = cfg.model_copy(deep=True)
    one_role.keywords.roles = ["backend engineer"]
    page = {
        "results": [
            {
                "id": case_id,
                "title": title,
                "company": {"display_name": "Acme"},
                "location": {"display_name": "India"},
                "redirect_url": f"https://www.adzuna.in/details/{case_id}",
                "description": description,
                "salary_min": structured[0],
                "salary_max": structured[1],
                "created": "2026-07-14T00:00:00Z",
            }
            for case_id, title, description, structured, _ in _ADZUNA_CASES
        ]
    }
    with respx.mock:
        respx.route(method="GET", host="api.adzuna.com").mock(
            return_value=httpx.Response(200, json=page)  # a short page ends each walk
        )
        res = AdzunaSource("in", "A", "K").fetch(one_role, now_clock)
    return {str(job.source_native_id): job for job in res.jobs}


@pytest.mark.parametrize(("case_id", "expected"), [(c[0], c[4]) for c in _ADZUNA_CASES])
def test_adzuna_location_mode_and_pay(
    case_id: str, expected: tuple[Any, ...], now_clock: FixedClock, cfg: Config
) -> None:
    job = _adzuna_jobs(cfg, now_clock)[case_id]
    assert (
        job.location,
        job.is_remote,
        job.salary_min,
        job.salary_max,
        job.salary_period,
    ) == expected
    if job.salary_max is not None and job.salary_min is None:
        assert job.salary_currency == "INR"  # a stated stipend counts only INR figures


def test_adzuna_uid_hashes_the_api_location(now_clock: FixedClock, cfg: Config) -> None:
    """The setup line moves the shown location, not the uid: a stored row keeps its flags."""
    job = _adzuna_jobs(cfg, now_clock)["hybrid-ny"]
    assert job.location == "Hybrid - New York, NY"
    assert job.job_uid == content_hash("Acme", "Internal Audit Intern", "India")


def test_adzuna_strips_web3_boilerplate(now_clock: FixedClock, cfg: Config) -> None:
    for case_id in ("hybrid-ny", "setup-silent"):  # "Crypto.com" puts a dot inside the sentence
        description = _adzuna_jobs(cfg, now_clock)[case_id].description or ""
        assert "blockchain infrastructure" not in description
        assert "Complete candidate qualifications" in description  # the rest stays


def test_adzuna_unpaid_intern_buckets_fail(now_clock: FixedClock, cfg: Config) -> None:
    """Contract 3 end to end: the runner keeps 0-0 and the stipend floor fails it."""
    job = _adzuna_jobs(cfg, now_clock)["unpaid"]
    job.is_internship = True
    cfg.salary.min_internship = 12000
    _normalize_salary(job, cfg)
    assert (job.salary_min, job.salary_max) == (0, 0)
    assert salary_bucket(job, cfg) is SalaryBucket.FAIL


# ── RemoteOK ────────────────────────────────────────────────────────────────────────────


def _remoteok_item(item_id: int, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": item_id,
        "position": f"Backend Engineer {item_id}",
        "company": "Acme",
        "url": f"https://remoteok.com/remote-jobs/{item_id}",
        "location": "",
        "description": "Build services.",
    }
    return base | fields


def _remoteok_fetch(items: list[dict[str, Any]], clock: FixedClock, cfg: Config) -> list[Job]:
    with respx.mock:
        respx.route(method="GET", host="remoteok.com").mock(
            return_value=httpx.Response(200, json=[{"legal": "notice"}, *items])
        )
        res = RemoteOkSource().fetch(cfg, clock)
    assert res.succeeded is True
    return res.jobs


# days before now (None = undated), kept?
_AGE_CASES = [(29.9, True), (30.1, False), (47.0, False), (None, True)]


@pytest.mark.parametrize(("age_days", "kept"), _AGE_CASES)
@pytest.mark.parametrize("field", ["date", "epoch"])
def test_remoteok_drops_postings_older_than_30_days(
    age_days: float | None, kept: bool, field: str, now_clock: FixedClock, cfg: Config
) -> None:
    fields: dict[str, Any] = {}
    if age_days is not None:
        posted = now_clock.now() - timedelta(days=age_days)
        fields[field] = posted.isoformat() if field == "date" else int(posted.timestamp())
    jobs = _remoteok_fetch([_remoteok_item(1, **fields)], now_clock, cfg)
    assert len(jobs) == int(kept)


def test_remoteok_all_old_feed_is_a_success(now_clock: FixedClock, cfg: Config) -> None:
    """An all-old feed must not look like a suspicious empty: its stored rows should expire."""
    old = (now_clock.now() - timedelta(days=40)).isoformat()
    assert _remoteok_fetch([_remoteok_item(1, date=old)], now_clock, cfg) == []


# API location, stored location, is_remote
_LOCATION_CASES = [
    ("", "Remote", True),
    ("Remote", "Remote", True),
    ("Remoto", "Remoto", True),
    ("Worldwide", "Worldwide", True),
    ("Los Angeles", "Los Angeles", None),
    ("California, California, United States", "California, California, United States", None),
    ("Remote - US", "Remote - US", None),
    ("India", "India", None),
    ("Ù\x85Ø³Ù\x82Ø·", "Ù\x85Ø³Ù\x82Ø·", None),  # double-encoded Arabic: a place, not silence
]


@pytest.mark.parametrize(("api_location", "location", "is_remote"), _LOCATION_CASES)
def test_remoteok_remote_only_without_a_place(
    api_location: str, location: str, is_remote: bool | None, now_clock: FixedClock, cfg: Config
) -> None:
    (job,) = _remoteok_fetch([_remoteok_item(1, location=api_location)], now_clock, cfg)
    assert (job.location, job.is_remote) == (location, is_remote)


# API salary_min, salary_max, stored salary_min, salary_max
_REMOTEOK_PAY_CASES = [
    (60000, 90000, 60000, 90000),
    (30, 36, None, None),  # junk: not an annual salary
    (0, 0, None, None),
    (20000, None, 20000, None),
]


@pytest.mark.parametrize(("api_min", "api_max", "salary_min", "salary_max"), _REMOTEOK_PAY_CASES)
def test_remoteok_annual_usd_pay(
    api_min: Any, api_max: Any, salary_min: Any, salary_max: Any, now_clock: FixedClock, cfg: Config
) -> None:
    item = _remoteok_item(1, salary_min=api_min, salary_max=api_max)
    (job,) = _remoteok_fetch([item], now_clock, cfg)
    assert (job.salary_min, job.salary_max) == (salary_min, salary_max)
    assert (job.salary_currency, job.salary_period) == ("USD", "year")


# ── Himalayas ───────────────────────────────────────────────────────────────────────────


def test_himalayas_requests_real_page_size_and_is_windowed(
    now_clock: FixedClock, cfg: Config
) -> None:
    page = {"jobs": [{"guid": "h1", "title": "Backend Intern", "pubDate": 1720000000}]}
    with respx.mock:
        route = respx.route(method="GET", host="himalayas.app").mock(
            return_value=httpx.Response(200, json=page)
        )
        res = HimalayasSource().fetch(cfg, now_clock)
    assert route.calls.last.request.url.params["limit"] == "20"
    assert res.succeeded is True
    assert res.exhaustive is False  # one page of 20: absence proves nothing


# title, description, minSalary, maxSalary, salaryPeriod, stored (salary_min, salary_max)
_HIMA_PAY_CASES = [
    (
        "Go Developer - Fully Remote | Upto $130/task Task based",
        "About the job",
        130,
        130,
        "hourly",
        (None, None),
    ),
    (
        "Research Engineer - Code Generation",
        "<p>Experts are paid per task that meets the spec</p>",
        50,
        100,
        "hourly",
        (None, None),
    ),
    ("AI Trainer", "Task-based work", 8, 8, "hourly", (None, None)),
    (
        "GitHub Contributor",
        "You will be tasked with creating a reproducible bug",
        50,
        60,
        "hourly",
        (50, 60),
    ),
    ("Business Development Executive", "Recruitment", 30000, 40000, "monthly", (30000, 40000)),
]


@pytest.mark.parametrize(
    ("title", "description", "api_min", "api_max", "period", "stored"), _HIMA_PAY_CASES
)
def test_himalayas_per_task_pay_is_unknown(
    title: str,
    description: str,
    api_min: int,
    api_max: int,
    period: str,
    stored: tuple[Any, Any],
    now_clock: FixedClock,
    cfg: Config,
) -> None:
    item = {
        "guid": "h1",
        "title": title,
        "description": description,
        "minSalary": api_min,
        "maxSalary": api_max,
        "currency": "USD",
        "salaryPeriod": period,
        "pubDate": 1789400000,
    }
    with respx.mock:
        respx.route(method="GET", host="himalayas.app").mock(
            return_value=httpx.Response(200, json={"jobs": [item]})
        )
        (job,) = HimalayasSource().fetch(cfg, now_clock).jobs
    assert (job.salary_min, job.salary_max) == stored
