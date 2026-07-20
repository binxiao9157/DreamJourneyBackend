from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionInput,
    build_ready_memory_projection,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_knowledge_dimension_confirmation import (
    InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository,
    OwnerTruthKnowledgeDimensionConfirmationConflict,
    OwnerTruthKnowledgeDimensionConfirmationCommand,
    OwnerTruthKnowledgeDimensionConfirmationService,
    OwnerTruthKnowledgeDimensionConfirmationStaleMemory,
    OwnerTruthKnowledgeDimensionConfirmationUnavailable,
    PostgresOwnerTruthKnowledgeDimensionConfirmationRepository,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _ProjectionReader:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot

    def read(self, *, context: OwnerTruthCommandContext) -> dict[str, object]:
        del context
        return self.snapshot


class _Store:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.reader = _ProjectionReader(snapshot)
        self.repository = InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository()

    def owner_truth_memory_projection_repository(self):
        return self.reader

    def owner_truth_knowledge_dimension_confirmation_repository(self):
        return self.repository


class OwnerTruthKnowledgeDimensionConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = "owner-confirmation"
        self.vault = "vault-confirmation"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault,
            owner_subject_id=self.owner,
            actor_subject_id=self.owner,
        )
        self.content = {"claim": "I chose to keep evenings free for my family."}
        self.content_hash = _hash(self.content)
        source_id = str(uuid4())
        self.memory = OwnerTruthMemoryProjectionInput(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            vault_id=self.vault,
            owner_subject_id=self.owner,
            authority_epoch=4,
            version_number=1,
            source_id=source_id,
            source_version=1,
            memory_kind="knowledge",
            perspective_type="firstPerson",
            epistemic_status="recalled",
            sensitivity="standard",
            content_schema_version="owner-truth-v1",
            content_hash=self.content_hash,
            content=self.content,
            evidence_refs=({"sourceId": source_id, "sourceVersion": 1},),
        )
        self.store = _Store(
            build_ready_memory_projection(
                vault_id=self.vault,
                owner_subject_id=self.owner,
                authority_epoch=4,
                inputs=(self.memory,),
            )
        )

    def _command(self, **overrides: object) -> OwnerTruthKnowledgeDimensionConfirmationCommand:
        values: dict[str, object] = {
            "command_id": "dimension-confirm-001",
            "expected_content_hash": self.content_hash,
            "dimension": "keyDecisions",
            "covered_facets": ("choice", "reason"),
        }
        values.update(overrides)
        return OwnerTruthKnowledgeDimensionConfirmationCommand(**values)

    def test_confirmation_is_default_off(self) -> None:
        with self.assertRaises(OwnerTruthKnowledgeDimensionConfirmationUnavailable):
            OwnerTruthKnowledgeDimensionConfirmationService(self.store).confirm(
                context=self.context,
                memory_version_id=self.memory.memory_version_id,
                command=self._command(),
            )

    def test_confirmation_is_append_only_hash_bound_and_idempotent(self) -> None:
        service = OwnerTruthKnowledgeDimensionConfirmationService(self.store, enabled=True)
        first = service.confirm(
            context=self.context,
            memory_version_id=self.memory.memory_version_id,
            command=self._command(),
        )
        replay = service.confirm(
            context=self.context,
            memory_version_id=self.memory.memory_version_id,
            command=self._command(),
        )

        self.assertEqual(first.outcome, "created")
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(first.confirmation_id, replay.confirmation_id)
        snapshot = self.store.repository.snapshot()
        self.assertEqual(len(snapshot["records"]), 1)
        self.assertNotIn("evenings free", str(snapshot))
        self.assertNotIn("claim", str(snapshot))

    def test_same_command_with_different_selection_conflicts(self) -> None:
        service = OwnerTruthKnowledgeDimensionConfirmationService(self.store, enabled=True)
        service.confirm(
            context=self.context,
            memory_version_id=self.memory.memory_version_id,
            command=self._command(),
        )

        with self.assertRaises(OwnerTruthKnowledgeDimensionConfirmationConflict):
            service.confirm(
                context=self.context,
                memory_version_id=self.memory.memory_version_id,
                command=self._command(covered_facets=("choice", "outcome")),
            )

    def test_same_dimension_cannot_be_rewritten_with_a_second_command(self) -> None:
        service = OwnerTruthKnowledgeDimensionConfirmationService(self.store, enabled=True)
        service.confirm(
            context=self.context,
            memory_version_id=self.memory.memory_version_id,
            command=self._command(),
        )

        with self.assertRaises(OwnerTruthKnowledgeDimensionConfirmationConflict):
            service.confirm(
                context=self.context,
                memory_version_id=self.memory.memory_version_id,
                command=self._command(command_id="dimension-confirm-002"),
            )

    def test_hash_mismatch_or_ineligible_memory_fails_before_receipt_write(self) -> None:
        service = OwnerTruthKnowledgeDimensionConfirmationService(self.store, enabled=True)
        with self.assertRaises(OwnerTruthKnowledgeDimensionConfirmationStaleMemory):
            service.confirm(
                context=self.context,
                memory_version_id=self.memory.memory_version_id,
                command=self._command(expected_content_hash=_hash("old-version")),
            )
        self.assertEqual(self.store.repository.snapshot()["records"], [])

    def test_invalid_dimension_and_facets_are_rejected(self) -> None:
        with self.assertRaisesRegex(Exception, "not supported"):
            self._command(dimension="inventedDimension")
        with self.assertRaisesRegex(Exception, "unsupported"):
            self._command(covered_facets=("inventedFacet",))

    def test_postgres_projection_record_retains_vault_scope(self) -> None:
        record = PostgresOwnerTruthKnowledgeDimensionConfirmationRepository._row_to_record(
            {
                "vault_id": self.vault,
                "id": str(uuid4()),
                "command_id_hash": _hash("command"),
                "command_payload_hash": _hash("payload"),
                "memory_id": self.memory.memory_id,
                "memory_version_id": self.memory.memory_version_id,
                "bound_content_hash": self.content_hash,
                "owner_subject_id": self.owner,
                "actor_subject_id": self.owner,
                "authority_epoch": 4,
                "dimension": "keyDecisions",
                "covered_facets": ["choice", "reason"],
                "confirmation_method": "ownerExplicitSelection",
                "schema_version": "owner-truth-knowledge-dimension-confirmation-v1",
                "ui_schema_version": "knowledge-dimension-review-v1",
            }
        )

        self.assertEqual(record["vaultId"], self.vault)

    def test_postgres_projection_query_selects_and_returns_vault_scope(self) -> None:
        row = {
            "vault_id": self.vault,
            "id": str(uuid4()),
            "command_id_hash": _hash("command"),
            "command_payload_hash": _hash("payload"),
            "memory_id": self.memory.memory_id,
            "memory_version_id": self.memory.memory_version_id,
            "bound_content_hash": self.content_hash,
            "owner_subject_id": self.owner,
            "actor_subject_id": self.owner,
            "authority_epoch": 4,
            "dimension": "keyDecisions",
            "covered_facets": ["choice", "reason"],
            "confirmation_method": "ownerExplicitSelection",
            "schema_version": "owner-truth-knowledge-dimension-confirmation-v1",
            "ui_schema_version": "knowledge-dimension-review-v1",
        }

        class Cursor:
            def __init__(self) -> None:
                self.executions: list[str] = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                return None

            def execute(self, query, params) -> None:
                del params
                self.executions.append(str(query))

            def fetchone(self):
                return {
                    "owner_subject_id": self_owner,
                    "authority_epoch": 4,
                    "status": "active",
                }

            def fetchall(self):
                return [row]

        class Connection:
            def __init__(self, cursor: Cursor) -> None:
                self._cursor = cursor

            def cursor(self, *, row_factory=None):
                del row_factory
                return self._cursor

        self_owner = self.owner
        cursor = Cursor()
        records = PostgresOwnerTruthKnowledgeDimensionConfirmationRepository(
            Connection(cursor)
        ).list_for_projection(
            context=self.context,
            memory_version_ids=(self.memory.memory_version_id,),
        )

        self.assertEqual(records[0]["vaultId"], self.vault)
        self.assertIn("SELECT vault_id, id", cursor.executions[-1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
