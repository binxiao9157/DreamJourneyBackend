from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.knowledge_dimension_read import (
    OwnerTruthKnowledgeDimensionReadState,
)
from app.domain.owner_truth.knowledge_recommendations import (
    KnowledgeDimension,
    RecommendationCandidate,
    RecommendationEvidenceKind,
    RecommendationSlot,
)
from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionInput,
    build_ready_memory_projection,
    build_rebuilding_memory_projection,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_knowledge_dimension_confirmation import (
    InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository,
    OwnerTruthKnowledgeDimensionConfirmationCommand,
    OwnerTruthKnowledgeDimensionConfirmationService,
)
from app.services.owner_truth_knowledge_recommendation_read import (
    OwnerTruthKnowledgeRecommendationReadError,
    OwnerTruthKnowledgeRecommendationReadService,
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
        del context
        self.read_count += 1
        return self.snapshot


class _Store:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.reader = _ProjectionReader(snapshot)
        self.repository = InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield object()

    def owner_truth_memory_projection_repository(self):
        return self.reader

    def owner_truth_knowledge_dimension_confirmation_repository(self):
        return self.repository


class OwnerTruthKnowledgeRecommendationReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-recommendation-read"
        self.vault_id = "vault-recommendation-read"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.content = {"claim": "I chose to preserve weekday evenings for my family."}
        self.memory = OwnerTruthMemoryProjectionInput(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=5,
            version_number=1,
            source_id=str(uuid4()),
            source_version=1,
            memory_kind="knowledge",
            perspective_type="firstPerson",
            epistemic_status="recalled",
            sensitivity="standard",
            content_schema_version="owner-truth-v1",
            content_hash=_hash(self.content),
            content=self.content,
            evidence_refs=({"sourceId": str(uuid4()), "sourceVersion": 1},),
        )
        self.values_content = {"claim": "I value leaving time for reflection before major commitments."}
        self.values_memory = OwnerTruthMemoryProjectionInput(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=5,
            version_number=1,
            source_id=str(uuid4()),
            source_version=1,
            memory_kind="knowledge",
            perspective_type="firstPerson",
            epistemic_status="recalled",
            sensitivity="standard",
            content_schema_version="owner-truth-v1",
            content_hash=_hash(self.values_content),
            content=self.values_content,
            evidence_refs=({"sourceId": str(uuid4()), "sourceVersion": 1},),
        )

    def _store(self, *, rebuilding: bool = False) -> _Store:
        if rebuilding:
            snapshot = build_rebuilding_memory_projection(
                vault_id=self.vault_id,
                owner_subject_id=self.owner_id,
                authority_epoch=5,
            )
        else:
            snapshot = build_ready_memory_projection(
                vault_id=self.vault_id,
                owner_subject_id=self.owner_id,
                authority_epoch=5,
                inputs=(self.memory, self.values_memory),
            )
        return _Store(snapshot)

    def _confirm(self, store: _Store) -> None:
        result = OwnerTruthKnowledgeDimensionConfirmationService(store, enabled=True).confirm(
            context=self.context,
            memory_version_id=self.memory.memory_version_id,
            command=OwnerTruthKnowledgeDimensionConfirmationCommand(
                command_id="recommendation-read-confirm-001",
                expected_content_hash=self.memory.content_hash,
                dimension="keyDecisions",
                covered_facets=("choice", "reason"),
            ),
        )
        self.assertEqual(result.outcome, "created")
        values_result = OwnerTruthKnowledgeDimensionConfirmationService(store, enabled=True).confirm(
            context=self.context,
            memory_version_id=self.values_memory.memory_version_id,
            command=OwnerTruthKnowledgeDimensionConfirmationCommand(
                command_id="recommendation-read-confirm-002",
                expected_content_hash=self.values_memory.content_hash,
                dimension="values",
                covered_facets=("priority",),
            ),
        )
        self.assertEqual(values_result.outcome, "created")

    def _candidate(self, **overrides: object) -> RecommendationCandidate:
        values: dict[str, object] = {
            "candidate_id": "recommendation-continuity",
            "owner_subject_id": self.owner_id,
            "vault_id": self.vault_id,
            "slot": RecommendationSlot.CONTINUITY,
            "thread_id": "thread-recommendation-read",
            "target_dimension": KnowledgeDimension.KEY_DECISIONS,
            "missing_facet": "outcome",
            "question_template_id": "continue-key-decision",
            "evidence_kind": RecommendationEvidenceKind.CONFIRMED_MEMORY,
            "evidence_refs": (self.memory.memory_version_id,),
            "reason_code": "qaConfirmedMemory",
        }
        values.update(overrides)
        return RecommendationCandidate(**values)

    def test_reads_current_owner_confirmed_coverage_and_returns_value_free_selection(self) -> None:
        store = self._store()
        self._confirm(store)
        result = OwnerTruthKnowledgeRecommendationReadService(store).read(
            context=self.context,
            candidates=(
                self._candidate(),
                self._candidate(
                    candidate_id="recommendation-breadth",
                    slot=RecommendationSlot.BREADTH,
                    thread_id="thread-recommendation-breadth",
                    target_dimension=KnowledgeDimension.VALUES,
                    missing_facet="reflection",
                    question_template_id="broaden-values",
                    evidence_refs=(self.values_memory.memory_version_id,),
                ),
            ),
        )

        self.assertEqual(result.state, OwnerTruthKnowledgeDimensionReadState.READY)
        self.assertIsNotNone(result.selection)
        assert result.selection is not None
        self.assertEqual([item.slot for item in result.selection.selected], [
            RecommendationSlot.CONTINUITY,
            RecommendationSlot.BREADTH,
        ])
        summary = result.value_free_summary()
        rendered = str(summary)
        self.assertNotIn("weekday evenings", rendered)
        self.assertNotIn("claim", rendered)
        self.assertEqual(summary["selectionState"], "ready")
        self.assertEqual(store.reader.read_count, 3)

    def test_rejects_unconfirmed_or_unbound_candidate_evidence(self) -> None:
        store = self._store()
        service = OwnerTruthKnowledgeRecommendationReadService(store)

        with self.assertRaisesRegex(
            OwnerTruthKnowledgeRecommendationReadError,
            "current owner-confirmed MemoryVersion",
        ):
            service.read(context=self.context, candidates=(self._candidate(),))

        self._confirm(store)
        with self.assertRaisesRegex(
            OwnerTruthKnowledgeRecommendationReadError,
            "current owner-confirmed MemoryVersion",
        ):
            service.read(
                context=self.context,
                candidates=(self._candidate(evidence_refs=(str(uuid4()),)),),
            )

    def test_non_ready_projection_returns_empty_selection_without_evaluating_candidates(self) -> None:
        store = self._store(rebuilding=True)
        result = OwnerTruthKnowledgeRecommendationReadService(store).read(
            context=self.context,
            candidates=(self._candidate(evidence_refs=(str(uuid4()),)),),
        )

        self.assertEqual(result.state, OwnerTruthKnowledgeDimensionReadState.REBUILDING)
        self.assertIsNone(result.selection)
        self.assertEqual(result.value_free_summary()["selected"], [])
        self.assertEqual(result.value_free_summary()["filtered"], [])

    def test_rejects_non_confirmed_memory_evidence_kinds(self) -> None:
        store = self._store()
        self._confirm(store)

        with self.assertRaisesRegex(
            OwnerTruthKnowledgeRecommendationReadError,
            "confirmedMemory",
        ):
            OwnerTruthKnowledgeRecommendationReadService(store).read(
                context=self.context,
                candidates=(
                    self._candidate(evidence_kind=RecommendationEvidenceKind.SAVED_CONTINUATION),
                ),
            )

    def test_rejects_confirmed_memory_from_a_different_dimension(self) -> None:
        store = self._store()
        self._confirm(store)

        with self.assertRaisesRegex(
            OwnerTruthKnowledgeRecommendationReadError,
            "target knowledge dimension",
        ):
            OwnerTruthKnowledgeRecommendationReadService(store).read(
                context=self.context,
                candidates=(
                    self._candidate(
                        evidence_refs=(self.values_memory.memory_version_id,),
                    ),
                ),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
