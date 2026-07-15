"""Enables `python -m job_aggregator ...` — delegates to the CLI."""

from job_aggregator.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
