"""Filesystem path resolution.

Runtime data (DB, generated RSS feed, logs) lives under a DATA_DIR that defaults to
`./data` relative to the current working directory, overridable via env. Package resources
(schema.sql, templates, static assets) are resolved relative to this package's location so
they work regardless of the CWD or install mode.
"""

from __future__ import annotations

import os
from pathlib import Path

# Directory of the installed `job_aggregator` package (src/job_aggregator/...).
PACKAGE_DIR = Path(__file__).resolve().parent

# ── Package resources (shipped inside the package) ──
SCHEMA_SQL_PATH = PACKAGE_DIR / "storage" / "schema.sql"
TEMPLATES_DIR = PACKAGE_DIR / "dashboard" / "templates"
STATIC_DIR = PACKAGE_DIR / "dashboard" / "static"

# ── Repo-level config seed. The repo root is three parents up from this file
#    (src/job_aggregator/paths.py -> src/job_aggregator -> src -> repo root). ──
REPO_ROOT = PACKAGE_DIR.parent.parent
DEFAULT_CONFIG_YAML = REPO_ROOT / "config" / "default_config.yaml"


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


def log_dir() -> Path:
    d = data_dir() / "logs"
    return d
