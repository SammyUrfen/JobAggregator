"""Shared pytest fixtures. See PLAN Part II for the concrete test cases each module needs.

Principles (his): table-driven, deterministic, injected FixedClock, respx for HTTP. The
correctness core (dedup, salary, filters, stale, runner) is tested hardest.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from job_aggregator.clock import FixedClock


@pytest.fixture
def fixed_clock() -> FixedClock:
    """A deterministic clock anchored at 2026-01-01T00:00:00Z; advance() in tests."""
    return FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))


@pytest.fixture
def db(tmp_path):
    """A fresh initialized SQLite DB on disk (WAL). Requires Phase 1 (storage.db)."""
    from job_aggregator.storage.db import connect, init_db

    conn = connect(tmp_path / "test.db")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def sample_config():
    """The default seed config as a validated Config. Requires config.schema (contract)."""
    import yaml

    from job_aggregator.config.schema import Config
    from job_aggregator.paths import DEFAULT_CONFIG_YAML

    data = yaml.safe_load(DEFAULT_CONFIG_YAML.read_text())
    return Config.model_validate(data)
