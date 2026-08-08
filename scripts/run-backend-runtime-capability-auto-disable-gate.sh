#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
STORE_BACKEND=memory "$PYTHON_BIN" -m unittest \
  tests.test_runtime_capability_control \
  tests.test_runtime_capabilities \
  tests.test_async_effect_dead_letter_repository \
  tests.test_data_rights_external_effect_receipts \
  tests.test_release_policy

echo "Backend runtime capability automatic-disable gate passed"
