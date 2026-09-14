"""Truthful per-role résumé tailoring (Track C/D).

Pipeline (ResumeFlow-style, but with deterministic anti-fabrication guards from the research):

  1. Extract keywords from the job description (deterministic, used by the fallback).
  2. With a backend, ONE call names what the job needs, SELECTS projects by the evidence in their
     bullets (the profile lists projects strongest first), and chooses the skills rows. Without
     one, projects and skill items are ranked by keyword overlap.
  3. REWRITE each selected project's bullets via the backend to emphasize JD-relevant
     facts — but every rewrite passes a MERGE-EXCLUSION guard: a rewritten bullet that introduces a
     number not present in the source bullets, or names a technology (from anywhere in the
     profile) that the project itself does not name, is REJECTED. This makes metric and
     technology fabrication structurally impossible, not merely discouraged by the prompt.
  4. Score fact PRESERVATION (retained source numbers), and flag a project that kept none of its
     numbers, for the user to review.

If no backend is given, tailoring is pure selection/reordering of the user's own words — zero
fabrication risk and zero LLM cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from job_aggregator.config.schema import ResumeConfig
from job_aggregator.errors import AgentError
from job_aggregator.profile.schema import SkillGroup  # runtime: tailoring builds new skill rows

if TYPE_CHECKING:
    from job_aggregator.apply.backends import AgentBackend
    from job_aggregator.profile.schema import Profile, Project

# Common words carry no matching signal; drop them from JD keyword extraction.
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "you",
        "our",
        "are",
        "will",
        "have",
        "who",
        "this",
        "that",
        "your",
        "their",
        "from",
        "was",
        "were",
        "has",
        "had",
        "not",
        "but",
        "all",
        "any",
        "can",
        "job",
        "role",
        "team",
        "work",
        "working",
        "experience",
        "years",
        "year",
        "strong",
        "good",
        "using",
        "used",
        "use",
        "including",
        "etc",
        "such",
        "must",
        "should",
        "well",
        "able",
        "looking",
        "candidate",
        "candidates",
        "responsibilities",
        "requirements",
        "skills",
        "ability",
        "knowledge",
        "understanding",
        "plus",
        "preferred",
        "required",
        "we",
        "a",
        "an",
        "in",
        "on",
        "of",
        "to",
        "as",
        "is",
        "or",
        "at",
        "be",
        "by",
        "it",
    }
)
_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")
# A "hard fact" for the anti-fabrication guard: any number (250, 7, 1.85, 0.49, 26). Commas
# stripped so "1,000,000" == "1000000". These are what tailoring must never invent.
_NUMBER_RE = re.compile(r"\d[\d,]*\.?\d*")
# The template's own rule is "3 bullets max per project". A live run with no cap gave 4 projects
# of 40-55-word bullets, about 500 words: a catalogue, not a story.
_MAX_BULLETS_PER_PROJECT = 3
# About two printed lines at the template's \small size. A longer bullet packs in a second story.
_MAX_BULLET_WORDS = 35
# Splits a tech or skill item into the names inside it: "PostgreSQL/PostGIS" -> two names.
_TECH_SPLIT_RE = re.compile(r"[/,()&]")
# A technology name carries a capital, a digit, "+" or "#". A lowercase item ("queue",
# "threading") is an ordinary word, and a guard on it rejects honest rewrites.
_NAME_LIKE_RE = re.compile(r"[A-Z0-9+#]")
# Below this length a term is an acronym (SQL, RL) or "Go": match its case exactly.
_CASELESS_MIN_LEN = 4
# A tailored skills section holds at most this many rows, each with at most this many items. The
# untailored section had 7 rows and about 100 items, and it pushed every PDF onto a second page.
_MAX_SKILL_GROUPS = 4
_MAX_SKILLS_PER_GROUP = 8
# Splits a skills line on commas outside parentheses: "Database internals (B+tree, LSM), Redis".
_SKILL_ITEM_SPLIT_RE = re.compile(r",\s*(?![^()]*\))")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2 and t not in _STOPWORDS}


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUMBER_RE.findall(text)}


def known_tech(profile: Profile) -> frozenset[str]:
    """Every technology name the profile uses anywhere (project tech lines + skills).

    The tech guard needs the whole profile: the name a model adds to one project for a job
    ("PostgreSQL" on a SQLite project) is a real skill from a different project.
    """
    items = [t for p in profile.projects for t in p.tech]
    items += [i for g in profile.skills for i in g.items]
    terms = (part.strip() for item in items for part in _TECH_SPLIT_RE.split(item))
    return frozenset(t for t in terms if len(t) >= 2 and _NAME_LIKE_RE.search(t))


def _mentions(text: str, term: str, *, ignore_case: bool = False) -> bool:
    """Whole-name match. Case-sensitive by default: "Go" matches "in Go." but not "Google" or
    "go". A version number may follow the name, so "C++" matches "C++20" (a live run rejected an
    honest WALterDB rewrite without this)."""
    flags = re.IGNORECASE if ignore_case else 0
    pattern = rf"(?<![\w+#]){re.escape(term)}(?:\d[\d.]*)?(?![\w+#])"
    return re.search(pattern, text, flags) is not None


def _project_names(project: Project, term: str) -> bool:
    """Does the project's own text (name, tagline, tech, tags, bullets) name `term`?

    Case is ignored for a term of 4+ characters: a skill item such as "Concurrency" is written
    lowercase in the bullets, and a case-sensitive check rejects an honest rewrite. A shorter term
    is an acronym or "Go", where the lowercase form is a different word.
    """
    text = " ".join(
        [project.name, project.tagline or "", *project.tech, *project.tags, *project.bullets]
    )
    return _mentions(text, term, ignore_case=len(term) >= _CASELESS_MIN_LEN)


def jd_keywords(job_description: str) -> set[str]:
    """Deterministic keyword set from a job description."""
    return _tokens(job_description)


def _project_terms(project: Project) -> set[str]:
    parts = [project.name, project.tagline or "", *project.tech, *project.tags]
    return {t for chunk in parts for t in _tokens(chunk)}


def score_project(project: Project, keywords: set[str]) -> int:
    """How many of the JD keywords this project's name/tech/tags touch."""
    return len(_project_terms(project) & keywords)


