"""Phase 4 — sources.jobspy_source.

mock jobspy.scrape_jobs DataFrame -> per-site Jobs + sub_results + error handling.

Deterministic, no network: the `_scrape_jobs` seam is monkeypatched with a fake that returns a
pandas DataFrame (dispatched on site) or raises (to simulate a 429).

See PLAN.md Part II (Phase 4) for the exact table-driven cases to implement.
"""

from __future__ import annotations

from collections.abc import Callable, Container
from datetime import date
from typing import Any

import pandas as pd
import pytest

from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.models.job import SalaryBucket
from job_aggregator.sources import jobspy_source as js

Fake = Callable[..., Any]


def _cfg(sites: list[str], terms: list[str]) -> Config:
    c = Config()
    c.sources.jobspy.sites = sites
    c.sources.jobspy.search_terms = terms
    return c


def _row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "Backend Engineer Intern",
        "company": "Acme",
        "job_url": "https://naukri.com/jobs/1?utm_source=x",
        "location": "Bengaluru, India",
        "is_remote": True,
        "description": "Build backend services.",
        "date_posted": date(2026, 7, 10),
        "min_amount": None,
        "max_amount": None,
        "currency": None,
        "interval": None,
    }
    base.update(over)
    return base


def _fake(
    rows_by_site: dict[str, list[dict[str, Any]]], *, raises_for: Container[str] = frozenset()
) -> Fake:
    def fake(**kwargs: Any) -> Any:
        site = kwargs["site_name"][0]
        if site in raises_for:
            raise RuntimeError("429 Too Many Requests")
        return pd.DataFrame(rows_by_site.get(site, []))

    return fake


def _subs(res: Any) -> dict[str, tuple[bool, int]]:
    return {name: (ok, n) for name, ok, n in res.sub_results}


def test_normalizes_basic_row(monkeypatch: pytest.MonkeyPatch, now_clock: FixedClock) -> None:
    monkeypatch.setattr(js, "_scrape_jobs", _fake({"naukri": [_row()]}))
    res = js.JobSpySource().fetch(_cfg(["naukri"], ["backend"]), now_clock)
    assert res.succeeded is True
    assert res.n_fetched == 1
    job = res.jobs[0]
    assert job.source == "jobspy_naukri"
    assert "utm_" not in job.url
    assert len(job.job_uid) == 64
    assert job.posted_at is not None
    assert job.posted_at.tzinfo is not None
    assert res.sub_results == [("jobspy_naukri", True, 1)]


def test_per_site_tagging_and_sub_results(
    monkeypatch: pytest.MonkeyPatch, now_clock: FixedClock
) -> None:
    monkeypatch.setattr(
        js,
        "_scrape_jobs",
        _fake(
            {
                "naukri": [_row(job_url="https://naukri.com/n1")],
                "indeed": [_row(company="Beta", job_url="https://indeed.com/i1")],
            }
        ),
    )
    res = js.JobSpySource().fetch(_cfg(["naukri", "indeed"], ["backend"]), now_clock)
    assert {j.source for j in res.jobs} == {"jobspy_naukri", "jobspy_indeed"}
    subs = _subs(res)
    assert subs["jobspy_naukri"] == (True, 1)
    assert subs["jobspy_indeed"] == (True, 1)


def test_linkedin_429_tolerated(monkeypatch: pytest.MonkeyPatch, now_clock: FixedClock) -> None:
    monkeypatch.setattr(js, "_scrape_jobs", _fake({"naukri": [_row()]}, raises_for={"linkedin"}))
    res = js.JobSpySource().fetch(_cfg(["naukri", "linkedin"], ["backend"]), now_clock)
    subs = _subs(res)
    assert subs["jobspy_naukri"] == (True, 1)
    assert subs["jobspy_linkedin"] == (False, 0)  # 429'd site untouched by stale-delete
    assert res.succeeded is True  # Naukri carried the source
    assert "linkedin" in (res.error or "")


