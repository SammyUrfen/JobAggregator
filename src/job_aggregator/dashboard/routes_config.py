"""Config routes (Phase 8): GET /config (form) + PUT /api/config (validate + save).

The dashboard is the source of truth for config. Submitted flat form fields are overlaid onto
the CURRENT config dict (so nested keys the form doesn't expose — fx_rates, jobspy sites/terms,
ATS token lists — are preserved), then `Config.model_validate` is the single validation
authority. Edits take effect on the next run.
"""

from __future__ import annotations

import copy
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, ValidationError

from job_aggregator.config.schema import Config
from job_aggregator.config.store import load_effective_config, save_config
from job_aggregator.dashboard.deps import (
    SchedulerProtocol,
    get_conn,
    get_scheduler,
    get_templates,
    header_context,
)
from job_aggregator.errors import ConfigError
from job_aggregator.profile.store import load_profile_text, save_profile_text

router = APIRouter()

# Sources whose enabled flag the form exposes (top-level toggles). `remotive` is intentionally
# absent: it's a documented dead-end with no Source implementation, so a toggle for it would do
# nothing (the config field stays for the record, but the UI must not imply it works).
_SOURCE_TOGGLES = (
    "jobspy",
    "unstop",
    "internshala",
    "remoteok",
    "himalayas",
    "jobicy",
    "adzuna",
    "jooble",
)


class ConfigForm(BaseModel):
    """Editable subset of Config as flat form fields. `extra="ignore"` tolerates the CSRF-free
    form posting whatever it likes; unknown fields are dropped, absent fields stay unchanged."""

    model_config = ConfigDict(extra="ignore")

    run_hour_local: int | None = None
    hours_old: int | None = None
    grace_days: int | None = None
    catch_up_on_startup: bool | None = None

    salary_min_remote: int | None = None
    salary_min_in_office: int | None = None
    salary_min_internship: int | None = None
    salary_on_missing: str | None = None
    demote_in_office_if_unknown: bool | None = None

    remote_preferred: bool | None = None
    require_level: bool | None = None
    keywords_roles: str | None = None
    keywords_bonus: str | None = None
    keywords_must_have: str | None = None
    keywords_level_required: str | None = None
    keywords_exclude: str | None = None
    keywords_intern_queries: str | None = None
    max_experience_years: int | None = None
    locations: str | None = None

    notify_telegram_enabled: bool | None = None
    notify_email_enabled: bool | None = None
    notify_rss_enabled: bool | None = None
    notify_email_to: str | None = None

    # per-source search terms (internship targeting)
    jobspy_search_terms: str | None = None
    unstop_search_terms: str | None = None

    # source toggles carried as a JSON-ish dict is overkill; each is an explicit field below.
    src_jobspy: bool | None = None
    src_unstop: bool | None = None
    src_internshala: bool | None = None
    src_remoteok: bool | None = None
    src_himalayas: bool | None = None
    src_jobicy: bool | None = None
    src_adzuna: bool | None = None
    src_jooble: bool | None = None
    src_remotive: bool | None = None

    # apply agent (Track D) + the résumé/form-fill LLM backend
    apply_enabled: bool | None = None
    apply_engine: str | None = None  # "agentic" (Claude drives the browser) | "deterministic"
    apply_use_browser_cookies: bool | None = None
    resume_backend: str | None = None  # "coding_agent" (Claude Code, no key) | "openai_compatible"
    resume_tailor_with_llm: bool | None = None  # rewrite bullets with the LLM vs deterministic
    resume_base_url: str | None = None  # openai_compatible endpoint
    resume_model: str | None = None


def _split(value: str) -> list[str]:
    """Split a textarea (newline- or comma-separated) into a trimmed, non-empty list."""
    parts = value.replace(",", "\n").split("\n")
    return [p.strip() for p in parts if p.strip()]


def _overlay(target: dict[str, Any], fields: tuple[tuple[object, str], ...]) -> None:
    """Set each (value, key) on `target` when value is not None."""
    for value, key in fields:
        if value is not None:
            target[key] = value


def _apply_keywords(merged: dict[str, Any], f: ConfigForm) -> None:
    kw = merged["keywords"]
    if f.remote_preferred is not None:
        merged["remote_preferred"] = f.remote_preferred
    if f.require_level is not None:
        kw["require_level"] = f.require_level
    if f.keywords_roles is not None:
        kw["roles"] = _split(f.keywords_roles)
    if f.keywords_bonus is not None:
        kw["bonus"] = _split(f.keywords_bonus)
    if f.keywords_must_have is not None:
        kw["must_have"] = _split(f.keywords_must_have)
    if f.keywords_level_required is not None:
        kw["level_required"] = _split(f.keywords_level_required)
    if f.keywords_exclude is not None:
        kw["exclude"] = _split(f.keywords_exclude)
    if f.keywords_intern_queries is not None:
        kw["intern_queries"] = _split(f.keywords_intern_queries)
    if f.max_experience_years is not None:
        kw["max_experience_years"] = f.max_experience_years
    if f.locations is not None:
        merged["locations"] = _split(f.locations)


