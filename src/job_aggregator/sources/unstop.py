"""Unstop public JSON source (Phase 3).

India internships/jobs. Loops the configured opportunity kinds and filters on `updated_at` recency
(the API surfaces stale 2022 posts otherwise).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from job_aggregator.errors import SourceError
from job_aggregator.pipeline.signals import remote_flag, work_mode
from job_aggregator.sources._http import get_json, make_client
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
# Unstop pay_in tokens -> our normalized period. The live API sends "monthly" and "annually"
# (54 + 48 of 102 items, audit 2026-09-15). Only the short keys were mapped, so salary_period
# stayed None on every row and no pay floor ever ran. The short keys stay for older payloads.
_UNSTOP_PERIOD = {
    "month": "month",
    "monthly": "month",
    "year": "year",
    "annually": "year",
    "week": "week",
    "hour": "hour",
}
# jobDetail.type -> Job.is_remote. WHY not `region`: region is "online" on 102 of 102 items (it
# describes the application, not the work), so every row used to be stored as remote.
_UNSTOP_REMOTE = {"wfh": True, "in_office": False, "hybrid": False}
# Unstop is an India platform: every one of the 102 audited location entries says India. Used
# only when a location entry leaves the country out, so the location gate still matches.
_DEFAULT_COUNTRY = "India"
# public_url is a scheme-less relative path (e.g. 'internships/<slug>-<id>'); seo_url is absolute.
_UNSTOP_BASE = "https://unstop.com"


def _names(value: Any) -> list[str]:
    """Names from a list of {name: ...} dicts or strings. A lone dict or string counts as a list
    of one. WHY: workfunction arrives as a LIST of dicts, and the old dict-only read put a raw
    Python repr ("Function: [{'id': 2004, ...") into the description."""
    entries = value if isinstance(value, list) else [value]
    names = [str(e.get("name", "") if isinstance(e, dict) else e or "").strip() for e in entries]
    return [n for n in names if n]


def _description(item: Any) -> str | None:
    """Assemble a description from the search-result payload: the `details` HTML JD plus the
    structured skill/function names. WHY: with no description at all, the must_have stack-anchor
    gate ran on the title alone and false-dropped real tech internships ("Data Engineer
    Internship" died as no_relevant_skill). All fields are optional and shape-tolerant."""
    parts: list[str] = []
    details = item.get("details")
    if isinstance(details, str) and details.strip():
        parts.append(details.strip())
    if skills := _names(item.get("required_skills")):
        parts.append("Skills: " + ", ".join(skills))
    if functions := _names(item.get("workfunction")):
        parts.append("Function: " + ", ".join(functions))
    return "\n".join(parts) or None


def _location(item: Any) -> str | None:
    """The stated cities plus their country ("Bangalore, Mumbai, India"), "India" for a pan-India
    posting, else None.

    WHY never a bare city: the location gate matches whole tokens against the configured list
    ("Bengaluru, India", "India", ...). A bare "Pune" matches nothing, so the row would drop as
    location_mismatch (21 extra drops in the audit replay).
    """
    entries = [e for e in item.get("locations") or [] if isinstance(e, dict)]
    cities = list(dict.fromkeys(str(e["city"]).strip() for e in entries if e.get("city")))
    if cities:
        countries = dict.fromkeys(str(e.get("country") or _DEFAULT_COUNTRY) for e in entries)
        return ", ".join([*cities, *countries])
    regn = item.get("regnRequirements") or {}
    if isinstance(regn, dict) and regn.get("work_location_type") == "pan_india":
        return _DEFAULT_COUNTRY
    return None


def _is_remote(item: Any, detail: dict[str, Any]) -> bool | None:
    """Remote only when Unstop says so: jobDetail.type first, else what the JD text states."""
    mode = detail.get("type")
    if mode in _UNSTOP_REMOTE:
        return _UNSTOP_REMOTE[mode]
    details = item.get("details")
    return remote_flag(work_mode(details if isinstance(details, str) else None))


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

            def fetch_page(page: int, opp: str, term: str | None) -> tuple[list[Any], bool]:
                """One search page: (items, is_last_page)."""
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
                body = data.get("data") if isinstance(data, dict) else None
                body = body if isinstance(body, dict) else {}
                inner = body.get("data")
                items = inner if isinstance(inner, list) else []
                last, current = body.get("last_page"), body.get("current_page", page)
                if isinstance(last, int) and isinstance(current, int):
                    return items, current >= last
                # No page count in the JSON (a shape change): fall back to the short-page stop.
                return items, len(items) < _PER_PAGE

            # One paginated walk per (opportunity x term); no terms configured = the raw feed.
            terms: list[str | None] = [*self.search_terms] if self.search_terms else [None]
            for opp in self.opportunities:
                for term in terms:
                    try:
                        # partial binds THIS opp/term (avoids the late-binding closure trap).
                        items, walk_done = self._walk(partial(fetch_page, opp=opp, term=term))
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

    def _walk(self, fetch_page: Callable[[int], tuple[list[Any], bool]]) -> tuple[list[Any], bool]:
        """Walk pages until the JSON's own current_page reaches last_page, or max_pages.

        Returns (items, exhausted) with the same contract as `_http.paginate_until_empty`. WHY a
        separate loop: that helper stops on a SHORT page, and Unstop pages come back short while
        more pages exist (oppstatus=open filters after paging). Audit 2026-09-15: internships x
        software page 1 held 29 of 30 items with last_page=2. The walk stopped and reported
        exhausted=True, and page 2 was never read. A short or even empty page does not end this
        walk. Only the page count does.

        A SourceError on page 1 propagates (the walk failed). On a later page it keeps the pages
        already read and reports not exhausted.
        """
        items: list[Any] = []
        for page in range(1, self.max_pages + 1):
            try:
                batch, last = fetch_page(page)
            except SourceError:
                if page == 1:
                    raise
                return items, False
            items.extend(batch)
            if last:
                return items, True
        return items, False  # stopped on the cap: we did NOT see the full view

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
        posting = RawPosting(
            source="unstop",
            source_native_id=str(item.get("id")),
            title=str(item.get("title", "")),
            company=str(org.get("name") or item.get("organisation_name") or ""),
            url=_opportunity_url(item),
            location=_location(item),
            uid_location="",  # the location this adapter hashed before it read cities
            is_remote=_is_remote(item, detail),
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
        # The posting's own "unpaid" flag -> the UNPAID contract (0 INR/month), so the stipend
        # floor drops it. WHY set directly: pos_int_or_none maps 0 to None ("not stated"), and
        # unpaid posts carry show_salary=1 with null amounts, so they used to store as unknown.
        # "paid" with no amount stays unknown: silence is never read as unpaid.
        if detail.get("paid_unpaid") == "unpaid":
            posting.salary_min = posting.salary_max = 0
            posting.salary_currency, posting.salary_period = "INR", "month"
        return posting
