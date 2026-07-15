"""The Source contract — every job source implements this (PLAN §3).

Golden rule: `fetch()` NEVER raises. Any failure (exception, non-2xx, network error, or a
"suspicious empty" result from a normally-populated source) becomes
`SourceResult(succeeded=False, error=...)`. A failed source is one we "couldn't see", so
the stale-delete pass leaves its jobs untouched — this is the correctness crux (PLAN §4.5).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config
    from job_aggregator.models.job import Job


@dataclass
class RawPosting:
    """A raw source record before normalization. Adapters usually normalize inline and may
    not need this, but it's handy for staged pipelines and tests."""

    payload: dict[str, object]


@dataclass
class SourceResult:
    """The outcome of one source fetch within a cycle.

    A single adapter (e.g. JobSpy or an ATS looping over many company tokens) MAY report
    multiple logical sub-sources via `sub_results`; the runner records one `source_runs`
    row per entry so the stale-delete guard is per-sub-source. When `sub_results` is empty,
    the runner records a single row keyed on `source`.
    """

    source: str
    succeeded: bool
    jobs: list[Job] = field(default_factory=list)
    n_fetched: int = 0
    duration_ms: int = 0
    error: str | None = None
    # Optional per-sub-source outcomes: list of (name, succeeded, n_fetched). Used by
    # JobSpy (per-site) and ATS (per-company) so the success guard is fine-grained.
    sub_results: list[tuple[str, bool, int]] = field(default_factory=list)


class Source(ABC):
    """Base class for all sources. `name` is the stable id used in jobs.source and
    source_runs.source (see PLAN §3 for the canonical name list)."""

    #: stable identifier, e.g. "greenhouse", "remoteok", "jobspy" (per-site tagging inside)
    name: ClassVar[str]

    @abstractmethod
    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        """Fetch, normalize, and return this source's jobs. Must not raise."""
        raise NotImplementedError
