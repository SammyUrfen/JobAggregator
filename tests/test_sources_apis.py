"""Phase 3 — sources Tier B via respx + fixtures.

remoteok/himalayas/jobicy/adzuna/jooble/unstop; suspicious-empty -> succeeded=False.
Plus get_json retry/backoff and the registry build.

See PLAN.md Part II (Phase 3) for the exact table-driven cases to implement.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.errors import SourceError
from job_aggregator.sources import registry
from job_aggregator.sources._http import get_json, make_client
from job_aggregator.sources.adzuna import AdzunaSource
from job_aggregator.sources.himalayas import HimalayasSource
from job_aggregator.sources.jobicy import JobicySource
from job_aggregator.sources.jooble import JoobleSource
from job_aggregator.sources.remoteok import RemoteOkSource
from job_aggregator.sources.unstop import UnstopSource

Loader = Callable[[str], Any]


def _noop(_seconds: float) -> None:
    """A sleep that never waits (retry tests)."""


# ── RemoteOK ────────────────────────────────────────────────────────────────────────────


def test_remoteok_maps_and_skips_legal(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        respx.route(method="GET", host="remoteok.com").mock(
            return_value=httpx.Response(200, json=load_fixture("remoteok.json"))
        )
        res = RemoteOkSource().fetch(cfg, now_clock)
    assert res.succeeded is True
    assert res.n_fetched == 1  # legal notice element[0] stripped
    job = res.jobs[0]
    assert job.is_remote is True
    assert job.url.startswith("https://remoteok.com/")  # host lowercased by canonical_url
    assert job.salary_min is None  # 0 -> None


def test_remoteok_suspicious_empty_when_only_legal(now_clock: FixedClock, cfg: Config) -> None:
    with respx.mock:
        respx.route(method="GET", host="remoteok.com").mock(
            return_value=httpx.Response(200, json=[{"legal": "notice only"}])
        )
        res = RemoteOkSource().fetch(cfg, now_clock)
    assert res.succeeded is False


# ── Himalayas ───────────────────────────────────────────────────────────────────────────


def test_himalayas_country_param_and_period(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        route = respx.route(method="GET", host="himalayas.app").mock(
            return_value=httpx.Response(200, json=load_fixture("himalayas.json"))
        )
        res = HimalayasSource(country="in").fetch(cfg, now_clock)
    assert res.succeeded is True
    job = res.jobs[0]
    assert job.salary_period == "year"  # 'annual' -> year
    assert job.posted_at is not None  # epoch-seconds pubDate parsed
    assert route.calls.last.request.url.params["country"] == "IN"  # uppercased


# ── Jobicy ──────────────────────────────────────────────────────────────────────────────


def test_jobicy_filters_by_job_type(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        respx.route(method="GET", host="jobicy.com").mock(
            return_value=httpx.Response(200, json=load_fixture("jobicy.json"))
        )
        res = JobicySource(job_type="internship").fetch(cfg, now_clock)
    assert res.n_fetched == 1
    assert res.jobs[0].title == "Machine Learning Intern"


def test_jobicy_no_filter_keeps_both(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        respx.route(method="GET", host="jobicy.com").mock(
            return_value=httpx.Response(200, json=load_fixture("jobicy.json"))
        )
        res = JobicySource(job_type=None).fetch(cfg, now_clock)
    assert res.n_fetched == 2


def test_jobicy_suspicious_empty(now_clock: FixedClock, cfg: Config) -> None:
    with respx.mock:
        respx.route(method="GET", host="jobicy.com").mock(
            return_value=httpx.Response(200, json={"jobs": []})
        )
        res = JobicySource().fetch(cfg, now_clock)
    assert res.succeeded is False


# ── Adzuna ──────────────────────────────────────────────────────────────────────────────


def test_adzuna_keys_currency_and_utm(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        route = respx.route(method="GET", host="api.adzuna.com").mock(
            return_value=httpx.Response(200, json=load_fixture("adzuna.json"))
        )
        res = AdzunaSource(country="in", app_id="AID", app_key="AKEY").fetch(cfg, now_clock)
    job = res.jobs[0]
    assert job.salary_currency == "INR"
    assert "utm_" not in job.url  # tracking params stripped
    params = route.calls.last.request.url.params
    assert params["app_id"] == "AID"
    assert params["app_key"] == "AKEY"


# ── Jooble ──────────────────────────────────────────────────────────────────────────────


def test_jooble_posts_json_body(load_fixture: Loader, now_clock: FixedClock, cfg: Config) -> None:
    with respx.mock:
        route = respx.route(method="POST", host="jooble.org").mock(
            return_value=httpx.Response(200, json=load_fixture("jooble.json"))
        )
        res = JoobleSource(api_key="KEY", keywords="backend", location="India").fetch(
            cfg, now_clock
        )
    assert res.succeeded is True
    body = json.loads(route.calls.last.request.content)
    assert body == {"keywords": "backend", "location": "India"}


# ── Unstop ──────────────────────────────────────────────────────────────────────────────


def test_unstop_drops_stale_keeps_recent(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        respx.route(method="GET", host="unstop.com").mock(
            return_value=httpx.Response(200, json=load_fixture("unstop.json"))
        )
        res = UnstopSource(opportunities=["internships"], search_terms=[], max_age_days=30).fetch(
            cfg, now_clock
        )
    assert res.n_fetched == 1  # 2022 posting dropped by recency
    job = res.jobs[0]
    assert job.salary_currency == "INR"
    assert job.salary_period == "month"


def test_unstop_loops_opportunities(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    with respx.mock:
        route = respx.route(method="GET", host="unstop.com").mock(
            return_value=httpx.Response(200, json=load_fixture("unstop.json"))
        )
        res = UnstopSource(
            opportunities=["internships", "jobs"], search_terms=[], max_age_days=30
        ).fetch(cfg, now_clock)
    assert route.calls.call_count == 2  # one GET per opportunity
    assert res.succeeded is True


def test_unstop_all_fail_is_failed(now_clock: FixedClock, cfg: Config) -> None:
    with respx.mock:
        respx.route(method="GET", host="unstop.com").mock(return_value=httpx.Response(403))
        res = UnstopSource(
            opportunities=["internships", "jobs"], search_terms=[], max_age_days=30
        ).fetch(cfg, now_clock)
    assert res.succeeded is False


# ── get_json retry/backoff ──────────────────────────────────────────────────────────────


def test_get_json_retries_5xx_then_200() -> None:
    with respx.mock:
        route = respx.get("https://x.test/j").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})]
        )
        with make_client() as client:
            data = get_json(client, "https://x.test/j", sleep=_noop)
    assert data == {"ok": 1}
    assert route.call_count == 2


def test_get_json_retry_after_capped() -> None:
    delays: list[float] = []
    with respx.mock:
        respx.get("https://x.test/j").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "120"}),
                httpx.Response(200, json={}),
            ]
        )
        with make_client() as client:
            get_json(client, "https://x.test/j", sleep=delays.append)
    assert delays == [30.0]  # capped to MAX_RETRY_AFTER_S


def test_get_json_gives_up_raises() -> None:
    with respx.mock:
        respx.get("https://x.test/j").mock(return_value=httpx.Response(500))
        with make_client() as client, pytest.raises(SourceError):
            get_json(client, "https://x.test/j", max_retries=2, sleep=_noop)


def test_get_json_404_not_retried() -> None:
    with respx.mock:
        route = respx.get("https://x.test/j").mock(return_value=httpx.Response(404))
        with make_client() as client, pytest.raises(SourceError):
            get_json(client, "https://x.test/j", sleep=_noop)
    assert route.call_count == 1  # terminal, no retry


def test_get_json_connect_error_retried() -> None:
    with respx.mock:
        route = respx.get("https://x.test/j").mock(side_effect=httpx.ConnectError("boom"))
        with make_client() as client, pytest.raises(SourceError):
            get_json(client, "https://x.test/j", max_retries=1, sleep=_noop)
    assert route.call_count == 2  # initial + one retry


# ── registry ────────────────────────────────────────────────────────────────────────────


def test_registry_builds_tier_b_and_c(monkeypatch: pytest.MonkeyPatch, cfg: Config) -> None:
    monkeypatch.setenv("ADZUNA_APP_ID", "a")
    monkeypatch.setenv("ADZUNA_APP_KEY", "b")
    monkeypatch.setenv("JOOBLE_API_KEY", "k")
    c = cfg.model_copy(deep=True)
    c.sources.ats.greenhouse.tokens = ["acme"]
    c.sources.ats.lever.slugs = ["acme"]
    c.sources.ats.ashby.orgs = ["Acme"]
    c.sources.ats.smartrecruiters.company_ids = ["acme"]
    names = {s.name for s in registry.build_enabled_sources(c)}
    assert {"remoteok", "himalayas", "jobicy", "unstop", "adzuna", "jooble"} <= names
    assert {"greenhouse", "lever", "ashby", "smartrecruiters"} <= names


def test_registry_skips_keyed_sources_without_env(
    monkeypatch: pytest.MonkeyPatch, cfg: Config
) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)
    names = {s.name for s in registry.build_enabled_sources(cfg)}
    assert "adzuna" not in names
    assert "jooble" not in names
    assert "remoteok" in names  # keyless sources still built


def test_registry_omits_disabled(cfg: Config) -> None:
    c = cfg.model_copy(deep=True)
    c.sources.remoteok.enabled = False
    names = {s.name for s in registry.build_enabled_sources(c)}
    assert "remoteok" not in names
