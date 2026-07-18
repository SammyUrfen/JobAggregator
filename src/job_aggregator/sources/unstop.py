"""Unstop public JSON source (Phase 3).

India internships/jobs. Loops the configured opportunity kinds and filters on `updated_at` recency
(the API surfaces stale 2022 posts otherwise).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from job_aggregator.errors import SourceError
from job_aggregator.sources._http import get_json, make_client, paginate_until_empty
from job_aggregator.sources.base import (
    RawPosting,
    Source,
    SourceResult,
    build_result,
    elapsed_ms,
    parse_iso,
    pos_int_or_none,
)

if TYPE_CHECKING:
    from job_aggregator.clock import Clock
    from job_aggregator.config.schema import Config

_URL = "https://unstop.com/api/public/opportunity/search-result"
_PER_PAGE = 30
# Unstop currency icon tokens -> ISO currency.
_UNSTOP_CCY = {"fa-rupee": "INR", "fa-inr": "INR", "fa-dollar": "USD", "fa-usd": "USD"}
# Unstop pay_in tokens -> our normalized period.
_UNSTOP_PERIOD = {"month": "month", "year": "year", "week": "week", "hour": "hour"}
# public_url is a scheme-less relative path (e.g. 'internships/<slug>-<id>'); seo_url is absolute.
_UNSTOP_BASE = "https://unstop.com"


def _description(item: Any) -> str | None:
    """Assemble a description from the search-result payload: the `details` HTML JD plus the
    structured skill/function names. WHY: with no description at all, the must_have stack-anchor
    gate ran on the title alone and false-dropped real tech internships ("Data Engineer
    Internship" died as no_relevant_skill). All fields are optional and shape-tolerant."""
    parts: list[str] = []
    details = item.get("details")
    if isinstance(details, str) and details.strip():
        parts.append(details.strip())
    skills = item.get("required_skills")
    if isinstance(skills, list) and skills:
        names = [str(s.get("name", "") if isinstance(s, dict) else s or "").strip() for s in skills]
        names = [n for n in names if n]
        if names:
            parts.append("Skills: " + ", ".join(names))
    wf = item.get("workfunction")
    wf_name = str(wf.get("name", "") if isinstance(wf, dict) else wf or "").strip()
    if wf_name:
        parts.append(f"Function: {wf_name}")
    return "\n".join(parts) or None


def _opportunity_url(item: Any) -> str:
    """Unstop's canonical public link.

    `seo_url` is the full absolute opportunity URL and is preferred. `public_url` is only a
    scheme-less relative path which 404s as a bare href (it resolves against the dashboard host), so
    it must be joined onto the Unstop host. `short_url` is a last resort. WHY: the previous
    code preferred `public_url` and stored the relative path unchanged.
    """
    seo = item.get("seo_url")
    if seo:
        return str(seo)
    rel = item.get("public_url")
    if rel:
        rel = str(rel)
        # Prefix the host only when it really is relative (defensive vs. a future shape change).
        return (
            rel if rel.startswith(("http://", "https://")) else f"{_UNSTOP_BASE}/{rel.lstrip('/')}"
        )
    return str(item.get("short_url") or "")


class UnstopSource(Source):
    name = "unstop"

    def __init__(
        self,
        opportunities: list[str],
        search_terms: list[str],
        max_age_days: int,
        max_pages: int = 5,
    ) -> None:
        self.opportunities = opportunities
        self.search_terms = search_terms
        self.max_age_days = max_age_days
        self.max_pages = max_pages

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        cutoff = clock.now() - timedelta(days=self.max_age_days)
        all_items: list[Any] = []
        errors: list[str] = []
        seen_ids: set[str] = set()
        ok = 0
        exhausted = True
        with make_client() as client:

            def fetch_page(page: int, opp: str, term: str | None) -> list[Any]:
                params: dict[str, Any] = {
                    "opportunity": opp,
                    "per_page": _PER_PAGE,
                    "page": page,
                    # Only opportunities whose APPLICATION WINDOW is open. Unstop keeps closed
                    # posts "LIVE" (status/regn_open both lie); oppstatus=open is server-side
                    # equivalent to regnRequirements.reg_status=="STARTED" (verified live: same
                    # 4 ids either way, ~12x fewer pages). Measured 2026-07-18: only ~1-7% of
                    # unstop internships are actually open — everything else was noise the user
                    # could not apply to.
                    "oppstatus": "open",
                }
                # searchTerm narrows the 10k-item all-domains firehose (travel/HR/sales
                # internships) to on-topic postings — verified live: searchTerm=backend drops
                # the total to ~358 with page 1 all backend internships. Without a term the
                # source is un-targeted and the filter keeps ~2 of 300.
                if term:
                    params["searchTerm"] = term
                data = get_json(client, _URL, params=params)
                inner = ((data.get("data") or {}).get("data")) if isinstance(data, dict) else None
                return inner if isinstance(inner, list) else []

            # One paginated walk per (opportunity x term); no terms configured = the raw feed.
            terms: list[str | None] = [*self.search_terms] if self.search_terms else [None]
            for opp in self.opportunities:
                for term in terms:
                    try:
                        # partial binds THIS opp/term (avoids the late-binding closure trap).
                        items, walk_done = paginate_until_empty(
                            partial(fetch_page, opp=opp, term=term),
                            max_pages=self.max_pages,
                            page_size=_PER_PAGE,
                        )
                    except SourceError as exc:
                        errors.append(f"{opp}/{term or '*'}: {exc}")
                        exhausted = False
                        continue
                    ok += 1
                    exhausted = exhausted and walk_done
                    for item in items:  # the same posting matches several terms — dedupe by id
                        key = str(item.get("id") or "")
                        if key and key in seen_ids:
                            continue
                        if key:
                            seen_ids.add(key)
                        all_items.append(item)
        if ok == 0:
            return SourceResult.failed(
                self.name, f"all opportunities failed: {errors}", duration_ms=elapsed_ms(start)
            )
        return build_result(
            self.name,
            all_items,
            lambda item: self._map(item, cutoff),
            duration_ms=elapsed_ms(start),
            exhaustive=exhausted,
        )

    def _map(self, item: Any, cutoff: datetime) -> RawPosting | None:
        posted = parse_iso(item.get("updated_at")) or parse_iso(item.get("start_date"))
        # Drop stale postings; an unparseable date is kept (can't prove it's old).
        if posted is not None and posted < cutoff:
            return None
        # Defense-in-depth behind oppstatus=open: drop anything whose registration window has
        # ended ("Application Closed" on the site). reg_status is the ONLY truthful field —
        # item.status stays "LIVE" and regn_open stays 1 on closed posts (verified 5/5 against
        # live pages). Fail-open when regnRequirements is absent (can't prove it's closed).
        regn = item.get("regnRequirements") or {}
        if isinstance(regn, dict) and regn.get("reg_status") == "FINISHED":
            return None
        detail = item.get("jobDetail") or {}
        disclosed = detail.get("show_salary") == 1 and not detail.get("not_disclosed")
        org = item.get("organisation") or {}
        region = str(item.get("region") or "").strip()
        return RawPosting(
            source="unstop",
            source_native_id=str(item.get("id")),
            title=str(item.get("title", "")),
            company=str(org.get("name") or item.get("organisation_name") or ""),
            url=_opportunity_url(item),
            location=region if region and region.lower() != "online" else None,
            is_remote=True if region.lower() == "online" else None,
            description=_description(item),
            salary_min=pos_int_or_none(detail.get("min_salary")) if disclosed else None,
            salary_max=pos_int_or_none(detail.get("max_salary")) if disclosed else None,
            salary_currency=_UNSTOP_CCY.get(str(detail.get("currency", "")).lower())
            if disclosed
            else None,
            salary_period=_UNSTOP_PERIOD.get(str(detail.get("pay_in", "")).lower())
            if disclosed
            else None,
            posted_at=posted,
        )
