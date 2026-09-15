"""Adzuna jobs API (Phase 3). Keys injected from env by the registry; INR for country=in."""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

from job_aggregator.errors import SourceError
from job_aggregator.pipeline import signals
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

log = logging.getLogger(__name__)

_RESULTS_PER_PAGE = 50
_PAGES_PER_QUERY = 2  # bound requests: Adzuna is queried once per role phrase (see _role_queries).
_MAX_ROLE_QUERIES = 6
_ADZUNA_CATEGORY = (
    "it-jobs"  # constrain to software/IT at the source (drops accounting/PR/teaching)
)
# Dedicated internship query: title_only=intern (server-side stemmed — also matches
# "internship") beats what-phrase intern queries, which match descriptions and return 1800+
# noisy rows. Verified live 2026-07-18: 690 IT internships, 266 within 35 days.
_INTERN_TITLE_ONLY = "intern"
# Without a recency cap the intern query surfaces years-old posts (saw created=2022-09-01).
_INTERN_MAX_DAYS_OLD = 35
_INTERN_PAGES = 6  # ~266 fresh IT internships ≈ 6 pages of 50
# Adzuna localizes pay to the country's currency; map the ISO country to it.
_ADZUNA_CCY = {"in": "INR", "gb": "GBP", "us": "USD", "au": "AUD", "ca": "CAD", "de": "EUR"}
# Adzuna sends a monthly INR stipend in the same fields as an annual salary (live 2026-09-15: an
# intern-to-hire with a lone salary_min=15000 would read as 1,250/month and fail a 12k floor).
# A lone figure below this is a monthly stipend. The known ceiling: a stipend above it that Adzuna
# sends this way still reads as annual (0 in the audit).
_MONTHLY_PAY_CEILING = 50_000
# Hashtag Web3 reposts show "India" as the Adzuna location. Their real one is only in this
# preview line: of 75 such rows on 2026-09-15, 1 was in Bangalore and 17 in hybrid New York.
_SETUP_LOCATION_RE = re.compile(r"Location & Setup:\s*(.+?)\s*Overview:")
# The same template adds this sentence to every repost. Its "infrastructure" is a must_have
# anchor, so audit and treasury interns passed the role gate. The lazy `.{0,80}?` (not `[^.]*`)
# lets a company name hold a dot, e.g. "Crypto.com".
_WEB3_BOILERPLATE_RE = re.compile(r"Contribute directly to .{0,80}?blockchain infrastructure\.\s*")


def _role_queries(cfg: Config) -> list[str]:
    """Adzuna `what` phrases, one per configured role. A per-role AND phrase ("backend engineer")
    returns genuinely on-topic tech jobs, whereas a broad word-OR of the same roles returns generic
    noise (accounting/PR/teaching) that merely contains a common word like "engineer" — verified
    live. Falls back to a generic software query when no roles are configured."""
    all_roles = [r.strip() for r in cfg.keywords.roles if r.strip()]
    queries = all_roles[:_MAX_ROLE_QUERIES]
    if len(all_roles) > _MAX_ROLE_QUERIES:
        # The textarea implies every role matters; say which ones this source will not query.
        log.warning(
            "adzuna queries only the first %d roles; not queried: %s",
            _MAX_ROLE_QUERIES,
            ", ".join(all_roles[_MAX_ROLE_QUERIES:]),
        )
    return queries or ["software engineer"]


