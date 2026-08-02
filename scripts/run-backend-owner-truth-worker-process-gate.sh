#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_owner_truth_worker_process
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/async_effects/owner_truth_candidate_extraction_worker.py \
  app/async_effects/owner_truth_memory_projection_worker.py
echo "Owner Truth worker process gate passed"