def select_projects(profile: Profile, keywords: set[str], max_projects: int) -> list[Project]:
    """Rank projects by JD relevance, keeping profile order for ties (stable sort)."""
    ranked = sorted(profile.projects, key=lambda p: score_project(p, keywords), reverse=True)
    return ranked[:max_projects]


def reorder_skills(skills: list[SkillGroup], keywords: set[str]) -> list[SkillGroup]:
    """Surface skill groups that touch the JD first; stable for ties."""

    def relevance(group: SkillGroup) -> int:
        return sum(1 for item in group.items if _tokens(item) & keywords)

    return sorted(skills, key=relevance, reverse=True)


def select_skills(skills: list[SkillGroup], keywords: set[str]) -> list[SkillGroup]:
    """Deterministic skills section: only the items that touch the JD, most relevant groups first,
    capped like the LLM path. If no item touches the JD, every group (reordered), so a résumé never
    loses its skills section."""
    picked: list[SkillGroup] = []
    for group in reorder_skills(skills, keywords):
        items = [i for i in group.items if _tokens(i) & keywords][:_MAX_SKILLS_PER_GROUP]
        if items:
            picked.append(SkillGroup(category=group.category, items=items))
    return picked[:_MAX_SKILL_GROUPS] or reorder_skills(skills, keywords)


@dataclass
class TailoredResume:
    projects: list[Project]  # selected, ranked, bullets possibly reworded (facts preserved)
    skills: list[SkillGroup]  # only the groups and items this job needs, profile spelling
    summary: str
    preservation: float  # 0..1: fraction of source numeric facts still present after rewriting
    jd_keywords: list[str]
    flags: list[str] = field(default_factory=list)  # human-readable warnings to review
    used_llm: bool = False  # True when an LLM actually reworded bullets (vs pure selection)
    # The model's read of what the job needs: the reason behind its project choice. Shown for
    # review only, never printed on the résumé.
    needs: list[str] = field(default_factory=list)


