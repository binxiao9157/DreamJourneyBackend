#!/usr/bin/env bash
set -euo pipefail

# G0-only: validates value-free worker readiness/backlog evidence. It never
# claims a job, starts a consumer, persists a dead letter, or calls a Provider.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_async_effect_readiness_evidence \
  tests.test_async_effect_worker
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/readiness_evidence.py \
  app/async_effects/worker.py

echo "Async-effect readiness/evidence G0 contract gate passed"
