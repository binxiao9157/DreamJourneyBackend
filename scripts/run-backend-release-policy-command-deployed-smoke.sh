#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

: "${BACKEND_BASE_URL:=${DREAMJOURNEY_BACKEND_BASE_URL:-}}"
: "${BACKEND_API_TOKEN:=${DREAMJOURNEY_BACKEND_API_TOKEN:-}}"
: "${EXPECTED_RELEASE_POLICY_COMMAND_MODE:=observe}"

if [[ -z "$BACKEND_BASE_URL" ]]; then
  echo "BACKEND_BASE_URL is required" >&2
  exit 1
fi
if [[ -z "$BACKEND_API_TOKEN" ]]; then
  echo "BACKEND_API_TOKEN is required" >&2
  exit 1
fi

cd "$ROOT_DIR"
BACKEND_BASE_URL="$BACKEND_BASE_URL" \
BACKEND_API_TOKEN="$BACKEND_API_TOKEN" \
EXPECTED_RELEASE_POLICY_COMMAND_MODE="$EXPECTED_RELEASE_POLICY_COMMAND_MODE" \
  "$PYTHON_BIN" scripts/backend-release-policy-command-deployed-smoke.py
