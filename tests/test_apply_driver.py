"""Track D — apply/driver: ApplicationFields + FakeDriver (no browser needed)."""

from __future__ import annotations

from job_aggregator.apply.driver import ApplicationFields, FakeDriver, FillResult


def _fields() -> ApplicationFields:
    return ApplicationFields(
        full_name="Bibek Charah",
        first_name="Bibek",
        last_name="Charah",
        email="b@example.com",
        resume_path="/tmp/r.pdf",
        phone="123",
        location="Bengaluru",
        linkedin="https://linkedin.com/in/x",
        github=None,
    )


def test_text_map_drops_none_and_file() -> None:
    m = _fields().text_map()
    assert m["email"] == "b@example.com"
    assert m["linkedin"].startswith("https://")
    assert "github" not in m  # None dropped
    assert "resume" not in m and "resume_path" not in m  # the file is handled separately


def test_fill_result_defaults_not_submitted() -> None:
    assert FillResult().submitted is False


def test_fake_driver_records_and_returns() -> None:
    canned = FillResult(filled=["email", "resume"], unfilled=["phone"], screenshot_path="/s.png")
    driver = FakeDriver(canned)
    out = driver.fill_form(
        "https://x/apply", _fields(), selectors={"email": "#email"}, storage_state={"cookies": []}
    )
    assert out is canned
    assert out.submitted is False
    call = driver.calls[0]
    assert call["url"] == "https://x/apply"
    assert call["selectors"] == {"email": "#email"}
    assert call["storage_state"] == {"cookies": []}
    assert call["headful"] is True
