"""The user's ground-truth profile (Track C).

This is the SINGLE SOURCE OF TRUTH the résumé-tailoring pipeline reads. Its whole reason to
exist is anti-fabrication: tailoring may re-order, re-emphasize, or re-word what is here, but it
may NEVER introduce a project, employer, date, metric, or skill that is not present in this file
(the merge-exclusion rule in the design doc). Keep every field factual.

Bullets are stored as PLAIN TEXT (no LaTeX): they are facts, not formatting. The renderer adds
emphasis/escaping when it fills the LaTeX template, and the tailor rewrites *wording* from these
facts — so a clean, source-agnostic fact is the right unit here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Link(BaseModel):
    label: str  # e.g. "LinkedIn", "GitHub"
    url: str


class Contact(BaseModel):
    name: str
    email: str
    phone: str | None = None
    location: str | None = None
    links: list[Link] = Field(default_factory=list)


class Education(BaseModel):
    institution: str
    degree: str
    location: str | None = None
    start: str | None = None  # free-form, e.g. "Aug 2024"
    end: str | None = None  # e.g. "Jul 2028" or "Present"
    grade: str | None = None  # e.g. "CGR: 8.83"
    url: str | None = None


class Experience(BaseModel):
    """Formal work/internship experience. Empty is valid (projects-first résumé)."""

    company: str
    title: str
    location: str | None = None
    start: str | None = None
    end: str | None = None
    url: str | None = None
    bullets: list[str] = Field(default_factory=list)


class Project(BaseModel):
    name: str
    tagline: str | None = None  # one-line subtitle, e.g. "Relational + LSM DB engine"
    url: str | None = None
    tech: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)  # factual achievement lines (plain text)
    # Free-form tags used to score a project's relevance to a given job description (e.g.
    # "systems", "distributed", "rl"). Not shown on the résumé.
    tags: list[str] = Field(default_factory=list)


class SkillGroup(BaseModel):
    category: str  # e.g. "Languages", "Databases"
    items: list[str]


class Profile(BaseModel):
    """The root ground-truth profile."""

    contact: Contact
    # A short factual positioning line + the aspiration that guides emphasis (never fabrication).
    summary: str | None = None
    ambitions: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
