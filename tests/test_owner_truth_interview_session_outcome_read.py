from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    InterviewFatigue,
    InterviewReviewBatchState,
    InterviewReviewBatchTrigger,
    InterviewSessionState,
    OwnerTruthInterviewReviewBatchSnapshot,
    OwnerTruthInterviewSessionSnapshot,
)
from app.domain.owner_truth.interview_candidate_review import (
    InterviewCandidateReviewReadiness,
    OwnerTruthInterviewCandidateReviewComposition,
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
from app.services.owner_truth_interview_session_outcome_read import (
    OwnerTruthInterviewSessionOutcomeReadService,
)
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

    def read(self, *, context: OwnerTruthCommandContext) -> dict[str, object]:
        del context
        return self.snapshot


class _ConversationRepository:
    def __init__(
        self,
        *,
        session: OwnerTruthInterviewSessionSnapshot,
        batches: tuple[OwnerTruthInterviewReviewBatchSnapshot, ...],
    ) -> None:
        self.session = session
        self.batches = batches

    def get_interview_session(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewSessionSnapshot:
        del context
        if session_id != self.session.session_id:
            raise AssertionError("unexpected session")
        return self.session

    def list_interview_review_batches(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
    ) -> tuple[OwnerTruthInterviewReviewBatchSnapshot, ...]:
        del context
        if session_id != self.session.session_id:
            raise AssertionError("unexpected session")
        return self.batches


class _CandidateReviewRepository:
    def __init__(
        self,
        compositions: dict[str, OwnerTruthInterviewCandidateReviewComposition],
    ) -> None:
        self.compositions = compositions

    def compose(
        self,
        *,
        review_batch_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewCandidateReviewComposition:
        del context
        return self.compositions[review_batch_id]


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
        candidate_reviews: _CandidateReviewRepository,
        cues: _CueRepository,
    ) -> None:
        self.reader = reader
        self.confirmations = confirmations
        self.conversation = conversation
        self.candidate_reviews = candidate_reviews
        self.cues = cues

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_conversation_repository(self):
        return self.conversation

    def owner_truth_interview_candidate_review_repository(self):
        return self.candidate_reviews

    def owner_truth_memory_projection_repository(self):
        return self.reader

    def owner_truth_knowledge_dimension_confirmation_repository(self):
        return self.confirmations

    def owner_truth_saved_continuation_cue_repository(self):
        return self.cues


class OwnerTruthInterviewSessionOutcomeReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-session-outcome"
        self.vault_id = "vault-session-outcome"
        self.session_id = str(uuid4())
        self.thread_id = str(uuid4())
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.memory = self._memory(content={"claim": "private session outcome must not leak"})
        self.other_memory = self._memory(content={"claim": "different session source must not count"})
        self.snapshot = build_ready_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=5,
            inputs=(self.memory, self.other_memory),
        )

    def _memory(self, *, content: dict[str, object]) -> OwnerTruthMemoryProjectionInput:
        source_id = str(uuid4())
        return OwnerTruthMemoryProjectionInput(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=5,
            version_number=1,
            source_id=source_id,
            source_version=1,
            memory_kind="knowledge",
            perspective_type="firstPerson",
            epistemic_status="recalled",
            sensitivity="standard",
            content_schema_version="owner-truth-v1",
            content_hash=_hash(content),
            content=content,
            evidence_refs=({"sourceId": source_id, "sourceVersion": 1},),
        )

    def _session(
        self,
        *,
        pending_review_batch_id: str | None = None,
        state: InterviewSessionState = InterviewSessionState.ACTIVE,
        boundary: InterviewBoundary = InterviewBoundary.OPEN,
    ) -> OwnerTruthInterviewSessionSnapshot:
        return OwnerTruthInterviewSessionSnapshot(
            session_id=self.session_id,
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            thread_id=self.thread_id,
            state=state,
            boundary=boundary,
            row_version=1,
            thread_version=1,
            turn_count=2,
            deepening_turn_count=0,
            candidate_batch_turn_count=0,
            pending_review_batch_id=pending_review_batch_id,
            fatigue=InterviewFatigue.NORMAL,
            authority_epoch=5,
        )

    def _batch(
        self,
        *,
        state: InterviewReviewBatchState = InterviewReviewBatchState.ACKNOWLEDGED,
    ) -> OwnerTruthInterviewReviewBatchSnapshot:
        return OwnerTruthInterviewReviewBatchSnapshot(
            review_batch_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            session_id=self.session_id,
            thread_id=self.thread_id,
            trigger=InterviewReviewBatchTrigger.TURN_THRESHOLD,
            state=state,
            captured_candidate_batch_turn_count=1,
            owner_turn_start_count=1,
            owner_turn_end_count=1,
            through_message_sequence=2,
            row_version=1,
            authority_epoch=5,
        )

    @staticmethod
    def _composition(
        *,
        batch_id: str,
        source_id: str,
    ) -> OwnerTruthInterviewCandidateReviewComposition:
        return OwnerTruthInterviewCandidateReviewComposition(
            review_batch_id=batch_id,
            admission_id=str(uuid4()),
            source_id=source_id,
            source_version=1,
            authority_epoch=5,
            readiness=InterviewCandidateReviewReadiness.NO_CANDIDATES,
            latest_extraction_status="succeeded",
            selected_extraction_id=None,
            batch_candidates=(),
            single_candidates=(),
        )

    def _store(
        self,
        *,
        session: OwnerTruthInterviewSessionSnapshot,
        batches: tuple[OwnerTruthInterviewReviewBatchSnapshot, ...],
        snapshot: dict[str, object] | None = None,
        confirm_memory: OwnerTruthMemoryProjectionInput | None = None,
        compositions: dict[str, OwnerTruthInterviewCandidateReviewComposition] | None = None,
        cues: tuple[ServerPlannedContinuationCue, ...] = (),
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
        if confirm_memory is not None and str(reader.snapshot.get("state") or "") == "ready":
            OwnerTruthKnowledgeDimensionConfirmationService(confirmation_store, enabled=True).confirm(
                context=self.context,
                memory_version_id=confirm_memory.memory_version_id,
                command=OwnerTruthKnowledgeDimensionConfirmationCommand(
                    command_id=f"session-outcome-confirm-{confirm_memory.memory_version_id}",
                    expected_content_hash=confirm_memory.content_hash,
                    dimension=KnowledgeDimension.KEY_DECISIONS,
                    covered_facets=("choice",),
                ),
            )
        return _Store(
            reader=reader,
            confirmations=confirmations,
            conversation=_ConversationRepository(session=session, batches=batches),
            candidate_reviews=_CandidateReviewRepository(compositions or {}),
            cues=_CueRepository(cues),
        )

    def test_counts_only_confirmed_memory_from_this_sessions_admitted_source(self) -> None:
        batch = self._batch()
        session = self._session()
        cue = ServerPlannedContinuationCue(
            cue_id=str(uuid4()),
            owner_subject_id=self.owner_id,
            vault_id=self.vault_id,
            authority_epoch=5,
            thread_id=self.thread_id,
            session_id=self.session_id,
            expected_session_version=1,
            memory_version_id=self.memory.memory_version_id,
            target_dimension=KnowledgeDimension.KEY_DECISIONS,
            missing_facet="reason",
        )
        store = self._store(
            session=session,
            batches=(batch,),
            confirm_memory=self.memory,
            compositions={
                batch.review_batch_id: self._composition(
                    batch_id=batch.review_batch_id,
                    source_id=self.memory.source_id,
                )
            },
            cues=(cue,),
        )

        result = OwnerTruthInterviewSessionOutcomeReadService(store).read(
            session_id=self.session_id,
            context=self.context,
        )

        self.assertEqual(result.confirmation_state, OwnerTruthKnowledgeDimensionReadState.READY)
        self.assertEqual(result.confirmed_memory_version_count, 1)
        self.assertEqual(result.eligible_saved_continuation_cue_count, 1)
        self.assertEqual(result.acknowledged_review_batch_count, 1)
        summary = result.value_free_summary()
        self.assertEqual(summary["presentation"]["state"], "narrativeRecorded")
        self.assertEqual(summary["thisSession"]["confirmedMemoryVersionCount"], 1)
        self.assertNotIn("private session outcome", str(summary))
        self.assertNotIn("different session", str(summary))

    def test_confirmed_memory_from_another_source_does_not_count_as_this_session(self) -> None:
        batch = self._batch()
        store = self._store(
            session=self._session(),
            batches=(batch,),
            confirm_memory=self.memory,
            compositions={
                batch.review_batch_id: self._composition(
                    batch_id=batch.review_batch_id,
                    source_id=self.other_memory.source_id,
                )
            },
        )

        result = OwnerTruthInterviewSessionOutcomeReadService(store).read(
            session_id=self.session_id,
            context=self.context,
        )

        self.assertEqual(result.confirmed_memory_version_count, 0)
        self.assertEqual(result.eligible_saved_continuation_cue_count, 0)

    def test_pending_review_uses_existing_presentation_semantics(self) -> None:
        batch = self._batch(state=InterviewReviewBatchState.PENDING_ACKNOWLEDGEMENT)
        store = self._store(
            session=self._session(pending_review_batch_id=batch.review_batch_id),
            batches=(batch,),
            confirm_memory=self.memory,
        )

        result = OwnerTruthInterviewSessionOutcomeReadService(store).read(
            session_id=self.session_id,
            context=self.context,
        )

        self.assertEqual(result.presentation_state, "reviewPending")
        self.assertFalse(result.can_continue)
        self.assertTrue(result.can_continue_later)
        self.assertEqual(result.pending_review_batch_count, 1)
        self.assertEqual(result.admitted_review_batch_count, 0)

    def test_rebuilding_dimension_projection_omits_confirmation_derived_counts(self) -> None:
        batch = self._batch()
        rebuilding = build_rebuilding_memory_projection(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=5,
        )
        store = self._store(
            session=self._session(),
            batches=(batch,),
            snapshot=rebuilding,
            compositions={
                batch.review_batch_id: self._composition(
                    batch_id=batch.review_batch_id,
                    source_id=self.memory.source_id,
                )
            },
        )

        result = OwnerTruthInterviewSessionOutcomeReadService(store).read(
            session_id=self.session_id,
            context=self.context,
        )

        self.assertEqual(result.confirmation_state, OwnerTruthKnowledgeDimensionReadState.REBUILDING)
        self.assertIsNone(result.confirmed_memory_version_count)
        self.assertIsNone(result.eligible_saved_continuation_cue_count)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
