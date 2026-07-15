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


@dataclass(frozen=True)
class FilterVerdict:
    keep: bool
    score: float
    reasons: list[str] = field(default_factory=list)


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
    for loc in cfg.locations:
        nl = norm_location(loc)
        if not nl or nl == "remote":
            continue
        if nl in jl or jl in nl or (set(nl.split()) & set(jl.split())):
            return True
    return False


def _hard_drop_reason(
    job: Job, cfg: Config, *, title_lc: str, hay: str, title_roles: list[str], desc_roles: list[str]
) -> str | None:
    """The first disqualifying reason (exclude → level → role → location), or None if the job
    clears every hard gate. Salary is handled separately since it can flag rather than drop."""
    kw = cfg.keywords
    for ex in kw.exclude:  # 1. hard excludes (title only)
        if _matches(title_lc, ex):
            return f"excluded:{ex}"
    if kw.require_level and not any(_matches(hay, lv) for lv in kw.level_required):
        return "no_level"  # 2. level
    if not title_roles and not desc_roles:
        return "no_role_match"  # 3. role
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
