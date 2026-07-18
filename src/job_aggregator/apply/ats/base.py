"""Per-ATS deterministic form maps (Track D, opt-in).

Known ATS forms (Greenhouse/Lever/Ashby/SmartRecruiters) have a stable field layout, so we can fill
them by CSS selector — the reliable core, no LLM guessing. An unknown host returns None and the
driver falls back to its generic/best-effort path. Selectors are best-effort and must be verified
against live forms; they are pure data, so a wrong one is a one-line fix (unit-tested for shape).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class AtsForm:
    name: str
    # Substrings that identify this ATS in the URL host (any match wins).
    host_markers: tuple[str, ...]
    # logical field -> CSS selector on this ATS's application form.
    selectors: dict[str, str]


def match_ats(url: str, forms: tuple[AtsForm, ...]) -> AtsForm | None:
    """Return the first form whose host_markers appear in the URL host, else None."""
    host = (urlparse(url).netloc or "").lower()
    if not host:
        return None
    for form in forms:
        if any(marker in host for marker in form.host_markers):
            return form
    return None
