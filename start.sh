#!/usr/bin/env bash
# start.sh — run the JobAggregator dashboard host-native (the Docker deployment is retired:
# a container can never host the apply agent's browser, so serve now runs where the browser is).
#
#   ./start.sh                 # dashboard on http://localhost:8770 (Ctrl-C stops it)
#   JOBAGG_PORT=9000 ./start.sh
#
# For start-on-boot, install the systemd USER unit instead of running this by hand:
#   mkdir -p ~/.config/systemd/user
#   cp deploy/job-aggregator-serve.service ~/.config/systemd/user/
#   systemctl --user daemon-reload && systemctl --user enable --now job-aggregator-serve
#   loginctl enable-linger "$USER"     # start at BOOT, not merely at login
#
# WHY the shape of this script:
# - The conda env is "activated" by using its interpreter's ABSOLUTE path — that alone pins
#   python + every installed dep; no `conda activate` (which needs an interactive shell hook).
# - PATH is extended, not replaced: the apply agent spawns `claude` (~/.local/bin) and
#   `npx`/`pdflatex` (/usr/bin), which a systemd user session may not have on PATH.
# - serve loads .env itself (python-dotenv, CWD-relative) — hence the cd to the repo root.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON=/home/SammyUrfen/miniconda3/envs/job-aggregator/bin/python
PORT="${JOBAGG_PORT:-8770}"   # the dedicated dashboard port (never 8000 — dev-server clash)

# Tools the apply agent shells out to; keep their homes on PATH under systemd's minimal env.
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
# Started at BOOT (linger) the unit predates graphical login, so no display vars are inherited
# and the apply button would refuse. If the Wayland socket exists by now, point at it.
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -S "/run/user/$(id -u)/wayland-0" ]; then
  export WAYLAND_DISPLAY=wayland-0
fi
# The Telegram run-summary shows this address; override in .env if you use a hostname.
export JOBAGG_PUBLIC_URL="${JOBAGG_PUBLIC_URL:-http://localhost:${PORT}}"

cd "$REPO_DIR"
exec "$PYTHON" -m job_aggregator serve --host 127.0.0.1 --port "$PORT"
