"""Facts a posting STATES in its own words: work mode, pay per month, internship duration.

Pure functions over the posting text. Each one answers only when the text says so, and returns
None when it is silent. Silence is never read as a no: a posting that states no stipend can be a
good one, while a posting that says "unpaid" is not.

Adapters call these to fill what a source does not structure (LinkedIn has no work-mode field,
Adzuna sends pay only in a 500-character preview). The filters call them to drop postings the
owner never wants: unpaid ones, and internships longer than keywords.max_internship_months.

The patterns come from a live audit on 2026-09-15: 1,371 captured postings plus page checks of
the originals (LinkedIn, Internshala, Unstop, Adzuna). Each pattern names the false match it was
narrowed to avoid.
"""

from __future__ import annotations

import html
import re
from typing import Literal

WorkMode = Literal["remote", "remote_or_hybrid", "hybrid", "onsite"]

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def flatten(text: str | None) -> str:
    """Lowercased plain text: tags removed, entities decoded, whitespace collapsed."""
    if not text:
        return ""
    return _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text))).strip().lower()


# ── work mode ────────────────────────────────────────────────────────────────────────────────
# A statement that the ROLE is not remote. "Remote diagnostics" and "remote server" are
# technical phrases, so no bare "remote" appears in any pattern. Negations are checked first.
_ONSITE_RE = re.compile(
    r"no remote|not (?:a )?remote|remote (?:work )?(?:is )?not (?:available|possible|allowed)"
    r"|\bon-?site\b(?! interview)|\bin-office\b|\bin office\b(?! hours)|work from (?:the )?office"
    r"|\bwfo\b|work location: in person|workplace type: on-?site"
)
# "Hybrid retrieval" and "hybrid search" are technical, so hybrid must name work, a role or days.
_HYBRID_RE = re.compile(
    r"\bhybrid\b(?! (?:retrieval|search|cloud|model|app|approach|architecture|rag|system))"
)
# Nouns that make "a remote ..." a technical phrase ("via remote desktop", "GIS remote sensing").
_TECH_REMOTE = (
    r"(?:sensing|desktop|debugging|access|sessions?|calls?|servers?|replication|repositor(?:y|ies)"
    r"|control|monitoring|diagnostics|devices?|api|procedure|execution|management)\b"
)
_REMOTE_RE = re.compile(
    r"location\s*[:\-]?\s*(?:[a-z]+\s*)?\(?remote|\(remote\)|fully remote|100% remote|all-remote"
    r"|remote[- ](?:first|role|internship|position|job|opportunity|friendly|only|work)"
    r"|\b(?:is|a|full-time|part-time),? remote\b(?! " + _TECH_REMOTE + r")| - remote\b"
    r"|work[- ]from[- ]home|\bwfh\b"
    r"|remote ?/ ?hybrid|hybrid ?/ ?remote|work remotely|working remotely|, remote\)"
)


def work_mode(text: str | None) -> WorkMode | None:
    """The work mode the text states, or None when it states none or contradicts itself.

    "Hybrid / Remote" counts as remote_or_hybrid: the role offers a remote option. A text that
    says both on-site and remote returns None (measured: interview lines such as "in-office round,
    remote technical" cause most of these).
    """
    t = flatten(text)
    onsite, hybrid, remote = (
        bool(_ONSITE_RE.search(t)),
        bool(_HYBRID_RE.search(t)),
        bool(_REMOTE_RE.search(t)),
    )
    if remote and onsite:
        return None
    if remote:
        return "remote_or_hybrid" if hybrid else "remote"
    if hybrid:
        return "hybrid"
    if onsite:
        return "onsite"
    return None


# Location words that name no place. "Remote - US" and "LATAM - Remote" name one, so they limit
# where the hire may live and must pass the location gate.
PLACELESS_WORDS = frozenset({"remote", "remoto", "worldwide", "anywhere", "global"})
_WORD_RE = re.compile(r"[^\W\d_]+")  # letters of any script: "مسقط" names a place


def place_words(location: str | None) -> set[str]:
    """The words of a location that name a place ("remote - india" -> {"india"})."""
    return set(_WORD_RE.findall(flatten(location))) - PLACELESS_WORDS


def remote_flag(mode: WorkMode | None) -> bool | None:
    """Job.is_remote from a stated mode: True when a remote option exists, False for hybrid or
    on-site, None when unknown (the location gate then decides)."""
    if mode is None:
        return None
    return mode in ("remote", "remote_or_hybrid")


