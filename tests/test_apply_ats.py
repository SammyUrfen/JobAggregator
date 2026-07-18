"""Track D — apply/ats: deterministic ATS detection + form-map shape (pure, no browser)."""

from __future__ import annotations

import pytest

from job_aggregator.apply.ats import ATS_FORMS, detect_ats


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://boards.greenhouse.io/acme/jobs/123", "greenhouse"),
        ("https://job-boards.greenhouse.io/acme/jobs/9", "greenhouse"),
        ("https://jobs.lever.co/acme/abc-123", "lever"),
        ("https://jobs.ashbyhq.com/acme/xyz", "ashby"),
        ("https://jobs.smartrecruiters.com/Acme/12345", "smartrecruiters"),
    ],
)
def test_detect_known_ats(url: str, name: str) -> None:
    form = detect_ats(url)
    assert form is not None
    assert form.name == name


@pytest.mark.parametrize(
    "url",
    ["https://www.indeed.com/viewjob?jk=abc", "https://linkedin.com/jobs/view/1", "", "not a url"],
)
def test_detect_unknown_returns_none(url: str) -> None:
    assert detect_ats(url) is None


def test_every_ats_form_maps_email_and_resume() -> None:
    for form in ATS_FORMS:
        assert form.host_markers, form.name
        assert "email" in form.selectors, form.name
        assert "resume" in form.selectors, form.name