# Per-project delimiter. The model echoes '### <name>' so we can attribute its chosen+reworded
# bullets back to a real project from ONE call — the LLM both SELECTS which projects to show and
# words them for the job, seeing the WHOLE portfolio + JD at once (deterministic keyword ranking
# is only the fallback).
_PROJECT_MARK = "### "
# Reserved section headers in the model's reply. They are not projects.
_NEEDS_SECTION = "NEEDS"
_SKILLS_SECTION = "SKILLS"
# Bound the candidate pool sent to the model (prompt size); the real profile has ~15 projects.
_MAX_CANDIDATES = 40
# Strip only true LIST numbering ("- ", "* ", "3. ", "12) ") — digits need the dot/bracket +
# space. A greedy [-*•\d.\s]+ would amputate a bullet that LEADS with a metric ("91 Catch2
# tests…" -> "Catch2 tests…"), silently losing facts.
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•]|\d{1,3}[.)])\s+")


def _strip_bullet(line: str) -> str:
    return _BULLET_PREFIX_RE.sub("", line).strip()


def _select_prompt(
    candidates: list[Project], skills: list[SkillGroup], job_description: str, max_projects: int
) -> tuple[str, str]:
    """The (system, user) prompt: give the model the JD + EVERY candidate project (strongest
    first) + every skill, and ask it to name the job's needs, pick `max_projects` projects by
    evidence, reword their bullets, and choose the skills rows, all under '### ' headers.

    Selection by evidence answers a live miss. With projects sent in keyword order and no sign of
    strength, a posting that said "System Design" pulled in a set of textbook LLD exercises over a
    measured distributed-typeahead build. So the model first names what the job needs, then looks
    for bullets that prove each need, and it prefers the earlier (stronger) project on a tie.

    The rewrite instruction asks for a PROBLEM -> WHY -> MECHANISM -> OUTCOME story rather than a
    catalogue of what was used. A bullet that says "~7K LOC, C++20, CMake, B+tree" tells a reader
    nothing they can interview against; "slotted pages let a variable-length row move without
    invalidating the index entries pointing at it" tells them how the thing works.

    The first live run of the older prompt showed two faults, and this text answers both. It
    packed every source fact into 40-55-word bullets, and it read "emphasize what the job cares
    about" as "add the job's keywords": a SQLite project gained PostgreSQL. So the prompt says
    that tailoring chooses and never adds, and it sets a bullet budget. The guards downstream
    (`_guard_bullets`) still block new numbers and new technology names. The prompt only shapes
    the voice.
    """
    system = (
        "You are an expert résumé editor tailoring an engineer's résumé to ONE job. Work in four "
        "steps, then answer in the OUTPUT FORMAT at the end.\n"
        "\n"
        "STEP 1: READ THE JOB FOR WHAT IT NEEDS. Read the responsibilities, not only the skill "
        "tags. Name the 3 to 5 capabilities this job needs most, in plain words (for example: "
        "design REST APIs, keep a database fast as data grows, reason about system design, test "
        "and debug before release).\n"
        "\n"
        f"STEP 2: CHOOSE {max_projects} PROJECTS BY EVIDENCE, NOT BY KEYWORD. For each need, "
        "look for the bullets that PROVE it. A project whose bullets show a capability in depth "
        "beats a project whose tech line only shares a word with the job. A database engine "
        "built from scratch proves 'optimize databases' better than an app that stores rows in "
        "PostgreSQL. A system designed and measured at scale proves 'system design' better than "
        "textbook design exercises. Cover different needs with different projects instead of "
        "proving one need four times. The candidates are listed STRONGEST FIRST: when two "
        "projects prove a need about equally, choose the one listed first. Order the chosen "
        "projects so the one that serves this job best comes first.\n"
        "\n"
        f"STEP 3: WRITE AT MOST {_MAX_BULLETS_PER_PROJECT} BULLETS FOR EACH CHOSEN PROJECT, "
        "told to a reader hiring for THIS job.\n"
        "\n"
        "TAILORING MEANS CHOOSING, NOT ADDING. Pick the true parts of each project that matter "
        "for this job, put them in order, and word them for this reader. Never add a technology, "
        "tool, database, platform or deployment that the project's own bullets and tech line do "
        "not name, even when the JOB asks for it. A job that wants PostgreSQL does not make a "
        "SQLite project use PostgreSQL.\n"
        "\n"
        "THE BULLETS OF ONE PROJECT READ AS A STORY. The first bullet says what problem the "
        "project solves and its core idea, so a reader who stops there still knows what was "
        "built and why. The next bullet gives the hardest design decision: why the obvious "
        "approach fails and what mechanism replaced it. The last bullet gives the outcome, or "
        "how the work was proven. Each bullet makes plain the skill it shows (for example "
        "concurrency correctness, crash recovery, query performance, reward design). Use the "
        "JOB's word for that skill only when the bullet's facts prove it.\n"
        "\n"
        "HOW TO WRITE A BULLET. Lead with the problem or the design decision, not with the verb "
        "'Built'. Name a technology only where the choice was load-bearing, never as a list of "
        "everything touched. Prefer the specific mechanism ('a wait-for-graph detector that "
        "rolls back a victim transaction') over the category ('concurrency control'). Each "
        f"bullet is one or two sentences and at most {_MAX_BULLET_WORDS} words. Leave out detail "
        "this job does not need. Do not pack three mechanisms into one bullet.\n"
        "\n"
        "DO NOT write résumé filler: no lines-of-code counts, no file/class/module/commit counts, "
        "no bare technology lists, no adjectives doing the work of evidence ('robust', "
        "'scalable', 'cutting-edge').\n"
        "\n"
        "STEP 4: CHOOSE THE SKILLS. From CANDIDATE SKILLS, keep only the skills this job asks "
        f"for or the chosen projects show. Write at most {_MAX_SKILL_GROUPS} rows as "
        f"'Category: item, item', with at most {_MAX_SKILLS_PER_GROUP} items in a row, the most "
        "relevant rows and items first. Copy every category and item exactly as written. You "
        "may shorten an item to one name inside it ('PostgreSQL' from 'PostgreSQL/PostGIS'). "
        "Never add a skill that is not in the list.\n"
        "\n"
        "STRICT RULES: (1) Choose ONLY from the given projects. Never invent a project. "
        "(2) Use ONLY facts present in that project's own bullets, tagline and tech line. Never "
        "invent numbers, technologies, companies, users, deployments or outcomes. Reframing is "
        "allowed, adding is not. (3) A number you use must appear exactly as written in that "
        "project's bullets. Keep the outcome numbers that prove the story (throughput, latency, "
        "a score delta, a defect count). You may leave out a number that belongs to a part of "
        "the story this job does not need.\n"
        "\n"
        "OUTPUT FORMAT, and nothing else:\n"
        f"{_PROJECT_MARK}{_NEEDS_SECTION}\n"
        "one line for each need from step 1\n"
        f"{_PROJECT_MARK}<exact project name>\n"
        "its bullets, one on each line, no numbering\n"
        f"[repeat the project block for each chosen project, at most {max_projects}]\n"
        f"{_PROJECT_MARK}{_SKILLS_SECTION}\n"
        "one 'Category: item, item' line for each skills row"
    )
    parts = [f"JOB:\n{job_description.strip()}", "", "CANDIDATE PROJECTS (strongest first):"]
    for project in candidates:
        parts.append(f"{_PROJECT_MARK}{project.name}")
        if project.tagline:
            parts.append(f"({project.tagline}; tech: {', '.join(project.tech)})")
        parts.extend(f"- {b}" for b in project.bullets)
        parts.append("")
    parts.append("CANDIDATE SKILLS:")
    parts.extend(f"{g.category}: {', '.join(g.items)}" for g in skills)
    return system, "\n".join(parts)