# ── pay ──────────────────────────────────────────────────────────────────────────────────────
# "Unpaid" counts only when it describes the role or its pay. A bare "unpaid" also appears in
# benefits ("unpaid parental leave") and in the work itself ("chase unpaid invoices").
_UNPAID_RE = re.compile(
    # "unpaid internship", "unpaid AI intern", "unpaid 3-month remote internship"
    r"\bunpaid[ ,]+(?:\d+[- ]?(?:week|month)s?[ -]+)?(?:[a-z/&-]+\s+){0,2}"
    r"(?:internships?|interns?|roles?|positions?|opportunit(?:y|ies)|training|program(?:me)?|work)\b"
    # "Stipend: Unpaid", "Internship Type: Unpaid", "internship: 3 months, unpaid"
    r"|(?:stipend|type|compensation|pay|period)\s*[:\-]?\s*(?:is\s+)?unpaid\b"
    r"|(?:months?|weeks?),\s*unpaid\b"
    # a label list: "(Unpaid | NGO)", "on-site | regular | unpaid"
    r"|\(unpaid\b|\bunpaid\)|\|\s*unpaid\b|\bunpaid\s*\|"
    r"|\bno stipend\b|without (?:any )?stipend|not a paid (?:internship|role|position)"
    r"|does not (?:include|offer|provide) (?:a |any )?stipend"
    r"|stipend\s*[:\-]\s*(?:nil|none|not applicable|n/a|zero|0\b(?!\s*(?:-|–|to)\s*\d))"  # noqa: RUF001
)
# "We never offer unpaid internships" is a promise, not the pay of this role.
_NEGATION_BEFORE_RE = re.compile(r"\b(?:never|not|no|don't|do not|won't|will not)\b[^.\n]{0,20}$")
# Thousands may be split by a comma or a space ("9 500" in Adzuna previews), or grouped the Indian
# way ("1,10,000"). The look-behind stops a match from starting inside a figure ("10,000" of it).
_NUM = (
    r"(?<!\d)(?<!\d[,.])"
    r"(\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?|\d{1,3}(?:[, ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(k)?"
)
_CUR = r"(?:₹|rs\.?|inr)\s*"
_MONTH = r"\s*(?:/-)?\s*(?:per month|/\s*month|/\s*mo\b|a month|p\.?m\.?\b|monthly)"
_RANGE_RE = re.compile(_CUR + _NUM + r"\s*(?:-|–|to)\s*(?:" + _CUR + r")?" + _NUM + _MONTH)  # noqa: RUF001 - JDs use the en dash
_SINGLE_RE = re.compile(r"(?:" + _CUR + r")?" + _NUM + _MONTH)  # the currency sign is optional
# Pay must sit right after a pay word. Without the anchor a "₹1,000 learning reimbursement" or
# an "INR 1,150 gym" benefit line read as the stipend (2 false reads in the audit).
# Whole words only: "pay" inside "Razorpay" or "payments" anchored a ₹999/month product price.
_PAY_WORD_RE = re.compile(r"\b(?:stipend|pay|salary|compensation|ctc|what you get)\b[^.\n]{0,40}$")
# A figure after a foreign currency is not INR ("compensation: $250pm" is about ₹20,750).
_FOREIGN_CURRENCY_RE = re.compile(r"(?:\$|€|£|\busd|\beur|\bgbp|\bpkr|\baed|\bsgd)\s*$")
_NOT_PAY_RE = re.compile(r"reimburs|allowance|gym|wellbeing|well-being|incentive")
# Below this a figure is not a monthly stipend ("₹1.00 - ₹2.00 per month" placeholders).
_MIN_REAL_AMOUNT = 100
# A stated amount at or above this beats an "unpaid" line elsewhere ("unpaid for the first
# week, then ₹15,000 per month" is a paid role).
_PAID_OVERRIDE_AMOUNT = 1000


def _amount(number: str, thousands: str | None) -> float:
    value = float(number.replace(",", "").replace(" ", ""))
    return value * 1000 if thousands else value


def _pay_anchored(t: str, start: int) -> bool:
    before = t[max(0, start - 45) : start]
    return bool(_PAY_WORD_RE.search(before)) and not _NOT_PAY_RE.search(before)


def _states_unpaid(t: str) -> bool:
    return any(
        not _NEGATION_BEFORE_RE.search(t[max(0, m.start() - 30) : m.start()])
        for m in _UNPAID_RE.finditer(t)
    )


def says_unpaid(text: str | None) -> bool:
    """True when the text says the role pays nothing and states no real amount elsewhere."""
    t = flatten(text)
    return _states_unpaid(t) and not _stated_amounts(t, _PAID_OVERRIDE_AMOUNT)


