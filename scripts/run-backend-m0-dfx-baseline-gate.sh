#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest -q tests.test_m0_dfx_baseline
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/observability/m0_dfx_baseline.py \
  scripts/backend-m0-dfx-baseline.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from app.observability.m0_dfx_baseline import (
    M0_DFX_BASELINE_SCHEMA_VERSION,
    M0_DFX_PROBE_IDS,
)

assert M0_DFX_BASELINE_SCHEMA_VERSION.endswith("-v1")
assert M0_DFX_PROBE_IDS == {
    "contextPacket",
    "stage2MediaCandidateProjection",
    "crossVaultRevocation",
}
print("M0 DFX baseline contract gate passed")
PY

if [[ "${RUN_M0_DFX_BASELINE:-0}" == "1" ]]; then
  : "${M0_DFX_BUILD_ID:?M0_DFX_BUILD_ID is required}"
  : "${DATABASE_URL:?DATABASE_URL is required}"
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-m0-dfx-baseline.py
else
  echo "M0 DFX runtime baseline skipped; set RUN_M0_DFX_BASELINE=1"
fi
