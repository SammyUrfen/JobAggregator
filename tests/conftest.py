"""Shared pytest fixtures. See PLAN Part II for the concrete test cases each module needs.

Principles (his): table-driven, deterministic, injected FixedClock, respx for HTTP. The
correctness core (dedup, salary, filters, stale, runner) is tested hardest. Fixtures are grown
additively per phase — Phase 0 added fixed_clock/db/sample_config; Phase 1 adds the storage
fixtures (clock/conn/run_id/make_job).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from job_aggregator.clock import FixedClock

if TYPE_CHECKING:
    from job_aggregator.config.schema import Config
    from job_aggregator.models.job import Job

# A single fixed instant anchors every time-dependent test so ISO timestamps are reproducible.
FIXED_INSTANT = datetime(2026, 1, 1, tzinfo=UTC)
# A later "now" used by Phase 3 source tests (recency filters compare against this).
FIXED_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixed_clock() -> FixedClock:
    """A deterministic clock anchored at FIXED_INSTANT; advance() in tests."""
    return FixedClock(FIXED_INSTANT)


@pytest.fixture
def clock() -> FixedClock:
    """Phase 1+ alias: a fresh deterministic clock anchored at FIXED_INSTANT."""
    return FixedClock(FIXED_INSTANT)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A fresh initialized SQLite DB on disk (WAL). Requires Phase 1 (storage.db)."""
    from job_aggregator.storage.db import connect, init_db

    conn = connect(tmp_path / "test.db")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A fresh initialized on-disk SQLite DB (WAL) — the Phase 1 storage-test connection."""
    from job_aggregator.storage.db import connect, init_db

    connection = connect(tmp_path / "jobs.db")
    init_db(connection)
    yield connection
    connection.close()


@pytest.fixture
def run_id(conn: sqlite3.Connection, clock: FixedClock) -> int:
    """A started (status='running') manual run whose id jobs can reference as last_seen_cycle."""
    from job_aggregator.storage.runs_repo import start_run

    return start_run(conn, "manual", clock)


@pytest.fixture
def make_job() -> Callable[..., Job]:
    """Factory building a normalized Job with sensible defaults + a unique job_uid per call.

    Pass keyword overrides to vary any field (e.g. make_job(job_uid="a", is_remote=True)).
    """
    from job_aggregator.models.job import Job

    counter = {"n": 0}

    def _make(**overrides: object) -> Job:
        counter["n"] += 1
        n = counter["n"]
        defaults: dict[str, object] = {
            "job_uid": f"uid-{n:04d}",
            "source": "greenhouse",
            "title": "Backend Engineering Intern",
            "company": "Acme Labs",
            "location": "Bengaluru, India",
            "is_remote": False,
            "url": f"https://example.com/jobs/{n}",
        }
        defaults.update(overrides)
        # model_validate (not Job(**defaults)) keeps mypy happy: the dict is object-typed, and
        # validate accepts a mapping rather than per-field typed kwargs. Validation still runs.
        return Job.model_validate(defaults)

    return _make


@pytest.fixture
def now_clock() -> FixedClock:
    """A deterministic clock anchored at FIXED_NOW (Phase 3 source tests)."""
    return FixedClock(FIXED_NOW)


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """Load a recorded JSON response from tests/fixtures/ by filename."""

    def _load(name: str) -> Any:
        return json.loads((FIXTURES_DIR / name).read_text())

    return _load


@pytest.fixture
def fx_rates() -> dict[str, float]:
    """The default approximate FX table (INR per 1 unit), for salary conversion tests."""
    return {"USD": 83.0, "EUR": 90.0, "GBP": 105.0}


@pytest.fixture
def cfg() -> Config:
    """The default seed config as a validated Config (Phase 2+ alias of sample_config)."""
    import yaml

    from job_aggregator.config.schema import Config
    from job_aggregator.paths import DEFAULT_CONFIG_YAML

    return Config.model_validate(yaml.safe_load(DEFAULT_CONFIG_YAML.read_text()))


@pytest.fixture
def sample_config() -> Config:
    """The default seed config as a validated Config. Requires config.schema (contract)."""
    import yaml

    from job_aggregator.config.schema import Config
    from job_aggregator.paths import DEFAULT_CONFIG_YAML

    data = yaml.safe_load(DEFAULT_CONFIG_YAML.read_text())
    return Config.model_validate(data)
