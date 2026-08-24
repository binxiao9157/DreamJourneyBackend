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
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION,
    OWNER_TRUTH_SCHEMA_VERSION_V2,
    OWNER_TRUTH_SCHEMA_VERSION_V3,
    empty_memory_facets,
)
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

    def _candidate(
        self,
        *,
        kind: MemoryKind = MemoryKind.EXPERIENCE,
        schema_version: str = OWNER_TRUTH_SCHEMA_VERSION,
    ) -> OwnerTruthCandidateSnapshot:
        if schema_version == OWNER_TRUTH_SCHEMA_VERSION_V3:
            content = {
                MemoryKind.EXPERIENCE: {
                    "event": "小时候在院子里听雨",
                    "time": {"start": None, "end": None, "precision": "unknown"},
                    "facets": empty_memory_facets(confidence=1.0),
                },
                MemoryKind.KNOWLEDGE: {
                    "statement": "父亲总会先修好自行车",
                    "knowledgeType": "personal_experience",
                    "domains": [],
                    "facets": empty_memory_facets(confidence=1.0),
                },
                MemoryKind.EMOTION: {
                    "emotion": "怀念",
                    "expression": "想起这件事时，我会怀念父亲。",
                    "facets": empty_memory_facets(confidence=1.0),
                },
            }[kind]
        else:
            content = {
                MemoryKind.EXPERIENCE: {"summary": "小时候在院子里听雨"},
                MemoryKind.KNOWLEDGE: {"claim": "父亲总会先修好自行车"},
                MemoryKind.EMOTION: {"label": "怀念"},
            }[kind]
        if schema_version == OWNER_TRUTH_SCHEMA_VERSION_V2:
            content["facets"] = {
                **empty_memory_facets(confidence=0.91),
                "people": [
                    {
                        "value": "外公",
                        "evidenceMode": "ownerStated",
                        "confidence": 1.0,
                    }
                ],
                "emotions": [
                    {
                        "value": "安心",
                        "evidenceMode": "inferred",
                        "confidence": 0.72,
                    }
                ],
            }
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
            content_schema_version=schema_version,
            payload={
                "content": content,
                "contentSchemaVersion": schema_version,
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
        corrected_value: dict[str, object] | None = None,
        corrected_value_schema_version: str | None = None,
    ) -> OwnerTruthCandidateReviewCommand:
        return OwnerTruthCandidateReviewCommand(
            command_id=command_id,
            candidate_id=candidate.candidate_id,
            expected_candidate_version=candidate.row_version,
            action=action,
            corrected_value=corrected_value,
            corrected_value_schema_version=(
                corrected_value_schema_version or candidate.content_schema_version
            ),
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

    def test_accept_v2_preserves_reviewed_facets_in_immutable_memory_version(self) -> None:
        candidate = self._candidate(schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2)
        self.store.repository.seed(candidate)

        result = self.service.decide_and_activate(
            command=self._command(
                candidate,
                command_id="memory-activation-v2-accept-001",
                action=CandidateReviewAction.ACCEPT,
            ),
            context=self.context,
        )

        activated = self.store.repository.snapshot()["memoryActivations"][result.review.receipt_id]
        self.assertEqual(
            activated["payload"]["contentSchemaVersion"],
            OWNER_TRUTH_SCHEMA_VERSION_V2,
        )
        self.assertEqual(
            activated["payload"]["content"]["facets"],
            candidate.content["facets"],
        )
        self.assertEqual(
            activated["payload"]["content"]["facets"]["emotions"][0]["evidenceMode"],
            "inferred",
        )

    def test_accept_v3_preserves_typed_memory_content(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            schema_version=OWNER_TRUTH_SCHEMA_VERSION_V3,
        )
        self.store.repository.seed(candidate)

        result = self.service.decide_and_activate(
            command=self._command(
                candidate,
                command_id="memory-activation-v3-accept-001",
                action=CandidateReviewAction.ACCEPT,
            ),
            context=self.context,
        )

        activated = self.store.repository.snapshot()["memoryActivations"][result.review.receipt_id]
        self.assertEqual(
            activated["payload"]["contentSchemaVersion"],
            OWNER_TRUTH_SCHEMA_VERSION_V3,
        )
        self.assertEqual(
            activated["payload"]["content"]["statement"],
            "父亲总会先修好自行车",
        )
        self.assertNotIn("claim", activated["payload"]["content"])

    def test_correct_v2_persists_owner_stated_facet_without_mutating_proposal(self) -> None:
        candidate = self._candidate(schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2)
        original_payload = json.loads(json.dumps(candidate.payload, ensure_ascii=False))
        self.store.repository.seed(candidate)
        corrected = {
            "summary": "小时候在院子里和外公一起听雨",
            "facets": {
                **empty_memory_facets(confidence=1.0),
                "people": [
                    {
                        "value": "外公",
                        "evidenceMode": "ownerStated",
                        "confidence": 1.0,
                    }
                ],
                "relationships": [
                    {
                        "value": "外孙与外公",
                        "evidenceMode": "ownerStated",
                        "confidence": 1.0,
                    }
                ],
            },
        }

        result = self.service.decide_and_activate(
            command=self._command(
                candidate,
                command_id="memory-activation-v2-correct-001",
                action=CandidateReviewAction.CORRECT,
                corrected_value=corrected,
            ),
            context=self.context,
        )

        snapshot = self.store.repository.snapshot()
        self.assertEqual(snapshot["candidates"][candidate.candidate_id]["payload"], original_payload)
        activated = snapshot["memoryActivations"][result.review.receipt_id]
        self.assertEqual(activated["payload"]["content"], corrected)
        self.assertEqual(
            activated["payload"]["content"]["facets"]["relationships"][0]["evidenceMode"],
            "ownerStated",
        )

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
