"""Greenhouse application-form selectors (best-effort; verify against a live board)."""

from __future__ import annotations

from job_aggregator.apply.ats.base import AtsForm

GREENHOUSE = AtsForm(
    name="greenhouse",
    host_markers=("greenhouse.io", "boards.greenhouse.io", "job-boards.greenhouse.io"),
    selectors={
        "first_name": "#first_name",
        "last_name": "#last_name",
        "email": "#email",
        "phone": "#phone",
        "linkedin": "input[name*='linkedin' i]",
        "resume": "input[type='file'][id*='resume' i], input[type='file']",
    },
)
