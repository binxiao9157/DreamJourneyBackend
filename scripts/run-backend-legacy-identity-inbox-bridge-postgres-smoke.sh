#!/usr/bin/env bash
set -euo pipefail

# Disposable G2 verification only. This runner creates and drops a synthetic
# database; it does not resolve or modify live account, inbox, or Provider data.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

: "${DATABASE_URL:?DATABASE_URL is required}"
cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" scripts/backend-legacy-identity-inbox-bridge-postgres-smoke.py
