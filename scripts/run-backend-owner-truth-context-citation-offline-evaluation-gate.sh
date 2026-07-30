#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

cd "$ROOT_DIR"

PYTHONPATH=. "$PYTHON_BIN" -m unittest -q \
  tests.test_owner_truth_context_citation_offline_evaluation

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from app.services.owner_truth_context_citation_offline_evaluation import (
    OWNER_TRUTH_CONTEXT_CITATION_EVALUATION_METRIC_ALLOWLIST,
    OWNER_TRUTH_CONTEXT_CITATION_FORBIDDEN_ENGAGEMENT_METRICS,
    OWNER_TRUTH_CONTEXT_CITATION_OFFLINE_EVALUATION_SCHEMA_VERSION,
)

assert OWNER_TRUTH_CONTEXT_CITATION_OFFLINE_EVALUATION_SCHEMA_VERSION.endswith("-v1")
assert OWNER_TRUTH_CONTEXT_CITATION_EVALUATION_METRIC_ALLOWLIST
assert not (
    OWNER_TRUTH_CONTEXT_CITATION_EVALUATION_METRIC_ALLOWLIST
    & OWNER_TRUTH_CONTEXT_CITATION_FORBIDDEN_ENGAGEMENT_METRICS
)
print("Owner Truth Context/Citation offline evaluation gate passed")
PY
