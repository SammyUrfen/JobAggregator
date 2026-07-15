"""Shared httpx client + resilient JSON GET/POST (Phase 3)."""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_TIMEOUT = 20.0  # seconds; sources are I/O bound, keep generous but bounded
MAX_RETRIES = 3         # retry on 429/5xx with exponential backoff
USER_AGENT = "job-aggregator/0.1 (+personal use)"


def make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.Client:
    """Build a configured sync httpx.Client (timeout, UA header). Phase 3."""
    raise NotImplementedError("Phase 3: configured httpx client")


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict | None = None,
    method: str = "GET",
    json_body: dict | None = None,
    max_retries: int = MAX_RETRIES,
) -> Any:
    """GET/POST JSON with retry/backoff on 429 + 5xx. Raises SourceError on final failure
    (the caller adapter converts it into a failed SourceResult). Phase 3."""
    raise NotImplementedError("Phase 3: JSON fetch with retry/backoff")
