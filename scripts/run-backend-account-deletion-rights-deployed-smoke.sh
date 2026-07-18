#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

: "${BACKEND_BASE_URL:=${DREAMJOURNEY_BACKEND_BASE_URL:-}}"
: "${OUTPUT_PATH:=}"
: "${DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE:=}"

[[ -n "$BACKEND_BASE_URL" ]] || { echo "BACKEND_BASE_URL is required" >&2; exit 1; }
[[ "$DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE" == "1" ]] || { echo "DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 is required" >&2; exit 1; }

cd "$ROOT_DIR"
BACKEND_BASE_URL="$BACKEND_BASE_URL" \
OUTPUT_PATH="$OUTPUT_PATH" \
DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE="$DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE" \
PYTHONPATH=. \
  "$PYTHON_BIN" scripts/backend-account-deletion-rights-deployed-smoke.py
