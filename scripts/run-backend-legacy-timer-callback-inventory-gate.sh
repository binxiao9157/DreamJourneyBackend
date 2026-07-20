#!/usr/bin/env bash
set -euo pipefail

# G0 inventory only. This validates source markers and value-free metadata; it
# never starts a worker, invokes a direct dispatch, reads host timers, or calls
# a Provider.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_legacy_timer_callback_inventory
PYTHONPATH=. "$PYTHON_BIN" scripts/check_legacy_timer_callback_inventory.py

echo "Legacy timer/callback backend inventory G0 gate passed"
