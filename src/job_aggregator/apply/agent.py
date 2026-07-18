"""Apply orchestrator (Track D, opt-in).

Per-job, human-triggered: tailor a résumé PDF, build the applicant field map, pick the deterministic
ATS selectors (or fall back to the driver's generic path), load any saved session, and drive the
browser to FILL the form and STOP. The human reviews + submits. NOTHING is auto-submitted:
`apply.auto_submit` is refused outright, and the driver never clicks Submit.

Only `driver.fill_form` touches a browser; everything here is pure orchestration, tested with a
`FakeDriver` + `backend=None` (pure, deterministic résumé selection).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from job_aggregator.apply.ats import detect_ats
from job_aggregator.apply.driver import ApplicationFields
from job_aggregator.apply.session import load_state, save_state
from job_aggregator.errors import AgentError
from job_aggregator.paths import resumes_dir
from job_aggregator.resume.render import compile_pdf, render_latex
from job_aggregator.resume.tailor import tailor_resume

if TYPE_CHECKING:
    from job_aggregator.apply.backends import AgentBackend
    from job_aggregator.apply.driver import BrowserDriver
    from job_aggregator.config.schema import Config
    from job_aggregator.models.job import Job
    from job_aggregator.profile.schema import Profile


@dataclass
class ApplyResult:
    job_uid: str
    url: str
    ats: str | None  # detected ATS name, or None (generic fallback path)
    resume_pdf: str
    filled: list[str]
    unfilled: list[str]
    needs_login: bool
    submitted: bool  # ALWAYS False by contract
    preservation: float
    flags: list[str]


def _split_name(full: str) -> tuple[str, str]:
    parts = full.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _link_for(profile: Profile, *labels: str) -> str | None:
    """The first profile link whose label matches (case-insensitive) any of `labels`."""
    for link in profile.contact.links:
        if link.label.strip().lower() in labels:
            return link.url
    return None


def build_fields(profile: Profile, resume_pdf: str) -> ApplicationFields:
    """Assemble the applicant field map from the profile + the tailored résumé PDF path."""
    c = profile.contact
    first, last = _split_name(c.name)
    return ApplicationFields(
        full_name=c.name,
        first_name=first,
        last_name=last,
        email=c.email,
        resume_path=resume_pdf,
        phone=c.phone,
        location=c.location,
        linkedin=_link_for(profile, "linkedin"),
        github=_link_for(profile, "github"),
    )


def _domain(url: str) -> str:
    return (urlparse(url).netloc or "").lower()


def apply_to_job(
    job: Job,
    profile: Profile,
    cfg: Config,
    *,
    driver: BrowserDriver,
    backend: AgentBackend | None = None,
) -> ApplyResult:
    """Fill (never submit) one job's application. Raises AgentError if auto_submit/disabled."""
    if cfg.apply.auto_submit:  # enforced, not just defaulted: NG1 — never blind auto-submit
        raise AgentError(
            "apply.auto_submit is not supported — you always review and submit yourself",
            details={"auto_submit": True},
        )
    if not cfg.apply.enabled:
        raise AgentError("apply.enabled is false — the browser apply agent is opt-in")

    # 1. tailor + render a résumé PDF for THIS job (backend=None -> pure selection, no LLM/network)
    jd = f"{job.title}\n{job.description or ''}"
    tailored = tailor_resume(profile, jd, backend=backend, config=cfg.resume)
    pdf = resumes_dir() / f"{job.job_uid}.pdf"
    compile_pdf(render_latex(profile, tailored), pdf)  # RenderError propagates when no LaTeX engine

    # 2. field map + deterministic ATS selectors (None -> the driver uses its generic path)
    fields = build_fields(profile, str(pdf))
    ats = detect_ats(job.url)
    selectors = ats.selectors if ats else None

    # 3. saved (encrypted) session for this domain; None -> the driver prompts a fresh login
    domain = _domain(job.url)
    state = load_state(domain)

    # 4. fill headful, STOP before submit
    result = driver.fill_form(
        job.url, fields, selectors=selectors, storage_state=state, headful=True
    )

    # 5. persist a freshly-created session so the next apply to this domain skips the login
    if result.new_state is not None:
        save_state(domain, result.new_state)

    return ApplyResult(
        job_uid=job.job_uid,
        url=job.url,
        ats=ats.name if ats else None,
        resume_pdf=str(pdf),
        filled=result.filled,
        unfilled=result.unfilled,
        needs_login=result.needs_login,
        submitted=False,
        preservation=tailored.preservation,
        flags=tailored.flags,
    )
