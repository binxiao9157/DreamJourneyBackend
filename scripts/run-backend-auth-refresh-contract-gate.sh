#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

cd "$ROOT_DIR"
"$PYTHON_BIN" scripts/backend-auth-refresh-contract-check.py
"$PYTHON_BIN" -m unittest tests.test_auth_sessions tests.test_postgres_store

echo "Backend auth refresh contract gate passed"
