"""LLM grounding: map the applicant's data onto detected form fields (Track D).

The Set-of-Marks idea from the Form Controller Agent: the code detects the fields (label, type,
current value) and the LLM only decides WHICH applicant value belongs in WHICH empty field. It maps
provided values — it never invents a name, email, or fact (anti-fabrication, same spirit as the
résumé guard). Returns a `{field_index: value}` plan the driver types. Pure except the backend call,
so it's unit-tested with a fake backend (no browser).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from job_aggregator.apply.backends import AgentBackend
    from job_aggregator.apply.detector import Field
    from job_aggregator.apply.driver import ApplicationFields

_SYSTEM = (
    "You fill job-application forms. You are given the applicant's data and a numbered list of the "
    "form's EMPTY text fields. Return ONLY a JSON object mapping each field index (string) to the "
    "applicant value that belongs there. Use ONLY values from the applicant data — never invent a "
    "name, email, phone, or any fact. Omit a field entirely if no applicant value fits. "
    'Example: {"0": "Ada Lovelace", "2": "ada@example.com"}'
)


def _prompt(fields: list[Field], app: ApplicationFields) -> str:
    empty = "\n".join(f.describe() for f in fields if f.is_text and not f.filled)
    return f"APPLICANT DATA:\n{json.dumps(app.text_map(), indent=2)}\n\nEMPTY FIELDS:\n{empty}"


def plan_fills(
    fields: list[Field], app: ApplicationFields, backend: AgentBackend
) -> dict[int, str]:
    """Ask the backend which applicant value belongs in each empty text field.

    Returns `{}` on an empty form, a backend error, or an unparseable reply (the driver then leaves
    those fields for the human) — a fill agent must never hard-fail or fabricate.
    """
    valid = {f.index for f in fields if f.is_text and not f.filled}
    if not valid:
        return {}
    try:
        raw = backend.complete(_SYSTEM, _prompt(fields, app))
    except Exception:  # any backend failure degrades to "fill nothing" (never fabricate or crash)
        return {}
    return _parse_plan(raw, valid)


def _parse_plan(raw: str, valid: set[int]) -> dict[int, str]:
    """Extract a `{int: non-empty-str}` mapping from the model's reply, tolerating fences/prose.
    Only indices in `valid` and string values survive (defends against a hallucinated field)."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        obj = json.loads(raw[start : end + 1])
    except ValueError:
        return {}
    if not isinstance(obj, dict):
        return {}
    plan: dict[int, str] = {}
    for key, value in obj.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if idx in valid and isinstance(value, str) and value.strip():
            plan[idx] = value
    return plan
