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
from job_aggregator.sources._http import get_json, make_client, paginate_until_empty
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
            side_effect=[
                httpx.Response(200, json=load_fixture("jooble.json")),
                httpx.Response(200, json={"jobs": []}),  # page 2 empty -> stop paginating
            ]
        )
        res = JoobleSource(api_key="KEY", queries=["backend"], location="India").fetch(
            cfg, now_clock
        )
    assert res.succeeded is True
    body = json.loads(route.calls[0].request.content)
    assert body == {"keywords": "backend", "location": "India", "page": 1}


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
    # seo_url (absolute) is preferred over the scheme-less relative public_url that used to 404
    assert job.url == "https://unstop.com/internships/backend-development-intern-acme-30111"
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


def test_unstop_requests_open_applications_only(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    """oppstatus=open must ride on every request — Unstop keeps closed posts "LIVE" so the
    server-side filter is the only cheap way to skip un-appliable postings."""
    with respx.mock:
        route = respx.route(method="GET", host="unstop.com").mock(
            return_value=httpx.Response(200, json=load_fixture("unstop.json"))
        )
        UnstopSource(opportunities=["internships"], search_terms=[], max_age_days=30).fetch(
            cfg, now_clock
        )
    assert route.calls[0].request.url.params["oppstatus"] == "open"


def test_unstop_drops_finished_registration(now_clock: FixedClock) -> None:
    """Defense-in-depth behind oppstatus=open: reg_status FINISHED ("Application Closed" on the
    site) is dropped; a missing regnRequirements fails OPEN (can't prove it's closed)."""

    def item(iid: int, reg_status: str | None) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": iid,
            "title": f"Backend Internship {iid}",
            "organisation": {"name": "Acme"},
            "seo_url": f"https://unstop.com/internships/x-{iid}",
            "updated_at": "2026-07-10T00:00:00+00:00",
        }
        if reg_status is not None:
            base["regnRequirements"] = {"reg_status": reg_status}
        return base

    items = [item(1, "FINISHED"), item(2, "STARTED"), item(3, None)]
    with respx.mock:
        respx.route(method="GET", host="unstop.com").mock(
            side_effect=[
                httpx.Response(200, json={"data": {"data": items}}),
                httpx.Response(200, json={"data": {"data": []}}),
            ]
        )
        res = UnstopSource(opportunities=["internships"], search_terms=[], max_age_days=30).fetch(
            Config(), now_clock
        )
    kept_ids = {j.source_native_id for j in res.jobs}
    assert kept_ids == {"2", "3"}  # FINISHED dropped; STARTED + unknown kept


def test_unstop_sends_search_terms_and_dedupes(
    load_fixture: Loader, now_clock: FixedClock, cfg: Config
) -> None:
    """The config search_terms must reach the API as `searchTerm` (they used to be silently
    ignored, so the source pulled the all-domains firehose), one walk per (opportunity x term),
    with items deduped by id across walks."""
    with respx.mock:
        route = respx.route(method="GET", host="unstop.com").mock(
            return_value=httpx.Response(200, json=load_fixture("unstop.json"))
        )
        res = UnstopSource(
            opportunities=["internships"],
            search_terms=["backend", "machine learning"],
            max_age_days=30,
        ).fetch(cfg, now_clock)
    assert route.calls.call_count == 2  # one walk per term
    assert route.calls[0].request.url.params["searchTerm"] == "backend"
    assert route.calls[1].request.url.params["searchTerm"] == "machine learning"
    assert res.n_fetched == 1  # both walks returned the same posting -> deduped by id


