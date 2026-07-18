"""Ashby application-form selectors (best-effort; Ashby is a dynamic React form — verify live)."""

from __future__ import annotations

from job_aggregator.apply.ats.base import AtsForm

ASHBY = AtsForm(
    name="ashby",
    host_markers=("ashbyhq.com", "jobs.ashbyhq.com"),
    selectors={
        "full_name": "input[name*='name' i]",
        "email": "input[type='email']",
        "phone": "input[type='tel']",
        "linkedin": "input[name*='linkedin' i]",
        "resume": "input[type='file']",
    },
)
