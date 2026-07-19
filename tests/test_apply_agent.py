"""Track D — apply/agent: orchestration (fields, ATS wiring, never-auto-submit) via FakeDriver."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_aggregator.apply import agent as agent_mod
from job_aggregator.apply.agent import _split_name, apply_to_job, build_fields
from job_aggregator.apply.driver import FakeDriver, FillResult
from job_aggregator.config.schema import Config
from job_aggregator.errors import AgentError
from job_aggregator.models.job import Job
from job_aggregator.paths import PROFILE_EXAMPLE_YAML
from job_aggregator.profile.store import load_profile


def _profile() -> object:
    return load_profile(PROFILE_EXAMPLE_YAML)


def _cfg(*, enabled: bool = True, auto_submit: bool = False) -> Config:
    c = Config()
    c.apply.enabled = enabled
    c.apply.auto_submit = auto_submit
    return c


def _job(url: str = "https://jobs.lever.co/acme/abc") -> Job:
    return Job.model_validate(
        {
            "job_uid": "a" * 64,
            "source": "lever",
            "title": "Backend Engineer",
            "company": "Acme",
            "url": url,
            "description": "Python backend",
        }
    )


@pytest.mark.parametrize(
    ("full", "first", "last"),
    [("Bibek Jyoti Charah", "Bibek", "Jyoti Charah"), ("Madonna", "Madonna", ""), ("   ", "", "")],
)
def test_split_name(full: str, first: str, last: str) -> None:
    assert _split_name(full) == (first, last)


def test_build_fields_from_profile() -> None:
    p = _profile()
    f = build_fields(p, "/tmp/r.pdf")
    assert f.email == p.contact.email  # type: ignore[attr-defined]
    assert f.full_name == p.contact.name  # type: ignore[attr-defined]
    assert f.resume_path == "/tmp/r.pdf"
    assert f.first_name  # non-empty


def test_build_fields_includes_background_for_screening_answers() -> None:
    from job_aggregator.apply.agent import build_background

    p = _profile()
    f = build_fields(p, "/tmp/r.pdf")
    # the agent needs profile substance to DRAFT screening answers (not leave them blank)
    assert f.background  # non-empty
    if p.projects:  # type: ignore[attr-defined]
        assert p.projects[0].name in f.background  # type: ignore[attr-defined]
    # background is NOT a form value — it must not leak into the fill map
    assert "background" not in f.text_map()
    # build_background is deterministic + reads only the profile
    assert build_background(p) == f.background


def test_apply_refuses_auto_submit() -> None:
    with pytest.raises(AgentError):
        apply_to_job(_job(), _profile(), _cfg(auto_submit=True), driver=FakeDriver())


def test_apply_refuses_when_disabled() -> None:
    with pytest.raises(AgentError):
        apply_to_job(_job(), _profile(), _cfg(enabled=False), driver=FakeDriver())


def test_apply_fills_never_submits_and_wires_ats(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JOBAGG_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(agent_mod, "compile_pdf", lambda tex, out: out)  # skip real LaTeX
    monkeypatch.setattr(agent_mod, "load_state", lambda domain: None)  # no crypto in tests
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        agent_mod, "save_state", lambda domain, state: saved.update({domain: state})
    )

    driver = FakeDriver(FillResult(filled=["email"], new_state={"cookies": [1]}))
    res = apply_to_job(_job("https://jobs.lever.co/acme/abc"), _profile(), _cfg(), driver=driver)
    assert res.submitted is False
    assert res.ats == "lever"  # known ATS -> deterministic selectors passed to the driver
    assert driver.calls[0]["selectors"] is not None
    assert res.filled == ["email"]
    assert "jobs.lever.co" in saved  # a fresh session state was persisted

    # unknown host -> None selectors (the driver's generic path); no new_state -> no save
    driver2 = FakeDriver(FillResult(filled=[], new_state=None))
    res2 = apply_to_job(
        _job("https://www.indeed.com/viewjob?jk=1"), _profile(), _cfg(), driver=driver2
    )
    assert res2.ats is None
    assert driver2.calls[0]["selectors"] is None