class AdzunaSource(Source):
    name = "adzuna"

    def __init__(self, country: str, app_id: str, app_key: str, max_pages: int = 10) -> None:
        self.country = country
        self.app_id = app_id
        self.app_key = app_key
        self.max_pages = max_pages

    def fetch(self, cfg: Config, clock: Clock) -> SourceResult:
        start = time.perf_counter()
        base = f"https://api.adzuna.com/v1/api/jobs/{self.country}/search"
        role_pages = min(self.max_pages, _PAGES_PER_QUERY)
        seen: set[str] = set()
        items: list[Any] = []
        exhausted = True
        # (extra query params, pages) per walk: role phrases first, then the dedicated
        # internship query (title-targeted + recency-capped — see _INTERN_* constants).
        walks: list[tuple[dict[str, Any], int]] = [
            ({"what": what}, role_pages) for what in _role_queries(cfg)
        ]
        walks.append(
            (
                {"title_only": _INTERN_TITLE_ONLY, "max_days_old": _INTERN_MAX_DAYS_OLD},
                min(self.max_pages, _INTERN_PAGES),
            )
        )
        with make_client() as client:
            for extra, pages in walks:

                def fetch_page(page: int, extra: dict[str, Any] = extra) -> list[Any]:
                    params: dict[str, Any] = {
                        "app_id": self.app_id,
                        "app_key": self.app_key,
                        "results_per_page": _RESULTS_PER_PAGE,
                        "content-type": "application/json",
                        "sort_by": "date",
                        "category": _ADZUNA_CATEGORY,
                        **extra,
                    }
                    data = get_json(client, f"{base}/{page}", params=params)
                    results = data.get("results") if isinstance(data, dict) else None
                    return results if isinstance(results, list) else []

                try:
                    page_items, walk_done = paginate_until_empty(
                        fetch_page, max_pages=pages, page_size=_RESULTS_PER_PAGE
                    )
                except SourceError as exc:
                    # First-query first-page failure is systemic (bad key) -> fail; a later query
                    # failing keeps what earlier queries returned.
                    if not items:
                        return SourceResult.failed(
                            self.name, str(exc), duration_ms=elapsed_ms(start)
                        )
                    exhausted = False
                    continue
                exhausted = exhausted and walk_done
                for it in page_items:
                    key = str(it.get("id") or "")
                    if key and key in seen:
                        continue
                    if key:
                        seen.add(key)
                    items.append(it)
        return build_result(
            self.name, items, self._map, duration_ms=elapsed_ms(start), exhaustive=exhausted
        )

    def _map(self, item: Any) -> RawPosting:
        company = (item.get("company") or {}).get("display_name", "")
        title = str(item.get("title", ""))
        api_location = (item.get("location") or {}).get("display_name")
        location = api_location
        description = item.get("description")
        remote: bool | None = None
        setup = None
        if description:
            description = _WEB3_BOILERPLATE_RE.sub("", description)
            setup = _SETUP_LOCATION_RE.search(description)
        if setup:
            location = setup.group(1)
            # Remote only when the value names no place: "Remote - Canada" and "LATAM - Remote"
            # limit where the hire may live, so they go through the location gate.
            if not signals.place_words(location):
                remote = True
            elif signals.remote_flag(signals.work_mode(location)) is False:
                remote = False  # "Hybrid - New York, NY"
        else:
            remote = signals.remote_flag(signals.work_mode(f"{title} {description or ''}"))
        salary_min = pos_int_or_none(item.get("salary_min"))
        salary_max = pos_int_or_none(item.get("salary_max"))
        currency = _ADZUNA_CCY.get(self.country.lower())
        top = max(salary_min or 0, salary_max or 0)
        # Only a LONE figure can be a monthly stipend sent in the salary fields. A range under the
        # ceiling ("36000-48000" for a "Stipend: 3-5k" posting) is still annual pay.
        lone_monthly = salary_max is None and currency == "INR" and top < _MONTHLY_PAY_CEILING
        period = "month" if lone_monthly else "year"
        stated = None if top else signals.stated_monthly_pay_inr(description)
        if stated is not None:
            # Set directly: pos_int_or_none would turn an unpaid 0 into unknown pay. The stated
            # figure is the top of its range, so it only fills the max (0-0 means unpaid).
            salary_min = 0 if stated == 0 else None
            salary_max, currency, period = stated, "INR", "month"
        return RawPosting(
            source="adzuna",
            source_native_id=str(item.get("id")),
            title=title,
            company=str(company),
            url=str(item.get("redirect_url", "")),
            location=location,
            uid_location=api_location or "",  # the setup line must not re-key stored rows
            is_remote=remote,
            description=description,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=currency,
            salary_period=period,
            posted_at=parse_iso(item.get("created")),
        )
