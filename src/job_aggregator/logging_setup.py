"""Logging configuration. Stdlib logging with a compact, readable formatter.

Call `configure_logging()` once at process start (CLI handlers, dashboard lifespan).
"""

from __future__ import annotations

import logging
import os

_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"


def configure_logging(level: str | None = None) -> None:
    """Configure root logging. Level precedence: arg > JOBAGG_LOG_LEVEL env > INFO."""
    resolved = (level or os.environ.get("JOBAGG_LOG_LEVEL") or "INFO").upper()
    logging.basicConfig(level=resolved, format=_FORMAT, datefmt=_DATEFMT)
    # httpx/apscheduler are chatty at INFO; keep them at WARNING unless debugging.
    if resolved != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("apscheduler").setLevel(logging.WARNING)
