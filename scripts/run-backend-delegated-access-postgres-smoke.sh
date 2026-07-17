#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

: "${BACKEND_BASE_URL:?BACKEND_BASE_URL is required}"
: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKEND_API_TOKEN:=${DREAMJOURNEY_BACKEND_API_TOKEN:-}}"
: "${BACKEND_API_TOKEN:?BACKEND_API_TOKEN is required}"

cd "$ROOT_DIR"
PYTHONPATH=. \
BACKEND_BASE_URL="$BACKEND_BASE_URL" \
DATABASE_URL="$DATABASE_URL" \
BACKEND_API_TOKEN="$BACKEND_API_TOKEN" \
DELEGATED_ACCESS_SMOKE_IN_PROCESS="${DELEGATED_ACCESS_SMOKE_IN_PROCESS:-1}" \
"$PYTHON_BIN" scripts/backend-delegated-access-postgres-smoke.py
