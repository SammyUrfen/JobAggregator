"""Feed-quality gates: unpaid postings and over-long internships (filters), plus their config knobs
in the dashboard form. Texts come from real postings in the 2026-09-15 audit. The owner's rule is
"never drop on silence", so every gate has a kept case where the posting states nothing."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_aggregator.clock import FixedClock
from job_aggregator.config.schema import Config
from job_aggregator.config.store import seed_from_yaml
from job_aggregator.dashboard.app import create_app
from job_aggregator.models.job import Job
from job_aggregator.pipeline.filters import score_and_filter
from job_aggregator.storage.db import connect, init_db

JobFactory = Callable[..., Job]

# Python/backend satisfy the must_have anchor, so each case reaches the new gates.
_ANCHOR = "Build Python backend services. "
_INTERN = {"title": "Backend Engineering Intern", "is_internship": True}
_FULL_TIME = {"title": "Backend Engineer", "is_internship": False}


@pytest.mark.parametrize(
    ("job", "description", "reason"),
    [
        # unpaid: any posting that SAYS it pays nothing
        (_INTERN, "Stipend: Unpaid", "unpaid"),
        (_INTERN, "3-Month Unpaid Internship", "unpaid"),
        (_FULL_TIME, "Founder role, without any STIPEND", "unpaid"),
        (_INTERN, "Stipend details will be shared later", None),  # silence is not unpaid
        (_INTERN, "Benefits: unpaid leave on request", None),  # a benefits line, not the pay
        # duration: internships only, longer than keywords.max_internship_months (4)
        (_INTERN, "Duration: 6 months", "duration:6m"),
        (_INTERN, "This is a 12-month internship", "duration:12m"),
        (_INTERN, "This is a 36 month internship", None),  # a hyphen-stripped "3-6", kept
        (_INTERN, "a 5-6-month internship from January", "duration:5m"),  # a range's lower bound
        (_INTERN, "Duration: 4 months", None),  # at the cap, not over it
        (_INTERN, "a 3-6 months internship", None),  # a 3-month commitment
        (_INTERN, "Duration: 12 weeks", None),  # 2.8 months
        (_INTERN, "Join our team", None),  # no stated duration
        (_FULL_TIME, "Starts with a 6-month training program", None),  # not an internship
    ],
)
def test_quality_gates(
    make_job: JobFactory, cfg: Config, job: dict[str, object], description: str, reason: str | None
) -> None:
    verdict = score_and_filter(make_job(**job, description=_ANCHOR + description), cfg)
    assert verdict.keep is (reason is None)
    if reason is not None:
        assert verdict.reasons == [reason]


@pytest.mark.parametrize(
    ("salary", "keep"),
    [
        ({"salary_min": 10000, "salary_max": 15000}, True),  # the source's positive pay wins
        ({"salary_max": 15000, "salary_parsed": False}, True),  # positive even if unconvertible
        # contract: an adapter stores unpaid as 0, which is not a positive salary
        ({"salary_min": 0, "salary_max": 0, "salary_currency": "INR"}, False),
        ({}, False),
    ],
)
def test_unpaid_text_yields_to_a_positive_salary(
    make_job: JobFactory, cfg: Config, salary: dict[str, object], keep: bool
) -> None:
    job = make_job(**_INTERN, description=_ANCHOR + "This is an unpaid internship", **salary)
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is keep
    if not keep:
        assert verdict.reasons == ["unpaid"]


def test_duration_gate_zero_disables(make_job: JobFactory, cfg: Config) -> None:
    off = cfg.model_copy(deep=True)
    off.keywords.max_internship_months = 0
    job = make_job(**_INTERN, description=_ANCHOR + "Duration: 6 months")
    assert score_and_filter(job, off).keep is True


@pytest.mark.parametrize(
    ("job", "reason"),
    [
        # an earlier gate still names the drop: the title exclude comes first
        ({"title": "Senior Backend Intern", "is_internship": True}, "excluded:senior"),
        # the new gates run before location, so a far-away unpaid posting reports "unpaid"
        ({**_INTERN, "location": "Berlin, Germany"}, "unpaid"),
    ],
)
def test_quality_gate_order(
    make_job: JobFactory, cfg: Config, job: dict[str, object], reason: str
) -> None:
    verdict = score_and_filter(make_job(**job, description=_ANCHOR + "Stipend: Unpaid"), cfg)
    assert verdict.reasons == [reason]


# ── dashboard config form ───────────────────────────────────────────────────────────────────


class _Scheduler:
    """The SchedulerProtocol surface the config routes touch; nothing runs."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def trigger_now(self, trigger: str = "manual") -> int | str | None:
        return None

    def reschedule_daily(self, run_hour: int) -> None: ...
    @property
    def next_run_at(self) -> datetime | None:
        return None


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = tmp_path / "jobs.db"
    conn = connect(path)
    init_db(conn)
    seed_from_yaml(conn)
    conn.close()
    return str(path)


@pytest.fixture
def client(db_path: str) -> Iterator[TestClient]:
    now = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    app = create_app(db_path=db_path, clock=FixedClock(now), scheduler=_Scheduler())
    with TestClient(app) as c:
        yield c


def _stored(db_path: str) -> dict[str, object]:
    conn = connect(db_path)
    try:
        data: dict[str, object] = json.loads(
            conn.execute("SELECT data FROM config WHERE id=1").fetchone()["data"]
        )
    finally:
        conn.close()
    return data


@pytest.mark.parametrize(
    ("name", "value"),
    [("max_internship_months", 4), ("closure_checks_per_run", 25)],
)
def test_config_page_renders_quality_knobs(client: TestClient, name: str, value: int) -> None:
    html = client.get("/config").text
    assert re.search(rf'<input type="number" id="{name}" name="{name}"[^>]*value="{value}"', html)


@pytest.mark.parametrize(
    ("name", "value", "section", "key"),
    [
        ("max_internship_months", "6", "keywords", "max_internship_months"),
        ("max_internship_months", "0", "keywords", "max_internship_months"),  # 0 = off
        ("closure_checks_per_run", "10", "schedule", "closure_checks_per_run"),
        ("closure_checks_per_run", "0", "schedule", "closure_checks_per_run"),
    ],
)
def test_config_put_persists_quality_knobs(
    client: TestClient, db_path: str, name: str, value: str, section: str, key: str
) -> None:
    assert client.put("/api/config", data={name: value}).status_code == 200
    stored = _stored(db_path)[section]
    assert isinstance(stored, dict)
    assert stored[key] == int(value)


@pytest.mark.parametrize(
    ("name", "dotted"),
    [
        ("max_internship_months", "keywords.max_internship_months"),
        ("closure_checks_per_run", "schedule.closure_checks_per_run"),
    ],
)
def test_config_put_rejects_negative_quality_knobs(
    client: TestClient, name: str, dotted: str
) -> None:
    r = client.put("/api/config", data={name: "-1"})
    assert r.status_code == 422
    assert r.json()["error"]["details"]["errors"][0]["field"] == dotted


@pytest.mark.parametrize(
    ("location", "keep"),
    [
        ("Remote - US", False),  # names a place outside the configured ones
        ("LATAM - Remote", False),
        ("Remote - India", True),
        ("Bengaluru, Karnataka", True),
        ("Worldwide", True),  # names no place
        ("Remote", True),
    ],
)
def test_location_gate_matches_places_not_the_word_remote(
    make_job: JobFactory, cfg: Config, location: str, keep: bool
) -> None:
    job = make_job(**_INTERN, description=_ANCHOR, location=location, is_remote=None)
    assert score_and_filter(job, cfg).keep is keep
