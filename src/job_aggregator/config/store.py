"""DB-backed config store (Phase 1). Seeds from YAML on init; the dashboard is the writer.

The full effective Config lives as one JSON row (`config` table, id=1). The runner loads it at
the START of each cycle, so dashboard edits take effect on the NEXT run. Secrets are NOT part
of the Config — they come from env (see .env.example).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from job_aggregator.config.schema import Config
from job_aggregator.errors import ConfigError
from job_aggregator.paths import DEFAULT_CONFIG_YAML


def _serialize(cfg: Config) -> str:
    """Config -> JSON string (mode='json' so enums/dates are already primitive)."""
    return json.dumps(cfg.model_dump(mode="json"))


def _now_iso() -> str:
    # Config timestamps are audit-only (never used in stale/dedup logic), so a direct UTC read
    # is fine here — no injected Clock in the store signatures.
    return datetime.now(UTC).isoformat()


def seed_from_yaml(conn: sqlite3.Connection, yaml_path: Path | None = None) -> None:
    """Write the single config row (id=1) from the seed YAML, ONLY if absent.

    Idempotent: never clobbers a config the user has already edited via the dashboard.
    """
    if conn.execute("SELECT 1 FROM config WHERE id = 1").fetchone() is not None:
        return
    source = yaml_path or DEFAULT_CONFIG_YAML
    try:
        raw = yaml.safe_load(source.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(
            f"could not read seed config: {source}", details={"path": str(source)}
        ) from exc
    try:
        cfg = Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(
            "seed config failed validation", details={"errors": exc.errors()}
        ) from exc
    conn.execute(
        "INSERT INTO config (id, data, updated_at) VALUES (1, ?, ?)",
        (_serialize(cfg), _now_iso()),
    )
    conn.commit()


def load_effective_config(conn: sqlite3.Connection) -> Config:
    """Read + validate the single config row. Raises ConfigError if it is missing or invalid."""
    try:
        row = conn.execute("SELECT data FROM config WHERE id = 1").fetchone()
    except sqlite3.OperationalError as exc:
        # `connect()` lazily creates an empty, table-less file, so a fresh path reaches here with
        # "no such table: config". Turn that into the friendly first-run hint, not a raw traceback.
        raise ConfigError("database not initialized — run `initdb` first") from exc
    if row is None:
        raise ConfigError("no config row present — run `initdb` first")
    try:
        data = json.loads(row["data"])
        return Config.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ConfigError("stored config is corrupt or invalid") from exc


def save_config(conn: sqlite3.Connection, cfg: Config) -> None:
    """Persist Config as JSON into the single config row (dashboard writer)."""
    conn.execute(
        "UPDATE config SET data = ?, updated_at = ? WHERE id = 1",
        (_serialize(cfg), _now_iso()),
    )
    conn.commit()
