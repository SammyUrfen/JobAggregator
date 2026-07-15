"""Command-line entry point.

Subcommands (see PLAN.md Part II for full behaviour):
    initdb        create data/jobs.db and seed config from config/default_config.yaml
    run           execute ONE aggregation cycle now and print a summary   [Phase 5/6]
    serve         launch the FastAPI dashboard (which owns the daily scheduler)  [Phase 8]
    show-config   print the effective config currently stored in the DB

Design note: heavy third-party imports (fastapi, jobspy, apscheduler, pydantic) are done
LAZILY inside each handler so that `python -m job_aggregator --help` works with only the
stdlib present — i.e. before `pip install -e .[dev]`. Do not add top-level heavy imports.
"""

from __future__ import annotations

import argparse

from job_aggregator import __version__


def cmd_initdb(args: argparse.Namespace) -> int:
    """Create the DB and seed the config row. Implemented in Phase 1."""
    from job_aggregator.logging_setup import configure_logging
    from job_aggregator.storage.db import connect, init_db

    configure_logging(args.log_level)
    conn = connect(args.db)
    init_db(conn)
    # Phase 1: seed config via config.store.seed_from_yaml(conn, default_config_path)
    from job_aggregator.config.store import seed_from_yaml

    seed_from_yaml(conn)
    print(f"initialized database at {args.db}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run one aggregation cycle now. Implemented in Phase 5/6."""
    from job_aggregator.clock import SystemClock
    from job_aggregator.config.store import load_effective_config
    from job_aggregator.logging_setup import configure_logging
    from job_aggregator.pipeline.runner import run_cycle
    from job_aggregator.storage.db import connect

    configure_logging(args.log_level)
    conn = connect(args.db)
    cfg = load_effective_config(conn)
    summary = run_cycle(conn, cfg, SystemClock(), trigger="manual")
    print(summary)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Launch the dashboard. Implemented in Phase 8."""
    import uvicorn

    from job_aggregator.logging_setup import configure_logging

    configure_logging(args.log_level)
    uvicorn.run(
        "job_aggregator.dashboard.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def cmd_show_config(args: argparse.Namespace) -> int:
    """Print the effective config. Implemented in Phase 1."""
    from job_aggregator.config.store import load_effective_config
    from job_aggregator.storage.db import connect

    conn = connect(args.db)
    cfg = load_effective_config(conn)
    print(cfg.model_dump_json(indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    from job_aggregator.paths import default_db_path

    parser = argparse.ArgumentParser(
        prog="job-aggregator",
        description="Self-hosted multi-source job/internship aggregator.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--db", default=str(default_db_path()), help="path to the SQLite DB")
    parser.add_argument("--log-level", default="INFO", help="logging level (default: INFO)")

    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("initdb", help="create and seed the database")
    p_init.set_defaults(func=cmd_initdb)

    p_run = sub.add_parser("run", help="execute one aggregation cycle now")
    p_run.set_defaults(func=cmd_run)

    p_serve = sub.add_parser("serve", help="launch the dashboard web app")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="uvicorn autoreload (dev)")
    p_serve.set_defaults(func=cmd_serve)

    p_show = sub.add_parser("show-config", help="print the effective config")
    p_show.set_defaults(func=cmd_show_config)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
