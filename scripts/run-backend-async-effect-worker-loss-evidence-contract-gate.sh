#!/usr/bin/env bash
set -euo pipefail

# G0/G2 boundary only: validates value-free expired-lease observation and its
# append-only persistence. It never claims/retries a job, starts a worker, or
# calls a Provider.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_async_effect_lease_repository \
  tests.test_async_effect_worker \
  tests.test_async_effect_worker_loss_evidence \
  tests.test_async_effect_worker_loss_observation_repository \
  tests.test_async_effect_worker_loss_observation_migration_contract
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/lease_repository.py \
  app/async_effects/worker.py \
  app/async_effects/worker_loss_evidence.py \
  app/async_effects/worker_loss_observation_repository.py

echo "Async-effect worker-loss evidence G0/G2 contract gate passed"
