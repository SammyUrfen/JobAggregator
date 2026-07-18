"""Pydantic v2 config schema (PLAN §5). Mirrors config/default_config.yaml.

The full Config is persisted as JSON in the single-row `config` table and edited from the
dashboard; the runner loads it at the start of each cycle, so edits apply on the NEXT run.
Secrets are NOT part of this model — they come from env (see .env.example, config/store.py).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Keywords(BaseModel):
    roles: list[str] = Field(default_factory=list)
    bonus: list[str] = Field(default_factory=list)
    # must_have: domain-anchor skills/tech from the user's actual stack. When non-empty, a job must
    # name at least one (title OR description) to count as relevant. This is the guard that stops a
    # generic role word ("Software Engineer" on an embedded/teaching post) from passing on the title
    # alone, no matter how lax `roles` is set. Empty list disables it (legacy behaviour).
    must_have: list[str] = Field(default_factory=list)
    level_required: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    require_level: bool = True
    # intern_queries: extra internship-targeted query phrases sent to the query-driven sources
    # (Adzuna `what`, Jooble keywords) IN ADDITION to the role phrases. Role phrases alone
    # ("backend engineer") structurally return mid/senior postings — verified live: 0 of 148
    # Jooble and 7 of 434 Adzuna results were internships until these were added.
    intern_queries: list[str] = Field(default_factory=list)
    # max_experience_years: drop a (non-internship) job whose DESCRIPTION demands more than this
    # many years of experience (title-only excludes miss "5+ years" buried in the JD — 52% of
    # kept Adzuna jobs demanded >=3y). 0 disables the gate. Trade-off, documented: a company
    # blurb like "10+ years serving clients" can false-match; we accept rare false drops over
    # a feed half-full of out-of-reach roles. Internships are always exempt.
    max_experience_years: int = Field(default=2, ge=0)


class SalaryConfig(BaseModel):
    currency: str = "INR"
    period: str = "month"
    # ge=0: negative floors are nonsense and the dashboard save-path relies on this rejection.
    min_remote: int = Field(default=30000, ge=0)
    min_in_office: int = Field(default=80000, ge=0)
    # Floor for INTERNSHIPS specifically (both remote and in-office). Indian internship stipends
    # sit far below any full-time floor — min_remote=30k dropped real "SDE Intern" posts (verified
    # live). 0 = never drop an internship on pay; scoring still ranks paid ones higher.
    min_internship: int = Field(default=0, ge=0)
    on_missing: Literal["keep_and_flag", "drop"] = "keep_and_flag"
    demote_in_office_if_unknown: bool = True
    # Approximate FX rates to normalize foreign pay to INR/month; user-updatable.
    fx_rates: dict[str, float] = Field(
        default_factory=lambda: {"USD": 83.0, "EUR": 90.0, "GBP": 105.0}
    )


class ScheduleConfig(BaseModel):
    run_hour_local: int = Field(default=3, ge=0, le=23)
    hours_old: int = 48
    grace_days: int = 3
    catch_up_on_startup: bool = True
    # For WINDOWED sources (page caps / results_wanted / hours_old windows — adzuna, jooble,
    # jobspy, capped unstop walks): absence from a fetch is NOT evidence a posting died, so
    # their jobs only go stale once the posting itself is older than this many days (then the
    # normal grace_days delete applies). Exhaustive sources keep pure absence-based expiry.
    windowed_retire_days: int = Field(default=30, ge=1)


class JobSpyConfig(BaseModel):
    enabled: bool = True
    # Naukri and Google are intentionally excluded from the default. Naukri gates its API behind a
    # recaptcha and returns 406 on every headless scrape (NG3 — no Naukri automation); Google jobs
    # caps at ~10 results/query and often warns "initial cursor not found". Both are near-zero yield
    # and pure log-noise here — re-add them only behind a working headful/proxy path (Track D).
    sites: list[str] = Field(default_factory=lambda: ["linkedin", "indeed"])
    search_terms: list[str] = Field(default_factory=list)
    location: str = "Bengaluru, India"
    country_indeed: str = "india"
    # False by default: is_remote=True on LinkedIn strangled intern results (8 calls asking 40
    # each returned 37 rows; the same probe without it filled every slot). Bengaluru internships
    # are mostly on-site/hybrid; `location` anchors geography and remote still arrives untagged.
    is_remote: bool = False
    results_wanted: int = 40
    hours_old: int = 48
    proxies: list[str] = Field(default_factory=list)
    # Server-side employment-type filter ("internship" | "fulltime" | ... | "" = none). LinkedIn's
    # f_JT composes with hours_old; Indeed CANNOT combine it with hours_old, so when set the
    # adapter drops hours_old for Indeed only (documented in _build_scrape_kwargs).
    job_type: str = "internship"
    # LinkedIn rows ship NO description unless this is set; without one the must_have/role gates
    # run title-only and kill generic "Software Intern" titles. Slower (one extra request per
    # job) but the daily run can afford it.
    linkedin_fetch_description: bool = True


class UnstopConfig(BaseModel):
    enabled: bool = True
    opportunities: list[str] = Field(default_factory=lambda: ["internships", "jobs"])
    search_terms: list[str] = Field(default_factory=list)
    max_age_days: int = 30
    # Pages to walk per opportunity before stopping (safety cap; pagination also stops on an
    # empty/short page). ge=1 so a misconfig can't disable the source. See sources/_http.py.
    max_pages: int = Field(default=5, ge=1)


class SimpleSourceConfig(BaseModel):
    """Toggle-only source with optional free-form params (remoteok, jobicy, etc.)."""

    enabled: bool = True


class HimalayasConfig(SimpleSourceConfig):
    country: str = "IN"


class JobicyConfig(SimpleSourceConfig):
    job_type: str = "internship"


class AdzunaConfig(SimpleSourceConfig):
    country: str = "in"
    # Pages of 50 to walk before stopping (also stops on an empty/short page). 10 -> up to ~500.
    max_pages: int = Field(default=10, ge=1)


class InternshalaConfig(SimpleSourceConfig):
    """Internshala listing-page scrape — the highest-density Indian internship source. Each slug
    is a server-side filter page (…/internships/<slug>/); counts verified live 2026-07-18."""

    slugs: list[str] = Field(
        default_factory=lambda: [
            "work-from-home-backend-development-internships",
            "work-from-home-machine-learning-internships",
            "work-from-home-data-science-internships",
            "work-from-home-python-django-internships",
            "backend-development-internship-in-bangalore",
            "machine-learning-internship-in-bangalore",
            "software-development-internship-in-bangalore",
        ]
    )
    # Pages of ~50 to walk per slug (also stops on a short/empty page).
    max_pages: int = Field(default=3, ge=1)


class JoobleConfig(SimpleSourceConfig):
    # Pages to walk before stopping (also stops on an empty page from the API).
    max_pages: int = Field(default=10, ge=1)


class GreenhouseConfig(SimpleSourceConfig):
    tokens: list[str] = Field(default_factory=list)


class LeverConfig(SimpleSourceConfig):
    slugs: list[str] = Field(default_factory=list)


class AshbyConfig(SimpleSourceConfig):
    orgs: list[str] = Field(default_factory=list)


class SmartRecruitersConfig(SimpleSourceConfig):
    company_ids: list[str] = Field(default_factory=list)


class AtsConfig(BaseModel):
    greenhouse: GreenhouseConfig = Field(default_factory=GreenhouseConfig)
    lever: LeverConfig = Field(default_factory=LeverConfig)
    ashby: AshbyConfig = Field(default_factory=AshbyConfig)
    smartrecruiters: SmartRecruitersConfig = Field(default_factory=SmartRecruitersConfig)


class SourcesConfig(BaseModel):
    jobspy: JobSpyConfig = Field(default_factory=JobSpyConfig)
    unstop: UnstopConfig = Field(default_factory=UnstopConfig)
    remoteok: SimpleSourceConfig = Field(default_factory=SimpleSourceConfig)
    himalayas: HimalayasConfig = Field(default_factory=HimalayasConfig)
    jobicy: JobicyConfig = Field(default_factory=JobicyConfig)
    adzuna: AdzunaConfig = Field(default_factory=AdzunaConfig)
    jooble: JoobleConfig = Field(default_factory=JoobleConfig)
    internshala: InternshalaConfig = Field(default_factory=InternshalaConfig)
    remotive: SimpleSourceConfig = Field(default_factory=lambda: SimpleSourceConfig(enabled=False))
    ats: AtsConfig = Field(default_factory=AtsConfig)


class TelegramConfig(BaseModel):
    enabled: bool = False


class EmailConfig(BaseModel):
    enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 25
    to: str = ""


class RssConfig(BaseModel):
    enabled: bool = True
    path: str = "data/feed.xml"
    max_items: int = 100


class NotifyConfig(BaseModel):
    on: Literal["new_only", "all", "off"] = "new_only"
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    email: EmailConfig = Field(default_factory=EmailConfig)
    rss: RssConfig = Field(default_factory=RssConfig)
    # Base URL of the dashboard, used in the Telegram end-of-run summary link. Override at
    # runtime with env JOBAGG_PUBLIC_URL (handy in Docker where the host port differs).
    dashboard_url: str = "http://localhost:8000"


class ResumeConfig(BaseModel):
    """Résumé-tailoring engine (Track C/D). The API key is NEVER stored here — it comes from the
    env var named by `api_key_env`. Two backends: an OpenAI-compatible HTTP endpoint, or a local
    coding agent (Claude Code / Codex) invoked as a subprocess."""

    backend: Literal["openai_compatible", "coding_agent"] = "openai_compatible"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"  # which env var holds the key (never the key itself)
    # coding-agent backend: a command that reads a prompt on stdin, writes the completion to stdout.
    agent_command: list[str] = Field(default_factory=lambda: ["claude", "-p"])
    max_projects: int = Field(default=4, ge=1)  # projects to include on the tailored résumé
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)  # low = truthful, deterministic


class ApplyConfig(BaseModel):
    """The browser apply agent (Track D). Opt-in and OFF by default: it fills forms in a headful
    browser you can watch, then STOPS for you to review and submit (never auto-submits)."""

    enabled: bool = False
    auto_submit: bool = False  # intentionally unsupported today; documents the deliberate choice
    # "agentic": a Claude Code session drives the visible browser via the playwright MCP — it
    # clicks through Apply/Easy Apply on a POSTING page to reach the real form, and waits (with
    # you) through captcha/login walls. "deterministic": the original selector-based fill, which
    # only works when the job URL already IS a form. agentic auto-falls-back to deterministic
    # when the claude/npx CLIs are missing.
    engine: Literal["agentic", "deterministic"] = "agentic"
    # Import the target site's login cookies from your own browser (Zen/Firefox family) so the
    # agent's Chromium starts logged in — Easy/Quick Apply needs your account session.
    use_browser_cookies: bool = True
    browser_cookie_db: str = ""  # explicit cookies.sqlite path; "" = auto-discover (Zen first)
    # Upper bound for one agentic session. Generous ON PURPOSE: the human may be solving a
    # captcha or a login wall in the middle of it.
    agent_timeout_s: int = Field(default=900, ge=60)
    # Model for the apply session. Without this the claude CLI inherits the user's default
    # (observed: an opus-1M flagship — slow and quota-hungry for a form fill); sonnet handles
    # form navigation easily. "" = inherit the CLI default.
    agent_model: str = "sonnet"


class Config(BaseModel):
    """The root effective configuration."""

    keywords: Keywords = Field(default_factory=Keywords)
    locations: list[str] = Field(default_factory=list)
    remote_preferred: bool = True
    salary: SalaryConfig = Field(default_factory=SalaryConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
    resume: ResumeConfig = Field(default_factory=ResumeConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)
