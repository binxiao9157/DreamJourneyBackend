#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

cd "$ROOT_DIR"

echo "== Knowledge governance compile check =="
"$PYTHON_BIN" -m compileall -q app tests

echo "== Knowledge governance + source cascade deterministic smoke =="
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest -v \
  tests.test_knowledge_governance \
  tests.test_archive_store \
  tests.test_auth_sessions \
  tests.test_route_ownership_registry \
  tests.test_core_services \
  tests.test_postgres_store

echo "Backend knowledge governance source cascade smoke passed"