def _apply_notify_sources(merged: dict[str, Any], f: ConfigForm) -> None:
    notify, sources = merged["notify"], merged["sources"]
    # Per-source search terms (the internship-targeting knobs): jobspy's per-site scrape terms and
    # unstop's API searchTerm list — previously only editable via YAML/DB.
    if f.jobspy_search_terms is not None:
        sources["jobspy"]["search_terms"] = _split(f.jobspy_search_terms)
    if f.unstop_search_terms is not None:
        sources["unstop"]["search_terms"] = _split(f.unstop_search_terms)
    _overlay(notify["telegram"], ((f.notify_telegram_enabled, "enabled"),))
    _overlay(notify["email"], ((f.notify_email_enabled, "enabled"), (f.notify_email_to, "to")))
    _overlay(notify["rss"], ((f.notify_rss_enabled, "enabled"),))
    for name in _SOURCE_TOGGLES:
        value = getattr(f, f"src_{name}")
        if value is not None:
            sources[name]["enabled"] = value


def _apply_form(current: dict[str, Any], f: ConfigForm) -> dict[str, Any]:
    """Overlay the editable fields onto a deep copy of the current config dict."""
    merged = copy.deepcopy(current)
    _overlay(
        merged["schedule"],
        (
            (f.run_hour_local, "run_hour_local"),
            (f.hours_old, "hours_old"),
            (f.grace_days, "grace_days"),
            (f.catch_up_on_startup, "catch_up_on_startup"),
        ),
    )
    _overlay(
        merged["salary"],
        (
            (f.salary_min_remote, "min_remote"),
            (f.salary_min_in_office, "min_in_office"),
            (f.salary_min_internship, "min_internship"),
            (f.salary_on_missing, "on_missing"),
            (f.demote_in_office_if_unknown, "demote_in_office_if_unknown"),
        ),
    )
    _apply_keywords(merged, f)
    _apply_notify_sources(merged, f)
    _overlay(
        merged["apply"],
        (
            (f.apply_enabled, "enabled"),
            (f.apply_engine, "engine"),
            (f.apply_use_browser_cookies, "use_browser_cookies"),
        ),
    )
    _overlay(
        merged["resume"],
        (
            (f.resume_backend, "backend"),
            (f.resume_tailor_with_llm, "tailor_with_llm"),
            (f.resume_base_url, "base_url"),
            (f.resume_model, "model"),
        ),
    )
    return merged


def _dotted(loc: tuple[Any, ...]) -> str:
    return ".".join(str(p) for p in loc)


@router.get("/config", response_class=HTMLResponse)
def config_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    scheduler: SchedulerProtocol = Depends(get_scheduler),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    cfg = load_effective_config(conn)
    context: dict[str, Any] = {
        **header_context(conn, scheduler),
        "config": cfg.model_dump(mode="json"),
        "source_toggles": _SOURCE_TOGGLES,
    }
    return templates.TemplateResponse(request, "config.html", context)


@router.put("/api/config")
def put_config(
    form: Annotated[ConfigForm, Form()],
    conn: sqlite3.Connection = Depends(get_conn),
    scheduler: SchedulerProtocol = Depends(get_scheduler),
) -> dict[str, Any]:
    current = load_effective_config(conn).model_dump(mode="json")
    prev_run_hour = current["schedule"]["run_hour_local"]
    merged = _apply_form(current, form)
    try:
        cfg = Config.model_validate(merged)
    except ValidationError as exc:
        details: dict[str, object] = {
            "errors": [{"field": _dotted(e["loc"]), "message": e["msg"]} for e in exc.errors()]
        }
        raise ConfigError("config is invalid", details=details) from exc
    save_config(conn, cfg)
    # run_hour is the one config knob the running scheduler already committed to (its cron was
    # registered at boot). Push the change through NOW so it doesn't silently wait for a restart.
    if cfg.schedule.run_hour_local != prev_run_hour:
        scheduler.reschedule_daily(cfg.schedule.run_hour_local)
    return {"ok": True, "message": "Saved. Applies on the next run."}


@router.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    scheduler: SchedulerProtocol = Depends(get_scheduler),
    templates: Jinja2Templates = Depends(get_templates),
) -> HTMLResponse:
    """GET /profile — edit the ground-truth profile.yaml (used by tailoring + apply) as raw YAML."""
    ctx = {**header_context(conn, scheduler), "profile_yaml": load_profile_text()}
    return templates.TemplateResponse(request, "profile.html", ctx)


@router.put("/api/profile")
def put_profile(profile_yaml: Annotated[str, Form()]) -> dict[str, Any]:
    """Validate + save the profile YAML. ConfigError (invalid) -> 422 friendly; an invalid profile
    is never written, so a typo can't silently corrupt a tailored résumé."""
    save_profile_text(profile_yaml)
    return {"ok": True, "message": "Profile saved — tailoring and apply use it immediately."}
