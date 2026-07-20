#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

cd "$ROOT"

# The production API image intentionally excludes tests/.  Run the full unit
# suite when it is present, then always run the dependency-free policy smoke so
# this gate can also prove the deployed image imports and evaluates the policy.
if [[ -f tests/test_owner_truth_knowledge_recommendations.py ]]; then
  PYTHONPATH=. "$PYTHON_BIN" -m unittest tests.test_owner_truth_knowledge_recommendations
else
  echo "Owner Truth knowledge recommendation unit tests unavailable in this image; running deployed policy smoke"
fi

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from datetime import datetime, timezone

from app.domain.owner_truth.knowledge_recommendations import (
    ConfirmedMemoryDimensionEvidence,
    KnowledgeDimension,
    KnowledgeDimensionProjector,
    RecommendationCandidate,
    RecommendationEvidenceKind,
    RecommendationSelector,
    RecommendationSlot,
)

owner = "owner-deployed-smoke"
vault = "vault-deployed-smoke"
projection = KnowledgeDimensionProjector().project(
    owner_subject_id=owner,
    vault_id=vault,
    evidence=(
        ConfirmedMemoryDimensionEvidence(
            memory_version_id="memory-deployed-smoke",
            source_id="source-deployed-smoke",
            vault_id=vault,
            owner_subject_id=owner,
            dimension=KnowledgeDimension.KEY_DECISIONS,
            covered_facets=("choice",),
        ),
    ),
)
selection = RecommendationSelector().select(
    owner_subject_id=owner,
    vault_id=vault,
    coverage=projection,
    candidates=(
        RecommendationCandidate(
            candidate_id="continuity-deployed-smoke",
            owner_subject_id=owner,
            vault_id=vault,
            slot=RecommendationSlot.CONTINUITY,
            thread_id="thread-deployed-smoke",
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="reason",
            question_template_id="template-deployed-smoke",
            evidence_kind=RecommendationEvidenceKind.CONFIRMED_MEMORY,
            evidence_refs=("memory-deployed-smoke",),
            reason_code="recentThread",
        ),
        RecommendationCandidate(
            candidate_id="breadth-deployed-smoke",
            owner_subject_id=owner,
            vault_id=vault,
            slot=RecommendationSlot.BREADTH,
            thread_id="thread-breadth-smoke",
            target_dimension=KnowledgeDimension.VALUES,
            missing_facet="priority",
            question_template_id="template-breadth-smoke",
            evidence_kind=RecommendationEvidenceKind.COLD_START_BLUEPRINT,
            evidence_refs=("blueprint-deployed-smoke",),
            reason_code="knowledgeGap",
        ),
    ),
    now=datetime(2026, 7, 21, tzinfo=timezone.utc),
)
assert [item.slot.value for item in selection.selected] == ["continuity", "breadth"]
assert len(projection.coverage) == 6
print("Owner Truth knowledge recommendation deployed policy smoke passed")
PY

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
