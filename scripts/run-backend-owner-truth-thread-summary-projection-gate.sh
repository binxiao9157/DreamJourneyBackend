#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_owner_truth_thread_summary \
  tests.test_owner_truth_thread_summary_read_api \
  tests.test_owner_truth_thread_summary_projection \
  tests.test_owner_truth_thread_summary_projection_api \
  tests.test_owner_truth_thread_summary_projection_migration_contract \
  tests.test_owner_truth_thread_summary_projection_postgres_smoke_contract \
  tests.test_route_ownership_registry

PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/domain/owner_truth/thread_summary.py \
  app/services/owner_truth_thread_summary_read.py \
  app/services/owner_truth_thread_summary_projection.py

if [[ "${RUN_OWNER_TRUTH_THREAD_SUMMARY_PROJECTION_POSTGRES_SMOKE:-0}" == "1" ]]; then
  "$ROOT_DIR/scripts/run-backend-owner-truth-thread-summary-projection-postgres-smoke.sh"
fi

echo "Owner Truth Thread-summary projection gate passed"
