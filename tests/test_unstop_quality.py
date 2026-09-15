"""Unstop feed quality: work mode, location, pay period, unpaid, workfunction, pagination.

The items below are real search-result items from the live audit of 2026-09-15
(/tmp/jobagg-audit/unstop/raw/s_*.json), trimmed to the fields the adapter reads. Each case
names the defect it pins down.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
from job_aggregator.sources.base import SourceResult
from job_aggregator.sources.unstop import UnstopSource

# The audit day: every captured updated_at sits inside a 30-day window of it.
AUDIT_CLOCK = FixedClock(datetime(2026, 9, 15, 12, 0, tzinfo=UTC))


def _item(
    iid: int,
    *,
    mode: str | None,
    cities: list[str],
    location_type: str = "city",
    paid: str = "paid",
    pay_in: str = "monthly",
    show_salary: int = 0,
    pay: tuple[int, int] | None = None,
    details: str | None = None,
) -> dict[str, Any]:
    """A trimmed Unstop search item with the live shape (locations[] dicts, workfunction list)."""
    job_detail: dict[str, Any] = {
        "currency": "fa-rupee",
        "locations": cities,
        "show_salary": show_salary,
        "paid_unpaid": paid,
        "pay_in": pay_in,
        "not_disclosed": False,
    }
    if mode is not None:
        job_detail["type"] = mode
    if pay is not None:
        job_detail["min_salary"], job_detail["max_salary"] = pay
    item: dict[str, Any] = {
        "id": iid,
        "title": f"Software Engineer Internship {iid}",
        "organisation": {"name": f"Org {iid}"},
        "seo_url": f"https://unstop.com/internships/software-engineer-internship-{iid}",
        "updated_at": "2026-09-14T22:56:08+05:30",
        "region": "online",  # on every live item, so it must not decide remote
        "locations": [{"city": c, "state": "X", "country": "India"} for c in cities],
        "jobDetail": job_detail,
        "regnRequirements": {"reg_status": "STARTED", "work_location_type": location_type},
        "workfunction": [
            {"id": 2004, "name": "Software Development", "pivot": {"entity_id": iid}},
            {"id": 2010, "name": "AI Engineering", "pivot": {"entity_id": iid}},
        ],
    }
    if details is not None:
        item["details"] = details
    return item


def _fetch(pages: list[httpx.Response], max_pages: int = 5) -> tuple[SourceResult, respx.Route]:
    with respx.mock:
        route = respx.route(method="GET", host="unstop.com").mock(side_effect=pages)
        res = UnstopSource(
            opportunities=["internships"], search_terms=[], max_age_days=30, max_pages=max_pages
        ).fetch(Config(), AUDIT_CLOCK)
    return res, route


def _page(items: list[dict[str, Any]], current: int = 1, last: int = 1) -> httpx.Response:
    return httpx.Response(
        200, json={"data": {"current_page": current, "last_page": last, "data": items}}
    )


def _one(item: dict[str, Any]) -> Job:
    res, _ = _fetch([_page([item])])
    assert res.succeeded is True
    return res.jobs[0]


# ── work mode + location ─────────────────────────────────────────────────────────────────────

MODE_CASES = [
    # (label, item, is_remote, location)
    ("1750680 wfh, no city", _item(1750680, mode="wfh", cities=[]), True, None),
    (
        "1748180 in_office Kolkata (was a false remote)",
        _item(1748180, mode="in_office", cities=["Kolkata"]),
        False,
        "Kolkata, India",
    ),
    (
        "1748299 hybrid, two cities",
        _item(1748299, mode="hybrid", cities=["Bangalore", "Mumbai"]),
        False,
        "Bangalore, Mumbai, India",
    ),
    (
        "1750490 in_office pan_india, no cities",
        _item(1750490, mode="in_office", cities=[], location_type="pan_india"),
        False,
        "India",
    ),
    (
        "1751461 wfh listing Bangalore",
        _item(1751461, mode="wfh", cities=["Bangalore"]),
        True,
        "Bangalore, India",
    ),
    (
        "no type field, the JD states remote",
        _item(1, mode=None, cities=[], details="<p>This is a fully remote internship.</p>"),
        True,
        None,
    ),
    ("no type field, the JD is silent", _item(2, mode=None, cities=[]), None, None),
]


@pytest.mark.parametrize(
    ("label", "item", "is_remote", "location"), MODE_CASES, ids=[c[0] for c in MODE_CASES]
)
def test_unstop_work_mode_and_location(
    label: str, item: dict[str, Any], is_remote: bool | None, location: str | None
) -> None:
    """Remote comes from jobDetail.type, never from region ("online" on 102 of 102 items). The
    location carries the country: a bare city drops the row as location_mismatch."""
    job = _one(item)
    assert job.is_remote is is_remote
    assert job.location == location


def test_unstop_uid_ignores_the_new_location() -> None:
    """The old adapter stored no location, so the uid still hashes none: a stored row keeps its
    uid, its applied flag and its seen mark, and no posting is announced a second time."""
    job = _one(_item(1751461, mode="wfh", cities=["Bangalore"]))
    assert job.job_uid == content_hash("Org 1751461", "Software Engineer Internship 1751461", None)


def test_unstop_location_without_country_defaults_to_india() -> None:
    item = _item(3, mode="in_office", cities=["Pune"])
    del item["locations"][0]["country"]
    assert _one(item).location == "Pune, India"


# ── pay ──────────────────────────────────────────────────────────────────────────────────────

PAY_CASES = [
    # (label, item, (min, max, currency, period), bucket as an internship after the runner)
    (
        "1750680 unpaid -> the UNPAID contract",
        _item(1750680, mode="wfh", cities=[], paid="unpaid", show_salary=1),
        (0, 0, "INR", "month"),
        SalaryBucket.FAIL,
    ),
    (
        "1748180 1000-5000 monthly (period was None)",
        _item(1748180, mode="in_office", cities=["Kolkata"], show_salary=1, pay=(1000, 5000)),
        (1000, 5000, "INR", "month"),
        SalaryBucket.FAIL,
    ),
    (
        "1749799 360000-450000 annually",
        _item(
            1749799, mode="wfh", cities=[], pay_in="annually", show_salary=1, pay=(360000, 450000)
        ),
        (360000, 450000, "INR", "year"),
        SalaryBucket.PASS,  # 37,500 INR/month
    ),
    (
        "1751461 paid, amount hidden -> unknown, not unpaid",
        _item(1751461, mode="wfh", cities=["Bangalore"]),
        (None, None, None, None),
        SalaryBucket.UNKNOWN,
    ),
]


@pytest.mark.parametrize(
    ("label", "item", "salary", "bucket"), PAY_CASES, ids=[c[0] for c in PAY_CASES]
)
def test_unstop_pay_mapping_reaches_the_stipend_floor(
    label: str,
    item: dict[str, Any],
    salary: tuple[int | None, int | None, str | None, str | None],
    bucket: SalaryBucket,
) -> None:
    job = _one(item)
    assert (job.salary_min, job.salary_max, job.salary_currency, job.salary_period) == salary
    job.is_internship = True
    _normalize_salary(job, Config())
    assert salary_bucket(job, Config()) is bucket


def test_unstop_workfunction_list_is_named_not_repr() -> None:
    """workfunction is a list of dicts. The old dict-only read wrote a raw Python repr into the
    description ("Function: [{'id': 2004, ...")."""
    description = _one(_item(1748299, mode="hybrid", cities=["Bangalore"])).description
    assert description is not None
    assert "Function: Software Development, AI Engineering" in description
    assert "[{" not in description


# ── pagination ───────────────────────────────────────────────────────────────────────────────


def _items(n: int, start: int = 0) -> list[dict[str, Any]]:
    return [_item(1_700_000 + start + i, mode="wfh", cities=[]) for i in range(n)]


FORBIDDEN = httpx.Response(403)
PAGE_CASES = [
    # (label, responses, max_pages, calls, n_fetched, exhaustive)
    (
        "short page 1 of 2 (live: 29 items, last_page 2) reads page 2",
        [_page(_items(29), 1, 2), _page(_items(1, 29), 2, 2)],
        5,
        2,
        30,
        True,
    ),
    ("short single page (live: 14 items, last_page 1)", [_page(_items(14))], 5, 1, 14, True),
    (
        "empty page 1 of 2 does not end the walk",
        [_page([], 1, 2), _page(_items(3), 2, 2)],
        5,
        2,
        3,
        True,
    ),
    (
        "max_pages cap before last_page is not exhaustive",
        [_page(_items(30), 1, 3), _page(_items(30, 30), 2, 3)],
        2,
        2,
        60,
        False,
    ),
    (
        "last page equal to the cap is exhaustive",
        [_page(_items(30), 1, 2), _page(_items(4, 30), 2, 2)],
        2,
        2,
        34,
        True,
    ),
    (
        "later-page error keeps page 1, not exhaustive",
        [_page(_items(30), 1, 2), FORBIDDEN],
        5,
        2,
        30,
        False,
    ),
    (
        "no page count in the JSON falls back to the short-page stop",
        [httpx.Response(200, json={"data": {"data": _items(3)}})],
        5,
        1,
        3,
        True,
    ),
]


@pytest.mark.parametrize(
    ("label", "responses", "max_pages", "calls", "n_fetched", "exhaustive"),
    PAGE_CASES,
    ids=[c[0] for c in PAGE_CASES],
)
def test_unstop_pagination_follows_last_page(
    label: str,
    responses: list[httpx.Response],
    max_pages: int,
    calls: int,
    n_fetched: int,
    exhaustive: bool,
) -> None:
    """The walk ends on current_page >= last_page, not on a short page. The short-page stop
    reported exhaustive=True while page 2 still held an open posting (Cisco 1750573)."""
    res, route = _fetch(responses, max_pages=max_pages)
    assert route.call_count == calls
    assert [c.request.url.params["page"] for c in route.calls] == [
        str(p) for p in range(1, calls + 1)
    ]
    assert res.n_fetched == n_fetched
    assert res.exhaustive is exhaustive
