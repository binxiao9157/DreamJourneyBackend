#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_voice_dh_authority \
  tests.test_voice_dh_authority_migration_contract

if [[ "${RUN_VOICE_DH_AUTHORITY_POSTGRES_SMOKE:-0}" == "1" ]]; then
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-voice-dh-authority-postgres-smoke.py
fi
