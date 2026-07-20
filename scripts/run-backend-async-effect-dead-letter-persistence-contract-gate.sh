#!/usr/bin/env bash
set -euo pipefail

# G2 persistence boundary only: verifies durable dead-letter admission is
# value-free and idempotent. It does not enable a worker, replay work, or call
# any Provider.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_async_effect_dead_letter_contract \
  tests.test_async_effect_dead_letter_repository \
  tests.test_async_effect_dead_letter_persistence_migration_contract
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/dead_letter_effects.py \
  app/async_effects/dead_letter_repository.py

echo "Async-effect dead-letter persistence G2 contract gate passed"
