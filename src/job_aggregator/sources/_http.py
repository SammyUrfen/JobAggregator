"""Shared HTTP transport policy (Phase 3): one browser-UA client + a manual retry/backoff loop.

httpx's transport-level `retries=` only retries connection errors — never status codes — so 429
and 5xx handling is done here explicitly (honoring a numeric Retry-After, capped). `sleep` is
injectable so tests never actually wait. Adapters catch the SourceError this raises and convert
it into a failed SourceResult (fetch never raises).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from job_aggregator.errors import SourceError

# Several sources (RemoteOK, Himalayas, Unstop) 403 without a real browser UA.
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 JobAggregator/0.1 (+self-hosted)"
)
DEFAULT_TIMEOUT_S = 20.0  # sources are I/O bound; generous but bounded
DEFAULT_CONNECT_S = 10.0
DEFAULT_MAX_RETRIES = 3
BASE_BACKOFF_S = 0.5  # exponential base: 0.5, 1.0, 2.0, ...
MAX_RETRY_AFTER_S = 30.0  # never obey an absurd Retry-After
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def make_client(*, timeout: float | None = None, user_agent: str | None = None) -> httpx.Client:
    """Build a configured sync httpx.Client (browser UA, JSON Accept, redirects, timeouts).

    `user_agent` overrides the default browser UA for a single source — Himalayas' Cloudflare
    currently 403-challenges the realistic Chrome UA ("Just a moment...") but passes a bare UA.
    """
    t = httpx.Timeout(timeout or DEFAULT_TIMEOUT_S, connect=DEFAULT_CONNECT_S)
    return httpx.Client(
        timeout=t,
        follow_redirects=True,
        headers={"User-Agent": user_agent or BROWSER_UA, "Accept": "application/json"},
    )


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    """Numeric Retry-After (capped), else exponential backoff for this attempt."""
    ra = resp.headers.get("Retry-After")
    if ra and ra.strip().isdigit():
        return min(float(ra), MAX_RETRY_AFTER_S)
    return float(BASE_BACKOFF_S * (2**attempt))


def _request_with_retry(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """The shared retry/backoff loop behind get_json/get_text: retries connect/read errors and
    429/5xx, returns the first 2xx response, raises SourceError otherwise (a terminal 404 —
    e.g. an invalid ATS slug — is NOT retried)."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = client.request(method, url, params=params, json=json_body)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            last_exc = exc
            if attempt == max_retries:
                raise SourceError(
                    f"network error for {url}", details={"url": url, "error": str(exc)}
                ) from exc
            sleep(BASE_BACKOFF_S * (2**attempt))
            continue
        status = resp.status_code
        if status in RETRYABLE_STATUS:
            if attempt == max_retries:
                raise SourceError(
                    f"HTTP {status} (retries exhausted) for {url}",
                    details={"url": url, "status": status},
                )
            sleep(_retry_delay(resp, attempt))
            continue
        if 200 <= status < 300:
            return resp
        raise SourceError(f"HTTP {status} for {url}", details={"url": url, "status": status})
    raise SourceError(f"request failed for {url}", details={"url": url}) from last_exc


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Fetch + JSON-decode with retry on connect/read errors and 429/5xx.

    Raises SourceError on give-up, a non-retryable status (400/401/403/404), or a JSON decode
    failure. A terminal 404 (e.g. an invalid ATS slug) is NOT retried.
    """
    resp = _request_with_retry(
        client,
        url,
        params=params,
        method=method,
        json_body=json_body,
        max_retries=max_retries,
        sleep=sleep,
    )
    try:
        return resp.json()
    except ValueError as exc:
        raise SourceError(
            f"invalid JSON from {url}", details={"url": url, "error": str(exc)}
        ) from exc


def get_text(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """get_json's twin for HTML endpoints (Internshala listing pages): identical retry/backoff
    policy, returns the response body text instead of JSON-decoding it."""
    return _request_with_retry(
        client, url, params=params, max_retries=max_retries, sleep=sleep
    ).text


def paginate_until_empty(
    fetch_page: Callable[[int], list[Any]],
    *,
    max_pages: int,
    page_size: int | None = None,
    start_page: int = 1,
) -> tuple[list[Any], bool]:
    """Accumulate items page-by-page until the API signals completion or a safety cap.

    Stops on: an EMPTY page (the API's "no more postings" signal), a SHORT final page
    (< page_size, when page_size is known), or `max_pages` — whichever comes first. An unbounded
    "loop until empty" is a footgun (rate limits, runaway runs), so max_pages is a hard valve.

    Returns (items, exhausted). `exhausted` is True only when the walk ended on the API's own
    completion signal (empty/short page) — i.e. we saw the source's COMPLETE view. False means
    the max_pages cap or a later-page error truncated it: absence from such a WINDOWED fetch is
    NOT evidence a posting died, so stale-deletion must not treat it as one (the guard that used
    to silently delete live jobs beyond page N).

    Failure policy: a SourceError on the FIRST page propagates (the source reports failed); a
    SourceError on a LATER page stops pagination but KEEPS the pages already fetched — a mid-run
    rate-limit shouldn't discard good data (Reliability). `fetch_page(page)` returns the page's
    list of raw items (empty list = no results).
    """
    items: list[Any] = []
    for offset in range(max_pages):
        try:
            batch = fetch_page(start_page + offset)
        except SourceError:
            if offset == 0:
                raise  # first page failed -> the source genuinely failed
            return items, False  # keep earlier pages; view is truncated
        if not batch:
            return items, True
        items.extend(batch)
        if page_size is not None and len(batch) < page_size:
            return items, True
    return items, False  # stopped on the cap -> we did NOT see the full view
