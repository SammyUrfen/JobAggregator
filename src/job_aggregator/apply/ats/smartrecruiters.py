"""SmartRecruiters application-form selectors (best-effort; verify against a live posting)."""

from __future__ import annotations

from job_aggregator.apply.ats.base import AtsForm

SMARTRECRUITERS = AtsForm(
    name="smartrecruiters",
    host_markers=("smartrecruiters.com", "jobs.smartrecruiters.com"),
    selectors={
        "first_name": "input[name*='first' i]",
        "last_name": "input[name*='last' i]",
        "email": "input[type='email']",
        "phone": "input[type='tel']",
        "resume": "input[type='file']",
    },
)
