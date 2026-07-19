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


# Per-project delimiter for the batched rewrite. The model echoes '### <name>' back so we can
# attribute rewritten bullets to the right project from ONE call — 4x fewer subprocess spawns
# than per-project (each `claude -p` is ~12s), and the model tailors with the WHOLE résumé in
# view (no repeating the same emphasis across projects).
_PROJECT_MARK = "### "
# Strip only true LIST numbering ("- ", "* ", "3. ", "12) ") — digits need the dot/bracket +
# space. A greedy [-*•\d.\s]+ would amputate a bullet that LEADS with a metric ("91 Catch2
# tests…" -> "Catch2 tests…"), silently losing facts.
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•]|\d{1,3}[.)])\s+")


def _strip_bullet(line: str) -> str:
    return _BULLET_PREFIX_RE.sub("", line).strip()


def _rewrite_prompt(selected: list[Project], keywords: set[str]) -> tuple[str, str]:
    """The batched (system, user) prompt: every selected project's bullets under a '### <name>'
    header, asking for the same structure back."""
    system = (
        "You are a résumé editor. Rewrite each project's RESUME BULLETS to emphasize aspects "
        "relevant to the TARGET KEYWORDS for the job. STRICT RULES: (1) Use ONLY facts present in "
        "that project's own bullets — never invent numbers, technologies, companies, or outcomes. "
        "(2) Keep every metric exactly as written. (3) Return the SAME number of bullets for each "
        "project, one per line, no numbering, no commentary. (4) Echo each "
        f"'{_PROJECT_MARK}<name>' header verbatim and in order; put only that project's rewritten "
        "bullets beneath it."
    )
    parts = [f"TARGET KEYWORDS: {', '.join(sorted(keywords)[:40])}", ""]
    for project in selected:
        parts.append(f"{_PROJECT_MARK}{project.name}")
        parts.extend(f"- {b}" for b in project.bullets)
        parts.append("")
    return system, "\n".join(parts)


def _parse_rewrite(raw: str, selected: list[Project]) -> dict[str, list[str]]:
    """Map project name -> rewritten bullet lines from the model output. Lenient: with NO headers
    and a single project, treat every line as that project's bullets (keeps single-project
    behaviour). Unattributable multi-project output yields {} so the caller falls back to source."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(_PROJECT_MARK):
            current = stripped[len(_PROJECT_MARK) :].strip()
            sections[current] = []
        elif current is not None and stripped:
            sections[current].append(_strip_bullet(stripped))
    if not sections and len(selected) == 1:
        lines = [_strip_bullet(ln.strip()) for ln in raw.splitlines() if ln.strip()]
        return {selected[0].name: lines}
    return sections


def _guard_bullets(project: Project, candidates: list[str]) -> tuple[list[str], list[str]]:
    """Anti-fabrication guard for ONE project: keep a rewritten bullet only if it introduces no
    number absent from the source (structural, per arXiv 2605/2607); else fall back to the
    truthful original. Also flags a rewrite that DROPS a metric. Returns (bullets, flags)."""
    source_numbers: set[str] = set()
    for bullet in project.bullets:
        source_numbers |= _numbers(bullet)
    kept: list[str] = []
    flags: list[str] = []
    for i, original in enumerate(project.bullets):
        candidate = candidates[i] if i < len(candidates) else ""
        if candidate and _numbers(candidate) <= source_numbers:
            kept.append(candidate)
            lost = _numbers(original) - _numbers(candidate)
            if lost:
                flags.append(
                    f"{project.name}: rewrite dropped metric(s) {sorted(lost)} — review bullet "
                    f"{i + 1}"
                )
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
    """Produce a truthful, JD-tailored résumé view. `backend=None` -> pure selection (no LLM).
    With a backend, ONE call rewrites all selected projects' bullets (batched), each guarded by
    the anti-fabrication check — a backend failure or an unattributable reply degrades to the
    untouched originals, never a crash or a fabricated fact."""
    cfg = config or ResumeConfig()
    keywords = jd_keywords(job_description)
    selected = select_projects(profile, keywords, cfg.max_projects)
    skills = reorder_skills(profile.skills, keywords)

    flags: list[str] = []
    sections: dict[str, list[str]] = {}
    used_llm = False
    if backend is not None and selected:
        system, user = _rewrite_prompt(selected, keywords)
        try:
            raw = backend.complete(system, user, temperature=cfg.temperature)
            sections = _parse_rewrite(raw, selected)
            if not sections:
                flags.append("tailoring: model output could not be attributed — kept originals")
        except AgentError as exc:  # degrade: a backend failure never breaks tailoring
            flags.append(f"tailoring skipped ({exc})")

    tailored: list[Project] = []
    for project in selected:
        candidates = sections.get(project.name)
        if candidates:
            bullets, pflags = _guard_bullets(project, candidates)
            flags.extend(pflags)
            used_llm = True
        else:
            bullets = list(project.bullets)
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
