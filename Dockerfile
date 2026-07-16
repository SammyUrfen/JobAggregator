# syntax=docker/dockerfile:1
#
# JobAggregator — container image for the FastAPI dashboard + in-process scheduler.
#
# This is an OPTIONAL, best-effort template. The project's primary target is a personal
# laptop (see CLAUDE.md / PLAN.md); the container is a convenience for running the same
# single process elsewhere. Edit freely.
#
# Build:  docker build -t job-aggregator .
# Run:    docker run -p 8000:8000 -v jobagg-data:/data --env-file .env job-aggregator
#         (the DB auto-initializes on `serve`; visit http://localhost:8000)

FROM python:3.11-slim

# --- Non-root user ---------------------------------------------------------------------
# Run as an unprivileged user; nothing here needs root. UID/GID 10001 is arbitrary but
# high enough to avoid colliding with host system accounts on a bind mount.
RUN groupadd --gid 10001 app \
 && useradd  --uid 10001 --gid app --create-home --home-dir /home/app app

WORKDIR /app

# --- Dependencies + install ------------------------------------------------------------
# Copy the whole project, then install RUNTIME deps only (`pip install -e '.'`, NOT
# '.[dev]' — no pytest/ruff/mypy in the image). Editable install keeps the src-layout
# `job_aggregator` package importable without a separate build step.
#
# Note: we do a plain COPY (no separate requirements layer) because the dependency set
# lives in pyproject.toml and an editable install needs the source present anyway. If you
# want faster rebuilds, split out a wheel/requirements step yourself.
COPY . /app
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e '.'

# --- Runtime data ----------------------------------------------------------------------
# JOBAGG_DATA_DIR is honored by job_aggregator.paths.data_dir(); the SQLite DB, generated
# feed, and logs all land under it. Mount a volume here so data survives container replace.
ENV JOBAGG_DATA_DIR=/data
RUN mkdir -p /data && chown app:app /data
VOLUME /data

USER app

EXPOSE 8000

# --- Entrypoint ------------------------------------------------------------------------
# `serve` auto-initializes the DB (idempotent) and starts the FastAPI app + the in-process
# APScheduler. Bind to 0.0.0.0 so the port is reachable from outside the container.
#
# ⚠️  SINGLE PROCESS ONLY — NEVER add `--workers N` (or run multiple uvicorn workers, or
#     scale this service to >1 replica). The app holds a single SQLite database and one
#     in-process APScheduler; multiple workers would each spin up their own scheduler and
#     double-/triple-fetch every source, and would contend on the same SQLite file. `serve`
#     has no --workers flag by design. Scale vertically, not horizontally.
#
# If you instead drive runs from an external OS timer (see deploy/job-aggregator.timer),
# start this with JOBAGG_DISABLE_SCHEDULER=1 so the in-process scheduler stays off and only
# the timer fetches.
CMD ["python", "-m", "job_aggregator", "serve", "--host", "0.0.0.0", "--port", "8000"]
