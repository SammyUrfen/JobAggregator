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
from job_aggregator.sources.internshala import InternshalaSource
from job_aggregator.sources.jobicy import JobicySource
from job_aggregator.sources.jobspy_source import JobSpySource
from job_aggregator.sources.jooble import JoobleSource
from job_aggregator.sources.remoteok import RemoteOkSource
from job_aggregator.sources.unstop import UnstopSource

if TYPE_CHECKING:
    from job_aggregator.config.schema import AtsConfig, Config
    from job_aggregator.sources.base import Source

log = logging.getLogger(__name__)

# Fallback Jooble query when no roles are configured.
_DEFAULT_JOOBLE_QUERY = "backend intern"
# How many role terms to query Jooble with (one query each; keeps the request count bounded).
_JOOBLE_QUERY_TERMS = 6
# How many keywords.intern_queries ride along on Jooble (low internship share; keep it cheap).
_JOOBLE_INTERN_TERMS = 2


def _country_location(cfg: Config) -> str:
    """Jooble matches a country ('India') but returns 0 for a city ('Bengaluru'); pick a
    country-level location from config, else '' (nationwide)."""
    if any("india" in loc.lower() for loc in cfg.locations):
        return "India"
    return ""


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


def build_enabled_sources(cfg: Config) -> list[Source]:  # noqa: PLR0912 - a linear registry; one branch per source is the point
    """Instantiate every enabled source per cfg.sources in deterministic Tier-A-first order."""
    s = cfg.sources
    out: list[Source] = []

    # Tier A first so its (Naukri/LinkedIn) URLs win first-seen provenance, and Tier A precedes
    # Tier C for the runner's stable input order.
    if s.jobspy.enabled:
        out.append(JobSpySource())
    if s.remoteok.enabled:
        out.append(RemoteOkSource())
    if s.himalayas.enabled:
        out.append(HimalayasSource(country=s.himalayas.country))
    if s.jobicy.enabled:
        out.append(JobicySource(job_type=s.jobicy.job_type))
    if s.adzuna.enabled:
        app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
        if app_id and app_key:
            out.append(
                AdzunaSource(
                    country=s.adzuna.country,
                    app_id=app_id,
                    app_key=app_key,
                    max_pages=s.adzuna.max_pages,
                )
            )
        else:
            log.warning("adzuna enabled but ADZUNA_APP_ID/ADZUNA_APP_KEY unset; skipping")
    if s.jooble.enabled:
        key = os.environ.get("JOOBLE_API_KEY")
        if key:
            # One query per role term (a comma-joined dump returns 0), and a COUNTRY location — a
            # city like "Bengaluru" returns 0 from Jooble; "India"/nationwide works (verified live).
            # intern_queries ride along; Jooble's internship share is ~2% (measured), so this is
            # a cheap complement, not the internship lever (Unstop/Internshala/jobspy are).
            roles = cfg.keywords.roles[:_JOOBLE_QUERY_TERMS]
            if len(cfg.keywords.roles) > _JOOBLE_QUERY_TERMS:
                log.warning(
                    "jooble queries only the first %d roles; not queried: %s",
                    _JOOBLE_QUERY_TERMS,
                    ", ".join(cfg.keywords.roles[_JOOBLE_QUERY_TERMS:]),
                )
            queries = (roles + cfg.keywords.intern_queries[:_JOOBLE_INTERN_TERMS]) or [
                _DEFAULT_JOOBLE_QUERY
            ]
            out.append(
                JoobleSource(
                    api_key=key,
                    queries=queries,
                    location=_country_location(cfg),
                    max_pages=s.jooble.max_pages,
                )
            )
        else:
            log.warning("jooble enabled but JOOBLE_API_KEY unset; skipping")
    if s.unstop.enabled:
        out.append(
            UnstopSource(
                opportunities=s.unstop.opportunities,
                search_terms=s.unstop.search_terms,
                max_age_days=s.unstop.max_age_days,
                max_pages=s.unstop.max_pages,
            )
        )
    if s.internshala.enabled and s.internshala.slugs:
        out.append(InternshalaSource(slugs=s.internshala.slugs, max_pages=s.internshala.max_pages))

    out.extend(_build_ats(s.ats))
    return out
