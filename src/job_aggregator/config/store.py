"""DB-backed config store (Phase 1). Seeds from YAML on init; dashboard edits override."""

from __future__ import annotations

import sqlite3

from job_aggregator.config.schema import Config


def seed_from_yaml(conn: sqlite3.Connection, yaml_path: str | None = None) -> None:
    """Load config/default_config.yaml and INSERT it as the single config row IF absent.
    Idempotent: never clobbers an existing config the user has edited. Phase 1."""
    raise NotImplementedError("Phase 1: seed config row from default_config.yaml")


def load_effective_config(conn: sqlite3.Connection) -> Config:
    """Read the single config row (JSON) -> validated Config. Falls back to defaults if the
    row is missing. Called at the START of every run so dashboard edits apply next cycle."""
    raise NotImplementedError("Phase 1: load + validate config from DB")


def save_config(conn: sqlite3.Connection, cfg: Config) -> None:
    """Validate + persist Config as JSON into the single config row. Phase 1 / used by Phase 8."""
    raise NotImplementedError("Phase 1: persist config to DB")
