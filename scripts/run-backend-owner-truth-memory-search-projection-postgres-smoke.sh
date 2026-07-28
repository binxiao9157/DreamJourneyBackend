#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

: "$DATABASE_URL"
cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" scripts/backend-owner-truth-memory-search-projection-postgres-smoke.py
