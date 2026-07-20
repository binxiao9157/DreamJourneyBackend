from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.knowledge_dimension_read import (
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
from app.services.owner_truth_knowledge_dimension_confirmation import (
    InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository,
    OwnerTruthKnowledgeDimensionConfirmationCommand,
    OwnerTruthKnowledgeDimensionConfirmationService,
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


class _ConfirmationStore:
    def __init__(self, reader: _ProjectionReader) -> None:
        self.reader = reader
        self.repository = InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository()

    def owner_truth_memory_projection_repository(self):
        return self.reader

    def owner_truth_knowledge_dimension_confirmation_repository(self):
        return self.repository


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
        content = content or {"claim": "I chose to leave my first job."}
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
            content_hash=_hash(content),
            content=content,
            evidence_refs=({"sourceId": source_id, "sourceVersion": 1},),
        )

    def _snapshot(self, *inputs: OwnerTruthMemoryProjectionInput) -> dict[str, object]:
        return build_ready_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=3,
            inputs=inputs,
        )

    def _receipt(
        self,
        memory: OwnerTruthMemoryProjectionInput,
        *,
        dimension: str = "keyDecisions",
        facets: tuple[str, ...] = ("choice", "reason"),
        content_hash: str | None = None,
    ) -> dict[str, object]:
        return {
            "confirmationId": str(uuid4()),
            "commandIdHash": _hash("dimension-confirm-command"),
            "payloadHash": _hash({"dimension": dimension, "facets": facets}),
            "vaultId": self.vault_id,
            "ownerSubjectId": self.owner_id,
            "actorSubjectId": self.owner_id,
            "authorityEpoch": 3,
            "memoryId": memory.memory_id,
            "memoryVersionId": memory.memory_version_id,
            "boundContentHash": content_hash or memory.content_hash,
            "dimension": dimension,
            "coveredFacets": list(facets),
            "confirmationMethod": "ownerExplicitSelection",
            "schemaVersion": "owner-truth-knowledge-dimension-confirmation-v1",
            "uiSchemaVersion": "knowledge-dimension-review-v1",
        }

    def test_inline_annotation_is_ignored_without_a_separate_receipt(self) -> None:
        memory = self._input(
            content={
                "claim": "I chose a smaller city so I could care for family.",
                "knowledgeDimensionEvidence": {
                    "schemaVersion": "owner-truth-knowledge-dimension-evidence-v1",
                    "dimension": "keyDecisions",
                    "coveredFacets": ["choice", "reason"],
                    "classificationConfirmedByOwner": True,
                    "isAiInferenceOnly": False,
                },
            }
        )

        result = read_owner_confirmed_dimension_coverage(
            memory_projection=self._snapshot(memory),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
        )

        self.assertEqual(result.state, OwnerTruthKnowledgeDimensionReadState.READY)
        self.assertEqual(result.included_memory_version_ids, ())
        self.assertEqual(result.coverage.for_dimension("keyDecisions").covered_facets, ())
        self.assertEqual(result.exclusions[0].reason_code, "missingOwnerConfirmationReceipt")
        self.assertNotIn("smaller city", str(result.value_free_summary()))

    def test_matching_explicit_receipt_contributes_value_free_coverage(self) -> None:
        memory = self._input()

        result = read_owner_confirmed_dimension_coverage(
            memory_projection=self._snapshot(memory),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
            confirmations=(self._receipt(memory),),
        )

        self.assertEqual(result.state, OwnerTruthKnowledgeDimensionReadState.READY)
        self.assertEqual(result.included_memory_version_ids, (memory.memory_version_id,))
        self.assertEqual(
            result.coverage.for_dimension("keyDecisions").covered_facets,
            ("choice", "reason"),
        )
        self.assertNotEqual(result.checkpoint, result.memory_projection_checkpoint)
        self.assertNotIn("claim", str(result.value_free_summary()))

    def test_receipt_hash_mismatch_and_old_version_do_not_contribute(self) -> None:
        current = self._input()
        old = self._input()
        stale_hash = self._receipt(current, content_hash=_hash("old-content"))
        old_version = self._receipt(old)

        result = read_owner_confirmed_dimension_coverage(
            memory_projection=self._snapshot(current),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
            confirmations=(stale_hash, old_version),
        )

        self.assertEqual(result.included_memory_version_ids, ())
        self.assertEqual(result.exclusions[0].reason_code, "invalidOwnerConfirmationReceipt")

    def test_unsafe_current_memory_does_not_contribute_even_with_receipt(self) -> None:
        memory = self._input(sensitivity="sensitive")

        result = read_owner_confirmed_dimension_coverage(
            memory_projection=self._snapshot(memory),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
            confirmations=(self._receipt(memory),),
        )

        self.assertEqual(result.included_memory_version_ids, ())
        self.assertEqual(result.exclusions[0].reason_code, "sensitivityNotStandard")

    def test_service_reads_repository_receipts_and_rejects_missing_receipt_by_default(self) -> None:
        memory = self._input()
        reader = _ProjectionReader(self._snapshot(memory))
        store = _ConfirmationStore(reader)

        empty = OwnerTruthKnowledgeDimensionReadService(reader).read(context=self.context)
        self.assertEqual(empty.included_memory_version_ids, ())

        created = OwnerTruthKnowledgeDimensionConfirmationService(store, enabled=True).confirm(
            context=self.context,
            memory_version_id=memory.memory_version_id,
            command=OwnerTruthKnowledgeDimensionConfirmationCommand(
                command_id="dimension-read-confirm-001",
                expected_content_hash=memory.content_hash,
                dimension="keyDecisions",
                covered_facets=("choice", "reason"),
            ),
        )
        self.assertEqual(created.outcome, "created")

        result = OwnerTruthKnowledgeDimensionReadService(
            reader,
            store.owner_truth_knowledge_dimension_confirmation_repository(),
        ).read(context=self.context)

        self.assertEqual(reader.read_count, 3)
        self.assertEqual(result.included_memory_version_ids, (memory.memory_version_id,))
        persisted = store.repository.snapshot()
        self.assertNotIn("I chose", str(persisted))
        self.assertNotIn("claim", str(persisted))

    def test_rebuilding_projection_remains_fail_closed(self) -> None:
        result = read_owner_confirmed_dimension_coverage(
            memory_projection=build_rebuilding_memory_projection(
                vault_id=self.vault_id,
                owner_subject_id=self.owner_id,
                authority_epoch=3,
            ),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
            confirmations=(),
        )

        self.assertEqual(result.state, OwnerTruthKnowledgeDimensionReadState.REBUILDING)
        self.assertIsNone(result.coverage)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
