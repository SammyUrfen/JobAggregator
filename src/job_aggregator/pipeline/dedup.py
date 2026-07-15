"""Dedup primitives (Phase 2). Cross-source identity via a content hash. See PLAN §4.2."""

from __future__ import annotations

# Company suffixes stripped before hashing so "Acme Technologies Pvt Ltd" == "Acme".
COMPANY_SUFFIXES = (
    "inc",
    "inc.",
    "llc",
    "ltd",
    "ltd.",
    "pvt",
    "private limited",
    "llp",
    "corp",
    "corporation",
    "technologies",
    "labs",
    "co",
    "gmbh",
)
# Tracking query params dropped when canonicalizing URLs.
TRACKING_PARAMS = (
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gh_src",
    "ref",
    "source",
    "src",
)
FUZZY_THRESHOLD = 90  # rapidfuzz token_sort_ratio above which two titles are "the same"


def norm_company(s: str) -> str:
    raise NotImplementedError("Phase 2: normalize company (lowercase, strip punct + suffixes)")


def norm_title(s: str) -> str:
    raise NotImplementedError("Phase 2: normalize title")


def norm_location(s: str | None) -> str:
    """Lowercase; map remote synonyms -> 'remote'; else normalized city/country."""
    raise NotImplementedError("Phase 2: normalize location")


def content_hash(company: str, title: str, location: str | None) -> str:
    """sha256 of norm(company)|norm(title)|norm(location) -> the job_uid (PLAN §4.2)."""
    raise NotImplementedError("Phase 2: content hash")


def canonical_url(url: str) -> str:
    """Lowercase host, drop fragment + TRACKING_PARAMS, keep meaningful path/query."""
    raise NotImplementedError("Phase 2: canonical url")


def fuzzy_is_dup(title_a: str, title_b: str, threshold: int = FUZZY_THRESHOLD) -> bool:
    """rapidfuzz.token_sort_ratio(title_a, title_b) >= threshold."""
    raise NotImplementedError("Phase 2: fuzzy dup check")