def test_unstop_maps_details_skills_function_as_description(now_clock: FixedClock) -> None:
    """details/required_skills/workfunction feed the description — without one, the must_have
    stack-anchor gate ran title-only and false-dropped real tech internships."""
    item = {
        "id": 991,
        "title": "Data Engineer Internship",
        "organisation": {"name": "TalentCV"},
        "seo_url": "https://unstop.com/internships/data-engineer-991",
        "updated_at": "2026-07-10T00:00:00+00:00",
        "region": "online",
        "details": "<p>Build data pipelines in Python.</p>",
        "required_skills": [{"name": "Python"}, {"name": "SQL"}],
        "workfunction": "Backend Development",
    }
    with respx.mock:
        respx.route(method="GET", host="unstop.com").mock(
            side_effect=[
                httpx.Response(200, json={"data": {"data": [item]}}),
                httpx.Response(200, json={"data": {"data": []}}),
            ]
        )
        res = UnstopSource(opportunities=["internships"], search_terms=[], max_age_days=30).fetch(
            Config(), now_clock
        )
    job = res.jobs[0]
    assert job.description is not None
    assert "Build data pipelines in Python." in job.description
    assert "Skills: Python, SQL" in job.description
    assert "Function: Backend Development" in job.description
    assert job.is_remote is True  # region "online"


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


# ── paginate_until_empty helper ───────────────────────────────────────────────────────────


def test_paginate_stops_on_empty_page() -> None:
    calls: list[int] = []
    pages = {1: [1, 2, 3], 2: [4, 5], 3: []}  # page 3 empty

    def fetch(page: int) -> list[Any]:
        calls.append(page)
        return pages[page]

    items, exhausted = paginate_until_empty(fetch, max_pages=10)
    assert items == [1, 2, 3, 4, 5]
    assert exhausted is True  # the API's own completion signal = we saw the full view
    assert calls == [1, 2, 3]  # stopped once the API returned none


def test_paginate_stops_on_short_page() -> None:
    def fetch(page: int) -> list[Any]:
        return list(range(50)) if page == 1 else list(range(10))  # page 2 is short

    items, exhausted = paginate_until_empty(fetch, max_pages=10, page_size=50)
    assert len(items) == 60
    assert exhausted is True


def test_paginate_respects_max_pages_cap_and_reports_windowed() -> None:
    items, exhausted = paginate_until_empty(lambda page: [page], max_pages=3)  # never empty
    assert items == [1, 2, 3]
    assert exhausted is False  # cap hit -> the view is a WINDOW, not the full inventory


def test_paginate_first_page_error_propagates() -> None:
    def fetch(page: int) -> list[Any]:
        raise SourceError("boom")

    with pytest.raises(SourceError):
        paginate_until_empty(fetch, max_pages=5)


def test_paginate_later_page_error_keeps_earlier_but_not_exhausted() -> None:
    def fetch(page: int) -> list[Any]:
        if page == 1:
            return [1, 2]
        raise SourceError("rate limited mid-pagination")

    items, exhausted = paginate_until_empty(fetch, max_pages=5)
    assert items == [1, 2]  # page-1 kept
    assert exhausted is False  # truncated by the error -> absence proves nothing


# ── source pagination + query targeting ─────────────────────────────────────────────────


def _adzuna_page(n: int, start: int = 0) -> dict[str, Any]:
    return {
        "results": [
            {
                "id": str(i),
                "title": f"Backend Engineer {i}",  # unique -> no within-source dedup
                "company": {"display_name": "Acme"},
                "location": {"display_name": "Remote"},
                "redirect_url": f"https://ex/{i}",
                "created": "2026-07-14T00:00:00Z",
            }
            for i in range(start, start + n)
        ]
    }


