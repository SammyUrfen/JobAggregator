"""The normalized Job domain model (PLAN §2.1/§2.2).

A `Job` is what a source adapter produces AFTER normalization but BEFORE it touches the DB.
Persistence-only columns (first_seen_at, last_seen_at, last_seen_cycle, status, and the user
flags applied/bookmarked/hidden/notes) are added by storage.jobs_repo, NOT carried here.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    NEW = "new"  # inserted this cycle
    ACTIVE = "active"  # seen in a recent successful cycle
    STALE = "stale"  # not seen last cycle (soft), within grace window
    DELETED = "deleted"  # missing beyond grace window (soft-hidden, row kept)


class SalaryBucket(StrEnum):
    PASS = "pass"  # parsed AND meets threshold
    UNKNOWN = "unknown"  # not parseable -> KEEP + flag (most Indian internships)
    FAIL = "fail"  # parsed AND below threshold -> dropped before insert


class Job(BaseModel):
    """A normalized posting. `job_uid` is the cross-source dedup hash (see pipeline.dedup)."""

    job_uid: str
    source: str
    source_native_id: str | None = None
    title: str
    company: str
    location: str | None = None
    is_remote: bool | None = None
    url: str
    description: str | None = None

    salary_min: int | None = None  # normalized INR/month
    salary_max: int | None = None
    salary_currency: str | None = None
    salary_period: str | None = None  # 'month' | 'year' | 'hour'
    salary_raw: str | None = None
    salary_parsed: bool = False
    salary_bucket: SalaryBucket | None = None

    posted_at: datetime | None = None
    match_score: float | None = Field(default=None, description="keyword/skill relevance score")
