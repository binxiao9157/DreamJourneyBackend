#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_apns_delivery \
  tests.test_apns_postgres_outbox_migration_contract

if [[ "${RUN_BACKEND_APNS_POSTGRES_OUTBOX_SMOKE:-0}" == "1" ]]; then
  : "${DATABASE_URL:?DATABASE_URL is required}"
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-apns-postgres-outbox-smoke.py
fi
