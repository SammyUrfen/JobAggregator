"""Shared normalization helpers (Phase 2, pure). See PLAN §4.

`clean_text` and `parse_date` are used everywhere a source hands us messy strings/dates.
`build_job` is a convenience constructor (clean fields, canonical URL, content-hash uid,
salary → INR/month, bucket) — it is NOT on the hot path (Tier-B/C use base.to_job, Tier A
builds Job directly), so correctness + clarity outweigh speed here.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from job_aggregator.models.job import Job
from job_aggregator.pipeline.dedup import canonical_url, content_hash
from job_aggregator.pipeline.salary import convert_bounds, salary_bucket

if TYPE_CHECKING:
    from job_aggregator.config.schema import Config

# Values at/above this magnitude are epoch milliseconds; below, epoch seconds.
_EPOCH_MS_THRESHOLD = 1e11
# Zero-width characters that NFKC does not strip (ZWSP, ZWNJ, ZWJ, BOM).
_ZERO_WIDTH = ("\u200b", "‌", "‍", "﻿")
_DIGITS = re.compile(r"-?\d+")


def clean_text(value: str | None) -> str | None:
    """NFKC-normalize, strip zero-width chars, collapse whitespace, trim. None/empty -> None."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", value)
    for zw in _ZERO_WIDTH:
        text = text.replace(zw, "")
    text = " ".join(text.split())  # NFKC folds nbsp -> space, so this collapses everything
    return text or None


def _from_epoch(value: float) -> datetime | None:
    """Epoch seconds or milliseconds (auto-detected) -> aware UTC datetime, or None if absurd."""
    seconds = value / 1000.0 if abs(value) >= _EPOCH_MS_THRESHOLD else value
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_str(value: str) -> datetime | None:
    """Parse a string date: digit-string epoch, or ISO-8601 (trailing Z allowed). Else None."""
    s = value.strip()
    if not s:
        return None
    if _DIGITS.fullmatch(s):
        return _from_epoch(float(s))
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_date(value: object) -> datetime | None:
    """Best-effort date coercion to aware UTC. datetime/date passthrough; epoch s/ms;
    ISO-8601 (incl. trailing Z); digit-string epochs; else None. Never raises."""
    # bool is an int subclass — never treat True/False as an epoch.
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    if isinstance(value, str):
        return _parse_str(value)
    return None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def build_job(cfg: Config, **fields: object) -> Job:
    """Assemble a normalized Job from loose keyword fields.

    Cleans text, canonicalizes the URL, computes the content-hash uid, converts salary to
    INR/month, and sets `salary_bucket`. `status` and persistence columns are storage's job.
    """
    company = clean_text(_as_str(fields.get("company"))) or ""
    title = clean_text(_as_str(fields.get("title"))) or ""
    location = clean_text(_as_str(fields.get("location")))
    currency = _as_str(fields.get("salary_currency"))
    period = _as_str(fields.get("salary_period"))
    s_min, s_max, parsed = convert_bounds(
        _as_int(fields.get("salary_min")),
        _as_int(fields.get("salary_max")),
        currency,
        period,
        cfg.salary.fx_rates,
    )
    data: dict[str, object] = {
        "job_uid": content_hash(company, title, location),
        "source": _as_str(fields.get("source")) or "unknown",
        "source_native_id": _as_str(fields.get("source_native_id")),
        "title": title,
        "company": company,
        "location": location,
        "is_remote": _as_bool(fields.get("is_remote")),
        "url": canonical_url(_as_str(fields.get("url")) or ""),
        "description": clean_text(_as_str(fields.get("description"))),
        "salary_min": s_min,
        "salary_max": s_max,
        "salary_currency": currency.upper() if currency else None,
        "salary_period": "month" if parsed else period,
        "salary_raw": _as_str(fields.get("salary_raw")),
        "salary_parsed": parsed,
        "posted_at": parse_date(fields.get("posted_at")),
        "match_score": _as_float(fields.get("match_score")),
    }
    job = Job.model_validate(data)
    job.salary_bucket = salary_bucket(job, cfg)
    return job
