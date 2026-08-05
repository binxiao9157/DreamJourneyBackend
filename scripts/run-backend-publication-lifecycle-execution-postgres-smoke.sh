#!/usr/bin/env bash
set -euo pipefail

# Uses a disposable database derived from DATABASE_URL. It never writes smoke
# records to the configured application database.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

: "${DATABASE_URL:?DATABASE_URL is required}"
cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" scripts/backend-publication-lifecycle-execution-postgres-smoke.py
