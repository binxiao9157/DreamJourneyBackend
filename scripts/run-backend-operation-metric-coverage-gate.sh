#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

cd "$ROOT"
STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" -m unittest \
  tests.test_operation_metric_coverage \
  tests.test_operation_metrics

STORE_BACKEND=memory PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
import app.main as main_module
from app.observability.operation_metric_coverage import build_operation_metric_coverage_manifest

manifest = build_operation_metric_coverage_manifest(main_module._operation_metric_expected_routes())
summary = manifest.observation_summary()
assert summary["httpRouteCoverage"]["expectedCount"] > 0
assert summary["httpRouteCoverage"]["expectedCount"] == summary["httpRouteCoverage"]["instrumentedCount"]
assert summary["criticalWorkerCoverage"]["notInstrumentedCount"] > 0
assert summary["coverageComplete"] is False
assert summary["sloClaimAllowed"] is False
print("Operation metric coverage G0 gate passed")
PY
