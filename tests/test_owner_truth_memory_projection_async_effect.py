from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.async_effects.repository import InMemoryEffectKernelRepository
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
from app.services.owner_truth_memory_projection_effects import (
    MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
    MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _TransactionalEffectWriter:
    """Test-only writer that can simulate a failure after a durable insert."""

    def __init__(self, *, fail_after_write: bool = False) -> None:
        self._repository = InMemoryEffectKernelRepository()
        self._fail_after_write = fail_after_write
        self.intents = []

    def accept(self, intent):
        summary = self._repository.accept(intent)
        self.intents.append(intent)
        if self._fail_after_write:
            raise RuntimeError("synthetic memory projection effect write failure")
        return summary

    def snapshot(self):
        return {
            "intents": list(self.intents),
            "records": self._repository.snapshot(),
        }

    def restore(self, snapshot) -> None:
        self.intents = list(snapshot["intents"])
        self._repository._records = deepcopy(snapshot["records"])

    @property
    def record_count(self) -> int:
        return self._repository.record_count()


class _AtomicCandidateEffectStore:
    """Semantic UoW double proving review, MemoryVersion and effect atomicity."""

    _REPOSITORY_FIELDS = (
        "_candidates",
        "_candidate_created_at",
        "_source_states",
        "_vault_states",
        "_receipts",
        "_candidate_receipts",
        "_corrected_values",
        "_memory_activations",
    )

    def __init__(self, *, effect_writer: _TransactionalEffectWriter | None = None) -> None:
        self.review_repository = InMemoryOwnerTruthCandidateReviewRepository()
        self.effect_writer = effect_writer or _TransactionalEffectWriter()
        self._active = False
        self.rollback_count = 0
        self.root_uow_count = 0

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        if not correlation_id or len(command_id) != 64:
            raise AssertionError("effect work must use opaque correlation and command hashes")
        if self._active:
            yield self
            return
        review_snapshot = self._snapshot_review_repository()
        effect_snapshot = self.effect_writer.snapshot()
        self._active = True
        self.root_uow_count += 1
        try:
            yield self
        except Exception:
            self._restore_review_repository(review_snapshot)
            self.effect_writer.restore(effect_snapshot)
            self.rollback_count += 1
            raise
        finally:
            self._active = False

    def _snapshot_review_repository(self):
        with self.review_repository._lock:
            return {
                field: deepcopy(getattr(self.review_repository, field))
                for field in self._REPOSITORY_FIELDS
            }

    def _restore_review_repository(self, snapshot) -> None:
        with self.review_repository._lock:
            for field, value in snapshot.items():
                setattr(self.review_repository, field, deepcopy(value))

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def effect_kernel_repository(self):
        if not self._active:
            raise AssertionError("effect write escaped its unit of work")
        return self.effect_writer


class OwnerTruthMemoryProjectionAsyncEffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-memory-projection-effect"
        self.owner_id = "subject-memory-projection-effect"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _AtomicCandidateEffectStore()
        self.service = OwnerTruthCandidateReviewService(self.store)

    def _candidate(self) -> OwnerTruthCandidateSnapshot:
        content = {"summary": "外婆会在傍晚讲起院子里的桂花。"}
        source_id = str(uuid4())
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
    ) -> OwnerTruthCandidateReviewCommand:
        return OwnerTruthCandidateReviewCommand(
            command_id=command_id,
            candidate_id=candidate.candidate_id,
            expected_candidate_version=candidate.row_version,
            action=action,
            corrected_value=None,
            corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            reason_code="ownerReviewed",
        )

    def test_activation_and_projection_effect_share_one_idempotent_uow(self) -> None:
        candidate = self._candidate()
        self.store.review_repository.seed(candidate)
        command = self._command(
            candidate,
            command_id="memory-projection-effect-accept-001",
            action=CandidateReviewAction.ACCEPT,
        )

        created = self.service.decide_and_activate(command=command, context=self.context)
        replayed = self.service.decide_and_activate(command=command, context=self.context)

        self.assertEqual(self.store.root_uow_count, 2)
        self.assertIsNotNone(created.projection_effect)
        self.assertIsNotNone(replayed.projection_effect)
        self.assertEqual(created.projection_effect.outcome, "accepted")
        self.assertEqual(replayed.projection_effect.outcome, "deduplicated")
        self.assertEqual(created.projection_effect.operation_id, replayed.projection_effect.operation_id)
        self.assertEqual(self.store.effect_writer.record_count, 1)
        self.assertEqual(len(self.store.effect_writer.intents), 2)

        intent = self.store.effect_writer.intents[0]
        self.assertEqual(intent.operation_type, MEMORY_PROJECTION_REBUILD_OPERATION_TYPE)
        self.assertEqual(intent.event_type, MEMORY_PROJECTION_REBUILD_EVENT_TYPE)
        self.assertEqual(intent.job_type, MEMORY_PROJECTION_REBUILD_JOB_TYPE)
        self.assertEqual(intent.target.resource_type, "memoryVersion")
        self.assertEqual(intent.target.resource_id, created.memory_activation.memory_version_id)
        self.assertEqual(intent.target.resource_version, 1)
        self.assertEqual(intent.target.purpose, "compatibilityProjection")
        self.assertEqual(intent.target.authority_epoch, 0)
        self.assertEqual(intent.payload_hash, created.memory_activation.content_hash)
        self.assertNotIn(candidate.content["summary"], str(intent))
        self.assertNotIn(created.review.receipt_id, str(intent))

    def test_rejected_candidate_does_not_enqueue_a_projection_effect(self) -> None:
        candidate = self._candidate()
        self.store.review_repository.seed(candidate)

        result = self.service.decide_and_activate(
            command=self._command(
                candidate,
                command_id="memory-projection-effect-reject-001",
                action=CandidateReviewAction.REJECT,
            ),
            context=self.context,
        )

        self.assertEqual(result.memory_activation.outcome, "notApplicable")
        self.assertIsNone(result.projection_effect)
        self.assertEqual(self.store.effect_writer.record_count, 0)
        self.assertEqual(self.store.effect_writer.intents, [])

    def test_effect_write_failure_rolls_back_review_memory_and_effect(self) -> None:
        writer = _TransactionalEffectWriter(fail_after_write=True)
        store = _AtomicCandidateEffectStore(effect_writer=writer)
        service = OwnerTruthCandidateReviewService(store)
        candidate = self._candidate()
        store.review_repository.seed(candidate)

        with self.assertRaisesRegex(RuntimeError, "synthetic memory projection effect write failure"):
            service.decide_and_activate(
                command=self._command(
                    candidate,
                    command_id="memory-projection-effect-fail-001",
                    action=CandidateReviewAction.ACCEPT,
                ),
                context=self.context,
            )

        snapshot = store.review_repository.snapshot()
        self.assertEqual(store.rollback_count, 1)
        self.assertEqual(snapshot["candidates"][candidate.candidate_id]["decision"], "pending")
        self.assertEqual(snapshot["receipts"], {})
        self.assertEqual(snapshot["memoryActivations"], {})
        self.assertEqual(writer.record_count, 0)
        self.assertEqual(writer.intents, [])


if __name__ == "__main__":
    unittest.main()
