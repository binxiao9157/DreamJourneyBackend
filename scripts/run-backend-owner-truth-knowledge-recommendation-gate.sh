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
if [[ -f tests/test_owner_truth_knowledge_recommendations.py && -f tests/test_owner_truth_knowledge_dimension_read.py ]]; then
  PYTHONPATH=. "$PYTHON_BIN" -m unittest \
    tests.test_owner_truth_knowledge_recommendations \
    tests.test_owner_truth_knowledge_dimension_read \
    tests.test_owner_truth_knowledge_dimension_confirmation \
    tests.test_owner_truth_knowledge_dimension_confirmation_migration_contract \
    tests.test_owner_truth_knowledge_recommendation_read \
    tests.test_owner_truth_knowledge_recommendation_read_api
else
  echo "Owner Truth knowledge recommendation unit tests unavailable in this image; running deployed policy smoke"
fi

PYTHONPATH=. "$PYTHON_BIN" - <<'PY'
from datetime import datetime, timezone
from uuid import uuid4

from hashlib import sha256
import json

from app.domain.owner_truth.knowledge_dimension_read import read_owner_confirmed_dimension_coverage
from app.domain.owner_truth.knowledge_recommendations import (
    ConfirmedMemoryDimensionEvidence,
    KnowledgeDimension,
    KnowledgeDimensionProjector,
    RecommendationCandidate,
    RecommendationEvidenceKind,
    RecommendationSelector,
    RecommendationSlot,
)
from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionInput,
    build_ready_memory_projection,
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

source_id = str(uuid4())
content = {"claim": "The owner chose a new direction."}
content_hash = sha256(
    json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
memory_id = str(uuid4())
memory_version_id = str(uuid4())
dimension_snapshot = build_ready_memory_projection(
    vault_id=vault,
    owner_subject_id=owner,
    authority_epoch=0,
    inputs=(
        OwnerTruthMemoryProjectionInput(
            memory_id=memory_id,
            memory_version_id=memory_version_id,
            vault_id=vault,
            owner_subject_id=owner,
            authority_epoch=0,
            version_number=1,
            source_id=source_id,
            source_version=1,
            memory_kind="knowledge",
            perspective_type="firstPerson",
            epistemic_status="recalled",
            sensitivity="standard",
            content_schema_version="owner-truth-v1",
            content_hash=content_hash,
            content=content,
            evidence_refs=({"sourceId": source_id, "sourceVersion": 1},),
        ),
    ),
)
dimension_result = read_owner_confirmed_dimension_coverage(
    memory_projection=dimension_snapshot,
    owner_subject_id=owner,
    vault_id=vault,
    confirmations=(
        {
            "confirmationId": str(uuid4()),
            "commandIdHash": sha256(b"deployed-dimension-command").hexdigest(),
            "payloadHash": sha256(b"deployed-dimension-payload").hexdigest(),
            "vaultId": vault,
            "ownerSubjectId": owner,
            "actorSubjectId": owner,
            "authorityEpoch": 0,
            "memoryId": memory_id,
            "memoryVersionId": memory_version_id,
            "boundContentHash": content_hash,
            "dimension": "keyDecisions",
            "coveredFacets": ["choice"],
            "confirmationMethod": "ownerExplicitSelection",
            "schemaVersion": "owner-truth-knowledge-dimension-confirmation-v1",
            "uiSchemaVersion": "knowledge-dimension-review-v1",
        },
    ),
)
assert dimension_result.state.value == "ready"
assert dimension_result.included_memory_version_ids
assert dimension_result.coverage.for_dimension("keyDecisions").covered_facets == ("choice",)
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
dimension_source = Path("app/domain/owner_truth/knowledge_dimension_read.py").read_text(encoding="utf-8")
for required in (
    "missingOwnerConfirmationReceipt",
    "boundContentHash",
    "ownerExplicitSelection",
    "sensitivityNotStandard",
    "inferredEpistemicStatus",
    "non-ready dimension reads must not retain coverage evidence",
):
    assert required in dimension_source, f"missing M0-B dimension read invariant: {required}"
assert "knowledgeDimensionEvidence" not in dimension_source, "inline payload annotations must not count"
print("Owner Truth knowledge recommendation G0 gate passed")
PY
