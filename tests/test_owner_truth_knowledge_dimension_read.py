from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.knowledge_dimension_read import (
    OWNER_TRUTH_KNOWLEDGE_DIMENSION_EVIDENCE_SCHEMA_VERSION,
    OwnerTruthKnowledgeDimensionReadError,
    OwnerTruthKnowledgeDimensionReadService,
    OwnerTruthKnowledgeDimensionReadState,
    read_owner_confirmed_dimension_coverage,
)
from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionInput,
    build_ready_memory_projection,
    build_rebuilding_memory_projection,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)
from app.services.owner_truth_memory_projection import (
    InMemoryOwnerTruthMemoryProjectionRepository,
    OwnerTruthMemoryProjectionService,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _ProjectionReader:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot
        self.read_count = 0

    def read(self, *, context: OwnerTruthCommandContext) -> dict[str, object]:
        self.read_count += 1
        return self.snapshot


class _LiveProjectionStore:
    def __init__(self) -> None:
        self.review_repository = InMemoryOwnerTruthCandidateReviewRepository()
        self.projection_repository = InMemoryOwnerTruthMemoryProjectionRepository(
            self.review_repository
        )

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository


class OwnerTruthKnowledgeDimensionReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-dimension-read"
        self.vault_id = "vault-dimension-read"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )

    def _input(
        self,
        *,
        memory_kind: str = "knowledge",
        sensitivity: str = "standard",
        perspective_type: str = "firstPerson",
        epistemic_status: str = "recalled",
        content: dict[str, object] | None = None,
    ) -> OwnerTruthMemoryProjectionInput:
        source_id = str(uuid4())
        return OwnerTruthMemoryProjectionInput(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=3,
            version_number=1,
            source_id=source_id,
            source_version=1,
            memory_kind=memory_kind,
            perspective_type=perspective_type,
            epistemic_status=epistemic_status,
            sensitivity=sensitivity,
            content_schema_version="owner-truth-v1",
            content_hash="hash-dimension-read",
            content=content or {"claim": "I chose to leave my first job."},
            evidence_refs=(
                {
                    "sourceId": source_id,
                    "sourceVersion": 1,
                },
            ),
        )

    def _snapshot(self, *inputs: OwnerTruthMemoryProjectionInput) -> dict[str, object]:
        return build_ready_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=3,
            inputs=inputs,
        )

    @staticmethod
    def _owner_confirmed_annotation() -> dict[str, object]:
        return {
            "schemaVersion": OWNER_TRUTH_KNOWLEDGE_DIMENSION_EVIDENCE_SCHEMA_VERSION,
            "dimension": "keyDecisions",
            "coveredFacets": ["choice", "reason"],
            "classificationConfirmedByOwner": True,
            "isAiInferenceOnly": False,
        }

    def test_owner_confirmed_standard_knowledge_memory_contributes_value_free_coverage(self) -> None:
        memory = self._input(
            content={
                "claim": "I chose a smaller city so I could care for family.",
                "knowledgeDimensionEvidence": self._owner_confirmed_annotation(),
            }
        )
        reader = _ProjectionReader(self._snapshot(memory))

        result = OwnerTruthKnowledgeDimensionReadService(reader).read(context=self.context)

        self.assertEqual(reader.read_count, 1)
        self.assertEqual(result.state, OwnerTruthKnowledgeDimensionReadState.READY)
        self.assertEqual(result.included_memory_version_ids, (memory.memory_version_id,))
        self.assertEqual(result.coverage.for_dimension("keyDecisions").covered_facets, ("choice", "reason"))
        summary = result.value_free_summary()
        self.assertEqual(summary["coverage"]["dimensions"][2]["dimension"], "keyDecisions")
        self.assertNotIn("smaller city", str(summary))
        self.assertNotIn("claim", str(summary))

    def test_real_uuid_memory_and_source_identifiers_are_admissible(self) -> None:
        memory = self._input(
            content={
                "claim": "I chose a path that kept time for family.",
                "knowledgeDimensionEvidence": self._owner_confirmed_annotation(),
            }
        )

        result = read_owner_confirmed_dimension_coverage(
            memory_projection=self._snapshot(memory),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
        )

        self.assertEqual(result.included_memory_version_ids, (memory.memory_version_id,))

    def test_service_reads_a_real_current_memory_version_checkpoint(self) -> None:
        store = _LiveProjectionStore()
        review_service = OwnerTruthCandidateReviewService(store)
        projection_service = OwnerTruthMemoryProjectionService(store)
        source_id = str(uuid4())
        content = {
            "claim": "I chose work close to home.",
            "knowledgeDimensionEvidence": self._owner_confirmed_annotation(),
        }
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.KNOWLEDGE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "schemaVersion": "owner-truth-candidate-proposal-v1",
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "content": content,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
            },
        )
        store.review_repository.seed(candidate)
        review_service.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="dimension-read-real-memory-accept",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )
        projection_service.rebuild(context=self.context)

        result = OwnerTruthKnowledgeDimensionReadService(projection_service).read(
            context=self.context
        )

        self.assertEqual(result.state, OwnerTruthKnowledgeDimensionReadState.READY)
        self.assertEqual(len(result.included_memory_version_ids), 1)
        self.assertEqual(result.coverage.for_dimension("keyDecisions").covered_facets, ("choice", "reason"))

    def test_missing_or_unsafe_annotations_are_excluded_without_guessing_from_memory_text(self) -> None:
        accepted = self._input(
            content={
                "claim": "I selected work that kept weekends free.",
                "knowledgeDimensionEvidence": self._owner_confirmed_annotation(),
            }
        )
        missing = self._input()
        ai_only = self._input(
            content={
                "claim": "A model guessed I value stability.",
                "knowledgeDimensionEvidence": {
                    **self._owner_confirmed_annotation(),
                    "isAiInferenceOnly": True,
                },
            }
        )
        sensitive = self._input(
            sensitivity="sensitive",
            content={
                "claim": "Sensitive context.",
                "knowledgeDimensionEvidence": self._owner_confirmed_annotation(),
            },
        )
        inferred = self._input(
            epistemic_status="inferred",
            content={
                "claim": "Inferred context.",
                "knowledgeDimensionEvidence": self._owner_confirmed_annotation(),
            },
        )

        result = read_owner_confirmed_dimension_coverage(
            memory_projection=self._snapshot(accepted, missing, ai_only, sensitive, inferred),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
        )

        self.assertEqual(result.included_memory_version_ids, (accepted.memory_version_id,))
        self.assertEqual(
            {item.memory_version_id: item.reason_code for item in result.exclusions},
            {
                missing.memory_version_id: "missingDimensionEvidence",
                ai_only.memory_version_id: "aiInferenceOnly",
                sensitive.memory_version_id: "sensitivityNotStandard",
                inferred.memory_version_id: "inferredEpistemicStatus",
            },
        )
        self.assertEqual(result.coverage.excluded_evidence_count, 0)

    def test_invalid_facet_and_unconfirmed_annotation_cannot_increase_coverage(self) -> None:
        invalid = self._input(
            content={
                "claim": "Invalid metadata.",
                "knowledgeDimensionEvidence": {
                    **self._owner_confirmed_annotation(),
                    "coveredFacets": ["notAStableFacet"],
                },
            }
        )
        unconfirmed = self._input(
            content={
                "claim": "Unconfirmed metadata.",
                "knowledgeDimensionEvidence": {
                    **self._owner_confirmed_annotation(),
                    "classificationConfirmedByOwner": False,
                },
            }
        )

        result = read_owner_confirmed_dimension_coverage(
            memory_projection=self._snapshot(invalid, unconfirmed),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
        )

        self.assertEqual(result.included_memory_version_ids, ())
        self.assertEqual(
            {item.memory_version_id: item.reason_code for item in result.exclusions},
            {
                invalid.memory_version_id: "invalidDimensionEvidence",
                unconfirmed.memory_version_id: "dimensionClassificationNotOwnerConfirmed",
            },
        )
        self.assertEqual(
            result.coverage.for_dimension("keyDecisions").covered_facets,
            (),
        )

    def test_rebuilding_or_malformed_snapshot_never_returns_partial_coverage(self) -> None:
        rebuilding = build_rebuilding_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=3,
        )
        rebuilding_result = read_owner_confirmed_dimension_coverage(
            memory_projection=rebuilding,
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
        )
        self.assertEqual(rebuilding_result.state, OwnerTruthKnowledgeDimensionReadState.REBUILDING)
        self.assertIsNone(rebuilding_result.coverage)

        malformed = {
            "state": "ready",
            "ownerSubjectId": self.owner_id,
            "vaultId": self.vault_id,
            "authorityEpoch": 3,
            "checkpoint": "checkpoint",
            "entries": [{"citation": {"memoryVersionId": str(uuid4())}}],
        }
        unavailable = read_owner_confirmed_dimension_coverage(
            memory_projection=malformed,
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
        )
        self.assertEqual(unavailable.state, OwnerTruthKnowledgeDimensionReadState.UNAVAILABLE)
        self.assertIsNone(unavailable.coverage)
        self.assertEqual(unavailable.included_memory_version_ids, ())

    def test_cross_owner_or_vault_snapshot_is_rejected_before_any_read_result(self) -> None:
        snapshot = self._snapshot(self._input())
        with self.assertRaisesRegex(OwnerTruthKnowledgeDimensionReadError, "scope"):
            read_owner_confirmed_dimension_coverage(
                memory_projection=snapshot,
                owner_subject_id="other-owner",
                vault_id=self.vault_id,
            )


if __name__ == "__main__":
    unittest.main()
