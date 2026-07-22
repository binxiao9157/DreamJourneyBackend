from __future__ import annotations

import unittest
from uuid import uuid4

from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.interview_orchestration import InterviewAction
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_interview_decision_audit import (
    OwnerTruthInterviewDecisionAuditAccessDenied,
    OwnerTruthInterviewDecisionAuditCommand,
    OwnerTruthInterviewDecisionAuditService,
    OwnerTruthInterviewDecisionAuditStale,
    OwnerTruthInterviewDecisionAuditUnavailable,
)
from app.services.owner_truth_interview_session_orchestration import (
    InterviewSessionOrchestrationSignals,
)


class OwnerTruthInterviewDecisionAuditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.context = OwnerTruthCommandContext(
            vault_id="decision-audit-vault",
            owner_subject_id="decision-audit-owner",
            actor_subject_id="decision-audit-owner",
            policy_version="owner-truth-v1",
        )
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        self.conversation = OwnerTruthConversationService(
            self.store.owner_truth_conversation_repository()
        )
        with self.store.request_unit_of_work(
            correlation_id="decision-audit-start",
            command_id="decision-audit-start",
        ):
            started = self.conversation.start_session(
                command=StartInterviewSessionCommand(
                    command_id="decision-audit-start",
                    thread_id=self.thread_id,
                    session_id=self.session_id,
                    expected_thread_version=0,
                    entry_mode="naturalInput",
                ),
                context=self.context,
            )
        self.assertEqual(started.session_version, 1)

    def _append(
        self,
        *,
        command_id: str,
        author: ConversationMessageAuthor = ConversationMessageAuthor.OWNER,
        kind: ConversationMessageKind = ConversationMessageKind.NARRATIVE,
        text: str = "这段私有叙述不得进入访谈动作审计",
        expected_thread_version: int = 1,
        expected_session_version: int = 1,
    ):
        with self.store.request_unit_of_work(
            correlation_id=f"decision-audit-{command_id}",
            command_id=command_id,
        ):
            return self.conversation.append_message(
                command=AppendInterviewMessageCommand(
                    command_id=command_id,
                    thread_id=self.thread_id,
                    session_id=self.session_id,
                    message_id=str(uuid4()),
                    expected_thread_version=expected_thread_version,
                    expected_session_version=expected_session_version,
                    author=author,
                    kind=kind,
                    text=text,
                ),
                context=self.context,
            )

    def _service(self, *, enabled: bool = True) -> OwnerTruthInterviewDecisionAuditService:
        return OwnerTruthInterviewDecisionAuditService(self.store, enabled=enabled)

    def _command(self, *, message_id: str, expected_session_version: int, command_id: str):
        return OwnerTruthInterviewDecisionAuditCommand(
            command_id=command_id,
            thread_id=self.thread_id,
            session_id=self.session_id,
            message_id=message_id,
            expected_session_version=expected_session_version,
        )

    @staticmethod
    def _signals(**overrides: object) -> InterviewSessionOrchestrationSignals:
        values: dict[str, object] = {
            "topic_id": "topic-private-story",
            "topic_incomplete": True,
        }
        values.update(overrides)
        return InterviewSessionOrchestrationSignals(**values)

    def test_owner_narrative_records_one_value_free_decision_and_replays_after_state_advances(self) -> None:
        appended = self._append(command_id="decision-audit-owner-message")
        assert appended.message_id is not None
        command = self._command(
            command_id="decision-audit-record",
            message_id=appended.message_id,
            expected_session_version=appended.session_version,
        )

        created = self._service().decide_and_record(
            command=command,
            context=self.context,
            signals=self._signals(),
        )

        self.assertEqual(created.outcome, "created")
        self.assertEqual(created.action, InterviewAction.DEEPEN)
        self.assertEqual(created.reason_code, "highValueIncompleteStory")
        rendered = str(created.value_free_summary())
        self.assertNotIn("这段私有叙述", rendered)
        self.assertNotIn(self.thread_id, rendered)
        self.assertNotIn(self.session_id, rendered)
        self.assertNotIn(appended.message_id, rendered)

        self._append(
            command_id="decision-audit-next-message",
            expected_thread_version=appended.thread_version,
            expected_session_version=appended.session_version,
        )
        replayed = self._service().decide_and_record(
            command=command,
            context=self.context,
            signals=self._signals(topic_incomplete=False),
        )
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(replayed.decision_id, created.decision_id)
        self.assertEqual(replayed.action, InterviewAction.DEEPEN)

    def test_wrong_message_kind_and_stale_session_cannot_create_audit(self) -> None:
        assistant_message = self._append(
            command_id="decision-audit-assistant-message",
            author=ConversationMessageAuthor.ASSISTANT,
            kind=ConversationMessageKind.QUESTION,
            text="系统提问本身不能成为 owner narrative 审计对象",
        )
        assert assistant_message.message_id is not None
        with self.assertRaises(OwnerTruthInterviewDecisionAuditAccessDenied):
            self._service().decide_and_record(
                command=self._command(
                    command_id="decision-audit-assistant-record",
                    message_id=assistant_message.message_id,
                    expected_session_version=assistant_message.session_version,
                ),
                context=self.context,
                signals=self._signals(),
            )

        second_store = InMemoryStore()
        second_context = OwnerTruthCommandContext(
            vault_id="decision-audit-stale-vault",
            owner_subject_id="decision-audit-stale-owner",
            actor_subject_id="decision-audit-stale-owner",
        )
        thread_id = str(uuid4())
        session_id = str(uuid4())
        conversation = OwnerTruthConversationService(second_store.owner_truth_conversation_repository())
        conversation.start_session(
            command=StartInterviewSessionCommand(
                command_id="decision-audit-stale-start",
                thread_id=thread_id,
                session_id=session_id,
                expected_thread_version=0,
                entry_mode="naturalInput",
            ),
            context=second_context,
        )
        first = conversation.append_message(
            command=AppendInterviewMessageCommand(
                command_id="decision-audit-stale-first",
                thread_id=thread_id,
                session_id=session_id,
                message_id=str(uuid4()),
                expected_thread_version=1,
                expected_session_version=1,
                author=ConversationMessageAuthor.OWNER,
                kind=ConversationMessageKind.NARRATIVE,
                text="第一段私有叙述",
            ),
            context=second_context,
        )
        conversation.append_message(
            command=AppendInterviewMessageCommand(
                command_id="decision-audit-stale-second",
                thread_id=thread_id,
                session_id=session_id,
                message_id=str(uuid4()),
                expected_thread_version=first.thread_version,
                expected_session_version=first.session_version,
                author=ConversationMessageAuthor.OWNER,
                kind=ConversationMessageKind.NARRATIVE,
                text="第二段私有叙述",
            ),
            context=second_context,
        )
        assert first.message_id is not None
        with self.assertRaises(OwnerTruthInterviewDecisionAuditStale):
            OwnerTruthInterviewDecisionAuditService(second_store, enabled=True).decide_and_record(
                command=OwnerTruthInterviewDecisionAuditCommand(
                    command_id="decision-audit-stale-record",
                    thread_id=thread_id,
                    session_id=session_id,
                    message_id=first.message_id,
                    expected_session_version=first.session_version,
                ),
                context=second_context,
                signals=self._signals(),
            )

    def test_default_off_does_not_create_an_audit_lane(self) -> None:
        appended = self._append(command_id="decision-audit-disabled-message")
        assert appended.message_id is not None
        with self.assertRaises(OwnerTruthInterviewDecisionAuditUnavailable):
            self._service(enabled=False).decide_and_record(
                command=self._command(
                    command_id="decision-audit-disabled",
                    message_id=appended.message_id,
                    expected_session_version=appended.session_version,
                ),
                context=self.context,
                signals=self._signals(),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
