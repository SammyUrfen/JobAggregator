"""Phase 6 — cli: argparse dispatch for initdb/run/serve/show-config.

run prints RunSummary; --help works with the stdlib only.

See PLAN.md Part II (Phase 6) for the exact cases to implement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from job_aggregator import cli
from job_aggregator.errors import ConfigError
from job_aggregator.pipeline import runner
from job_aggregator.pipeline.runner import RunSummary


def test_version_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0


def test_initdb_then_show_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "jobs.db"
    assert cli.main(["initdb", "--db", str(db)]) == 0
    assert db.exists()
    assert cli.main(["show-config", "--db", str(db)]) == 0
    assert '"keywords"' in capsys.readouterr().out  # JSON config dumped


def test_run_prints_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "jobs.db"
    cli.main(["initdb", "--db", str(db)])
    monkeypatch.setattr(
        runner,
        "run_cycle",
        lambda conn, cfg, clock, trigger: RunSummary(1, "success", 0, 0, 0, 0, 0),
    )
    assert cli.main(["run", "--db", str(db)]) == 0
    assert "run #1" in capsys.readouterr().out


def test_config_error_maps_to_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from job_aggregator.config import store

    def boom(conn: Any) -> Any:
        raise ConfigError("bad config")

    monkeypatch.setattr(store, "load_effective_config", boom)
    rc = cli.main(["run", "--db", str(tmp_path / "jobs.db")])
    assert rc == 1
    assert "error [config_invalid]" in capsys.readouterr().err
