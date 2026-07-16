"""Phase 2 — pipeline.filters: score_and_filter hard drops + scoring.

Drops on exclude/level/salary-fail/location.

See PLAN.md Part II (Phase 2) for the exact table-driven cases to implement.
"""

from __future__ import annotations

from collections.abc import Callable

from job_aggregator.config.schema import Config
from job_aggregator.models.job import Job, SalaryBucket
from job_aggregator.pipeline.filters import score_and_filter

JobFactory = Callable[..., Job]


def test_hard_drop_exclude_keyword(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(title="Senior Backend Engineer")
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is False
    assert verdict.reasons == ["excluded:senior"]


def test_hard_drop_no_level(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(title="Backend Engineer", description="build APIs")
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is False
    assert verdict.reasons == ["no_level"]


def test_hard_drop_no_role(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(title="Graphic Designer Intern", description="run social campaigns")
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is False
    assert verdict.reasons == ["no_role_match"]


def test_keep_full_score(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(
        title="Backend Engineer Intern",
        is_remote=True,
        description="Go and Kubernetes, distributed systems",
        salary_bucket=SalaryBucket.PASS,
    )
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is True
    # 10 role-title + 3 role-desc(distributed systems) + 8 bonus(Go, Kubernetes) + 5 remote + 6 PASS
    assert verdict.score == 32.0


def test_hard_drop_salary_fail(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(
        title="Backend Engineer Intern", is_remote=False, salary_bucket=SalaryBucket.FAIL
    )
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is False
    assert verdict.reasons[0] == "salary_below_floor"


def test_keep_in_office_unknown_salary_demoted(make_job: JobFactory, cfg: Config) -> None:
    job = make_job(
        title="Backend Engineer Intern",
        is_remote=False,
        description="backend systems platform",
        salary_bucket=SalaryBucket.UNKNOWN,
    )
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is True
    assert verdict.score == 5.0  # 10 role-title - 5 in-office-unknown demote


def test_drop_when_on_missing_is_drop(make_job: JobFactory, cfg: Config) -> None:
    drop_cfg = cfg.model_copy(deep=True)
    drop_cfg.salary.on_missing = "drop"
    job = make_job(
        title="Backend Engineer Intern", is_remote=True, salary_bucket=SalaryBucket.UNKNOWN
    )
    verdict = score_and_filter(job, drop_cfg)
    assert verdict.keep is False
    assert verdict.reasons == ["salary_missing"]


def test_hard_drop_excluded_off_stack_domain(make_job: JobFactory, cfg: Config) -> None:
    # off-stack domain terms seeded into `exclude` (title screen) drop before the role gate
    job = make_job(title="Embedded Software Engineer Intern", description="firmware in C")
    verdict = score_and_filter(job, cfg)
    assert verdict.keep is False
    assert verdict.reasons == ["excluded:embedded"]


def test_hard_drop_no_relevant_skill(make_job: JobFactory, cfg: Config) -> None:
    # A generic role token matches, but no must_have stack term is present -> dropped as off-domain.
    relaxed = cfg.model_copy(deep=True)
    relaxed.keywords.roles = [*relaxed.keywords.roles, "software engineer"]
    job = make_job(title="Software Engineer Intern", description="join our friendly team")
    verdict = score_and_filter(job, relaxed)
    assert verdict.keep is False
    assert verdict.reasons == ["no_relevant_skill"]


def test_keep_with_must_have_skill(make_job: JobFactory, cfg: Config) -> None:
    relaxed = cfg.model_copy(deep=True)
    relaxed.keywords.roles = [*relaxed.keywords.roles, "software engineer"]
    job = make_job(
        title="Software Engineer Intern",
        description="work on our Python backend",  # 'python'/'backend' satisfy must_have
        is_remote=True,
        salary_bucket=SalaryBucket.PASS,
    )
    verdict = score_and_filter(job, relaxed)
    assert verdict.keep is True


def test_empty_must_have_disables_gate(make_job: JobFactory, cfg: Config) -> None:
    relaxed = cfg.model_copy(deep=True)
    relaxed.keywords.roles = [*relaxed.keywords.roles, "software engineer"]
    relaxed.keywords.must_have = []  # empty -> legacy behaviour: a role match alone is enough
    job = make_job(
        title="Software Engineer Intern",
        description="join our friendly team",
        is_remote=True,
        salary_bucket=SalaryBucket.PASS,
    )
    verdict = score_and_filter(job, relaxed)
    assert verdict.keep is True
