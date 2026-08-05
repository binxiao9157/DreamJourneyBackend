#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

: "${RUN_M0_DFX_DEPLOYED_SMOKE:?RUN_M0_DFX_DEPLOYED_SMOKE=1 is required}"
: "${M0_DFX_BUILD_ID:?M0_DFX_BUILD_ID is required}"
if [[ "$RUN_M0_DFX_DEPLOYED_SMOKE" != "1" ]]; then
  printf '%s\n' 'RUN_M0_DFX_DEPLOYED_SMOKE must equal 1' >&2
  exit 2
fi

cd "$ROOT_DIR"
RUN_M0_DFX_BASELINE=1 PYTHONPATH=. "$PYTHON_BIN" scripts/backend-m0-dfx-baseline.py
