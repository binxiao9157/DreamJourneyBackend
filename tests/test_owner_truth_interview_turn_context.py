from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
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
from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    InterviewBoundary,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationVersionConflict,
    OwnerTruthInterviewSessionStateConflict,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)
from app.services.owner_truth_conversation import (
    InMemoryOwnerTruthConversationRepository,
    OwnerTruthConversationService,
)
from app.services.owner_truth_interview_turn_context import (
    OwnerTruthInterviewTurnContextService,
    interview_turn_context_summary,
)
from app.services.owner_truth_memory_projection import (
    InMemoryOwnerTruthMemoryProjectionRepository,
    OwnerTruthMemoryProjectionService,
)
from app.services.owner_truth_memory_search_projection import (
    InMemoryOwnerTruthMemorySearchDocumentProjectionRepository,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Store:
    def __init__(self) -> None:
        self.review_repository = InMemoryOwnerTruthCandidateReviewRepository()
        self.projection_repository = InMemoryOwnerTruthMemoryProjectionRepository(
            self.review_repository
        )
        self.search_projection_repository = (
            InMemoryOwnerTruthMemorySearchDocumentProjectionRepository(
                self.projection_repository
            )
        )
        self.conversation_repository = InMemoryOwnerTruthConversationRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository

    def owner_truth_memory_search_document_projection_repository(self):
        return self.search_projection_repository

    def owner_truth_conversation_repository(self):
        return self.conversation_repository


class _EpochMismatchedConversationRepository:
    """Test double that returns a stale session authority binding only."""

    def __init__(self, delegate: InMemoryOwnerTruthConversationRepository) -> None:
        self._delegate = delegate

    def get_interview_session(self, *, session_id: str, context: OwnerTruthCommandContext):
        snapshot = self._delegate.get_interview_session(session_id=session_id, context=context)
        return replace(snapshot, authority_epoch=snapshot.authority_epoch + 1)

    def get_interview_message_authority(
        self,
        *,
        message_id: str,
        context: OwnerTruthCommandContext,
    ):
        snapshot = self._delegate.get_interview_message_authority(
            message_id=message_id,
            context=context,
        )
        return replace(snapshot, authority_epoch=snapshot.authority_epoch + 1)

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class OwnerTruthInterviewTurnContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-interview-turn-context"
        self.owner_id = "owner-interview-turn-context"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _Store()
        self.review = OwnerTruthCandidateReviewService(self.store)
        self.projection = OwnerTruthMemoryProjectionService(self.store)
        self.conversation = OwnerTruthConversationService(self.store.conversation_repository)
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        self.message_id = str(uuid4())
        self.thread_version = 1
        self.session_version = 1

    def _candidate(self, *, summary: str) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        content = {"summary": summary}
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
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
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [
                    {
                        "sourceId": source_id,
                        "sourceVersion": 1,
                        "span": {"start": 0, "end": 12},
                    }
                ],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    def _activate_and_rebuild(self, candidate: OwnerTruthCandidateSnapshot) -> None:
        self.store.review_repository.seed(candidate)
        self.review.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="turn-context-activate",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=candidate.row_version,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )
        self.projection.rebuild(context=self.context)

    def _start_and_append(self, *, text: str) -> None:
        started = self.conversation.start_session(
            command=StartInterviewSessionCommand(
                command_id="turn-context-start",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_thread_version=0,
                entry_mode="naturalInput",
            ),
            context=self.context,
        )
        self.thread_version = started.thread_version
        self.session_version = started.session_version
        appended = self.conversation.append_message(
            command=AppendInterviewMessageCommand(
                command_id="turn-context-append",
                thread_id=self.thread_id,
                session_id=self.session_id,
                message_id=self.message_id,
                expected_thread_version=self.thread_version,
                expected_session_version=self.session_version,
                author=ConversationMessageAuthor.OWNER,
                kind=ConversationMessageKind.NARRATIVE,
                text=text,
            ),
            context=self.context,
        )
        self.thread_version = appended.thread_version
        self.session_version = appended.session_version

    def _prepare(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "messageId": self.message_id,
            "expectedSessionVersion": self.session_version,
            "query": "想继续聊小时候的院子",
        }
        payload.update(overrides)
        return OwnerTruthInterviewTurnContextService(self.store, enabled=True).prepare(
            session_id=self.session_id,
            context=self.context,
            payload=payload,
        )

    def test_prepares_confirmed_projection_only_after_current_owner_message_binding(self) -> None:
        confirmed_summary = "确认过的院子回忆只能在进程内作为上下文"
        owner_message = "这是私有访谈原文，不能进入 QA 摘要"
        self._activate_and_rebuild(self._candidate(summary=confirmed_summary))
        self._start_and_append(text=owner_message)

        result = self._prepare()
        summary = interview_turn_context_summary(result)

        self.assertEqual(result["state"], "ready")
        self.assertTrue(result["readyForServerTurn"])
        self.assertFalse(result["providerDispatchAllowed"])
        self.assertIn(confirmed_summary, result["generationContext"]["text"])
        self.assertEqual(result["generationContext"]["sourceCount"], 1)
        self.assertEqual(result["messageAuthority"].author.value, "owner")
        self.assertEqual(result["messageAuthority"].kind.value, "narrative")
        rendered_summary = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            confirmed_summary,
            owner_message,
            "想继续聊小时候的院子",
            self.thread_id,
            self.session_id,
            self.message_id,
        ):
            self.assertNotIn(forbidden, rendered_summary)

    def test_rejects_stale_session_version_before_materializing_personal_context(self) -> None:
        self._activate_and_rebuild(self._candidate(summary="不应在陈旧会话上准备上下文"))
        self._start_and_append(text="有效的当前消息")

        with self.assertRaises(OwnerTruthConversationVersionConflict):
            self._prepare(expectedSessionVersion=self.session_version - 1)

    def test_rejects_paused_session_before_materializing_personal_context(self) -> None:
        self._activate_and_rebuild(self._candidate(summary="暂停访谈不能继续准备上下文"))
        self._start_and_append(text="有效的当前消息")
        paused = self.conversation.set_boundary(
            command=SetInterviewBoundaryCommand(
                command_id="turn-context-pause",
                thread_id=self.thread_id,
                session_id=self.session_id,
                expected_session_version=self.session_version,
                boundary=InterviewBoundary.DO_NOT_ASK,
            ),
            context=self.context,
        )
        self.session_version = paused.session_version

        with self.assertRaises(OwnerTruthInterviewSessionStateConflict):
            self._prepare()

    def test_rejects_cross_owner_even_when_session_identifier_is_known(self) -> None:
        self._activate_and_rebuild(self._candidate(summary="跨 Owner 不能读取确认记忆"))
        self._start_and_append(text="仅本人可见的会话消息")
        other_context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id="owner-interview-turn-context-other",
            actor_subject_id="owner-interview-turn-context-other",
        )

        with self.assertRaises(OwnerTruthConversationAccessDenied):
            OwnerTruthInterviewTurnContextService(self.store, enabled=True).prepare(
                session_id=self.session_id,
                context=other_context,
                payload={
                    "messageId": self.message_id,
                    "expectedSessionVersion": self.session_version,
                    "query": "不应跨 Owner 查询",
                },
            )

    def test_fails_closed_when_session_epoch_no_longer_matches_projection_authority(self) -> None:
        confirmed_summary = "过期 authority epoch 不能输出这段内容"
        self._activate_and_rebuild(self._candidate(summary=confirmed_summary))
        self._start_and_append(text="有效的当前消息")
        original_repository = self.store.conversation_repository
        self.store.conversation_repository = _EpochMismatchedConversationRepository(
            original_repository
        )

        result = self._prepare()
        summary = interview_turn_context_summary(result)

        self.assertEqual(result["state"], "authorityMismatch")
        self.assertFalse(result["readyForServerTurn"])
        self.assertEqual(result["generationContext"]["text"], "")
        self.assertIn("interview_session_authority_epoch_mismatch", result["fallbacks"])
        self.assertNotIn(confirmed_summary, json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
