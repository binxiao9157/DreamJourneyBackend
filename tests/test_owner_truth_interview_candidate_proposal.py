from __future__ import annotations

from contextlib import contextmanager
import unittest
from uuid import uuid4

from app.async_effects.repository import InMemoryEffectKernelRepository
from app.domain.owner_truth.contracts import SourceKind
from app.domain.owner_truth.interview_candidate_proposal import (
    AdmitInterviewReviewBatchForCandidateProposalCommand,
    OwnerTruthInterviewCandidateProposalAccessDenied,
    OwnerTruthInterviewCandidateProposalConflict,
    OwnerTruthInterviewCandidateProposalVersionConflict,
)
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
    OwnerTruthSourceCommandResult,
)
from app.services.owner_truth_interview_candidate_proposal import (
    InMemoryOwnerTruthInterviewCandidateProposalRepository,
    OwnerTruthInterviewCandidateProposalService,
)


class _AdmissionStore:
    def __init__(self) -> None:
        self.repository = InMemoryOwnerTruthInterviewCandidateProposalRepository()
        self.effects = InMemoryEffectKernelRepository()
        self.sources: dict[tuple[str, str], dict[str, object]] = {}

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_interview_candidate_proposal_repository(self):
        return self.repository

    def create_owner_truth_source(self, record):
        key = (record.vault_id, record.source_id)
        existing = self.sources.get(key)
        if existing is not None:
            if existing["contentHash"] != record.content_hash:
                raise AssertionError("source identity must remain immutable")
            return OwnerTruthSourceCommandResult(
                outcome="deduplicated",
                receipt_id=str(existing["receiptId"]),
                source_id=record.source_id,
                source_version=1,
                authority_epoch=0,
                content_hash=record.content_hash,
            )
        self.sources[key] = {
            "contentHash": record.content_hash,
            "contentPayload": dict(record.content_payload),
            "metadata": dict(record.metadata),
            "receiptId": record.receipt_id,
            "sourceKind": record.source_kind,
        }
        return OwnerTruthSourceCommandResult(
            outcome="created",
            receipt_id=record.receipt_id,
            source_id=record.source_id,
            source_version=1,
            authority_epoch=0,
            content_hash=record.content_hash,
        )

    def effect_kernel_repository(self):
        return self.effects


class OwnerTruthInterviewCandidateProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = OwnerTruthCommandContext(
            vault_id="interview-candidate-vault-a",
            owner_subject_id="interview-candidate-owner-a",
            actor_subject_id="interview-candidate-owner-a",
            policy_version="owner-truth-v1",
        )
        self.store = _AdmissionStore()
        self.service = OwnerTruthInterviewCandidateProposalService(self.store)
        self.review_batch_id = str(uuid4())
        self.thread_id = str(uuid4())
        self.session_id = str(uuid4())
        self.store.repository.seed_review_batch(
            review_batch_id=self.review_batch_id,
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            thread_id=self.thread_id,
            session_id=self.session_id,
            owner_messages=(
                (1, "我小时候常在河边听外公讲故事。"),
                (3, "那条河让我一直记得很安静。"),
            ),
        )

    def _command(self, *, command_id: str = "admit-review-batch-a", version: int = 2):
        return AdmitInterviewReviewBatchForCandidateProposalCommand(
            command_id=command_id,
            review_batch_id=self.review_batch_id,
            expected_review_batch_version=version,
        )

    def test_acknowledged_batch_creates_one_conversation_source_and_default_off_effect(self) -> None:
        created = self.service.admit_review_batch(command=self._command(), context=self.context)
        replayed = self.service.admit_review_batch(command=self._command(), context=self.context)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.source_id, replayed.source_id)
        self.assertEqual(created.owner_message_count, 2)
        self.assertEqual(self.store.effects.record_count(), 1)
        source = self.store.sources[(self.context.vault_id, created.source_id)]
        self.assertEqual(source["sourceKind"], SourceKind.CONVERSATION)
        self.assertEqual(
            source["contentPayload"],
            {
                "schemaVersion": "owner-truth-create-source-v1",
                "sourceKind": "conversation",
                "text": "我小时候常在河边听外公讲故事。\n\n那条河让我一直记得很安静。",
            },
        )
        metadata = source["metadata"]
        self.assertEqual(metadata["origin"], "interviewReviewBatchCandidateProposal")
        self.assertEqual(metadata["reviewBatchId"], self.review_batch_id)
        self.assertEqual(metadata["ownerMessageCount"], 2)
        self.assertNotIn("text", metadata)
        self.assertNotIn("content", metadata)
        self.assertNotIn("candidateId", metadata)
        self.assertNotIn("memoryVersionId", metadata)

    def test_pending_or_stale_batch_cannot_create_source_or_effect(self) -> None:
        pending_batch_id = str(uuid4())
        self.store.repository.seed_review_batch(
            review_batch_id=pending_batch_id,
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            thread_id=str(uuid4()),
            session_id=str(uuid4()),
            owner_messages=((1, "这条内容尚未确认。"),),
            state="pendingAcknowledgement",
            row_version=1,
        )
        with self.assertRaises(OwnerTruthInterviewCandidateProposalConflict):
            self.service.admit_review_batch(
                command=AdmitInterviewReviewBatchForCandidateProposalCommand(
                    command_id="pending-batch",
                    review_batch_id=pending_batch_id,
                    expected_review_batch_version=1,
                ),
                context=self.context,
            )
        with self.assertRaises(OwnerTruthInterviewCandidateProposalVersionConflict):
            self.service.admit_review_batch(command=self._command(version=1), context=self.context)
        self.assertEqual(self.store.sources, {})
        self.assertEqual(self.store.effects.record_count(), 0)

    def test_other_owner_cannot_admit_the_review_batch(self) -> None:
        other_context = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id="interview-candidate-owner-b",
            actor_subject_id="interview-candidate-owner-b",
            policy_version="owner-truth-v1",
        )
        with self.assertRaises(OwnerTruthInterviewCandidateProposalAccessDenied):
            self.service.admit_review_batch(command=self._command(), context=other_context)
        self.assertEqual(self.store.sources, {})
        self.assertEqual(self.store.effects.record_count(), 0)

    def test_text_source_defaults_keep_legacy_payload_shape_while_conversation_is_explicit(self) -> None:
        text_source = CreateTextSourceCommand(
            command_id="text-source-legacy-shape",
            source_id=str(uuid4()),
            expected_version=0,
            text="普通文本来源。",
            metadata={},
        ).write_record(context=self.context)
        conversation_source = CreateTextSourceCommand(
            command_id="conversation-source-shape",
            source_id=str(uuid4()),
            expected_version=0,
            text="访谈来源。",
            metadata={},
            source_kind=SourceKind.CONVERSATION,
        ).write_record(context=self.context)

        self.assertEqual(text_source.source_kind, SourceKind.TEXT)
        self.assertNotIn("sourceKind", text_source.content_payload)
        self.assertEqual(conversation_source.source_kind, SourceKind.CONVERSATION)
        self.assertEqual(conversation_source.content_payload["sourceKind"], "conversation")
        self.assertNotEqual(text_source.content_hash, conversation_source.content_hash)


if __name__ == "__main__":
    unittest.main()