def test_adzuna_paginates_and_query_targets(now_clock: FixedClock, cfg: Config) -> None:
    one = cfg.model_copy(deep=True)
    one.keywords.roles = ["backend engineer"]  # single role -> one `what` query + intern walk
    with respx.mock:
        route = respx.route(method="GET", host="api.adzuna.com").mock(
            side_effect=[
                httpx.Response(200, json=_adzuna_page(50)),  # role p1: full page -> keep going
                httpx.Response(200, json=_adzuna_page(10, start=50)),  # role p2: short -> stop
                httpx.Response(200, json=_adzuna_page(5, start=100)),  # intern walk: short -> stop
            ]
        )
        res = AdzunaSource("in", "A", "K", max_pages=5).fetch(one, now_clock)
    assert route.calls.call_count == 3
    assert res.n_fetched == 65
    assert res.exhaustive is True  # every walk ended on the API's own short-page signal
    assert route.calls[0].request.url.path.endswith("/search/1")
    assert route.calls[1].request.url.path.endswith("/search/2")
    params = route.calls[0].request.url.params
    assert params["what"] == "backend engineer"  # per-role AND phrase, not a broad word-OR
    assert params["category"] == "it-jobs"  # constrained to software/IT at the source
    intern_params = route.calls[2].request.url.params
    assert intern_params["title_only"] == "intern"  # dedicated internship query rides along
    assert "what" not in intern_params
    assert intern_params["max_days_old"] == "35"  # recency cap: intern search surfaces 2022 posts


def test_adzuna_no_roles_uses_fallback_query(now_clock: FixedClock) -> None:
    bare = Config()  # no roles -> a generic software fallback query, still IT-only
    bare.keywords.roles = []
    with respx.mock:
        route = respx.route(method="GET", host="api.adzuna.com").mock(
            return_value=httpx.Response(200, json=_adzuna_page(3))  # short -> one call
        )
        AdzunaSource("in", "A", "K").fetch(bare, now_clock)
    params = route.calls[0].request.url.params
    assert params["what"] == "software engineer"  # fallback when no roles configured
    assert params["category"] == "it-jobs"


def test_jooble_paginates_until_empty(now_clock: FixedClock, cfg: Config) -> None:
    def page(jid: str, title: str) -> dict[str, Any]:
        return {"jobs": [{"id": jid, "title": title, "link": f"https://j/{jid}"}]}

    with respx.mock:
        route = respx.route(method="POST", host="jooble.org").mock(
            side_effect=[
                httpx.Response(200, json=page("1", "Backend Eng")),
                httpx.Response(200, json=page("2", "ML Eng")),
                httpx.Response(200, json={"jobs": []}),  # stop
            ]
        )
        res = JoobleSource("K", ["backend"], "India", max_pages=10).fetch(cfg, now_clock)
    assert route.calls.call_count == 3
    assert res.n_fetched == 2
    assert json.loads(route.calls[1].request.content)["page"] == 2  # page incremented


def test_unstop_paginates_per_opportunity(now_clock: FixedClock, cfg: Config) -> None:
    recent = "2026-07-14T00:00:00Z"  # within the 30-day window of now_clock (2026-07-15)

    def page(n: int, start: int = 0) -> dict[str, Any]:
        return {
            "data": {
                "data": [
                    {
                        "id": str(i),
                        "title": f"Backend {i}",
                        "updated_at": recent,
                        "public_url": f"https://u/{i}",
                        "organisation": {"name": "Org"},
                    }
                    for i in range(start, start + n)
                ]
            }
        }

    with respx.mock:
        route = respx.route(method="GET", host="unstop.com").mock(
            side_effect=[
                httpx.Response(200, json=page(30)),  # opp1 page1 full -> continue
                httpx.Response(200, json=page(5, 30)),  # opp1 page2 short -> stop
                httpx.Response(200, json=page(2, 100)),  # opp2 page1 short -> stop
            ]
        )
        res = UnstopSource(
            opportunities=["internships", "jobs"], search_terms=[], max_age_days=30, max_pages=10
        ).fetch(cfg, now_clock)
    assert route.calls.call_count == 3  # opp1: 2 pages, opp2: 1 page
    assert res.n_fetched == 37
    assert route.calls[1].request.url.params["page"] == "2"


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
    assert {"remoteok", "himalayas", "unstop", "internshala", "adzuna", "jooble"} <= names
    assert {"greenhouse", "lever", "ashby", "smartrecruiters"} <= names
    # jobicy ships disabled: zero internships + no India geo server-side (verified live).
    assert "jobicy" not in names


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
