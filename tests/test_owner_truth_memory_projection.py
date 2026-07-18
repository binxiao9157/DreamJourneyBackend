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
from app.services.owner_truth_memory_projection import (
    InMemoryOwnerTruthMemoryProjectionRepository,
    OwnerTruthMemoryProjectionService,
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

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository


class OwnerTruthMemoryProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-memory-projection"
        self.owner_id = "subject-memory-projection"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _Store()
        self.review_service = OwnerTruthCandidateReviewService(self.store)
        self.projection_service = OwnerTruthMemoryProjectionService(self.store)

    def _candidate(
        self,
        *,
        kind: MemoryKind = MemoryKind.EXPERIENCE,
        content: dict[str, str] | None = None,
    ) -> OwnerTruthCandidateSnapshot:
        defaults = {
            MemoryKind.EXPERIENCE: {"summary": "小时候在院子里听雨"},
            MemoryKind.KNOWLEDGE: {"claim": "父亲总会先修好自行车"},
            MemoryKind.EMOTION: {"label": "怀念"},
        }
        content = content or defaults[kind]
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
                    {
                        "sourceId": source_id,
                        "sourceVersion": 1,
                        "span": {"start": 0, "end": 10},
                    }
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

    def _activate(
        self,
        candidate: OwnerTruthCandidateSnapshot,
        *,
        command_id: str,
        action: CandidateReviewAction = CandidateReviewAction.ACCEPT,
        corrected_value: dict[str, str] | None = None,
    ):
        self.store.review_repository.seed(candidate)
        return self.review_service.decide_and_activate(
            command=self._command(
                candidate,
                command_id=command_id,
                action=action,
                corrected_value=corrected_value,
            ),
            context=self.context,
        )

    def test_missing_checkpoint_fails_closed_then_rebuilds_deterministically(self) -> None:
        candidate = self._candidate()
        activation = self._activate(candidate, command_id="projection-accept-001")

        missing = self.projection_service.read(context=self.context)
        self.assertEqual(missing["state"], "rebuilding")
        self.assertEqual(missing["entries"], [])

        first = self.projection_service.rebuild(context=self.context)
        second = self.projection_service.rebuild(context=self.context)
        ready = self.projection_service.read(context=self.context)

        self.assertEqual(first.outcome, "rebuilt")
        self.assertEqual(second.outcome, "unchanged")
        self.assertEqual(first.snapshot["schemaVersion"], "owner-truth-memory-projection-v1")
        self.assertEqual(first.snapshot["projectionSource"], "v4")
        self.assertEqual(first.snapshot["state"], "ready")
        self.assertEqual(first.snapshot["checkpoint"], second.snapshot["checkpoint"])
        self.assertEqual(first.snapshot["entryCount"], 1)
        self.assertEqual(ready["checkpoint"], first.snapshot["checkpoint"])
        entry = ready["entries"][0]
        self.assertEqual(entry["memoryId"], activation.memory_activation.memory_id)
        self.assertEqual(entry["memoryVersionId"], activation.memory_activation.memory_version_id)
        self.assertEqual(entry["content"], candidate.content)
        self.assertEqual(entry["citation"]["sourceId"], candidate.source_id)
        self.assertNotIn("decisionReceiptId", str(entry))
        self.assertNotIn("rationale", str(entry))

    def test_corrected_owner_value_is_projected_not_original_candidate(self) -> None:
        candidate = self._candidate(kind=MemoryKind.KNOWLEDGE)
        corrected = {"claim": "父亲总会先修好自行车，再带我去公园"}
        self._activate(
            candidate,
            command_id="projection-correct-001",
            action=CandidateReviewAction.CORRECT,
            corrected_value=corrected,
        )

        snapshot = self.projection_service.rebuild(context=self.context).snapshot

        self.assertEqual(snapshot["entries"][0]["content"], corrected)
        self.assertNotEqual(snapshot["entries"][0]["content"], candidate.content)

    def test_rejected_candidate_never_enters_projection(self) -> None:
        candidate = self._candidate()
        result = self._activate(
            candidate,
            command_id="projection-reject-001",
            action=CandidateReviewAction.REJECT,
        )

        snapshot = self.projection_service.rebuild(context=self.context).snapshot

        self.assertEqual(result.memory_activation.outcome, "notApplicable")
        self.assertEqual(snapshot["entryCount"], 0)
        self.assertEqual(snapshot["entries"], [])

    def test_new_memory_or_source_revocation_marks_existing_checkpoint_rebuilding(self) -> None:
        first_candidate = self._candidate()
        self._activate(first_candidate, command_id="projection-first-001")
        self.projection_service.rebuild(context=self.context)

        second_candidate = self._candidate(kind=MemoryKind.EMOTION)
        self._activate(second_candidate, command_id="projection-second-001")
        changed = self.projection_service.read(context=self.context)
        self.assertEqual(changed["state"], "rebuilding")
        self.assertEqual(changed["entries"], [])

        self.projection_service.rebuild(context=self.context)
        self.store.review_repository._source_states[(self.vault_id, second_candidate.source_id)] = "deleted"
        revoked = self.projection_service.read(context=self.context)
        self.assertEqual(revoked["state"], "rebuilding")
        self.assertEqual(revoked["entries"], [])


if __name__ == "__main__":
    unittest.main()
