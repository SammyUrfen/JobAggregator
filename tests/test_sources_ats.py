"""Phase 3 — sources Tier C via respx.

greenhouse/lever/ashby/smartrecruiters; per-company loop + partial-success rule.

See PLAN.md Part II (Phase 3) for the exact table-driven cases to implement.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import respx

from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.sources.ats_ashby import AshbySource
from job_aggregator.sources.ats_greenhouse import GreenhouseSource
from job_aggregator.sources.ats_lever import LeverSource
from job_aggregator.sources.ats_smartrecruiters import SmartRecruitersSource
from job_aggregator.sources.base import from_epoch_millis

Loader = Callable[[str], Any]


# ── Greenhouse ──────────────────────────────────────────────────────────────────────────


def test_greenhouse_maps_and_company_fallback(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        respx.route(method="GET", host="boards-api.greenhouse.io").mock(
            return_value=httpx.Response(200, json=load_fixture("greenhouse.json"))
        )
        res = GreenhouseSource(tokens=["acme"]).fetch(cfg, now_clock)
    assert res.succeeded is True
    by_id = {j.source_native_id: j for j in res.jobs}
    assert by_id["401"].is_remote is True  # "Remote - India"
    assert by_id["402"].is_remote is None  # "Bengaluru"
    assert by_id["402"].company == "acme"  # missing company_name -> token fallback


def test_greenhouse_partial_success(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        respx.route(method="GET", url__regex=r".*/boards/acme/jobs.*").mock(
            return_value=httpx.Response(200, json=load_fixture("greenhouse.json"))
        )
        respx.route(method="GET", url__regex=r".*/boards/bad/jobs.*").mock(
            return_value=httpx.Response(404)
        )
        res = GreenhouseSource(tokens=["acme", "bad"]).fetch(cfg, now_clock)
    assert res.succeeded is True  # one company OK is enough (coverage rule)
    assert len(res.jobs) == 2  # both postings from acme; bad contributed none


def test_greenhouse_all_fail_is_failed(now_clock: FixedClock, cfg: Config) -> None:
    with respx.mock:
        respx.route(method="GET", host="boards-api.greenhouse.io").mock(
            return_value=httpx.Response(404)
        )
        res = GreenhouseSource(tokens=["bad1", "bad2"]).fetch(cfg, now_clock)
    assert res.succeeded is False


# ── Lever ───────────────────────────────────────────────────────────────────────────────


def test_lever_array_and_epoch_ms(load_fixture: Loader, now_clock: FixedClock, cfg: Config) -> None:
    with respx.mock:
        respx.route(method="GET", url__regex=r".*/postings/acme.*").mock(
            return_value=httpx.Response(200, json=load_fixture("lever.json"))
        )
        res = LeverSource(slugs=["acme"]).fetch(cfg, now_clock)
    assert res.succeeded is True
    job = res.jobs[0]
    assert job.is_remote is True  # workplaceType 'remote'
    assert job.posted_at == from_epoch_millis(1720000000000)


def test_lever_notfound_slug_fails_that_company(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        respx.route(method="GET", url__regex=r".*/postings/good.*").mock(
            return_value=httpx.Response(200, json=load_fixture("lever.json"))
        )
        respx.route(method="GET", url__regex=r".*/postings/bad.*").mock(
            return_value=httpx.Response(200, json=load_fixture("lever_notfound.json"))
        )
        res = LeverSource(slugs=["good", "bad"]).fetch(cfg, now_clock)
    assert res.succeeded is True  # good slug carried the source
    assert len(res.jobs) == 1


# ── Ashby ───────────────────────────────────────────────────────────────────────────────


def test_ashby_compensation_and_remote(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        respx.route(method="GET", url__regex=r".*/job-board/Acme.*").mock(
            return_value=httpx.Response(200, json=load_fixture("ashby.json"))
        )
        res = AshbySource(orgs=["Acme"]).fetch(cfg, now_clock)
    job = res.jobs[0]
    assert job.is_remote is True
    assert job.salary_currency == "INR"
    assert job.salary_period == "year"  # '1 YEAR' -> year


# ── SmartRecruiters ─────────────────────────────────────────────────────────────────────


def test_smartrecruiters_country_param_and_remote(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        route = respx.route(method="GET", host="api.smartrecruiters.com").mock(
            return_value=httpx.Response(200, json=load_fixture("smartrecruiters.json"))
        )
        res = SmartRecruitersSource(company_ids=["acme"], country="in").fetch(cfg, now_clock)
    job = res.jobs[0]
    assert job.is_remote is True
    assert route.calls.last.request.url.params["country"] == "in"
