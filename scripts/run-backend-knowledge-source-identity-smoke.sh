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

STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest -v \
  tests.test_knowledge_source_ref_audit \
  tests.test_knowledge_proposal \
  tests.test_route_ownership_registry \
  tests.test_auth_sessions

echo "Backend knowledge source identity smoke passed"
