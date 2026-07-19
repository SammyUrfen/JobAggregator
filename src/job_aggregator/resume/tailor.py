"""Truthful per-role résumé tailoring (Track C/D).

Pipeline (ResumeFlow-style, but with deterministic anti-fabrication guards from the research):

  1. Extract keywords from the job description (deterministic).
  2. SELECT + RANK the most relevant projects and skill groups by overlap (deterministic — no LLM,
     so selection can never invent anything).
  3. Optionally REWRITE each selected project's bullets via the backend to emphasize JD-relevant
     facts — but every rewrite passes a MERGE-EXCLUSION guard: a rewritten bullet that introduces a
     number not present in the source bullets is REJECTED and the truthful original is kept. This
     makes metric fabrication structurally impossible, not merely discouraged by the prompt.
  4. Score fact PRESERVATION (retained source numbers) and surface flags for the user to review.

If no backend is given, tailoring is pure selection/reordering of the user's own words — zero
fabrication risk and zero LLM cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from job_aggregator.config.schema import ResumeConfig
from job_aggregator.errors import AgentError

if TYPE_CHECKING:
    from job_aggregator.apply.backends import AgentBackend
    from job_aggregator.profile.schema import Profile, Project, SkillGroup

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
_PRESERVATION_FLOOR = 0.80  # below this, warn the user to eyeball the tailored résumé


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2 and t not in _STOPWORDS}


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUMBER_RE.findall(text)}


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


@dataclass
class TailoredResume:
    projects: list[Project]  # selected, ranked, bullets possibly reworded (facts preserved)
    skills: list[SkillGroup]  # reordered to surface JD-relevant groups first
    summary: str
    preservation: float  # 0..1: fraction of source numeric facts still present after rewriting
    jd_keywords: list[str]
    flags: list[str] = field(default_factory=list)  # human-readable warnings to review
    used_llm: bool = False  # True when an LLM actually reworded bullets (vs pure selection)


# Per-project delimiter. The model echoes '### <name>' so we can attribute its chosen+reworded
# bullets back to a real project from ONE call — the LLM both SELECTS which projects to show and
# words them for the job, seeing the WHOLE portfolio + JD at once (deterministic keyword ranking
# is only the fallback).
_PROJECT_MARK = "### "
# Bound the candidate pool sent to the model (prompt size); the real profile has ~15 projects.
_MAX_CANDIDATES = 40
# Strip only true LIST numbering ("- ", "* ", "3. ", "12) ") — digits need the dot/bracket +
# space. A greedy [-*•\d.\s]+ would amputate a bullet that LEADS with a metric ("91 Catch2
# tests…" -> "Catch2 tests…"), silently losing facts.
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•]|\d{1,3}[.)])\s+")


def _strip_bullet(line: str) -> str:
    return _BULLET_PREFIX_RE.sub("", line).strip()


def _select_prompt(
    candidates: list[Project], job_description: str, max_projects: int
) -> tuple[str, str]:
    """The (system, user) prompt: give the model the JD + EVERY candidate project, ask it to pick
    the most relevant `max_projects` and reword their bullets, echoing '### <name>' headers."""
    system = (
        "You are an expert résumé editor tailoring a candidate's résumé to ONE job. From the "
        f"CANDIDATE PROJECTS, SELECT the {max_projects} most relevant to the JOB, then rewrite "
        "each selected project's bullets to emphasize job-relevant aspects. STRICT RULES: "
        "(1) Choose "
        "ONLY from the given projects — never invent a project. (2) Use ONLY facts present in that "
        "project's own bullets — never invent numbers, technologies, companies, or outcomes. "
        "(3) Keep every metric exactly as written. (4) Output at most "
        f"{max_projects} projects, most relevant first, each as a line '{_PROJECT_MARK}<exact "
        "project name>' followed by its rewritten bullets (one per line, no numbering, no "
        "commentary between projects)."
    )
    parts = [f"JOB:\n{job_description.strip()}", "", "CANDIDATE PROJECTS:"]
    for project in candidates:
        parts.append(f"{_PROJECT_MARK}{project.name}")
        if project.tagline:
            parts.append(f"({project.tagline}; tech: {', '.join(project.tech)})")
        parts.extend(f"- {b}" for b in project.bullets)
        parts.append("")
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


def _guard_bullets(project: Project, candidates: list[str]) -> tuple[list[str], list[str]]:
    """Anti-fabrication guard for ONE project: keep each reworded bullet only if it introduces no
    number absent from that project's source bullets (structural, per arXiv 2605/2607); reject the
    rest. Count-agnostic (the model may condense/reorder) but capped at the source bullet count so
    it can't pad. If nothing survives, fall back to the truthful originals. Returns (bullets,
    flags)."""
    source_numbers: set[str] = set()
    for bullet in project.bullets:
        source_numbers |= _numbers(bullet)
    kept: list[str] = []
    flags: list[str] = []
    rejected = False
    for candidate in candidates:
        text = candidate.strip()
        if not text:
            continue
        if _numbers(text) <= source_numbers:
            kept.append(text)
        else:
            rejected = True
    kept = kept[: max(len(project.bullets), 1)]  # never output more bullets than the source
    if rejected:
        flags.append(f"{project.name}: rejected a rewrite that introduced unsupported facts")
    if not kept:
        kept = list(project.bullets)  # nothing usable -> the truthful originals
        flags.append(f"{project.name}: rewrites unusable — kept original bullets")
    return kept, flags


def _candidate_pool(profile: Profile, keywords: set[str]) -> list[Project]:
    """Projects offered to the model for selection: keyword-ranked so the strongest candidates
    lead, capped for prompt size. The model still chooses freely from the whole list."""
    ranked = sorted(profile.projects, key=lambda p: score_project(p, keywords), reverse=True)
    return ranked[:_MAX_CANDIDATES]


def tailor_resume(
    profile: Profile,
    job_description: str,
    *,
    backend: AgentBackend | None = None,
    config: ResumeConfig | None = None,
) -> TailoredResume:
    """Produce a truthful, JD-tailored résumé view.

    With a `backend`, ONE call lets the LLM both SELECT the most relevant projects from the whole
    portfolio AND reword their bullets — every reworded bullet is number-guarded, and an unusable
    reply or a backend failure degrades to deterministic keyword selection (never a crash or a
    fabricated fact). `backend=None` -> pure deterministic selection, bullets verbatim."""
    cfg = config or ResumeConfig()
    keywords = jd_keywords(job_description)
    skills = reorder_skills(profile.skills, keywords)

    flags: list[str] = []
    tailored: list[Project] = []
    used_llm = False

    if backend is not None and profile.projects:
        candidates = _candidate_pool(profile, keywords)
        by_name = {p.name: p for p in candidates}
        system, user = _select_prompt(candidates, job_description, cfg.max_projects)
        try:
            raw = backend.complete(system, user, temperature=cfg.temperature)
            sections = _parse_rewrite(raw, candidates)
        except AgentError as exc:  # degrade: a backend failure never breaks tailoring
            sections = {}
            flags.append(f"tailoring skipped ({exc})")
        for name, cand_bullets in list(sections.items())[: cfg.max_projects]:
            project = by_name.get(name)
            if project is None:
                continue  # a header the model invented / mangled — ignore it
            bullets, gflags = _guard_bullets(project, cand_bullets)
            flags.extend(gflags)
            tailored.append(project.model_copy(update={"bullets": bullets}))
            used_llm = True

    if not tailored:  # no backend, or the LLM produced nothing usable -> keyword ranking
        if backend is not None and not used_llm:
            flags.append("tailoring: LLM selection unusable — used keyword ranking")
        for project in select_projects(profile, keywords, cfg.max_projects):
            tailored.append(project.model_copy(update={"bullets": list(project.bullets)}))

    source_by_name = {p.name: p for p in profile.projects}
    src = [b for p in tailored for b in source_by_name[p.name].bullets]
    out = [b for p in tailored for b in p.bullets]
    preservation = _preservation(src, out)
    if preservation < _PRESERVATION_FLOOR:
        flags.append(f"low fact-preservation ({preservation:.0%}) — review before sending")

    return TailoredResume(
        projects=tailored,
        skills=skills,
        summary=profile.summary or "",
        preservation=preservation,
        jd_keywords=sorted(keywords),
        flags=flags,
        used_llm=used_llm,
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
