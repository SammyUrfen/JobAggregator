"""Phase 9 — config/store error paths + round-trip (correctness-core coverage)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from job_aggregator.config.schema import Config
from job_aggregator.config.store import load_effective_config, save_config, seed_from_yaml
from job_aggregator.errors import ConfigError
from job_aggregator.storage.db import connect, init_db


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "j.db")
    init_db(conn)
    return conn


def test_load_uninitialized_db_is_friendly(tmp_path: Path) -> None:
    conn = connect(tmp_path / "fresh.db")  # no init_db -> no config table
    with pytest.raises(ConfigError, match="initialized"):
        load_effective_config(conn)
    conn.close()


def test_load_missing_row_is_friendly(tmp_path: Path) -> None:
    conn = _db(tmp_path)  # tables exist but no config row
    with pytest.raises(ConfigError):
        load_effective_config(conn)
    conn.close()


def test_load_corrupt_json_raises_config_error(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    conn.execute("INSERT INTO config (id, data, updated_at) VALUES (1, 'not json{', '2026')")
    conn.commit()
    with pytest.raises(ConfigError):
        load_effective_config(conn)
    conn.close()


def test_seed_bad_yaml_path_raises_config_error(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    with pytest.raises(ConfigError):
        seed_from_yaml(conn, yaml_path=tmp_path / "does-not-exist.yaml")
    conn.close()


def test_seed_does_not_clobber_existing(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    seed_from_yaml(conn)
    cfg = load_effective_config(conn)
    cfg.schedule.run_hour_local = 9
    save_config(conn, cfg)
    seed_from_yaml(conn)  # must be a no-op (never overwrites an edited config)
    assert load_effective_config(conn).schedule.run_hour_local == 9
    conn.close()


def test_save_and_load_round_trip_preserves_defaults(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    seed_from_yaml(conn)
    save_config(conn, Config())
    assert load_effective_config(conn) == Config()  # default config round-trips exactly
    conn.close()
