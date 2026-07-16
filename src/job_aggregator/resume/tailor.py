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


def _rewrite_project(
    project: Project, keywords: set[str], backend: AgentBackend, temperature: float
) -> tuple[list[str], list[str]]:
    """Ask the backend to re-emphasize a project's bullets, then enforce merge-exclusion.

    Returns (bullets, flags). Any rewritten bullet that adds a number not in the source is dropped
    for the truthful original (structural anti-fabrication, per arXiv 2605/2607)."""
    system = (
        "You are a résumé editor. Rewrite the RESUME BULLETS to emphasize aspects relevant to the "
        "TARGET KEYWORDS. STRICT RULES: (1) Use ONLY facts present in the given bullets — never "
        "invent numbers, technologies, companies, or outcomes. (2) Keep every metric exactly as "
        "written. (3) Return the SAME number of bullets, one per line, no numbering, no commentary."
    )
    user = f"TARGET KEYWORDS: {', '.join(sorted(keywords)[:40])}\n\nRESUME BULLETS:\n" + "\n".join(
        f"- {b}" for b in project.bullets
    )
    raw = backend.complete(system, user, temperature=temperature)
    lines = [re.sub(r"^[-*•\d.\s]+", "", ln).strip() for ln in raw.splitlines() if ln.strip()]

    source_numbers: set[str] = set()
    for bullet in project.bullets:
        source_numbers |= _numbers(bullet)

    kept: list[str] = []
    flags: list[str] = []
    for i, original in enumerate(project.bullets):
        candidate = lines[i] if i < len(lines) else ""
        if candidate and _numbers(candidate) <= source_numbers:
            kept.append(candidate)
        else:
            kept.append(original)  # fall back to the truthful original
            if candidate:
                flags.append(
                    f"{project.name}: rejected a rewrite that introduced unsupported facts"
                )
    return kept, flags


def tailor_resume(
    profile: Profile,
    job_description: str,
    *,
    backend: AgentBackend | None = None,
    config: ResumeConfig | None = None,
) -> TailoredResume:
    """Produce a truthful, JD-tailored résumé view. `backend=None` -> pure selection (no LLM)."""
    cfg = config or ResumeConfig()
    keywords = jd_keywords(job_description)
    selected = select_projects(profile, keywords, cfg.max_projects)
    skills = reorder_skills(profile.skills, keywords)

    flags: list[str] = []
    tailored: list[Project] = []
    for project in selected:
        bullets = list(project.bullets)
        if backend is not None:
            try:
                bullets, pflags = _rewrite_project(project, keywords, backend, cfg.temperature)
                flags.extend(pflags)
            except AgentError as exc:  # degrade: never let a backend failure break tailoring
                flags.append(f"{project.name}: tailoring skipped ({exc})")
        tailored.append(project.model_copy(update={"bullets": bullets}))

    src = [b for p in selected for b in p.bullets]
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
