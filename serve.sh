#!/usr/bin/env bash
# Stock Cycle Tracker — build the React frontend (first run only) and
# serve the FastAPI app on http://localhost:8011
set -euo pipefail
cd "$(dirname "$0")"

# ── Python env ──────────────────────────────────────────────────────────
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if ! python - <<'EOF' 2>/dev/null
import fastapi, uvicorn, stock_cycle_tracker  # noqa
EOF
then
  echo "Installing Python dependencies…"
  pip install -e .
  pip install "fastapi>=0.110" "uvicorn>=0.29"
fi

# ── Frontend build (first run, or after frontend changes with --rebuild) ─
if [ "${1:-}" = "--rebuild" ] || [ ! -d frontend/dist ]; then
  echo "Building frontend…"
  (cd frontend && npm install && npm run build)
fi

echo "Serving on http://localhost:8011  (set SCT_HOST=0.0.0.0 [and SCT_PORT] for LAN access)"
exec python -m stock_cycle_tracker.web.server
