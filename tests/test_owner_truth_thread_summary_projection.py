from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    ConversationThreadState,
    InterviewBoundary,
    InterviewSessionState,
    OwnerTruthConversationThreadAuthoritySnapshot,
)
from app.domain.owner_truth.knowledge_recommendations import (
    KnowledgeDimension,
    ServerPlannedContinuationCue,
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
from app.services.owner_truth_thread_summary_projection import (
    InMemoryOwnerTruthThreadSummaryProjectionRepository,
    OwnerTruthThreadSummaryProjectionAccessDenied,
    OwnerTruthThreadSummaryProjectionService,
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


class _ConversationRepository:
    def __init__(self, threads: tuple[OwnerTruthConversationThreadAuthoritySnapshot, ...]) -> None:
        self.threads = threads

    def list_recommendation_candidate_thread_authorities(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[OwnerTruthConversationThreadAuthoritySnapshot, ...]:
        del context
        return self.threads


class _CueRepository:
    def __init__(self, cues: tuple[ServerPlannedContinuationCue, ...]) -> None:
        self.cues = cues

    def list_for_recommendation(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[ServerPlannedContinuationCue, ...]:
        del context
        return self.cues


class _Store:
    def __init__(
        self,
        *,
        reader: _ProjectionReader,
        confirmations: InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository,
        conversation: _ConversationRepository,
        cues: _CueRepository,
    ) -> None:
        self.reader = reader
        self.confirmations = confirmations
        self.conversation = conversation
        self.cues = cues
        self.repository = InMemoryOwnerTruthThreadSummaryProjectionRepository(
            memory_projection_repository=reader,
            confirmation_repository=confirmations,
            conversation_repository=conversation,
            continuation_cue_repository=cues,
        )

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_thread_summary_projection_repository(self):
        return self.repository


class OwnerTruthThreadSummaryProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-thread-summary-projection"
        self.vault_id = "vault-thread-summary-projection"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.memory = self._memory("current private claim must never leave the checkpoint")
        self.reader = _ProjectionReader(self._ready_snapshot(self.memory))
        self.confirmations = InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository()
        confirmation_store = type(
            "ConfirmationStore",
            (),
            {
                "owner_truth_memory_projection_repository": lambda _: self.reader,
                "owner_truth_knowledge_dimension_confirmation_repository": lambda _: self.confirmations,
            },
        )()
        OwnerTruthKnowledgeDimensionConfirmationService(
            confirmation_store,
            enabled=True,
        ).confirm(
            context=self.context,
            memory_version_id=self.memory.memory_version_id,
            command=OwnerTruthKnowledgeDimensionConfirmationCommand(
                command_id="thread-summary-projection-confirm-001",
                expected_content_hash=self.memory.content_hash,
                dimension=KnowledgeDimension.KEY_DECISIONS,
                covered_facets=("choice",),
            ),
        )
        first_thread = self._thread()
        second_thread = self._thread()
        conversation = _ConversationRepository((first_thread, second_thread))
        cues = _CueRepository((self._cue(first_thread), self._cue(second_thread)))
        self.store = _Store(
            reader=self.reader,
            confirmations=self.confirmations,
            conversation=conversation,
            cues=cues,
        )
        self.service = OwnerTruthThreadSummaryProjectionService(self.store)

    def _memory(self, claim: str) -> OwnerTruthMemoryProjectionInput:
        content = {"claim": claim}
        return OwnerTruthMemoryProjectionInput(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=7,
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

    def _ready_snapshot(self, memory: OwnerTruthMemoryProjectionInput) -> dict[str, object]:
        return build_ready_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=7,
            inputs=(memory,),
        )

    def _thread(self) -> OwnerTruthConversationThreadAuthoritySnapshot:
        return OwnerTruthConversationThreadAuthoritySnapshot(
            thread_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=7,
            state=ConversationThreadState.ACTIVE,
            session_id=str(uuid4()),
            session_state=InterviewSessionState.ACTIVE,
            session_boundary=InterviewBoundary.OPEN,
        )

    def _cue(
        self,
        thread: OwnerTruthConversationThreadAuthoritySnapshot,
    ) -> ServerPlannedContinuationCue:
        return ServerPlannedContinuationCue(
            cue_id=str(uuid4()),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
            authority_epoch=7,
            thread_id=thread.thread_id,
            session_id=thread.session_id,
            expected_session_version=1,
            memory_version_id=self.memory.memory_version_id,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="reason",
        )

    def test_rebuild_is_idempotent_and_only_exposes_value_free_handles(self) -> None:
        first = self.service.rebuild(context=self.context)
        second = self.service.rebuild(context=self.context)

        self.assertEqual(first.outcome, "rebuilt")
        self.assertEqual(second.outcome, "unchanged")
        assert first.projection is not None
        self.assertEqual(len(first.projection.summaries), 2)
        self.assertEqual(len(first.projection.associations), 1)
        stored = self.service.read(context=self.context)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.checkpoint, first.projection.checkpoint)
        rendered = json.dumps(first.value_free_summary(), ensure_ascii=False)
        self.assertNotIn("current private claim", rendered)
        self.assertNotIn('"claim"', rendered)

    def test_source_change_invalidates_checkpoint_until_explicit_rebuild(self) -> None:
        first = self.service.rebuild(context=self.context)
        assert first.projection is not None
        self.reader.snapshot = self._ready_snapshot(self._memory("replacement current memory"))

        self.assertIsNone(self.service.read(context=self.context))
        rebuilt = self.service.rebuild(context=self.context)

        self.assertEqual(rebuilt.outcome, "rebuilt")
        assert rebuilt.projection is not None
        self.assertNotEqual(rebuilt.projection.checkpoint, first.projection.checkpoint)

    def test_rebuilding_source_never_reuses_checkpoint(self) -> None:
        self.service.rebuild(context=self.context)
        self.reader.snapshot = build_rebuilding_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=7,
        )

        result = self.service.rebuild(context=self.context)

        self.assertEqual(result.outcome, "sourceRebuilding")
        self.assertIsNone(result.projection)
        self.assertIsNone(self.service.read(context=self.context))

    def test_tampered_persisted_checkpoint_fails_closed(self) -> None:
        rebuilt = self.service.rebuild(context=self.context)
        assert rebuilt.projection is not None
        key = (self.vault_id, rebuilt.projection.authority_epoch)
        self.store.repository._projections[key] = replace(
            rebuilt.projection,
            filtered_stale_cue_count=rebuilt.projection.filtered_stale_cue_count + 1,
        )

        self.assertIsNone(self.service.read(context=self.context))

    def test_cross_owner_is_denied_before_projection_access(self) -> None:
        denied = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id="another-owner",
        )

        with self.assertRaises(OwnerTruthThreadSummaryProjectionAccessDenied):
            self.service.rebuild(context=denied)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
