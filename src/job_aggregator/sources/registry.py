"""Build the list of enabled Source instances from config (Phase 3; JobSpy added Phase 4).

Secrets (Adzuna/Jooble keys) come from the environment, never the config row. A source that is
enabled but missing its key is skipped with a warning rather than failing the whole build.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from job_aggregator.sources.adzuna import AdzunaSource
from job_aggregator.sources.ats_ashby import AshbySource
from job_aggregator.sources.ats_greenhouse import GreenhouseSource
from job_aggregator.sources.ats_lever import LeverSource
from job_aggregator.sources.ats_smartrecruiters import SmartRecruitersSource
from job_aggregator.sources.himalayas import HimalayasSource
from job_aggregator.sources.jobicy import JobicySource
from job_aggregator.sources.jooble import JoobleSource
from job_aggregator.sources.remoteok import RemoteOkSource
from job_aggregator.sources.unstop import UnstopSource

if TYPE_CHECKING:
    from job_aggregator.config.schema import AtsConfig, Config
    from job_aggregator.sources.base import Source

log = logging.getLogger(__name__)

# Fallback Jooble query when no roles are configured.
_DEFAULT_JOOBLE_QUERY = "backend intern"


def _build_ats(ats: AtsConfig) -> list[Source]:
    """Tier-C ATS sources: enabled AND with a non-empty company list (no global search)."""
    out: list[Source] = []
    if ats.greenhouse.enabled and ats.greenhouse.tokens:
        out.append(GreenhouseSource(tokens=ats.greenhouse.tokens))
    if ats.lever.enabled and ats.lever.slugs:
        out.append(LeverSource(slugs=ats.lever.slugs))
    if ats.ashby.enabled and ats.ashby.orgs:
        out.append(AshbySource(orgs=ats.ashby.orgs))
    if ats.smartrecruiters.enabled and ats.smartrecruiters.company_ids:
        out.append(SmartRecruitersSource(company_ids=ats.smartrecruiters.company_ids))
    return out


def build_enabled_sources(cfg: Config) -> list[Source]:
    """Instantiate every enabled source per cfg.sources (Tier A/JobSpy is added in Phase 4)."""
    s = cfg.sources
    out: list[Source] = []

    if s.remoteok.enabled:
        out.append(RemoteOkSource())
    if s.himalayas.enabled:
        out.append(HimalayasSource(country=s.himalayas.country))
    if s.jobicy.enabled:
        out.append(JobicySource(job_type=s.jobicy.job_type))
    if s.adzuna.enabled:
        app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
        if app_id and app_key:
            out.append(AdzunaSource(country=s.adzuna.country, app_id=app_id, app_key=app_key))
        else:
            log.warning("adzuna enabled but ADZUNA_APP_ID/ADZUNA_APP_KEY unset; skipping")
    if s.jooble.enabled:
        key = os.environ.get("JOOBLE_API_KEY")
        if key:
            keywords = cfg.keywords.roles[0] if cfg.keywords.roles else _DEFAULT_JOOBLE_QUERY
            location = cfg.locations[0] if cfg.locations else ""
            out.append(JoobleSource(api_key=key, keywords=keywords, location=location))
        else:
            log.warning("jooble enabled but JOOBLE_API_KEY unset; skipping")
    if s.unstop.enabled:
        out.append(
            UnstopSource(
                opportunities=s.unstop.opportunities,
                search_terms=s.unstop.search_terms,
                max_age_days=s.unstop.max_age_days,
            )
        )

    out.extend(_build_ats(s.ats))
    return out
