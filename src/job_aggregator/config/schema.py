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
    level_required: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    require_level: bool = True


class SalaryConfig(BaseModel):
    currency: str = "INR"
    period: str = "month"
    min_remote: int = 30000
    min_in_office: int = 80000
    on_missing: Literal["keep_and_flag", "drop"] = "keep_and_flag"
    demote_in_office_if_unknown: bool = True
    # Approximate FX rates to normalize foreign pay to INR/month; user-updatable.
    fx_rates: dict[str, float] = Field(default_factory=lambda: {"USD": 83.0, "EUR": 90.0, "GBP": 105.0})


class ScheduleConfig(BaseModel):
    run_hour_local: int = Field(default=3, ge=0, le=23)
    hours_old: int = 48
    grace_days: int = 3
    catch_up_on_startup: bool = True


class JobSpyConfig(BaseModel):
    enabled: bool = True
    sites: list[str] = Field(default_factory=lambda: ["naukri", "linkedin", "indeed", "google"])
    search_terms: list[str] = Field(default_factory=list)
    location: str = "Bengaluru, India"
    country_indeed: str = "india"
    is_remote: bool = True
    results_wanted: int = 40
    hours_old: int = 48
    proxies: list[str] = Field(default_factory=list)


class UnstopConfig(BaseModel):
    enabled: bool = True
    opportunities: list[str] = Field(default_factory=lambda: ["internships", "jobs"])
    search_terms: list[str] = Field(default_factory=list)
    max_age_days: int = 30


class SimpleSourceConfig(BaseModel):
    """Toggle-only source with optional free-form params (remoteok, jobicy, etc.)."""

    enabled: bool = True


class HimalayasConfig(SimpleSourceConfig):
    country: str = "IN"


class JobicyConfig(SimpleSourceConfig):
    job_type: str = "internship"


class AdzunaConfig(SimpleSourceConfig):
    country: str = "in"


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
    jooble: SimpleSourceConfig = Field(default_factory=SimpleSourceConfig)
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


class Config(BaseModel):
    """The root effective configuration."""

    keywords: Keywords = Field(default_factory=Keywords)
    locations: list[str] = Field(default_factory=list)
    remote_preferred: bool = True
    salary: SalaryConfig = Field(default_factory=SalaryConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)
