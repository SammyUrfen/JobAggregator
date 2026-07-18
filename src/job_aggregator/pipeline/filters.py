"""Keyword scoring + hard filters (Phase 2, pure). See PLAN §4.4.

`score_and_filter` is PURE — no clock, no DB. It reads `job.salary_bucket` (the runner sets a
uniform bucket on every Job before filtering). Hard drops short-circuit with a single reason;
kept jobs get an additive score that drives the dashboard's default sort.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from job_aggregator.models.job import Job, SalaryBucket
from job_aggregator.pipeline.dedup import norm_location

if TYPE_CHECKING:
    from job_aggregator.config.schema import Config

# Score weights — named + commented so every term in the sum is explainable (his ethos).
ROLE_TITLE_WEIGHT = 10  # a target role in the TITLE is the strongest signal
ROLE_DESC_WEIGHT = 3  # the same role only in the description is weaker
BONUS_WEIGHT = 4  # each matched bonus skill (Go, Rust, PyTorch, ...)
REMOTE_BONUS = 5  # remote role when remote is preferred
SALARY_PASS_BONUS = 6  # salary parsed and clears the floor
IN_OFFICE_UNKNOWN_SALARY_PENALTY = 5  # demote in-office roles with unknown pay
ROLE_MATCH_CAP = 3  # diminishing returns past a few role hits
BONUS_MATCH_CAP = 5
# The user is an undergrad hunting internships: an internship outranks any same-relevance
# full-time role. 25 > the max title-role contribution (30 is possible but rare), so internships
# reliably float to the top of the default score sort without drowning true relevance signals.
INTERNSHIP_BONUS = 25

# Word-boundary internship markers. Title-only on purpose: descriptions mention "our internship
# program" on senior posts too. "International"/"internal" do NOT match (boundary check).
_INTERN_RE = re.compile(
    r"(?<![a-z0-9])(?:intern|interns|internship|internships|trainee|apprentice|apprenticeship)"
    r"(?![a-z0-9])"
)

# Years-of-experience demands, both phrase orders ("3+ years of experience" / "experience of
# 3 years"), requiring the word experience within the same clause (40 chars, no sentence break)
# so bare durations ("2 year contract") don't match. The captured number is a range's LOWER
# bound — the entry bar ("3-5 years" demands 3). Detection is token + proximity (not one big
# lazy regex): a single lazy window would swallow a later, larger demand in the same clause
# ("2+ years with Python and 6+ years of software experience" must report 6, not 2).
_YEARS_TOKEN = re.compile(
    r"(\d{1,2})\s*(?:\+\s*)?(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*(?:years?|yrs?)"  # noqa: RUF001 - JDs really use the en dash in ranges
)
_EXP_NEAR = re.compile(r"[^.\n]{0,40}?(?:experience|exp(?![a-z]))")
_YEARS_AFTER = re.compile(
    r"(?:experience|exp(?![a-z]))[^.\n]{0,40}?(\d{1,2})\s*\+?\s*(?:years?|yrs?)"
)


@dataclass(frozen=True)
class FilterVerdict:
    keep: bool
    score: float
    reasons: list[str] = field(default_factory=list)


def detect_internship(title: str) -> bool:
    """True when the TITLE names an internship/trainee/apprentice role. The runner stamps this
    on every Job before bucketing/scoring; the v2 DB migration backfills old rows with it."""
    return _INTERN_RE.search(title.lower()) is not None


def required_years(text_lc: str) -> int | None:
    """The highest years-of-experience demand found in already-lowercased text, or None.

    MAX across matches: a post that anywhere demands 5+ years is out of reach even if another
    clause says 2+. Trade-off (documented on keywords.max_experience_years): a company blurb
    ("10+ years serving clients") can false-match; we accept rare false drops over a feed that
    was 52% out-of-level roles.
    """
    hits = [
        int(m.group(1))
        for m in _YEARS_TOKEN.finditer(text_lc)
        if _EXP_NEAR.match(text_lc, m.end())  # "experience" within the same clause after it
    ]
    hits += [int(m.group(1)) for m in _YEARS_AFTER.finditer(text_lc)]
    return max(hits) if hits else None


def _matches(text_lc: str, keyword: str) -> bool:
    """Whole-token (word-boundary) match of an already-lowercased keyword in lowercased text."""
    kw = keyword.strip().lower()
    if not kw:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text_lc) is not None


def _location_ok(job: Job, cfg: Config) -> bool:
    if job.is_remote and cfg.remote_preferred:
        return True
    jl = norm_location(job.location)
    if not jl:  # unknown location -> don't hard-drop
        return True
    job_tokens = set(jl.split())
    for loc in cfg.locations:
        nl = norm_location(loc)
        if not nl or nl == "remote":
            continue
        # Whole-token overlap only — a raw substring match wrongly admits "Indiana, USA" for a
        # configured "India" (norm 'india' is a substring of 'indiana').
        if set(nl.split()) & job_tokens:
            return True
    return False


def _hard_drop_reason(  # noqa: PLR0911 - one early return per gate is the readable shape here
    job: Job, cfg: Config, *, title_lc: str, hay: str, title_roles: list[str], desc_roles: list[str]
) -> str | None:
    """The first disqualifying reason (exclude → level → role → domain → experience → location),
    or None if the job clears every hard gate. Salary is handled separately (it can flag)."""
    kw = cfg.keywords
    for ex in kw.exclude:  # 1. hard excludes (title only)
        if _matches(title_lc, ex):
            return f"excluded:{ex}"
    if kw.require_level and not any(_matches(hay, lv) for lv in kw.level_required):
        return "no_level"  # 2. level
    anchored = bool(kw.must_have) and any(_matches(hay, m) for m in kw.must_have)
    # 3. role — RELAXED for internships: intern titles rarely carry a full role phrase
    # ("Java Developer Internship" matches no configured role), so a stack anchor alone
    # qualifies an internship. Verified live: the strict gate was the top killer of real
    # tech internships (118 of 153 intern-titled unstop posts died here).
    if (not title_roles and not desc_roles) and not (job.is_internship and anchored):
        return "no_role_match"
    # 3b. domain anchor: even with a role-word hit, require at least one must-have skill from the
    # user's actual stack, so a wrong-DOMAIN job (embedded/teaching/music) that merely reuses a
    # generic title word like "Software Engineer" is dropped. Correctness does not
    # depend on enumerating every bad word. Empty must_have disables it (backward compatible).
    if kw.must_have and not anchored:
        return "no_relevant_skill"
    # 3c. experience bar: a description demanding more years than configured is out of level.
    # Internships are exempt (they often say "0-1 years"); 0 disables the gate.
    if not job.is_internship and kw.max_experience_years > 0:
        yrs = required_years(hay)
        if yrs is not None and yrs > kw.max_experience_years:
            return f"experience:{yrs}y"
    if not _location_ok(job, cfg):
        return "location_mismatch"  # 4. location
    return None


def score_and_filter(job: Job, cfg: Config) -> FilterVerdict:
    title_lc = job.title.lower()
    desc_lc = (job.description or "").lower()
    hay = f"{title_lc}\n{desc_lc}"
    kw = cfg.keywords

    title_roles = [r for r in kw.roles if _matches(title_lc, r)]
    desc_roles = [r for r in kw.roles if r not in title_roles and _matches(desc_lc, r)]
    drop = _hard_drop_reason(
        job, cfg, title_lc=title_lc, hay=hay, title_roles=title_roles, desc_roles=desc_roles
    )
    if drop is not None:
        return FilterVerdict(False, 0.0, [drop])

    bucket = job.salary_bucket  # 5. salary gate (reads the runner-set bucket)
    salary_flagged = False
    if bucket is SalaryBucket.FAIL:
        return FilterVerdict(False, 0.0, ["salary_below_floor"])
    if bucket is None or bucket is SalaryBucket.UNKNOWN:
        if cfg.salary.on_missing == "drop":
            return FilterVerdict(False, 0.0, ["salary_missing"])
        salary_flagged = True

    score = ROLE_TITLE_WEIGHT * min(len(title_roles), ROLE_MATCH_CAP)  # 6. score
    score += ROLE_DESC_WEIGHT * min(len(desc_roles), ROLE_MATCH_CAP)
    score += BONUS_WEIGHT * min(sum(1 for b in kw.bonus if _matches(hay, b)), BONUS_MATCH_CAP)
    reasons: list[str] = []
    if job.is_internship:
        score += INTERNSHIP_BONUS
        reasons.append("internship")
    if job.is_remote and cfg.remote_preferred:
        score += REMOTE_BONUS
    if bucket is SalaryBucket.PASS:
        score += SALARY_PASS_BONUS
    if salary_flagged and not job.is_remote and cfg.salary.demote_in_office_if_unknown:
        score -= IN_OFFICE_UNKNOWN_SALARY_PENALTY
        reasons.append("salary_unknown_flagged")
    reasons.extend(f"role_title:{r}" for r in title_roles)
    reasons.extend(f"role_desc:{r}" for r in desc_roles)
    return FilterVerdict(True, float(max(score, 0)), reasons)
