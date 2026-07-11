#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_knowledge_privacy_maintenance \
  tests.test_core_services.StoreTests.test_memory_v2_mutation_uses_one_canonical_source_ref_contract \
  tests.test_postgres_store.PostgresStoreTests.test_postgres_v2_mutation_uses_one_canonical_source_ref_contract

HELP_OUTPUT="$($PYTHON_BIN scripts/maintain_knowledge_privacy_metadata.py --help)"
grep -q -- "--apply" <<<"$HELP_OUTPUT"

echo "Backend knowledge privacy maintenance smoke passed"
