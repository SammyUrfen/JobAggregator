"""Dedup primitives (Phase 2). Cross-source identity via a content hash. See PLAN §4.2.

Identity is URL-independent: job_uid = sha256(norm_company · norm_title · norm_location), the
FULL 64-char hex digest (Phase 4 asserts len == 64). The same role on Naukri + LinkedIn + a
Greenhouse board therefore collapses to one row. Inputs are RAW here — every function
normalizes internally, so callers never pre-normalize or truncate.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

# Legal-entity / boilerplate suffixes stripped from a company name before hashing, so
# "Acme Technologies Pvt Ltd" and "Acme" hash identically.
_COMPANY_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "llp",
        "ltd",
        "limited",
        "pvt",
        "private",
        "plc",
        "corp",
        "corporation",
        "co",
        "gmbh",
        "ag",
        "sa",
        "srl",
        "bv",
        "group",
        "holdings",
        "technologies",
        "technology",
        "labs",
        "software",
        "systems",
        "solutions",
        "india",
        "global",
        "worldwide",
    }
)
# City/keyword synonyms folded to a canonical token so remote variants collapse.
_LOCATION_ALIASES = {
    "bangalore": "bengaluru",
    "blr": "bengaluru",
    "anywhere": "remote",
    "worldwide": "remote",
    "wfh": "remote",
    "distributed": "remote",
}
# Query params dropped when canonicalizing a URL (any `utm_*` key is also dropped, below).
_TRACKING_PARAMS = frozenset(
    {
        "gclid",
        "fbclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "referrer",
        "source",
        "src",
        "trk",
        "trackingid",
        "refid",
        "originalsubdomain",
        "position",
        "pagenum",
        "utm_id",
    }
)
# rapidfuzz token_sort_ratio at/above which two titles are treated as the same role.
FUZZY_TITLE_THRESHOLD = 88
# Only these URL schemes are allowed to survive canonicalization (others -> "" to block XSS).
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _ascii_fold(text: str) -> str:
    """Drop accents/diacritics by NFKD-decomposing then discarding non-ASCII bytes."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _tokens(text: str) -> list[str]:
    """Lowercase, ASCII-fold, and split on any run of non-alphanumeric characters."""
    return _NON_ALNUM.sub(" ", _ascii_fold(text).lower()).split()


def norm_company(name: str) -> str:
    toks = _tokens(name)
    stripped = list(toks)
    while stripped and stripped[-1] in _COMPANY_SUFFIXES:
        stripped.pop()
    # All-suffix guard: never collapse a name to empty (e.g. "Systems Ltd" keeps its tokens).
    return " ".join(stripped) if stripped else " ".join(toks)


def norm_title(title: str) -> str:
    return " ".join(_tokens(title))


def norm_location(loc: str | None) -> str:
    if not loc:
        return ""
    return " ".join(_LOCATION_ALIASES.get(t, t) for t in _tokens(loc))


def content_hash(company: str, title: str, location: str | None) -> str:
    """sha256(norm_company | norm_title | norm_location) -> the 64-char job_uid (PLAN §4.2)."""
    key = f"{norm_company(company)}|{norm_title(title)}|{norm_location(location)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def canonical_url(url: str) -> str:
    """Lowercase scheme+host, drop the fragment + tracking params, sort remaining query.

    Path case is preserved (many ATS slugs are case-sensitive); a lone trailing slash on a
    non-root path is trimmed. A bare/relative string with no scheme+host is returned as-is.
    """
    url = (url or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if not parts.scheme and not parts.netloc:
        return url
    # Security: only http(s) may reach an href. Drop javascript:/data:/vbscript: etc. so a
    # poisoned job URL from a third-party board can't become a clickable XSS vector.
    if parts.scheme and parts.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        return ""
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    kept.sort()
    path = parts.path.rstrip("/") if len(parts.path) > 1 else parts.path
    return urlunsplit(
        ((parts.scheme or "https").lower(), parts.netloc.lower(), path, urlencode(kept), "")
    )


def fuzzy_is_dup(title_a: str, title_b: str, *, threshold: int = FUZZY_TITLE_THRESHOLD) -> bool:
    """Near-duplicate title check (rapidfuzz token_sort_ratio). Second-layer only; runtime
    dedup is exact-hash on job_uid (Phase 5)."""
    return bool(fuzz.token_sort_ratio(title_a, title_b) >= threshold)
