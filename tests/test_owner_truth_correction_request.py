from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateReviewConflict,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_answer_citation import (
    OwnerTruthAnswerCitationCommand,
    OwnerTruthAnswerCitationService,
)
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_correction_request import (
    OwnerTruthCorrectionRequestCommand,
    OwnerTruthCorrectionRequestService,
    OwnerTruthCorrectionRequestStaleCitation,
    _authority_epoch_matches,
    correction_request_summary,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthCorrectionRequestTests(unittest.TestCase):
    def test_initial_authority_epoch_zero_is_not_treated_as_missing(self) -> None:
        self.assertTrue(_authority_epoch_matches(0, 0))
        self.assertTrue(_authority_epoch_matches("0", 0))
        self.assertFalse(_authority_epoch_matches(None, 0))
        self.assertFalse(_authority_epoch_matches(1, 0))

    def setUp(self) -> None:
        self.vault_id = "vault-correction-request"
        self.owner_id = "subject-correction-request"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = InMemoryStore()
        self.review_service = OwnerTruthCandidateReviewService(self.store)
        self.projection_service = OwnerTruthMemoryProjectionService(self.store)
        self.answer_service = OwnerTruthAnswerCitationService(self.store, enabled=True)
        self.service = OwnerTruthCorrectionRequestService(self.store, enabled=True)
        self.memory_candidate = self._activate_memory()
        self.answer = self._record_answer()
        self.citation = self.answer.citations[0]

    def _activate_memory(self) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        content = {"summary": "小时候在院子里听父亲讲故事"}
        candidate = OwnerTruthCandidateSnapshot(
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
                "schemaVersion": "owner-truth-candidate-proposal-v1",
                "candidateKind": "experience",
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [
                    {"sourceId": source_id, "sourceVersion": 1, "span": {"start": 0, "end": 10}}
                ],
                "reviewMode": "single",
            },
        )
        self.store.owner_truth_candidate_review_repository().seed(candidate)
        self.review_service.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="correction-request-activate-memory",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )
        self.projection_service.rebuild(context=self.context)
        return candidate

    def _record_answer(self):
        return self.answer_service.record(
            context=self.context,
            command=OwnerTruthAnswerCitationCommand(
                command_id="correction-request-answer-001",
                answer_text="我记得你曾在院子里听父亲讲故事。",
            ),
            context_payload={"intent": "echo_chat", "query": "请说说那段童年记忆"},
        )

    def _command(self, *, command_id: str, expected_memory_version_id: str | None = None):
        fields = self.citation["citation"]
        return OwnerTruthCorrectionRequestCommand(
            command_id=command_id,
            answer_id=self.answer.answer_id,
            citation_id=self.citation["citationId"],
            memory_id=fields["memoryId"],
            expected_memory_version_id=expected_memory_version_id or fields["memoryVersionId"],
            correction_text="不是父亲，是外祖父在院子里讲故事。",
            reason_code="ownerReportedCorrection",
        )

    def test_creates_pending_candidate_from_exact_answer_citation_and_replays(self) -> None:
        command = self._command(command_id="correction-request-001")
        created = self.service.request(context=self.context, command=command)
        replayed = self.service.request(context=self.context, command=command)
        summary = correction_request_summary(created)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.correction_request_id, replayed.correction_request_id)
        self.assertEqual(created.candidate_id, replayed.candidate_id)
        self.assertEqual(created.answer_id, self.answer.answer_id)
        self.assertEqual(created.citation_id, self.citation["citationId"])
        self.assertEqual(created.memory_id, self.citation["citation"]["memoryId"])
        self.assertEqual(
            created.expected_memory_version_id,
            self.citation["citation"]["memoryVersionId"],
        )
        self.assertEqual(summary["status"], "pendingReview")
        self.assertNotIn(command.correction_text, str(summary))
        self.assertNotIn(self.memory_candidate.content["summary"], str(summary))

        inbox = self.review_service.list_pending(context=self.context)
        correction = next(item for item in inbox if item.candidate_id == created.candidate_id)
        self.assertEqual(correction.review_mode, "correction")
        self.assertEqual(correction.source_id, created.correction_source_id)
        self.assertEqual(correction.content, self.memory_candidate.content)
        self.assertEqual(
            correction.source_refs[0]["sourceId"],
            created.correction_source_id,
        )

        with self.assertRaises(OwnerTruthCandidateReviewConflict):
            self.review_service.decide_and_activate(
                command=OwnerTruthCandidateReviewCommand(
                    command_id="correction-request-generic-decision",
                    candidate_id=created.candidate_id,
                    expected_candidate_version=1,
                    action=CandidateReviewAction.CORRECT,
                    corrected_value={"summary": "外祖父在院子里讲故事"},
                    corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                    reason_code="ownerReviewed",
                ),
                context=self.context,
            )

    def test_fails_closed_for_stale_citation_and_non_owner(self) -> None:
        with self.assertRaises(OwnerTruthCorrectionRequestStaleCitation):
            self.service.request(
                context=self.context,
                command=self._command(
                    command_id="correction-request-stale-001",
                    expected_memory_version_id=str(uuid4()),
                ),
            )

        non_owner = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id="another-subject",
        )
        with self.assertRaises(Exception) as error:
            self.service.request(
                context=non_owner,
                command=self._command(command_id="correction-request-denied-001"),
            )
        self.assertIn("only the Vault Owner", str(error.exception))

    def test_rejects_command_id_payload_reuse(self) -> None:
        first = self._command(command_id="correction-request-conflict-001")
        self.service.request(context=self.context, command=first)
        conflicting = OwnerTruthCorrectionRequestCommand(
            command_id=first.command_id,
            answer_id=first.answer_id,
            citation_id=first.citation_id,
            memory_id=first.memory_id,
            expected_memory_version_id=first.expected_memory_version_id,
            correction_text="实际发生在学校操场。",
            reason_code=first.reason_code,
        )
        with self.assertRaises(Exception) as error:
            self.service.request(context=self.context, command=conflicting)
        self.assertIn("commandId", str(error.exception))


if __name__ == "__main__":
    unittest.main()
