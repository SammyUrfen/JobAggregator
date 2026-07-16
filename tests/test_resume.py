"""Track C/D — résumé tailoring (selection + merge-exclusion + preservation) and LaTeX render."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_aggregator.config.schema import ResumeConfig
from job_aggregator.errors import AgentError, RenderError
from job_aggregator.profile.schema import Contact, Education, Profile, Project, SkillGroup
from job_aggregator.resume import render
from job_aggregator.resume.tailor import (
    reorder_skills,
    score_project,
    select_projects,
    tailor_resume,
)


class FakeBackend:
    """Returns a scripted completion; records that it was called. `raises` forces an AgentError."""

    def __init__(self, response: str = "", *, raises: bool = False) -> None:
        self.response = response
        self.raises = raises
        self.calls = 0

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.calls += 1
        if self.raises:
            raise AgentError("backend down")
        return self.response


def _profile(*projects: Project) -> Profile:
    return Profile(
        contact=Contact(name="A Dev", email="a@b.com", location="Bengaluru"),
        summary="systems builder",
        skills=[
            SkillGroup(category="Languages", items=["Go", "Python", "C++"]),
            SkillGroup(category="Frontend", items=["React", "CSS"]),
        ],
        education=[Education(institution="Uni", degree="B.Sc. CS", grade="CGPA: 9.0")],
        projects=list(projects),
    )


_DB = Project(
    name="WALterDB",
    tagline="DB engine",
    tech=["C++20", "B+tree"],
    tags=["database", "systems", "storage"],
    bullets=["Built a DB engine in C++20 with 7K LOC and 91 tests."],
)
_WEB = Project(
    name="Portfolio",
    tagline="website",
    tech=["React"],
    tags=["frontend", "web"],
    bullets=["Built a personal website with 3 pages."],
)


# ── selection / ranking ───────────────────────────────────────────────────────────────────


def test_select_ranks_relevant_project_first() -> None:
    prof = _profile(_WEB, _DB)  # DB listed second
    picked = select_projects(prof, {"database", "systems", "c++"}, max_projects=2)
    assert picked[0].name == "WALterDB"  # JD-relevant one floats up


def test_select_caps_to_max_projects() -> None:
    assert len(select_projects(_profile(_DB, _WEB), {"systems"}, max_projects=1)) == 1


def test_score_project_counts_overlap() -> None:
    assert score_project(_DB, {"database", "systems", "unrelated"}) == 2


def test_reorder_skills_surfaces_relevant_group() -> None:
    prof = _profile(_DB)
    ordered = reorder_skills(prof.skills, {"go", "python"})
    assert ordered[0].category == "Languages"  # matched group first


# ── tailoring: no backend = pure selection (zero fabrication risk) ────────────────────────


def test_tailor_without_backend_preserves_everything() -> None:
    res = tailor_resume(_profile(_DB), "backend database systems role", backend=None)
    assert res.preservation == 1.0
    assert res.flags == []
    assert res.projects[0].bullets == _DB.bullets  # untouched


# ── tailoring: merge-exclusion guard ──────────────────────────────────────────────────────


def test_guard_rejects_fabricated_number() -> None:
    # Backend invents "1000000 users" — a number absent from the source -> rejected, original kept.
    fake = FakeBackend("Scaled the DB engine to 1000000 users across 50 nodes.")
    res = tailor_resume(_profile(_DB), "systems", backend=fake, config=ResumeConfig(max_projects=1))
    assert res.projects[0].bullets == _DB.bullets  # fell back to the truthful original
    assert any("rejected" in f for f in res.flags)


def test_guard_accepts_rewrite_using_only_source_numbers() -> None:
    fake = FakeBackend("Engineered a C++20 database engine — 7K LOC, 91 tests.")
    res = tailor_resume(
        _profile(_DB), "c++ database", backend=fake, config=ResumeConfig(max_projects=1)
    )
    assert res.projects[0].bullets == ["Engineered a C++20 database engine — 7K LOC, 91 tests."]
    assert res.preservation == 1.0  # both source numbers (7, 91) retained


def test_low_preservation_is_flagged() -> None:
    fake = FakeBackend("Built a database engine.")  # drops the 7K / 91 metrics (no new numbers)
    res = tailor_resume(
        _profile(_DB), "database", backend=fake, config=ResumeConfig(max_projects=1)
    )
    assert res.preservation < 0.8
    assert any("preservation" in f for f in res.flags)


def test_backend_failure_degrades_to_original() -> None:
    fake = FakeBackend(raises=True)
    res = tailor_resume(
        _profile(_DB), "database", backend=fake, config=ResumeConfig(max_projects=1)
    )
    assert res.projects[0].bullets == _DB.bullets
    assert any("skipped" in f for f in res.flags)


# ── LaTeX render ──────────────────────────────────────────────────────────────────────────


def test_render_latex_includes_facts_and_wraps_document() -> None:
    prof = _profile(_DB)
    tex = render.render_latex(prof, tailor_resume(prof, "database systems", backend=None))
    assert "\\begin{document}" in tex and "\\end{document}" in tex
    assert "\\resumeProjectHeading" in tex  # preamble macros preserved
    assert "WALterDB" in tex and "7K LOC" in tex  # real facts rendered
    assert "A Dev" in tex and "a@b.com" in tex  # header


def test_render_latex_escapes_special_chars() -> None:
    proj = Project(name="R&D Tool", tech=["C#"], tags=["x"], bullets=["Saved 50% cost."])
    prof = _profile(proj)
    tex = render.render_latex(prof, tailor_resume(prof, "tool", backend=None))
    assert "R\\&D Tool" in tex  # & escaped
    assert "50\\% cost" in tex  # % escaped
    assert "R&D Tool" not in tex  # raw ampersand never leaks


def test_compile_pdf_without_engine_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(render, "_find_engine", lambda: None)
    with pytest.raises(RenderError, match="no LaTeX engine"):
        render.compile_pdf(
            r"\documentclass{article}\begin{document}x\end{document}", tmp_path / "x.pdf"
        )
