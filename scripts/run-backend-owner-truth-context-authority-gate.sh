#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_context_authority \
  tests.test_owner_truth_context_authority_api
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_context_authority.py \
  app/services/context_packet.py \
  app/main.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from app.core.config import Settings

assert not Settings().owner_truth_context_authority_closed_pilot_enabled
print("Owner Truth closed-pilot Context Authority gate passed")
PY
