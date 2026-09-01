"""Filesystem path resolution.

Runtime data (DB, generated RSS feed, logs) lives under a DATA_DIR that defaults to
`./data` relative to the current working directory, overridable via env. Package resources
(schema.sql, templates, static assets) are resolved relative to this package's location so
they work regardless of the CWD or install mode.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path

# Directory of the installed `job_aggregator` package (src/job_aggregator/...).
PACKAGE_DIR = Path(__file__).resolve().parent

# ── Package resources (shipped inside the package) ──
SCHEMA_SQL_PATH = PACKAGE_DIR / "storage" / "schema.sql"
TEMPLATES_DIR = PACKAGE_DIR / "dashboard" / "templates"
STATIC_DIR = PACKAGE_DIR / "dashboard" / "static"
RESUME_TEMPLATES_DIR = PACKAGE_DIR / "resume" / "templates"  # packaged LaTeX résumé templates

# ── Repo-level config seed. The repo root is three parents up from this file
#    (src/job_aggregator/paths.py -> src/job_aggregator -> src -> repo root). ──
REPO_ROOT = PACKAGE_DIR.parent.parent
DEFAULT_CONFIG_YAML = REPO_ROOT / "config" / "default_config.yaml"
# Committed placeholder profile (no PII). The real profile.yaml is git-ignored; copy from this.
PROFILE_EXAMPLE_YAML = REPO_ROOT / "config" / "profile.example.yaml"


def default_resume_template() -> Path:
    """The base LaTeX résumé template. Override with env JOBAGG_RESUME_TEMPLATE."""
    env = os.environ.get("JOBAGG_RESUME_TEMPLATE")
    return Path(env).resolve() if env else RESUME_TEMPLATES_DIR / "base_resume.tex"


def default_profile_path() -> Path:
    """The user's ground-truth profile (projects/skills/education). Override with env
    JOBAGG_PROFILE; defaults to `profile.yaml` at the repo root (it is personal, not secret —
    it is the public résumé content — so it lives beside the code, not in the DB)."""
    env = os.environ.get("JOBAGG_PROFILE")
    return Path(env).resolve() if env else REPO_ROOT / "profile.yaml"


def data_dir() -> Path:
    """Runtime data directory. Override with JOBAGG_DATA_DIR."""
    return Path(os.environ.get("JOBAGG_DATA_DIR", "data")).resolve()


def default_db_path() -> Path:
    """SQLite DB path. Override with JOBAGG_DB_PATH."""
    env = os.environ.get("JOBAGG_DB_PATH")
    return Path(env).resolve() if env else data_dir() / "jobs.db"


def feed_path() -> Path:
    """Generated RSS/Atom feed path."""
    return data_dir() / "feed.xml"


def resumes_dir() -> Path:
    """Where tailored résumé PDFs are written, one per job. Under DATA_DIR so it sits beside the
    DB/feed and is git-ignored like the rest of data/."""
    return data_dir() / "resumes"


# Everything outside this becomes a single dash, so a slug is always [a-z0-9-] — the filename can
# never escape resumes_dir() no matter what a job board put in a company name or title.
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Long enough to stay recognisable ("senior-backend-engineer-distributed-systems"), short enough
# that company + title + date + ".pdf" clears the 255-byte filename limit on ext4/APFS.
_SLUG_MAX = 48


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")[:_SLUG_MAX].strip("-") or "unknown"


def resume_path(company: str, title: str, *, when: date | None = None) -> Path:
    """Where THIS job's tailored résumé is written: `<company>_<job title>_<YYYY-MM-DD>.pdf`.

    Named for a human, not for the machine: these PDFs get attached to real applications and
    opened months later from a Downloads folder, where `3014c34233b5….pdf` tells you nothing.
    The date is part of the name because re-tailoring the same job later (a rewritten posting, a
    better profile) should keep the version you already sent rather than overwrite it — see
    `find_resume` for how the newest one is resolved back.
    """
    stamp = (when or date.today()).isoformat()
    return resumes_dir() / f"{_slug(company)}_{_slug(title)}_{stamp}.pdf"


def find_resume(company: str, title: str) -> Path | None:
    """The most recently tailored résumé for this job, or None if it was never tailored.

    ISO dates sort lexicographically, so the last glob match is the newest — no stat() calls and
    no dependence on mtime (a file copy would lie about that).
    """
    matches = sorted(resumes_dir().glob(f"{_slug(company)}_{_slug(title)}_*.pdf"))
    return matches[-1] if matches else None


def sessions_dir() -> Path:
    """Where Fernet-encrypted Playwright storageState blobs live, one per domain as <domain>.enc
    (Track D). Under DATA_DIR so it's git-ignored like the rest of data/; never commit these."""
    return data_dir() / "sessions"


def log_dir() -> Path:
    d = data_dir() / "logs"
    return d
