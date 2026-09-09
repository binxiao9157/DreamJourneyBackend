#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest -q \
  tests.test_owner_truth_b_memory_quality_fixture \
  tests.test_owner_truth_memory_search_quality_corpus \
  tests.test_owner_truth_memory_search_quality_evaluation_runner

echo "Owner Truth memory-search 200-case quality corpus gate passed"
