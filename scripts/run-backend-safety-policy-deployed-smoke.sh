#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN=python3

: "${BACKEND_BASE_URL:=${DREAMJOURNEY_BACKEND_BASE_URL:-}}"
: "${BACKEND_API_TOKEN:=${DREAMJOURNEY_BACKEND_API_TOKEN:-}}"

[[ -n "$BACKEND_BASE_URL" ]] || { echo "BACKEND_BASE_URL is required" >&2; exit 1; }
[[ -n "$BACKEND_API_TOKEN" ]] || { echo "BACKEND_API_TOKEN is required" >&2; exit 1; }

cd "$ROOT_DIR"
BACKEND_BASE_URL="$BACKEND_BASE_URL" \
BACKEND_API_TOKEN="$BACKEND_API_TOKEN" \
  "$PYTHON_BIN" scripts/backend-safety-policy-deployed-smoke.py
