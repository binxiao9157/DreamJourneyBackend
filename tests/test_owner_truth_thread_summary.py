from __future__ import annotations

from contextlib import contextmanager
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
from app.domain.owner_truth.knowledge_dimension_read import (
    OwnerTruthKnowledgeDimensionReadState,
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
from app.domain.owner_truth.thread_summary import (
    OwnerTruthThreadSummaryError,
    build_owner_truth_thread_summary_projection,
)
from app.services.owner_truth_knowledge_dimension_confirmation import (
    InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository,
    OwnerTruthKnowledgeDimensionConfirmationCommand,
    OwnerTruthKnowledgeDimensionConfirmationService,
)
from app.services.owner_truth_thread_summary_read import OwnerTruthThreadSummaryReadService


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
        threads: tuple[OwnerTruthConversationThreadAuthoritySnapshot, ...],
        cues: tuple[ServerPlannedContinuationCue, ...],
    ) -> None:
        self.reader = reader
        self.confirmations = confirmations
        self.conversation = _ConversationRepository(threads)
        self.cue_repository = _CueRepository(cues)

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_memory_projection_repository(self):
        return self.reader

    def owner_truth_knowledge_dimension_confirmation_repository(self):
        return self.confirmations

    def owner_truth_conversation_repository(self):
        return self.conversation

    def owner_truth_saved_continuation_cue_repository(self):
        return self.cue_repository


class OwnerTruthThreadSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-thread-summary"
        self.vault_id = "vault-thread-summary"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.memory = OwnerTruthMemoryProjectionInput(
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
            content_hash=_hash({"claim": "private thread summary must not leak this text"}),
            content={"claim": "private thread summary must not leak this text"},
            evidence_refs=({"sourceId": str(uuid4()), "sourceVersion": 1},),
        )
        self.snapshot = build_ready_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=7,
            inputs=(self.memory,),
        )

    def _thread(
        self,
        *,
        paused_cooldown: bool = False,
    ) -> OwnerTruthConversationThreadAuthoritySnapshot:
        return OwnerTruthConversationThreadAuthoritySnapshot(
            thread_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=7,
            state=ConversationThreadState.ACTIVE,
            session_id=str(uuid4()),
            session_state=(InterviewSessionState.PAUSED if paused_cooldown else InterviewSessionState.ACTIVE),
            session_boundary=(InterviewBoundary.COOLDOWN if paused_cooldown else InterviewBoundary.OPEN),
        )

    def _cue(
        self,
        thread: OwnerTruthConversationThreadAuthoritySnapshot,
        *,
        memory_version_id: str | None = None,
    ) -> ServerPlannedContinuationCue:
        return ServerPlannedContinuationCue(
            cue_id=str(uuid4()),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
            authority_epoch=7,
            thread_id=thread.thread_id,
            session_id=thread.session_id,
            expected_session_version=1,
            memory_version_id=memory_version_id or self.memory.memory_version_id,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="reason",
        )

    def _store(
        self,
        *,
        threads: tuple[OwnerTruthConversationThreadAuthoritySnapshot, ...],
        cues: tuple[ServerPlannedContinuationCue, ...],
        snapshot: dict[str, object] | None = None,
    ) -> _Store:
        reader = _ProjectionReader(snapshot or self.snapshot)
        confirmations = InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository()
        confirmation_store = type(
            "ConfirmationStore",
            (),
            {
                "owner_truth_memory_projection_repository": lambda _: reader,
                "owner_truth_knowledge_dimension_confirmation_repository": lambda _: confirmations,
            },
        )()
        if str(reader.snapshot.get("state") or "") == "ready":
            OwnerTruthKnowledgeDimensionConfirmationService(confirmation_store, enabled=True).confirm(
                context=self.context,
                memory_version_id=self.memory.memory_version_id,
                command=OwnerTruthKnowledgeDimensionConfirmationCommand(
                    command_id="thread-summary-confirmation",
                    expected_content_hash=self.memory.content_hash,
                    dimension=KnowledgeDimension.KEY_DECISIONS,
                    covered_facets=("choice",),
                ),
            )
        return _Store(reader=reader, confirmations=confirmations, threads=threads, cues=cues)

    def test_associates_only_threads_that_share_current_confirmed_memory(self) -> None:
        active = self._thread()
        cooldown = self._thread(paused_cooldown=True)
        store = self._store(
            threads=(active, cooldown),
            cues=(self._cue(active), self._cue(cooldown)),
        )

        result = OwnerTruthThreadSummaryReadService(store).read(context=self.context)

        self.assertEqual(result.state, OwnerTruthKnowledgeDimensionReadState.READY)
        assert result.projection is not None
        self.assertEqual(len(result.projection.summaries), 2)
        self.assertEqual(len(result.projection.associations), 1)
        association = result.projection.associations[0]
        self.assertEqual(association.thread_ids, tuple(sorted((active.thread_id, cooldown.thread_id))))
        self.assertEqual(association.reason_code, "sharedConfirmedMemoryVersion")
        rendered = str(result.value_free_summary())
        self.assertNotIn("private thread summary", rendered)
        self.assertNotIn("claim", rendered)

    def test_stale_cue_is_filtered_without_creating_an_association(self) -> None:
        active = self._thread()
        cooldown = self._thread(paused_cooldown=True)
        store = self._store(
            threads=(active, cooldown),
            cues=(self._cue(active), self._cue(cooldown, memory_version_id=str(uuid4()))),
        )

        result = OwnerTruthThreadSummaryReadService(store).read(context=self.context)

        assert result.projection is not None
        self.assertEqual(result.projection.filtered_stale_cue_count, 1)
        self.assertEqual(result.projection.associations, ())
        active_summary = next(
            item for item in result.projection.summaries if item.thread_id == active.thread_id
        )
        self.assertEqual(active_summary.anchors[0].memory_version_id, self.memory.memory_version_id)

    def test_cross_owner_thread_dependency_fails_closed(self) -> None:
        foreign = OwnerTruthConversationThreadAuthoritySnapshot(
            thread_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id="other-owner",
            authority_epoch=7,
            state=ConversationThreadState.ACTIVE,
            session_id=str(uuid4()),
            session_state=InterviewSessionState.ACTIVE,
            session_boundary=InterviewBoundary.OPEN,
        )
        store = self._store(threads=(foreign,), cues=())

        with self.assertRaises(OwnerTruthThreadSummaryError):
            OwnerTruthThreadSummaryReadService(store).read(context=self.context)

    def test_rebuilding_dimension_projection_keeps_thread_summary_unavailable(self) -> None:
        store = self._store(
            threads=(self._thread(),),
            cues=(),
            snapshot=build_rebuilding_memory_projection(
                vault_id=self.vault_id,
                owner_subject_id=self.owner_id,
                authority_epoch=7,
            ),
        )

        result = OwnerTruthThreadSummaryReadService(store).read(context=self.context)

        self.assertEqual(result.state, OwnerTruthKnowledgeDimensionReadState.REBUILDING)
        self.assertIsNone(result.projection)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