def _parse_rewrite(raw: str, candidates: list[Project]) -> dict[str, list[str]]:
    """Ordered map project name -> reworded bullet lines from the model output. Lenient: with NO
    headers and a single candidate, treat every line as that project's bullets. Unattributable
    multi-project output yields {} so the caller falls back to deterministic selection."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(_PROJECT_MARK):
            current = stripped[len(_PROJECT_MARK) :].strip()
            sections.setdefault(current, [])
        elif current is not None and stripped and not stripped.startswith("("):
            sections[current].append(_strip_bullet(stripped))
    if not sections and len(candidates) == 1:
        lines = [_strip_bullet(ln.strip()) for ln in raw.splitlines() if ln.strip()]
        return {candidates[0].name: lines}
    return sections


def _guard_bullets(
    project: Project, candidates: list[str], tech: frozenset[str]
) -> tuple[list[str], list[str]]:
    """Anti-fabrication guard for ONE project. Keep a reworded bullet only if it introduces no
    number absent from that project's source bullets (structural, per arXiv 2605/2607) AND names
    no technology from `tech` that the project's own text does not name (`_project_names`).
    Count-agnostic (the model may condense/reorder), capped at the source bullet count so it
    cannot pad, and at `_MAX_BULLETS_PER_PROJECT`. If nothing survives, fall back to the truthful
    originals. Returns (bullets, flags).

    ponytail: the tech check knows only names the profile uses somewhere. A job-only name
    (Kubernetes, when no project lists it) passes. Add a check on the job's own terms if a real
    run shows one.
    """
    source_numbers: set[str] = set()
    for bullet in project.bullets:
        source_numbers |= _numbers(bullet)
    foreign = sorted(t for t in tech if not _project_names(project, t))
    kept: list[str] = []
    flags: list[str] = []
    rejected = False
    added_tech: set[str] = set()
    for candidate in candidates:
        text = candidate.strip()
        if not text:
            continue
        named = {t for t in foreign if _mentions(text, t)}
        if named:
            added_tech |= named
        elif _numbers(text) <= source_numbers:
            kept.append(text)
        else:
            rejected = True
    # Never more bullets than the source, and never more than the template holds.
    kept = kept[: min(max(len(project.bullets), 1), _MAX_BULLETS_PER_PROJECT)]
    if rejected:
        flags.append(f"{project.name}: rejected a rewrite that introduced unsupported facts")
    if added_tech:
        flags.append(
            f"{project.name}: rejected a rewrite that added {', '.join(sorted(added_tech))}, "
            "which the project does not use"
        )
    if not kept:
        kept = list(project.bullets)  # nothing usable -> the truthful originals
        flags.append(f"{project.name}: rewrites unusable — kept original bullets")
    return kept, flags


def _guard_skills(lines: list[str], skills: list[SkillGroup]) -> tuple[list[SkillGroup], list[str]]:
    """Anti-fabrication guard for the skills section. A row must name a profile category, and an
    item must be a profile item of THAT category, or one name inside it ("PostgreSQL" from
    "PostgreSQL/PostGIS"). The profile's spelling wins, and the caps hold. Returns (groups,
    flags)."""
    by_category = {g.category.casefold(): g for g in skills}
    picked: dict[str, list[str]] = {}
    dropped: list[str] = []
    for line in lines:
        category, sep, rest = line.partition(":")
        group = by_category.get(category.strip().strip("*").strip().casefold())
        if not sep or group is None:
            dropped.append(line.strip())
            continue
        allowed: dict[str, str] = {}
        for item in group.items:
            for name in [item, *_TECH_SPLIT_RE.split(item)]:
                if name.strip():
                    allowed.setdefault(name.strip().casefold(), name.strip())
        items = picked.setdefault(group.category, [])
        for raw_item in _SKILL_ITEM_SPLIT_RE.split(rest):
            name = raw_item.strip().strip("*").rstrip(".").strip()
            canonical = allowed.get(name.casefold())
            if canonical is None:
                if name:
                    dropped.append(name)
            elif canonical not in items:
                items.append(canonical)
    groups = [
        SkillGroup(category=category, items=items[:_MAX_SKILLS_PER_GROUP])
        for category, items in picked.items()
        if items
    ][:_MAX_SKILL_GROUPS]
    flags = []
    if dropped:
        flags.append(f"skills: dropped {', '.join(dropped[:5])}, which your profile does not list")
    return groups, flags


def tailor_resume(
    profile: Profile,
    job_description: str,
    *,
    backend: AgentBackend | None = None,
    config: ResumeConfig | None = None,
) -> TailoredResume:
    """Produce a truthful, JD-tailored résumé view.

    With a `backend`, ONE call lets the LLM name the job's needs, SELECT projects by the evidence
    in their bullets (profile order = strongest first), reword those bullets, and choose the
    skills rows. Every bullet passes the number + technology guard, every skill passes the
    profile guard, and an unusable reply or a backend failure degrades to deterministic keyword
    selection (never a crash or a fabricated fact). `backend=None` -> pure deterministic
    selection, bullets verbatim."""
    cfg = config or ResumeConfig()
    keywords = jd_keywords(job_description)
    skills = select_skills(profile.skills, keywords)

    flags: list[str] = []
    tailored: list[Project] = []
    used_llm = False
    needs: list[str] = []

    if backend is not None and profile.projects:
        candidates = profile.projects[:_MAX_CANDIDATES]  # profile order: strongest first
        by_name = {p.name: p for p in candidates}
        tech = known_tech(profile)
        system, user = _select_prompt(candidates, profile.skills, job_description, cfg.max_projects)
        try:
            raw = backend.complete(system, user, temperature=cfg.temperature)
            sections = _parse_rewrite(raw, candidates)
        except AgentError as exc:  # degrade: a backend failure never breaks tailoring
            sections = {}
            flags.append(f"tailoring skipped ({exc})")
        needs = sections.pop(_NEEDS_SECTION, []) if _NEEDS_SECTION not in by_name else []
        skill_lines = sections.pop(_SKILLS_SECTION, []) if _SKILLS_SECTION not in by_name else []
        # Only real project headers count toward the cap: an invented header takes no slot.
        chosen = [(by_name[name], lines) for name, lines in sections.items() if name in by_name]
        for project, cand_bullets in chosen[: cfg.max_projects]:
            bullets, gflags = _guard_bullets(project, cand_bullets, tech)
            flags.extend(gflags)
            tailored.append(project.model_copy(update={"bullets": bullets}))
            used_llm = True
        if used_llm:
            llm_skills, sflags = _guard_skills(skill_lines, profile.skills)
            flags.extend(sflags)
            if llm_skills:
                skills = llm_skills
            else:
                flags.append("skills: the model gave no usable skills row — used keyword selection")

    if not tailored:  # no backend, or the LLM produced nothing usable -> keyword ranking
        if backend is not None and not used_llm:
            flags.append("tailoring: LLM selection unusable — used keyword ranking")
        for project in select_projects(profile, keywords, cfg.max_projects):
            tailored.append(project.model_copy(update={"bullets": list(project.bullets)}))

    source_by_name = {p.name: p for p in profile.projects}
    src = [b for p in tailored for b in source_by_name[p.name].bullets]
    out = [b for p in tailored for b in p.bullets]
    preservation = _preservation(src, out)
    # The prompt lets the model leave out numbers from parts of a story the job does not need, so
    # a low overall ratio is normal (36-56% in three live runs). The fault worth a review is a
    # project that kept NONE of its numbers: its story may have lost its result.
    for project in tailored:
        source_numbers = _numbers(" ".join(source_by_name[project.name].bullets))
        if source_numbers and not source_numbers & _numbers(" ".join(project.bullets)):
            flags.append(
                f"{project.name}: kept none of its numbers — check the story shows a result"
            )

    return TailoredResume(
        projects=tailored,
        skills=skills,
        summary=profile.summary or "",
        preservation=preservation,
        jd_keywords=sorted(keywords),
        flags=flags,
        used_llm=used_llm,
        needs=needs,
    )


def _preservation(original: list[str], tailored: list[str]) -> float:
    """Fraction of the source's numeric facts still present after rewriting (1.0 if none)."""
    src: set[str] = set()
    for bullet in original:
        src |= _numbers(bullet)
    if not src:
        return 1.0
    out: set[str] = set()
    for bullet in tailored:
        out |= _numbers(bullet)
    return len(src & out) / len(src)
