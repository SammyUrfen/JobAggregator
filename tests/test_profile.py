"""Track C — profile store: the real profile.yaml validates, and load errors are friendly."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_aggregator.errors import ConfigError
from job_aggregator.paths import PROFILE_EXAMPLE_YAML, default_resume_template
from job_aggregator.profile.schema import Profile
from job_aggregator.profile.store import load_profile


def test_example_profile_validates_and_has_structure() -> None:
    # The real profile.yaml is git-ignored (personal). The committed placeholder example must
    # always validate + carry the shape the tailor relies on (so a clean checkout is usable).
    prof = load_profile(PROFILE_EXAMPLE_YAML)
    assert prof.contact.name and prof.contact.email
    assert prof.projects, "example must show at least one project"
    for p in prof.projects:  # tailoring/relevance depend on facts + tech + tags
        assert p.bullets and p.tech and p.tags, f"{p.name} is missing facts/tech/tags"
    assert prof.education and any(s.category == "Languages" for s in prof.skills)


def test_load_missing_profile_raises_configerror(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="profile not found"):
        load_profile(tmp_path / "nope.yaml")


def test_load_malformed_yaml_raises_configerror(tmp_path: Path) -> None:
    bad = tmp_path / "profile.yaml"
    bad.write_text("contact: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid"):
        load_profile(bad)


def test_load_schema_violation_raises_configerror(tmp_path: Path) -> None:
    bad = tmp_path / "profile.yaml"
    bad.write_text("summary: no contact block here\n", encoding="utf-8")  # contact is required
    with pytest.raises(ConfigError, match="validation"):
        load_profile(bad)


def test_minimal_profile_is_valid() -> None:
    # Only contact is required; everything else defaults empty (a brand-new user's starting point).
    prof = Profile.model_validate({"contact": {"name": "A", "email": "a@b.com"}})
    assert prof.projects == [] and prof.skills == []


def test_packaged_template_exists_with_section_macros() -> None:
    tmpl = default_resume_template().read_text(encoding="utf-8")
    # the tailor/renderer rely on these macros + sections existing in the template
    tokens = (
        r"\resumeProjectHeading",
        r"\techstack",
        r"\section{Projects}",
        r"\section{Education}",
    )
    for token in tokens:
        assert token in tmpl, f"template missing {token!r}"


def test_load_profile_text_falls_back_to_example(tmp_path: Path) -> None:
    from job_aggregator.profile.store import load_profile_text

    text = load_profile_text(tmp_path / "nope.yaml")  # missing -> the committed example template
    assert "contact" in text


def test_save_profile_text_validates_and_writes(tmp_path: Path) -> None:
    from job_aggregator.paths import PROFILE_EXAMPLE_YAML
    from job_aggregator.profile.store import load_profile, save_profile_text

    target = tmp_path / "profile.yaml"
    save_profile_text(PROFILE_EXAMPLE_YAML.read_text(), target)
    assert target.exists()
    assert load_profile(target).contact.email  # round-trips as a valid Profile


def test_save_profile_text_rejects_invalid(tmp_path: Path) -> None:
    from job_aggregator.errors import ConfigError
    from job_aggregator.profile.store import save_profile_text

    target = tmp_path / "profile.yaml"
    with pytest.raises(ConfigError):
        save_profile_text("contact: 123", target)  # contact must be an object
    assert not target.exists()  # an invalid profile is never written
