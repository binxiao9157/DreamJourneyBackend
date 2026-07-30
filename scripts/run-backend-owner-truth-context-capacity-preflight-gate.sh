#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

cd "$ROOT_DIR"
PYTHONPATH=. "$PYTHON_BIN" -m unittest -q tests.test_owner_truth_context_capacity_preflight
PYTHONPATH=. "$PYTHON_BIN" -m py_compile \
  app/services/owner_truth_context_capacity_preflight.py \
  scripts/backend-owner-truth-context-capacity-preflight.py

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from app.services.owner_truth_context_capacity_preflight import (
    DFX_BURST_CONCURRENCY_TARGET,
    DFX_SUSTAINED_DURATION_SECONDS_TARGET,
    DFX_SUSTAINED_QPS_TARGET,
    OWNER_TRUTH_CONTEXT_CAPACITY_PREFLIGHT_SCHEMA_VERSION,
    ContextCapacityPreflightConfig,
)

assert OWNER_TRUTH_CONTEXT_CAPACITY_PREFLIGHT_SCHEMA_VERSION.endswith("-v1")
assert ContextCapacityPreflightConfig(
    sustained_qps=DFX_SUSTAINED_QPS_TARGET,
    sustained_duration_seconds=DFX_SUSTAINED_DURATION_SECONDS_TARGET,
    burst_concurrency=DFX_BURST_CONCURRENCY_TARGET,
).full_dfx_shape_requested
print("Owner Truth Context capacity preflight G0 gate passed")
PY

if [[ "${RUN_OWNER_TRUTH_CONTEXT_CAPACITY_PREFLIGHT:-0}" == "1" ]]; then
  PYTHONPATH=. "$PYTHON_BIN" scripts/backend-owner-truth-context-capacity-preflight.py
else
  echo "Owner Truth Context capacity runtime preflight skipped; set RUN_OWNER_TRUTH_CONTEXT_CAPACITY_PREFLIGHT=1"
fi
