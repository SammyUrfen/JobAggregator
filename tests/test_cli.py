"""Phase 6 — cli: argparse dispatch for initdb/run/serve/show-config.

run prints RunSummary; --help works with the stdlib only.

See PLAN.md Part II (Phase 6) for the exact cases to implement.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from job_aggregator import __version__, cli
from job_aggregator.errors import ConfigError
from job_aggregator.pipeline import runner
from job_aggregator.pipeline.runner import RunSummary

# Heavy deps that must NOT be imported just by importing the CLI (so `--help`/`--version` are fast
# and work before `pip install` of the full runtime).
_HEAVY_MODULES = {"fastapi", "jobspy", "uvicorn", "apscheduler", "pandas", "httpx"}


def test_version_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0


def test_version_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert __version__ in capsys.readouterr().out


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0


def test_no_subcommand_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2  # argparse "required subcommand" error


@pytest.mark.parametrize("sub", ["initdb", "run", "serve", "show-config"])
def test_all_subcommands_parse(sub: str) -> None:
    args = cli.build_parser().parse_args([sub, "--db", "/tmp/x.db"])
    assert args.command == sub
    assert callable(args.func)


def test_tailor_subcommand_parses() -> None:
    # uid is a required positional, so `tailor` is intentionally NOT in test_all_subcommands_parse.
    args = cli.build_parser().parse_args(["tailor", "abc123", "--db", "/tmp/x.db"])
    assert args.command == "tailor"
    assert args.uid == "abc123"
    assert args.llm is False  # LLM rewrite is opt-in via --llm
    assert callable(args.func)


def test_apply_subcommand_parses() -> None:
    args = cli.build_parser().parse_args(["apply", "abc123", "--db", "/tmp/x.db"])
    assert args.command == "apply"
    assert args.uid == "abc123"
    assert args.llm is False
    assert callable(args.func)


def test_cli_import_is_stdlib_only() -> None:
    # Importing the CLI module must not pull heavy runtime deps into sys.modules.
    code = (
        "import sys, job_aggregator.cli\n"
        f"heavy = {_HEAVY_MODULES!r}\n"
        "leaked = sorted(heavy & set(sys.modules))\n"
        "print(','.join(leaked))\n"
        "sys.exit(1 if leaked else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, (
        f"heavy imports leaked on `import job_aggregator.cli`: {result.stdout.strip()}"
    )


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