def test_empty_dataframe_marks_site_failed(
    monkeypatch: pytest.MonkeyPatch, now_clock: FixedClock
) -> None:
    monkeypatch.setattr(js, "_scrape_jobs", _fake({"naukri": []}))
    res = js.JobSpySource().fetch(_cfg(["naukri"], ["backend"]), now_clock)
    assert res.succeeded is False
    assert _subs(res)["jobspy_naukri"] == (False, 0)


def test_rows_missing_required_fields_dropped(
    monkeypatch: pytest.MonkeyPatch, now_clock: FixedClock
) -> None:
    monkeypatch.setattr(
        js,
        "_scrape_jobs",
        _fake({"naukri": [_row(), _row(title=None, company="Beta", job_url="https://x/2")]}),
    )
    res = js.JobSpySource().fetch(_cfg(["naukri"], ["backend"]), now_clock)
    assert res.n_fetched == 1  # the title-less row is dropped


def test_dedup_within_site_across_terms(
    monkeypatch: pytest.MonkeyPatch, now_clock: FixedClock
) -> None:
    monkeypatch.setattr(js, "_scrape_jobs", _fake({"naukri": [_row()]}))  # same row per term
    res = js.JobSpySource().fetch(_cfg(["naukri"], ["backend", "systems"]), now_clock)
    assert res.n_fetched == 1  # identical job_uid deduped across the two term-calls


def test_salary_yearly_inr_normalized(
    monkeypatch: pytest.MonkeyPatch, now_clock: FixedClock
) -> None:
    monkeypatch.setattr(
        js,
        "_scrape_jobs",
        _fake(
            {
                "naukri": [
                    _row(min_amount=600000, max_amount=900000, currency="INR", interval="yearly")
                ]
            }
        ),
    )
    res = js.JobSpySource().fetch(_cfg(["naukri"], ["backend"]), now_clock)
    job = res.jobs[0]
    assert job.salary_min == 50000  # 600000/yr -> INR/month
    assert job.salary_max == 75000
    assert job.salary_currency == "INR"
    assert job.salary_period == "month"
    assert job.salary_parsed is True
    assert job.salary_bucket in set(SalaryBucket)


def test_salary_missing_is_unknown(monkeypatch: pytest.MonkeyPatch, now_clock: FixedClock) -> None:
    monkeypatch.setattr(js, "_scrape_jobs", _fake({"naukri": [_row()]}))
    res = js.JobSpySource().fetch(_cfg(["naukri"], ["backend"]), now_clock)
    job = res.jobs[0]
    assert job.salary_parsed is False
    assert job.salary_bucket is SalaryBucket.UNKNOWN


def test_build_scrape_kwargs_indeed_omits_is_remote() -> None:
    k = js._build_scrape_kwargs("indeed", "backend", Config().sources.jobspy)
    assert "is_remote" not in k  # Indeed drops filters if combined
    assert k["country_indeed"] == "india"
    assert k["hours_old"] == 48
    assert k["results_wanted"] == 40


def test_build_scrape_kwargs_naukri_has_is_remote_no_country() -> None:
    k = js._build_scrape_kwargs("naukri", "backend", Config().sources.jobspy)
    assert k.get("is_remote") is True
    assert "country_indeed" not in k


def test_build_scrape_kwargs_proxies_passthrough() -> None:
    jc = Config().sources.jobspy
    jc.proxies = ["http://proxy:8080"]
    k = js._build_scrape_kwargs("naukri", "backend", jc)
    assert k["proxies"] == ["http://proxy:8080"]


def test_no_sites_or_terms_returns_empty_without_calling_seam(
    monkeypatch: pytest.MonkeyPatch, now_clock: FixedClock
) -> None:
    calls = {"n": 0}

    def fake(**kwargs: Any) -> Any:
        calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(js, "_scrape_jobs", fake)
    res = js.JobSpySource().fetch(_cfg([], []), now_clock)
    assert res.succeeded is True
    assert res.jobs == []
    assert calls["n"] == 0  # seam never called