def _stated_amounts(t: str, minimum: float) -> list[float]:
    """Every pay-anchored INR monthly figure: range tops and single figures. A single figure
    inside a matched range is that range's own bound, so it is skipped."""
    values: list[float] = []
    covered: list[tuple[int, int]] = []
    for m in _RANGE_RE.finditer(t):
        if _pay_anchored(t, m.start()):
            values.append(max(_amount(m.group(1), m.group(2)), _amount(m.group(3), m.group(4))))
            covered.append(m.span())
    for m in _SINGLE_RE.finditer(t):
        inside = any(a <= m.start() < b for a, b in covered)
        foreign = _FOREIGN_CURRENCY_RE.search(t[max(0, m.start() - 6) : m.start()])
        if not inside and not foreign and _pay_anchored(t, m.start()):
            values.append(_amount(m.group(1), m.group(2)))
    return [v for v in values if v >= minimum]


def stated_monthly_pay_inr(text: str | None) -> int | None:
    """The pay per month in INR that the text states: 0 for unpaid, the top of the highest stated
    range otherwise, None when the text states no pay. Only INR monthly figures count."""
    t = flatten(text)
    amounts = _stated_amounts(t, _MIN_REAL_AMOUNT)
    if _states_unpaid(t) and not [a for a in amounts if a >= _PAID_OVERRIDE_AMOUNT]:
        return 0
    return round(max(amounts)) if amounts else None


# ── internship duration ──────────────────────────────────────────────────────────────────────
_UNIT_MONTHS = {"week": 12 / 52, "month": 1.0, "year": 12.0}
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}  # fmt: skip
_QTY = r"(\d{1,2}(?:\.\d)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
_SPAN = _QTY + r"\s*(?:\+\s*)?(?:(?:-|–|to|or)\s*" + _QTY + r"\s*)?[\s-]*(weeks?|months?|years?)\b"  # noqa: RUF001
_SPAN_LEAD = r"[\s-]+(?:long\s+)?(?:paid\s+|unpaid\s+|remote\s+|full[- ]time\s+|part[- ]time\s+)?"
# Each pattern ties the span to the internship itself. "6 months of experience", "PPO after 6
# months" and "posted 1 month ago" name no duration and match none of them.
_INTERNSHIP_DURATION_RES = (
    # "Duration: 6 months", "duration of 4 months", "internship duration - 3 months"
    re.compile(
        r"\bduration\b\s*(?:of\s*(?:the\s*)?(?:internship\s*)?)?(?:is\s*)?[:\-]?\s*" + _SPAN
    ),
    # "6-month internship", "3 months paid internship"
    re.compile(_SPAN + _SPAN_LEAD + r"(?:internship|apprenticeship)\b"),
    # "internship of 6 months", "internship for a period of 3 months"
    re.compile(
        r"\b(?:internship|apprenticeship)\s+(?:is\s+)?(?:of|for)\s+(?:a\s+(?:period|duration)\s+of\s+)?"
        + _SPAN
    ),
)
# "2 month training program" names a length only when the text states no internship length:
# "Duration: 3 months. PPO hires join a 12-month training program" is a 3-month internship.
_PROGRAM_DURATION_RE = re.compile(_SPAN + _SPAN_LEAD + r"(?:training|program|programme)\b")
# A "duration" that belongs to something else: "probation duration: 6 months".
_OTHER_DURATION_BEFORE_RE = re.compile(r"(?:probation|bond|contract|course|notice)\s*$")
# No internship runs longer than this. Adzuna strips hyphens from some previews, so "3-6 month
# internship" arrives as "36 month internship" (2 real rows), and "a 4-year program" is a degree.
_MAX_PLAUSIBLE_MONTHS = 24


def _to_number(token: str) -> float:
    return float(_WORD_NUMBERS.get(token, token))


def internship_months(text: str | None) -> float | None:
    """The longest internship duration the text states, in months, or None when none is stated.

    A range counts by its lower bound: "3-6 months" asks for a 3-month commitment. Across several
    internship statements the longest one counts: a "2-month training, then a 6-month internship"
    asks for the 6 months that are the trap. A program or training length counts only when no
    internship length is stated, and a span over 24 months is ignored as a misread.
    """
    t = flatten(text)
    months = _spans(t, _INTERNSHIP_DURATION_RES) or _spans(t, (_PROGRAM_DURATION_RE,))
    return round(max(months), 1) if months else None


def _spans(t: str, patterns: tuple[re.Pattern[str], ...]) -> list[float]:
    months: list[float] = []
    for pattern in patterns:
        for m in pattern.finditer(t):
            if _OTHER_DURATION_BEFORE_RE.search(t[max(0, m.start() - 20) : m.start()]):
                continue
            value = _to_number(m.group(1)) * _UNIT_MONTHS[m.group(3).rstrip("s")]
            if value <= _MAX_PLAUSIBLE_MONTHS:
                months.append(value)
    return months
