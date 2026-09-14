"""Track C/D — résumé tailoring (selection + merge-exclusion + preservation) and LaTeX render."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_aggregator.config.schema import ResumeConfig
from job_aggregator.errors import AgentError, RenderError
from job_aggregator.profile.schema import (
    Contact,
    Education,
    Experience,
    Profile,
    Project,
    SkillGroup,
)
from job_aggregator.resume import render
from job_aggregator.resume.tailor import (
    TailoredResume,
    _guard_skills,
    _mentions,
    known_tech,
    reorder_skills,
    score_project,
    select_projects,
    select_skills,
    tailor_resume,
)


class FakeBackend:
    """Returns a scripted completion; records that it was called. `raises` forces an AgentError."""

    def __init__(self, response: str = "", *, raises: bool = False) -> None:
        self.response = response
        self.raises = raises
        self.calls = 0
        self.user = ""  # the last user prompt, so a test can check what the model was shown

    def complete(self, system: str, user: str, *, temperature: float = 0.2) -> str:
        self.calls += 1
        self.user = user
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
    assert res.used_llm is False  # deterministic selection, no LLM


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


def test_guard_rejects_a_technology_the_project_does_not_use() -> None:
    # The live fault: a job wanted PostgreSQL, and the model gave it to a SQLite project.
    # PostgreSQL is a real skill from another project, so only the whole-profile check sees it.
    sqlite_app = Project(
        name="Aggregator",
        tech=["Python", "SQLite"],
        bullets=[
            "Dedups jobs in SQLite on a content hash.",
            "Expires a job only on source success.",
        ],
    )
    pg_app = Project(name="Arena", tech=["Java", "PostgreSQL"], bullets=["Runs an auction."])
    fake = FakeBackend(
        "### Aggregator\n"
        "Dedups jobs in PostgreSQL on a content hash.\n"
        "Expires a job only after its source succeeds, in Python.\n"
    )
    res = tailor_resume(
        _profile(sqlite_app, pg_app),
        "postgresql",
        backend=fake,
        config=ResumeConfig(max_projects=1),
    )
    assert res.projects[0].bullets == ["Expires a job only after its source succeeds, in Python."]
    assert any("added PostgreSQL" in f for f in res.flags)


@pytest.mark.parametrize(
    ("text", "term", "hit"),
    [
        ("written in Go.", "Go", True),
        ("a Google OAuth flow", "Go", False),  # a name inside a longer name
        ("go and fetch", "Go", False),  # case-sensitive: the word, not the language
        ("C++20 with CMake", "C++20", True),
        ("C++20 with CMake", "C++", True),  # a version may follow the name
        ("Java 21 and Spring", "Java", True),
        ("PostgreSQL/PostGIS geofence", "PostGIS", True),
        ("SQLite tables", "SQL", False),
    ],
)
def test_mentions_matches_whole_names_only(text: str, term: str, hit: bool) -> None:
    assert _mentions(text, term) is hit


def test_known_tech_splits_items_and_skips_plain_words() -> None:
    prof = _profile(Project(name="X", tech=["PostgreSQL/PostGIS", "queue", "eBPF (bcc/bpftrace)"]))
    tech = known_tech(prof)
    assert {"PostgreSQL", "PostGIS", "eBPF", "Go", "C++"} <= tech  # "Go"/"C++" from skills
    assert "queue" not in tech and "bcc" not in tech  # lowercase words are not guarded


def test_guard_ignores_case_for_words_but_not_for_short_names() -> None:
    # "Concurrency" is a skill item. The project's tag says "concurrency", so the rewrite is honest.
    # "Go" is short: the English "go" in a bullet must not license the language.
    locks = Project(
        name="Arena",
        tech=["Java"],
        tags=["concurrency"],
        bullets=["Bids go through one lock per auction."],
    )
    other = Project(name="Mesh", tech=["Go"], bullets=["Relays video."])
    prof = _profile(locks, other).model_copy(
        update={"skills": [SkillGroup(category="Core", items=["Concurrency"])]}
    )
    fake = FakeBackend(
        "### Arena\nConcurrency: bids go through one lock per auction.\nBids run in Go.\n"
    )
    res = tailor_resume(prof, "java", backend=fake, config=ResumeConfig(max_projects=1))
    assert res.projects[0].bullets == ["Concurrency: bids go through one lock per auction."]
    assert any("added Go" in f for f in res.flags)


def test_guard_caps_bullets_per_project() -> None:
    wide = Project(name="Wide", tech=["Go"], bullets=[f"Fact {n}." for n in range(1, 6)])
    fake = FakeBackend("### Wide\n" + "".join(f"Fact {n}.\n" for n in range(1, 6)))
    res = tailor_resume(_profile(wide), "go", backend=fake, config=ResumeConfig(max_projects=1))
    assert res.projects[0].bullets == ["Fact 1.", "Fact 2.", "Fact 3."]


def test_project_that_keeps_none_of_its_numbers_is_flagged() -> None:
    fake = FakeBackend("Built a database engine.")  # drops the 7K / 91 metrics (no new numbers)
    res = tailor_resume(
        _profile(_DB), "database", backend=fake, config=ResumeConfig(max_projects=1)
    )
    assert res.preservation < 0.8
    assert any("WALterDB: kept none of its numbers" in f for f in res.flags)


def test_keeping_one_number_of_several_is_not_flagged() -> None:
    # Leaving out a number that this job does not need is allowed by the prompt.
    fake = FakeBackend("Built a DB engine in C++20 with 91 tests.")
    res = tailor_resume(
        _profile(_DB), "database", backend=fake, config=ResumeConfig(max_projects=1)
    )
    assert not [f for f in res.flags if "numbers" in f]


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


def test_render_latex_includes_experience_before_projects() -> None:
    # Experience was in the schema but never rendered, so an entry vanished from every PDF.
    prof = _profile(_DB).model_copy(
        update={
            "experience": [
                Experience(
                    company="Apache SkyWalking",
                    title="Open Source Contributor",
                    start="Sep 2026",
                    end="Present",
                    bullets=["Bounded the Top-N merge (#1322, merged)."],
                )
            ]
        }
    )
    tex = render.render_latex(prof, tailor_resume(prof, "database", backend=None))
    assert "\\section{Experience}" in tex
    assert "{Apache SkyWalking}{Sep 2026 -- Present}" in tex
    assert "(\\#1322, merged)" in tex  # escaped
    assert tex.index("\\section{Experience}") < tex.index("\\section{Projects}")


def test_render_latex_omits_experience_when_empty() -> None:
    prof = _profile(_DB)
    assert "\\section{Experience}" not in render.render_latex(prof, tailor_resume(prof, "db"))


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


# ── needs, evidence-based selection and tailored skills ────────────────────────────────────


def test_prompt_sends_projects_strongest_first_not_keyword_first() -> None:
    # The JD matches WALterDB's keywords, but the profile lists Portfolio first: the model must
    # see profile order, because profile order is the strength signal.
    fake = FakeBackend("### Portfolio\nShipped a personal website spanning 3 pages.\n")
    tailor_resume(_profile(_WEB, _DB), "database systems c++", backend=fake, config=ResumeConfig())
    assert fake.user.index("### Portfolio") < fake.user.index("### WALterDB")
    assert "CANDIDATE SKILLS:\nLanguages: Go, Python, C++" in fake.user


def test_llm_reply_with_needs_and_skills_sections() -> None:
    fake = FakeBackend(
        "### NEEDS\n"
        "- keep a database correct under concurrency\n"
        "### WALterDB\n"
        "Built a DB engine in C++20 with 91 tests.\n"
        "### SKILLS\n"
        "Languages: C++, Python\n"
    )
    res = tailor_resume(
        _profile(_DB, _WEB), "database", backend=fake, config=ResumeConfig(max_projects=1)
    )
    assert res.needs == ["keep a database correct under concurrency"]
    assert [p.name for p in res.projects] == ["WALterDB"]  # NEEDS took no project slot
    assert res.skills == [SkillGroup(category="Languages", items=["C++", "Python"])]
    assert res.flags == []


def test_an_invented_header_takes_no_project_slot() -> None:
    fake = FakeBackend(
        "### Nonexistent Project\nSome made-up work.\n"
        "### WALterDB\nBuilt a C++20 DB engine — 7K LOC, 91 tests.\n"
    )
    res = tailor_resume(
        _profile(_DB, _WEB), "database", backend=fake, config=ResumeConfig(max_projects=1)
    )
    assert [p.name for p in res.projects] == ["WALterDB"]


def test_missing_skills_section_falls_back_to_keyword_skills() -> None:
    fake = FakeBackend("### WALterDB\nBuilt a C++20 DB engine — 7K LOC, 91 tests.\n")
    res = tailor_resume(
        _profile(_DB), "python role", backend=fake, config=ResumeConfig(max_projects=1)
    )
    assert res.skills == [SkillGroup(category="Languages", items=["Python"])]
    assert any("no usable skills row" in f for f in res.flags)


def test_guard_skills_keeps_profile_items_only() -> None:
    skills = [
        SkillGroup(category="Languages", items=["Go", "Python"]),
        SkillGroup(
            category="Databases",
            items=["PostgreSQL/PostGIS", "Database internals (B+tree, LSM, WAL/ARIES)"],
        ),
    ]
    groups, flags = _guard_skills(
        [
            "**Languages**: go, Rust",  # case fixed to the profile spelling, Rust dropped
            "Cloud: AWS",  # a category the profile does not have
            "Databases: PostGIS, Database internals (B+tree, LSM, WAL/ARIES)",
        ],
        skills,
    )
    assert groups == [
        SkillGroup(category="Languages", items=["Go"]),
        SkillGroup(
            category="Databases",
            items=["PostGIS", "Database internals (B+tree, LSM, WAL/ARIES)"],
        ),
    ]
    assert len(flags) == 1 and "Rust" in flags[0] and "Cloud: AWS" in flags[0]


def test_guard_skills_rejects_an_item_under_the_wrong_category() -> None:
    skills = [
        SkillGroup(category="Languages", items=["Python"]),
        SkillGroup(category="Databases", items=["Redis"]),
    ]
    groups, flags = _guard_skills(["Languages: Redis, Python"], skills)
    assert groups == [SkillGroup(category="Languages", items=["Python"])]
    assert "Redis" in flags[0]


def test_guard_skills_caps_rows_and_items() -> None:
    skills = [
        SkillGroup(category=f"C{n}", items=[f"S{n}-{i}" for i in range(12)]) for n in range(6)
    ]
    lines = [f"C{n}: " + ", ".join(f"S{n}-{i}" for i in range(12)) for n in range(6)]
    groups, _ = _guard_skills(lines, skills)
    assert len(groups) == 4 and all(len(g.items) == 8 for g in groups)


def test_select_skills_keeps_only_items_the_job_names() -> None:
    skills = [
        SkillGroup(category="Languages", items=["Go", "Python", "C++"]),
        SkillGroup(category="Databases", items=["PostgreSQL", "Redis"]),
        SkillGroup(category="ML", items=["PyTorch"]),
    ]
    picked = select_skills(skills, {"postgresql", "python"})
    assert picked == [
        SkillGroup(category="Languages", items=["Python"]),
        SkillGroup(category="Databases", items=["PostgreSQL"]),
    ]
    assert select_skills(skills, {"nothing"}) == skills  # no match: keep every group


# ── LLM selection + rewrite (one call, the model chooses AND words the projects) ─────────


def test_llm_selects_and_words_both_projects() -> None:
    # ONE backend call; the model returns both projects under ### headers, reworded.
    response = (
        "### WALterDB\n"
        "Engineered a C++20 database engine — 7K LOC, 91 tests.\n"
        "### Portfolio\n"
        "Shipped a personal website spanning 3 pages.\n"
    )
    fake = FakeBackend(response)
    res = tailor_resume(
        _profile(_DB, _WEB), "database web", backend=fake, config=ResumeConfig(max_projects=2)
    )
    assert fake.calls == 1  # one call selects + words everything
    by_name = {p.name: p.bullets for p in res.projects}
    assert by_name["WALterDB"] == ["Engineered a C++20 database engine — 7K LOC, 91 tests."]
    assert by_name["Portfolio"] == ["Shipped a personal website spanning 3 pages."]
    assert res.used_llm is True


def test_llm_includes_only_the_projects_it_chose() -> None:
    # The model returns ONLY WALterDB -> the résumé shows ONLY WALterDB (it chose it); Portfolio
    # is not padded back in. This is the point of LLM selection.
    fake = FakeBackend("### WALterDB\nBuilt a C++20 database engine — 7K LOC, 91 tests.\n")
    res = tailor_resume(
        _profile(_DB, _WEB), "database web", backend=fake, config=ResumeConfig(max_projects=2)
    )
    assert [p.name for p in res.projects] == ["WALterDB"]  # only the chosen one
    assert res.used_llm is True


def test_llm_selection_honours_the_models_order() -> None:
    # WALterDB out-ranks Portfolio by keyword, but the model puts Portfolio first — its order wins.
    fake = FakeBackend(
        "### Portfolio\nShipped a personal website spanning 3 pages.\n"
        "### WALterDB\nBuilt a C++20 database engine — 7K LOC, 91 tests.\n"
    )
    res = tailor_resume(
        _profile(_DB, _WEB), "database systems", backend=fake, config=ResumeConfig(max_projects=2)
    )
    assert [p.name for p in res.projects] == ["Portfolio", "WALterDB"]  # model's display order


def test_llm_selection_caps_to_max_projects() -> None:
    # The model over-returns 2; max_projects=1 keeps only the first.
    fake = FakeBackend(
        "### WALterDB\nBuilt a C++20 DB engine — 7K LOC, 91 tests.\n"
        "### Portfolio\nShipped a 3-page website.\n"
    )
    res = tailor_resume(
        _profile(_DB, _WEB), "database", backend=fake, config=ResumeConfig(max_projects=1)
    )
    assert [p.name for p in res.projects] == ["WALterDB"]


def test_llm_ignores_a_hallucinated_project_name() -> None:
    # A '### Nonexistent' header the model invents is ignored; the real one is kept.
    fake = FakeBackend(
        "### Nonexistent Project\nSome made-up work.\n"
        "### WALterDB\nBuilt a C++20 DB engine — 7K LOC, 91 tests.\n"
    )
    res = tailor_resume(
        _profile(_DB, _WEB), "database", backend=fake, config=ResumeConfig(max_projects=2)
    )
    assert [p.name for p in res.projects] == ["WALterDB"]  # invented header dropped


def test_llm_unusable_output_falls_back_to_keyword_selection() -> None:
    # A reply with no headers (and >1 candidate) can't be attributed -> deterministic ranking,
    # bullets untouched.
    fake = FakeBackend("just prose, no headers at all")
    res = tailor_resume(
        _profile(_DB, _WEB), "database web", backend=fake, config=ResumeConfig(max_projects=2)
    )
    assert res.used_llm is False
    by_name = {p.name: p.bullets for p in res.projects}
    assert by_name["WALterDB"] == _DB.bullets  # verbatim originals
    assert any("keyword ranking" in f for f in res.flags)


# ── try_build_backend degradation ─────────────────────────────────────────────────────────


def test_try_build_backend_coding_agent_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_aggregator.apply import backends
    from job_aggregator.config.schema import ResumeConfig

    monkeypatch.setattr(backends.shutil, "which", lambda _exe: None)  # `claude` not on PATH
    assert backends.try_build_backend(ResumeConfig(backend="coding_agent")) is None


def test_try_build_backend_coding_agent_present(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_aggregator.apply import backends
    from job_aggregator.config.schema import ResumeConfig

    monkeypatch.setattr(backends.shutil, "which", lambda _exe: "/usr/bin/claude")
    assert backends.try_build_backend(ResumeConfig(backend="coding_agent")) is not None


def test_try_build_backend_openai_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from job_aggregator.apply import backends
    from job_aggregator.config.schema import ResumeConfig

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert backends.try_build_backend(ResumeConfig(backend="openai_compatible")) is None


# ── résumé filenames: company + job title + date, not a job_uid hash ──


def test_resume_path_is_named_for_the_job_and_dated(monkeypatch, tmp_path: Path) -> None:
    from datetime import date

    from job_aggregator import paths

    monkeypatch.setenv("JOBAGG_DATA_DIR", str(tmp_path))
    out = paths.resume_path("Acme Corp.", "Senior Backend Engineer", when=date(2026, 9, 1))
    assert out.name == "acme-corp_senior-backend-engineer_2026-09-01.pdf"
    assert out.parent == paths.resumes_dir()


def test_resume_path_slug_cannot_escape_the_resumes_dir(monkeypatch, tmp_path: Path) -> None:
    """A job board can put anything in a company name; the slug must stay [a-z0-9-]."""
    from datetime import date

    from job_aggregator import paths

    monkeypatch.setenv("JOBAGG_DATA_DIR", str(tmp_path))
    out = paths.resume_path("../../etc", "pa/ss?wd", when=date(2026, 9, 1))
    assert out.parent == paths.resumes_dir()
    assert out.name == "etc_pa-ss-wd_2026-09-01.pdf"


def test_find_resume_returns_the_newest_and_none_when_absent(monkeypatch, tmp_path: Path) -> None:
    from datetime import date

    from job_aggregator import paths

    monkeypatch.setenv("JOBAGG_DATA_DIR", str(tmp_path))
    paths.resumes_dir().mkdir(parents=True)
    assert paths.find_resume("Acme", "Backend Engineer") is None
    for day in (1, 15, 3):  # written out of order — resolution must not depend on write order
        paths.resume_path("Acme", "Backend Engineer", when=date(2026, 9, day)).write_bytes(b"x")
    newest = paths.find_resume("Acme", "Backend Engineer")
    assert newest is not None and newest.name.endswith("2026-09-15.pdf")
    assert paths.find_resume("Other Co", "Backend Engineer") is None  # scoped per company


# ── one-page fitting ───────────────────────────────────────────────────────────────────────


def _three_project_resume() -> tuple[Profile, TailoredResume]:
    projects = [
        Project(name=f"P{n}", bullets=[f"P{n} problem.", f"P{n} decision.", f"P{n} result."])
        for n in range(1, 4)
    ]
    prof = _profile(*projects)
    return prof, tailor_resume(prof, "p", backend=None, config=ResumeConfig(max_projects=3))


def test_build_pdf_trims_the_last_project_until_one_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pages = iter([2, 2, 1])
    monkeypatch.setattr(render, "_compile", lambda tex, out, engine: next(pages))
    prof, tailored = _three_project_resume()
    render.build_pdf(prof, tailored, tmp_path / "r.pdf")
    # Pass 1: P3 loses its middle bullet. Pass 2: P3 goes. Pass 3: one page.
    assert [p.name for p in tailored.projects] == ["P1", "P2"]
    assert tailored.flags == [
        "one page: dropped a middle bullet from P3",
        "one page: dropped P3, the last-ranked project",
    ]


def test_build_pdf_keeps_two_projects_even_when_still_long(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []
    monkeypatch.setattr(render, "_compile", lambda tex, out, engine: calls.append(1) or 2)
    prof, tailored = _three_project_resume()
    render.build_pdf(prof, tailored, tmp_path / "r.pdf")
    assert [p.name for p in tailored.projects] == ["P1", "P2"]
    assert tailored.projects[1].bullets == ["P2 problem.", "P2 result."]
    assert len(calls) == 4  # 3 trims, then a pass with nothing left to drop


def test_build_pdf_does_not_trim_when_the_page_count_is_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(render, "_compile", lambda tex, out, engine: None)  # e.g. tectonic
    prof, tailored = _three_project_resume()
    render.build_pdf(prof, tailored, tmp_path / "r.pdf")
    assert len(tailored.projects) == 3 and tailored.flags == []


def test_pages_regex_reads_the_pdflatex_log() -> None:
    log = "...\nOutput written on resume.pdf (2 pages, 110515 bytes).\nTranscript written"
    match = render._PAGES_RE.search(log)
    assert match is not None and match.group(1) == "2"


def test_render_latex_puts_the_grade_on_the_degree_line() -> None:
    prof = _profile(_DB)
    tex = render.render_latex(prof, tailor_resume(prof, "db"))
    assert "{B.Sc. CS, CGPA: 9.0}" in tex
