from __future__ import annotations

from contextlib import contextmanager
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
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Store:
    def __init__(self) -> None:
        self.repository = InMemoryOwnerTruthCandidateReviewRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.repository


class OwnerTruthMemoryActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-memory-activation"
        self.owner_id = "subject-memory-activation"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _Store()
        self.service = OwnerTruthCandidateReviewService(self.store)

    def _candidate(self, *, kind: MemoryKind = MemoryKind.EXPERIENCE) -> OwnerTruthCandidateSnapshot:
        content = {
            MemoryKind.EXPERIENCE: {"summary": "小时候在院子里听雨"},
            MemoryKind.KNOWLEDGE: {"claim": "父亲总会先修好自行车"},
            MemoryKind.EMOTION: {"label": "怀念"},
        }[kind]
        source_id = str(uuid4())
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=kind,
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
        command_id: str,
        action: CandidateReviewAction,
        corrected_value: dict[str, str] | None = None,
    ) -> OwnerTruthCandidateReviewCommand:
        return OwnerTruthCandidateReviewCommand(
            command_id=command_id,
            candidate_id=candidate.candidate_id,
            expected_candidate_version=candidate.row_version,
            action=action,
            corrected_value=corrected_value,
            corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            reason_code="ownerReviewed",
        )

    def test_accept_creates_one_initial_current_memory_version_and_replays(self) -> None:
        candidate = self._candidate()
        self.store.repository.seed(candidate)
        command = self._command(
            candidate,
            command_id="memory-activation-accept-001",
            action=CandidateReviewAction.ACCEPT,
        )

        created = self.service.decide_and_activate(command=command, context=self.context)
        replayed = self.service.decide_and_activate(command=command, context=self.context)

        self.assertEqual(created.review.outcome, "created")
        self.assertEqual(created.memory_activation.outcome, "created")
        self.assertEqual(replayed.review.outcome, "deduplicated")
        self.assertEqual(replayed.memory_activation.outcome, "deduplicated")
        self.assertEqual(created.memory_activation.memory_id, replayed.memory_activation.memory_id)
        snapshot = self.store.repository.snapshot()
        self.assertEqual(len(snapshot["memoryActivations"]), 1)
        activated = snapshot["memoryActivations"][created.review.receipt_id]
        self.assertEqual(activated["payload"]["content"], candidate.content)
        self.assertEqual(activated["payload"]["candidateId"], candidate.candidate_id)
        self.assertEqual(activated["payload"]["decisionReceiptId"], created.review.receipt_id)

    def test_correct_uses_owner_value_without_mutating_candidate_proposal(self) -> None:
        candidate = self._candidate(kind=MemoryKind.KNOWLEDGE)
        original_payload = json.loads(json.dumps(candidate.payload, ensure_ascii=False))
        self.store.repository.seed(candidate)
        corrected = {"claim": "父亲总会先修好自行车，再带我去公园"}

        result = self.service.decide_and_activate(
            command=self._command(
                candidate,
                command_id="memory-activation-correct-001",
                action=CandidateReviewAction.CORRECT,
                corrected_value=corrected,
            ),
            context=self.context,
        )

        self.assertEqual(result.review.decision, CandidateDecision.CORRECTED)
        self.assertEqual(result.memory_activation.outcome, "created")
        snapshot = self.store.repository.snapshot()
        self.assertEqual(snapshot["candidates"][candidate.candidate_id]["payload"], original_payload)
        activated = snapshot["memoryActivations"][result.review.receipt_id]
        self.assertEqual(activated["payload"]["content"], corrected)
        self.assertEqual(activated["contentHash"], result.review.candidate_after_hash)

    def test_reject_creates_no_memory_activation(self) -> None:
        candidate = self._candidate()
        self.store.repository.seed(candidate)

        result = self.service.decide_and_activate(
            command=self._command(
                candidate,
                command_id="memory-activation-reject-001",
                action=CandidateReviewAction.REJECT,
            ),
            context=self.context,
        )

        self.assertEqual(result.review.decision, CandidateDecision.REJECTED)
        self.assertEqual(result.memory_activation.outcome, "notApplicable")
        self.assertIsNone(result.memory_activation.memory_id)
        self.assertEqual(self.store.repository.snapshot()["memoryActivations"], {})


if __name__ == "__main__":
    unittest.main()
