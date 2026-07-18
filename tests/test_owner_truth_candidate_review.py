from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewAccessDenied,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateReviewConflict,
    OwnerTruthCandidateReviewSourceInactive,
    OwnerTruthCandidateSnapshot,
    OwnerTruthCandidateVersionConflict,
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
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)


def _hash(value):
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Store:
    def __init__(self):
        self.repository = InMemoryOwnerTruthCandidateReviewRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.repository


class OwnerTruthCandidateReviewTests(unittest.TestCase):
    def setUp(self):
        self.vault_id = "vault-owner-review"
        self.owner_subject_id = "subject-owner-review"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            actor_subject_id=self.owner_subject_id,
        )
        self.store = _Store()
        self.service = OwnerTruthCandidateReviewService(self.store)

    def _candidate(
        self,
        *,
        kind: MemoryKind = MemoryKind.EXPERIENCE,
        decision: CandidateDecision = CandidateDecision.PENDING,
        row_version: int = 1,
        candidate_id: str | None = None,
    ) -> OwnerTruthCandidateSnapshot:
        content = {
            MemoryKind.EXPERIENCE: {"summary": "小时候在院子里听雨"},
            MemoryKind.KNOWLEDGE: {"claim": "父亲总会先修好自行车"},
            MemoryKind.EMOTION: {"label": "怀念"},
        }[kind]
        source_id = str(uuid4())
        return OwnerTruthCandidateSnapshot(
            candidate_id=candidate_id or str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=source_id,
            memory_kind=kind,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=decision,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=row_version,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [
                    {"sourceId": source_id, "sourceVersion": 1, "span": {"start": 0, "end": 10}}
                ],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    @staticmethod
    def _command(
        candidate: OwnerTruthCandidateSnapshot,
        *,
        command_id: str = "candidate-review-001",
        action: CandidateReviewAction = CandidateReviewAction.ACCEPT,
        corrected_value=None,
        expected_version: int | None = None,
    ) -> OwnerTruthCandidateReviewCommand:
        return OwnerTruthCandidateReviewCommand(
            command_id=command_id,
            candidate_id=candidate.candidate_id,
            expected_candidate_version=expected_version or candidate.row_version,
            action=action,
            corrected_value=corrected_value,
            corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            reason_code="ownerReviewed",
        )

    def test_accepts_once_and_replays_same_command_without_second_receipt(self):
        candidate = self._candidate()
        self.store.repository.seed(candidate)
        command = self._command(candidate)

        created = self.service.decide(command=command, context=self.context)
        replayed = self.service.decide(command=command, context=self.context)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(created.decision, CandidateDecision.ACCEPTED)
        self.assertEqual(created.candidate_row_version, 2)
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(replayed.receipt_id, created.receipt_id)
        snapshot = self.store.repository.snapshot()
        self.assertEqual(snapshot["candidates"][candidate.candidate_id]["decision"], "accepted")
        self.assertEqual(len(snapshot["receipts"]), 1)
        self.assertEqual(snapshot["correctedValues"], {})

    def test_correct_preserves_processor_candidate_and_keeps_owner_value_separate(self):
        candidate = self._candidate(kind=MemoryKind.KNOWLEDGE)
        original_payload = json.loads(json.dumps(candidate.payload, ensure_ascii=False))
        self.store.repository.seed(candidate)
        corrected = {"claim": "父亲总是先修好自行车，再带我去公园"}

        result = self.service.decide(
            command=self._command(
                candidate,
                action=CandidateReviewAction.CORRECT,
                corrected_value=corrected,
            ),
            context=self.context,
        )

        snapshot = self.store.repository.snapshot()
        self.assertEqual(result.decision, CandidateDecision.CORRECTED)
        self.assertNotEqual(result.candidate_after_hash, candidate.content_hash)
        self.assertEqual(snapshot["candidates"][candidate.candidate_id]["payload"], original_payload)
        self.assertEqual(len(snapshot["correctedValues"]), 1)
        stored = next(iter(snapshot["correctedValues"].values()))
        self.assertEqual(stored["content"], corrected)
        self.assertEqual(stored["contentHash"], result.candidate_after_hash)

    def test_rejects_stale_cross_owner_reused_command_and_deleted_source(self):
        candidate = self._candidate()
        self.store.repository.seed(candidate)

        with self.assertRaises(OwnerTruthCandidateVersionConflict):
            self.service.decide(
                command=self._command(candidate, expected_version=2),
                context=self.context,
            )
        with self.assertRaises(OwnerTruthCandidateReviewAccessDenied):
            self.service.decide(
                command=self._command(candidate),
                context=OwnerTruthCommandContext(
                    vault_id=self.vault_id,
                    owner_subject_id=self.owner_subject_id,
                    actor_subject_id="subject-not-owner",
                ),
            )

        first = self.service.decide(command=self._command(candidate), context=self.context)
        other = self._candidate()
        self.store.repository.seed(other)
        with self.assertRaises(OwnerTruthCandidateReviewConflict):
            self.service.decide(
                command=self._command(other, command_id="candidate-review-001"),
                context=self.context,
            )
        self.assertEqual(first.decision, CandidateDecision.ACCEPTED)

        inactive = self._candidate()
        self.store.repository.seed(inactive, source_state="deleted")
        with self.assertRaises(OwnerTruthCandidateReviewSourceInactive):
            self.service.decide(
                command=self._command(inactive, command_id="candidate-review-inactive-001"),
                context=self.context,
            )

    def test_owner_inbox_only_returns_active_pending_candidates(self):
        pending = self._candidate()
        terminal = self._candidate(decision=CandidateDecision.REJECTED)
        inactive = self._candidate()
        self.store.repository.seed(pending)
        self.store.repository.seed(terminal)
        self.store.repository.seed(inactive, source_state="redacted")

        inbox = self.service.list_pending(context=self.context)

        self.assertEqual([item.candidate_id for item in inbox], [pending.candidate_id])
        self.assertEqual(inbox[0].content, pending.content)
        self.assertEqual(inbox[0].source_refs[0]["sourceId"], pending.source_id)


if __name__ == "__main__":
    unittest.main()
