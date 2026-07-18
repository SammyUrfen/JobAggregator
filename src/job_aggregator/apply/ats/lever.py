"""Lever application-form selectors (best-effort; verify against a live posting)."""

from __future__ import annotations

from job_aggregator.apply.ats.base import AtsForm

LEVER = AtsForm(
    name="lever",
    host_markers=("lever.co", "jobs.lever.co"),
    selectors={
        "full_name": "input[name='name']",
        "email": "input[name='email']",
        "phone": "input[name='phone']",
        "linkedin": "input[name='urls[LinkedIn]']",
        "github": "input[name='urls[GitHub]']",
        "resume": "input[name='resume'], input[type='file']",
    },
)
