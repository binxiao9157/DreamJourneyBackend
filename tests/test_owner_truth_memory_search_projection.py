from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionInput,
    build_rebuilding_memory_projection,
    build_ready_memory_projection,
)
from app.domain.owner_truth.search_documents import (
    OwnerTruthSearchDocumentProjectionError,
    OwnerTruthSearchDocumentProjectionRebuildResult,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_search_projection import (
    InMemoryOwnerTruthMemorySearchDocumentProjectionRepository,
    OwnerTruthMemorySearchDocumentProjectionService,
    OwnerTruthMemorySearchProjectionAccessDenied,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _MemoryProjectionReader:
    def __init__(self, snapshot: dict[str, object]) -> None:
        self.snapshot = snapshot

    def read(self, *, context: OwnerTruthCommandContext) -> dict[str, object]:
        del context
        return self.snapshot


class _Store:
    def __init__(self, source: _MemoryProjectionReader) -> None:
        self.repository = InMemoryOwnerTruthMemorySearchDocumentProjectionRepository(source)

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_memory_search_document_projection_repository(self):
        return self.repository


class OwnerTruthMemorySearchProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-memory-search-projection"
        self.vault_id = "vault-memory-search-projection"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.source = _MemoryProjectionReader(self._ready_snapshot("first indexed memory"))
        self.store = _Store(self.source)
        self.service = OwnerTruthMemorySearchDocumentProjectionService(self.store)

    def _ready_snapshot(self, claim: str) -> dict[str, object]:
        content = {"claim": claim, "tags": ["memory-search", "private"]}
        memory = OwnerTruthMemoryProjectionInput(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=3,
            version_number=1,
            source_id=str(uuid4()),
            source_version=1,
            memory_kind="knowledge",
            perspective_type="firstPerson",
            epistemic_status="recalled",
            sensitivity="standard",
            content_schema_version="owner-truth-v1",
            content_hash=_hash(content),
            content=content,
            evidence_refs=({"sourceId": str(uuid4()), "sourceVersion": 1},),
        )
        return build_ready_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=3,
            inputs=(memory,),
        )

    def test_rebuild_persists_private_index_and_idempotently_reuses_same_checkpoint(self) -> None:
        first = self.service.rebuild(context=self.context)
        second = self.service.rebuild(context=self.context)

        self.assertEqual(first.outcome, "rebuilt")
        self.assertEqual(second.outcome, "unchanged")
        self.assertIsNotNone(first.projection)
        stored = self.store.owner_truth_memory_search_document_projection_repository().read(
            context=self.context
        )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.checkpoint, first.projection.checkpoint)
        rendered = json.dumps(first.value_free_summary(), ensure_ascii=False)
        self.assertNotIn("first indexed memory", rendered)
        self.assertNotIn('"searchText":', rendered)
        self.assertNotIn("structuredTerms", rendered)

    def test_source_checkpoint_change_invalidates_old_index_until_explicit_rebuild(self) -> None:
        first = self.service.rebuild(context=self.context)
        assert first.projection is not None
        self.source.snapshot = self._ready_snapshot("replacement indexed memory")

        stale = self.store.owner_truth_memory_search_document_projection_repository().read(
            context=self.context
        )
        self.assertIsNone(stale)
        rebuilt = self.service.rebuild(context=self.context)
        self.assertEqual(rebuilt.outcome, "rebuilt")
        self.assertIsNotNone(rebuilt.projection)
        assert rebuilt.projection is not None
        self.assertNotEqual(rebuilt.projection.checkpoint, first.projection.checkpoint)

    def test_rebuilding_source_never_reuses_a_previous_index(self) -> None:
        self.service.rebuild(context=self.context)
        self.source.snapshot = build_rebuilding_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=3,
        )

        result = self.service.rebuild(context=self.context)

        self.assertEqual(result.outcome, "sourceRebuilding")
        self.assertIsNone(result.projection)
        self.assertIsNone(
            self.store.owner_truth_memory_search_document_projection_repository().read(
                context=self.context
            )
        )

    def test_cross_owner_rebuild_is_denied_before_accessing_private_projection(self) -> None:
        denied = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id="different-owner",
        )
        with self.assertRaises(OwnerTruthMemorySearchProjectionAccessDenied):
            self.service.rebuild(context=denied)

    def test_result_rejects_unsupported_outcome(self) -> None:
        with self.assertRaises(OwnerTruthSearchDocumentProjectionError):
            OwnerTruthSearchDocumentProjectionRebuildResult(
                outcome="ready",
                projection=None,
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
