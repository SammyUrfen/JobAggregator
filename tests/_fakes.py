"""Shared fakes for Phase 5+ runner/stale tests (imported, not collected).

`make_job` builds Jobs crafted to pass the permissive `sample_config`. `FakeSource`/`RaisingSource`
stand in for real sources; `RecordingNotifier` is duck-typed (it does NOT import notify.base, so
Phase 5 never depends forward on Phase 7).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_aggregator.sources.base import Source, SourceResult

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config
    from job_aggregator.models.job import Job

_DEFAULT_TITLE = "Backend Engineer Intern"


def make_job(
    uid: str,
    *,
    source: str = "greenhouse",
    title: str = _DEFAULT_TITLE,
    company: str = "Acme Labs",
    location: str | None = "Bengaluru, India",
    is_remote: bool = True,
    url: str | None = None,
    **over: object,
) -> Job:
    """A normalized Job with an explicit uid. Same uid across two jobs => dedup collapse; for
    genuinely distinct jobs use different uids AND dissimilar title+company."""
    from job_aggregator.models.job import Job

    data: dict[str, object] = {
        "job_uid": uid,
        "source": source,
        "title": title,
        "company": company,
        "location": location,
        "is_remote": is_remote,
        "url": url or f"https://example.com/jobs/{uid}",
    }
    data.update(over)
    return Job.model_validate(data)


class FakeSource(Source):
    """A Source that returns a canned SourceResult (no network)."""

    def __init__(
        self,
        name: str,
        jobs: list[Job],
        *,
        succeeded: bool = True,
        error: str | None = None,
        duration_ms: int = 1,
        sub_results: list[tuple[str, bool, int]] | None = None,
    ) -> None:
        self._name = name
        self._jobs = jobs
        self._succeeded = succeeded
        self._error = error
        self._duration_ms = duration_ms
        self._sub_results = sub_results or []

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        return SourceResult(
            source=self._name,
            succeeded=self._succeeded,
            jobs=list(self._jobs),
            n_fetched=len(self._jobs),
            duration_ms=self._duration_ms,
            error=self._error,
            sub_results=list(self._sub_results),
        )


class RaisingSource(Source):
    """A Source whose fetch() raises — exercises the runner's belt-and-suspenders guard."""

    def __init__(self, name: str = "boom") -> None:
        self._name = name

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._name

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        raise RuntimeError("boom")


class RecordingNotifier:
    """Duck-typed notifier: records the uids delivered on each notify_new call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def notify_new(self, jobs: list[Job], cfg: Config) -> None:
        self.calls.append([job.job_uid for job in jobs])
