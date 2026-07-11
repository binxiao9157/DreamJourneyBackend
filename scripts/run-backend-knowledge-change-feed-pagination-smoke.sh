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
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_core_services.StoreTests.test_memory_change_feed_applies_revision_upper_bound_limit_and_order \
  tests.test_core_services.KnowledgeSyncAPITests.test_change_feed_pagination_keeps_a_stable_target_revision \
  tests.test_core_services.KnowledgeSyncAPITests.test_change_feed_pagination_validates_limit_and_revision_window \
  tests.test_postgres_store.PostgresStoreTests.test_change_feed_uses_revision_upper_bound_order_and_sql_limit
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" scripts/backend-knowledge-delta-smoke.py
echo "Backend knowledge change feed pagination smoke passed"
