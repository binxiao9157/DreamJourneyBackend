from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.knowledge_dimension_read import (
    OwnerTruthKnowledgeDimensionReadState,
)
from app.domain.owner_truth.conversation import (
    ConversationThreadState,
    InterviewBoundary,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationThreadAuthoritySnapshot,
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
from app.services.owner_truth_saved_continuation import (
    InMemoryOwnerTruthSavedContinuationCueRepository,
)
from app.services.owner_truth_thread_preferences import (
    InMemoryOwnerTruthThreadPreferenceRepository,
    OwnerTruthThreadPreferenceSnapshot,
    ThreadPreferenceState,
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


class _ConversationThreadAuthorityReader:
    def __init__(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int,
        thread_ids: tuple[str, ...],
        state: ConversationThreadState = ConversationThreadState.ACTIVE,
        session_state: InterviewSessionState = InterviewSessionState.ACTIVE,
        session_boundary: InterviewBoundary = InterviewBoundary.OPEN,
    ) -> None:
        self._vault_id = vault_id
        self._owner_subject_id = owner_subject_id
        self._authority_epoch = authority_epoch
        self._thread_ids = frozenset(thread_ids)
        self._state = state
        self._session_id = str(uuid4())
        self._session_state = session_state
        self._session_boundary = session_boundary

    def get_interview_thread_authority(
        self,
        *,
        thread_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthConversationThreadAuthoritySnapshot:
        if (
            context.vault_id != self._vault_id
            or context.owner_subject_id != self._owner_subject_id
            or thread_id not in self._thread_ids
        ):
            raise OwnerTruthConversationAccessDenied(
                "conversation thread does not belong to this active Owner Vault"
            )
        return OwnerTruthConversationThreadAuthoritySnapshot(
            thread_id=thread_id,
            vault_id=self._vault_id,
            owner_subject_id=self._owner_subject_id,
            authority_epoch=self._authority_epoch,
            state=self._state,
            session_id=self._session_id,
            session_state=self._session_state,
            session_boundary=self._session_boundary,
        )

    def list_recommendation_eligible_thread_authorities(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[OwnerTruthConversationThreadAuthoritySnapshot, ...]:
        if (
            context.vault_id != self._vault_id
            or context.owner_subject_id != self._owner_subject_id
        ):
            raise OwnerTruthConversationAccessDenied(
                "conversation thread does not belong to this active Owner Vault"
            )
        snapshot_rows = tuple(
            OwnerTruthConversationThreadAuthoritySnapshot(
                thread_id=thread_id,
                vault_id=self._vault_id,
                owner_subject_id=self._owner_subject_id,
                authority_epoch=self._authority_epoch,
                state=self._state,
                session_id=self._session_id,
                session_state=self._session_state,
                session_boundary=self._session_boundary,
            )
            for thread_id in sorted(self._thread_ids)
        )
        return tuple(item for item in snapshot_rows if item.is_recommendation_eligible)

    def list_recommendation_candidate_thread_authorities(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[OwnerTruthConversationThreadAuthoritySnapshot, ...]:
        if (
            context.vault_id != self._vault_id
            or context.owner_subject_id != self._owner_subject_id
        ):
            raise OwnerTruthConversationAccessDenied(
                "conversation thread does not belong to this active Owner Vault"
            )
        snapshot_rows = tuple(
            OwnerTruthConversationThreadAuthoritySnapshot(
                thread_id=thread_id,
                vault_id=self._vault_id,
                owner_subject_id=self._owner_subject_id,
                authority_epoch=self._authority_epoch,
                state=self._state,
                session_id=self._session_id,
                session_state=self._session_state,
                session_boundary=self._session_boundary,
            )
            for thread_id in sorted(self._thread_ids)
        )
        return tuple(
            item
            for item in snapshot_rows
            if item.is_recommendation_eligible or item.is_elapsed_cooldown_candidate
        )


class _Store:
    def __init__(
        self,
        snapshot: dict[str, object],
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int,
        thread_ids: tuple[str, ...],
        thread_state: ConversationThreadState = ConversationThreadState.ACTIVE,
        session_state: InterviewSessionState = InterviewSessionState.ACTIVE,
        session_boundary: InterviewBoundary = InterviewBoundary.OPEN,
    ) -> None:
        self.reader = _ProjectionReader(snapshot)
        self.repository = InMemoryOwnerTruthKnowledgeDimensionConfirmationRepository()
        self.saved_continuation_cue_repository = (
            InMemoryOwnerTruthSavedContinuationCueRepository()
        )
        self.thread_preference_repository = InMemoryOwnerTruthThreadPreferenceRepository()
        self.conversation_repository = _ConversationThreadAuthorityReader(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=authority_epoch,
            thread_ids=thread_ids,
            state=thread_state,
            session_state=session_state,
            session_boundary=session_boundary,
        )

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield object()

    def owner_truth_memory_projection_repository(self):
        return self.reader

    def owner_truth_knowledge_dimension_confirmation_repository(self):
        return self.repository

    def owner_truth_conversation_repository(self):
        return self.conversation_repository

    def owner_truth_saved_continuation_cue_repository(self):
        return self.saved_continuation_cue_repository

    def owner_truth_thread_preference_repository(self):
        return self.thread_preference_repository


class _FixedThreadPreferenceRepository:
    def __init__(self, preference: OwnerTruthThreadPreferenceSnapshot) -> None:
        self.preference = preference

    def read(self, *, context, thread_id):
        if (
            context.vault_id == self.preference.vault_id
            and thread_id == self.preference.thread_id
        ):
            return self.preference
        return None


class _MappedThreadPreferenceRepository:
    def __init__(self, preferences: tuple[OwnerTruthThreadPreferenceSnapshot, ...]) -> None:
        self._preferences = {item.thread_id: item for item in preferences}

    def read(self, *, context, thread_id):
        preference = self._preferences.get(thread_id)
        if preference is None or context.vault_id != preference.vault_id:
            return None
        return preference


class OwnerTruthKnowledgeRecommendationReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-recommendation-read"
        self.vault_id = "vault-recommendation-read"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.thread_id = str(uuid4())
        self.breadth_thread_id = str(uuid4())
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

    def _store(
        self,
        *,
        rebuilding: bool = False,
        thread_ids: tuple[str, ...] | None = None,
        thread_authority_epoch: int = 5,
        thread_state: ConversationThreadState = ConversationThreadState.ACTIVE,
        session_state: InterviewSessionState = InterviewSessionState.ACTIVE,
        session_boundary: InterviewBoundary = InterviewBoundary.OPEN,
    ) -> _Store:
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
        return _Store(
            snapshot,
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            authority_epoch=thread_authority_epoch,
            thread_ids=thread_ids or (self.thread_id, self.breadth_thread_id),
            thread_state=thread_state,
            session_state=session_state,
            session_boundary=session_boundary,
        )

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
            "thread_id": self.thread_id,
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
                    thread_id=self.breadth_thread_id,
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

    def test_server_plans_only_value_free_breadth_from_current_authority(self) -> None:
        store = self._store(thread_ids=(self.thread_id,))
        self._confirm(store)

        result = OwnerTruthKnowledgeRecommendationReadService(store).plan(
            context=self.context,
        )

        self.assertEqual(result.state, OwnerTruthKnowledgeDimensionReadState.READY)
        assert result.selection is not None
        self.assertEqual([item.slot for item in result.selection.selected], [RecommendationSlot.BREADTH])
        summary = result.value_free_summary()
        self.assertNotIn("weekday evenings", str(summary))
        self.assertNotIn("claim", str(summary))

    def test_server_plan_returns_empty_selection_when_session_is_not_eligible(self) -> None:
        store = self._store(
            thread_ids=(self.thread_id,),
            thread_state=ConversationThreadState.PAUSED,
            session_state=InterviewSessionState.PAUSED,
            session_boundary=InterviewBoundary.DO_NOT_ASK,
        )
        self._confirm(store)

        result = OwnerTruthKnowledgeRecommendationReadService(store).plan(
            context=self.context,
        )

        assert result.selection is not None
        self.assertEqual(result.selection.selected, ())

    def test_elapsed_cooldown_is_server_clock_continuity_without_resuming_session(self) -> None:
        now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
        store = self._store(
            thread_ids=(self.thread_id,),
            thread_state=ConversationThreadState.ACTIVE,
            session_state=InterviewSessionState.PAUSED,
            session_boundary=InterviewBoundary.COOLDOWN,
        )
        self._confirm(store)
        store.thread_preference_repository = _FixedThreadPreferenceRepository(
            OwnerTruthThreadPreferenceSnapshot(
                vault_id=self.vault_id,
                thread_id=self.thread_id,
                owner_subject_id=self.owner_id,
                authority_epoch=5,
                preference=ThreadPreferenceState.COOLDOWN,
                cooldown_until=now + timedelta(seconds=60),
                row_version=1,
            )
        )
        service = OwnerTruthKnowledgeRecommendationReadService(store)

        before = service.plan(context=self.context, now=now + timedelta(seconds=59))
        assert before.selection is not None
        self.assertEqual(before.selection.selected, ())
        with self.assertRaisesRegex(OwnerTruthKnowledgeRecommendationReadError, "active state"):
            service.read(
                context=self.context,
                candidates=(self._candidate(),),
                now=now + timedelta(seconds=59),
            )

        after = service.plan(context=self.context, now=now + timedelta(seconds=60))
        assert after.selection is not None
        self.assertEqual([item.slot for item in after.selection.selected], [RecommendationSlot.CONTINUITY])
        self.assertEqual(after.selection.selected[0].question_template_id, "continueElapsedCooldown")
        self.assertEqual(after.selection.selected[0].reason_code, "elapsedCooldownContinuation")
        direct = service.read(
            context=self.context,
            candidates=(self._candidate(),),
            now=now + timedelta(seconds=60),
        )
        assert direct.selection is not None
        self.assertEqual([item.slot for item in direct.selection.selected], [RecommendationSlot.CONTINUITY])

    def test_elapsed_cooldown_outranks_open_thread_and_uses_stable_expiry_order(self) -> None:
        now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
        earlier_thread_id = self.thread_id
        later_thread_id = self.breadth_thread_id
        open_thread_id = str(uuid4())
        store = self._store(thread_ids=(earlier_thread_id, later_thread_id, open_thread_id))
        store.thread_preference_repository = _MappedThreadPreferenceRepository(
            (
                OwnerTruthThreadPreferenceSnapshot(
                    vault_id=self.vault_id,
                    thread_id=earlier_thread_id,
                    owner_subject_id=self.owner_id,
                    authority_epoch=5,
                    preference=ThreadPreferenceState.COOLDOWN,
                    cooldown_until=now - timedelta(seconds=60),
                    row_version=1,
                ),
                OwnerTruthThreadPreferenceSnapshot(
                    vault_id=self.vault_id,
                    thread_id=later_thread_id,
                    owner_subject_id=self.owner_id,
                    authority_epoch=5,
                    preference=ThreadPreferenceState.COOLDOWN,
                    cooldown_until=now - timedelta(seconds=1),
                    row_version=1,
                ),
            )
        )

        def authority(
            thread_id: str,
            *,
            session_state: InterviewSessionState,
            session_boundary: InterviewBoundary,
        ) -> OwnerTruthConversationThreadAuthoritySnapshot:
            return OwnerTruthConversationThreadAuthoritySnapshot(
                thread_id=thread_id,
                vault_id=self.vault_id,
                owner_subject_id=self.owner_id,
                authority_epoch=5,
                state=ConversationThreadState.ACTIVE,
                session_id=str(uuid4()),
                session_state=session_state,
                session_boundary=session_boundary,
            )

        open_authority = authority(
            open_thread_id,
            session_state=InterviewSessionState.ACTIVE,
            session_boundary=InterviewBoundary.OPEN,
        )
        earlier_cooldown = authority(
            earlier_thread_id,
            session_state=InterviewSessionState.PAUSED,
            session_boundary=InterviewBoundary.COOLDOWN,
        )
        later_cooldown = authority(
            later_thread_id,
            session_state=InterviewSessionState.PAUSED,
            session_boundary=InterviewBoundary.COOLDOWN,
        )
        service = OwnerTruthKnowledgeRecommendationReadService(store)

        selected, elapsed_ids = service._plan_thread_authorities(
            context=self.context,
            potential_thread_authorities=(open_authority, later_cooldown, earlier_cooldown),
            now=now,
        )
        replay_selected, replay_elapsed_ids = service._plan_thread_authorities(
            context=self.context,
            potential_thread_authorities=(earlier_cooldown, open_authority, later_cooldown),
            now=now,
        )

        self.assertEqual([item.thread_id for item in selected], [earlier_thread_id])
        self.assertEqual(elapsed_ids, frozenset((earlier_thread_id,)))
        self.assertEqual(
            [item.thread_id for item in replay_selected],
            [earlier_thread_id],
        )
        self.assertEqual(replay_elapsed_ids, frozenset((earlier_thread_id,)))

    def test_server_plan_fail_closes_a_thread_marked_do_not_ask(self) -> None:
        store = self._store(thread_ids=(self.thread_id,))
        self._confirm(store)
        store.thread_preference_repository = _FixedThreadPreferenceRepository(
            OwnerTruthThreadPreferenceSnapshot(
                vault_id=self.vault_id,
                thread_id=self.thread_id,
                owner_subject_id=self.owner_id,
                authority_epoch=5,
                preference=ThreadPreferenceState.DO_NOT_ASK,
                cooldown_until=None,
                row_version=1,
            )
        )

        result = OwnerTruthKnowledgeRecommendationReadService(store).plan(
            context=self.context,
        )

        assert result.selection is not None
        self.assertEqual(result.selection.selected, ())

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

    def test_rejects_unknown_or_stale_candidate_thread_authority(self) -> None:
        store = self._store()
        self._confirm(store)

        with self.assertRaisesRegex(
            OwnerTruthKnowledgeRecommendationReadError,
            "current Owner Truth conversation thread",
        ):
            OwnerTruthKnowledgeRecommendationReadService(store).read(
                context=self.context,
                candidates=(self._candidate(thread_id=str(uuid4())),),
            )

        stale_store = self._store(thread_authority_epoch=4)
        self._confirm(stale_store)
        with self.assertRaisesRegex(
            OwnerTruthKnowledgeRecommendationReadError,
            "current Owner Truth conversation thread",
        ):
            OwnerTruthKnowledgeRecommendationReadService(stale_store).read(
                context=self.context,
                candidates=(self._candidate(),),
            )

    def test_rejects_paused_candidate_thread_before_selection(self) -> None:
        store = self._store(thread_state=ConversationThreadState.PAUSED)
        self._confirm(store)

        with self.assertRaisesRegex(
            OwnerTruthKnowledgeRecommendationReadError,
            "current Owner Truth conversation thread in active state",
        ):
            OwnerTruthKnowledgeRecommendationReadService(store).read(
                context=self.context,
                candidates=(self._candidate(),),
            )

    def test_rejects_candidate_when_linked_session_is_not_open_and_active(self) -> None:
        cases = (
            (InterviewSessionState.PAUSED, InterviewBoundary.COOLDOWN),
            (InterviewSessionState.PAUSED, InterviewBoundary.DO_NOT_ASK),
            (InterviewSessionState.ACTIVE, InterviewBoundary.SKIP_ONCE),
        )
        for session_state, session_boundary in cases:
            with self.subTest(
                session_state=session_state.value,
                session_boundary=session_boundary.value,
            ):
                store = self._store(
                    session_state=session_state,
                    session_boundary=session_boundary,
                )
                self._confirm(store)

                with self.assertRaisesRegex(
                    OwnerTruthKnowledgeRecommendationReadError,
                    "current Owner Truth conversation thread in active state",
                ):
                    OwnerTruthKnowledgeRecommendationReadService(store).read(
                        context=self.context,
                        candidates=(self._candidate(),),
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
