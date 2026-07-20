#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

cd "$ROOT"
PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_owner_truth_knowledge_recommendations

"$PYTHON_BIN" - <<'PY'
from pathlib import Path

source = Path("app/domain/owner_truth/knowledge_recommendations.py").read_text(encoding="utf-8")
for required in (
    "class KnowledgeDimension",
    "class KnowledgeDimensionProjector",
    "class RecommendationSelector",
    "doNotAsk",
    "sensitiveWithoutRecentConsent",
    "aiInferenceOnly",
    "crisisSafetyOverride",
    "duplicateThreadFacet",
    "at most one continuity and one breadth",
):
    assert required in source, f"missing M0-B recommendation policy invariant: {required}"
print("Owner Truth knowledge recommendation G0 gate passed")
PY
