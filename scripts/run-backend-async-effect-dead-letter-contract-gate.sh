#!/usr/bin/env bash
set -euo pipefail

# G0-only: validates value-free dead-letter admission and server-authorized
# replay decisions. It does not persist a dead letter, re-enqueue a job, invoke
# a Provider, or let a client execute a replay.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_async_effect_dead_letter_contract
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/dead_letter_effects.py

echo "Async-effect dead-letter G0 contract gate passed"
