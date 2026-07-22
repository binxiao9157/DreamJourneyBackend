#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

: "${DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE:?DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE=1 is required}"
: "${OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL:?OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL is required}"
if [[ "$DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE" != "1" ]]; then
  printf '%s\n' 'DREAMJOURNEY_OWNER_TRUTH_FORMAL_SMOKE must equal 1' >&2
  exit 2
fi
cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" scripts/backend-owner-truth-interview-confirmation-formal-postgres-smoke.py
