"""Tier A: python-jobspy wrapper (Phase 4).

One JobSpySource drives jobspy.scrape_jobs across cfg.sources.jobspy.sites and search_terms,
converts the returned pandas DataFrame rows -> normalized Job objects tagged source=
"jobspy_<site>", and reports per-site success via SourceResult.sub_results so the stale-delete
guard is per-site. An empty DataFrame for a normally-populated site is "suspicious" -> that
site's success is False. See PLAN §3 (name rule) and the verified jobspy column mapping.
"""

from __future__ import annotations

from job_aggregator.clock import Clock
from job_aggregator.config.schema import Config
from job_aggregator.sources.base import Source, SourceResult


class JobSpySource(Source):
    name = "jobspy"

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        raise NotImplementedError("Phase 4: scrape_jobs -> per-site Jobs + sub_results")
