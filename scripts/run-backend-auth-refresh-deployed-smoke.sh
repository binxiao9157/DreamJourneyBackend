#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

: "${BACKEND_BASE_URL:=${DREAMJOURNEY_BACKEND_BASE_URL:-}}"
if [[ -z "$BACKEND_BASE_URL" ]]; then
  echo "BACKEND_BASE_URL is required" >&2
  exit 1
fi

cd "$ROOT_DIR"
BACKEND_BASE_URL="$BACKEND_BASE_URL" \
  "$PYTHON_BIN" scripts/backend-auth-refresh-deployed-smoke.py
