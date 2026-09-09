from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.async_effects.repository import InMemoryEffectKernelRepository
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
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
from app.domain.owner_truth.memory_changeset_group import (
    OwnerTruthMemoryChangeSetGroupCommand,
    OwnerTruthMemoryChangeSetGroupDependency,
    OwnerTruthMemoryChangeSetGroupSelection,
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    enrich_memory_payload_v5,
    reextract_owner_corrected_memory_payload,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
)
from app.services.owner_truth_memory_changeset_group_review import (
    OwnerTruthMemoryChangeSetGroupReviewService,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Store:
    def __init__(
        self,
        *,
        repository: InMemoryOwnerTruthCandidateReviewRepository | None = None,
        effects: InMemoryEffectKernelRepository | None = None,
    ) -> None:
        self.effects = effects or InMemoryEffectKernelRepository()
        self.repository = repository or InMemoryOwnerTruthCandidateReviewRepository(
            memory_projection_rebuild_runnable_reader=self.effects.is_runnable
        )

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_candidate_review_repository(self):
        return self.repository

    def effect_kernel_repository(self):
        return self.effects


class _FailSecondActivationRepository(InMemoryOwnerTruthCandidateReviewRepository):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.activation_calls = 0

    def activate_memory_version(self, **kwargs):
        self.activation_calls += 1
        result = super().activate_memory_version(**kwargs)
        if self.activation_calls == 2:
            raise RuntimeError("injected second formal-memory write failure")
        return result


class _FailSecondReceiptRepository(InMemoryOwnerTruthCandidateReviewRepository):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.decision_calls = 0

    def decide(self, **kwargs):
        self.decision_calls += 1
        result = super().decide(**kwargs)
        if self.decision_calls == 2:
            raise RuntimeError("injected second receipt write failure")
        return result


class _FailSecondEffectRepository(InMemoryEffectKernelRepository):
    def __init__(self) -> None:
        super().__init__()
        self.accept_calls = 0

    def accept(self, intent):
        self.accept_calls += 1
        result = super().accept(intent)
        if self.accept_calls == 2:
            raise RuntimeError("injected second Outbox write failure")
        return result


class OwnerTruthMemoryChangeSetGroupReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-memory-changeset-group"
        self.owner_id = "subject-memory-changeset-group"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )

    def _candidate(
        self,
        *,
        source_id: str | None = None,
        statement: str = "我在杭州时喜欢吃东坡肉",
    ) -> OwnerTruthCandidateSnapshot:
        source_id = source_id or str(uuid4())
        content = enrich_memory_payload_v5(
            kind=MemoryKind.KNOWLEDGE,
            payload={
                "statement": statement,
                "knowledgeType": "personal_preference",
                "domains": ["饮食"],
                "factType": "preference",
                "predicate": "prefers",
                "object": {"label": "东坡肉", "category": "dish"},
                "qualifiers": {
                    "polarity": "positive",
                    "currentApplicability": "historical",
                    "validTime": {"precision": "year", "expression": "2016年"},
                },
            },
            provenance={"mode": "selfReport"},
            memory_subject_id="person-owner",
            claim_subject_id="person-owner",
        )
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.KNOWLEDGE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=self.context.policy_version,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
                "evidenceRefs": [
                    {"sourceId": source_id, "sourceVersion": 1, "span": {"start": 0, "end": 12}}
                ],
                "reviewMode": "batch",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    def _prepare(
        self,
        *,
        repository: InMemoryOwnerTruthCandidateReviewRepository | None = None,
        effects: InMemoryEffectKernelRepository | None = None,
    ):
        store = _Store(repository=repository, effects=effects)
        first = self._candidate()
        second = self._candidate()
        store.repository.seed(first)
        store.repository.seed(second)
        selections = (
            OwnerTruthMemoryChangeSetGroupSelection(
                candidate_id=first.candidate_id,
                expected_candidate_version=first.row_version,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=None,
                reason_code="ownerReviewed",
            ),
            OwnerTruthMemoryChangeSetGroupSelection(
                candidate_id=second.candidate_id,
                expected_candidate_version=second.row_version,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=None,
                reason_code="ownerReviewed",
            ),
        )
        dependencies = (
            OwnerTruthMemoryChangeSetGroupDependency(
                before_candidate_id=first.candidate_id,
                after_candidate_id=second.candidate_id,
            ),
        )
        service = OwnerTruthMemoryChangeSetGroupReviewService(store)
        preview_command = OwnerTruthMemoryChangeSetGroupCommand(
            command_id="changeset-group-preview-001",
            selections=selections,
            dependencies=dependencies,
        )
        proposal = service.preview(command=preview_command, context=self.context)
        confirmation_command = OwnerTruthMemoryChangeSetGroupCommand(
            command_id="changeset-group-confirm-001",
            selections=selections,
            dependencies=dependencies,
            expected_memory_revision=proposal.base_memory_revision,
            expected_group_proposal_id=proposal.proposal_id,
            expected_group_proposal_hash=proposal.proposal_hash,
        )
        return store, service, proposal, confirmation_command, first, second

    def test_dependent_candidates_preview_then_commit_as_one_group(self) -> None:
        store, service, proposal, command, first, second = self._prepare()

        self.assertEqual(proposal.base_memory_revision, 0)
        self.assertEqual([member.operation_index for member in proposal.members], [0, 1])
        self.assertEqual(
            [member.proposal.change_set.base_memory_revision for member in proposal.members],
            [0, 1],
        )
        self.assertEqual(proposal.members[1].proposal.change_set.operations[0].target_memory_version, 1)

        result = service.confirm(command=command, context=self.context)

        self.assertEqual(result.outcome, "created")
        self.assertEqual([member.activation_outcome for member in result.members], ["created", "revised"])
        self.assertEqual(result.applied_memory_revision, 2)
        self.assertEqual(result.projection_effect_count, 2)
        self.assertEqual(store.repository.memory_revision(context=self.context), 2)
        snapshot = store.repository.snapshot()
        self.assertEqual(
            [snapshot["candidates"][candidate_id]["decision"] for candidate_id in (first.candidate_id, second.candidate_id)],
            ["accepted", "accepted"],
        )
        self.assertEqual(len(snapshot["receipts"]), 2)
        self.assertEqual(len(snapshot["memoryActivations"]), 2)
        self.assertEqual(len(snapshot["memoryChangeSetGroupReceipts"]), 1)
        self.assertEqual(store.effects.record_count(), 2)

        replayed = service.confirm(command=command, context=self.context)
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(replayed.members, result.members)
        self.assertEqual(store.repository.memory_revision(context=self.context), 2)
        self.assertEqual(store.effects.record_count(), 2)

    def test_owner_text_correction_reextracts_v5_fields_without_stale_semantics(self) -> None:
        candidate = self._candidate(statement="我最喜欢东坡肉")

        negative = reextract_owner_corrected_memory_payload(
            kind=candidate.memory_kind,
            source_payload=candidate.content,
            corrected_payload={"statement": "我不喜欢面条"},
        )
        self.assertEqual(negative["statement"], "我不喜欢面条")
        self.assertEqual(negative["factType"], "preference")
        self.assertEqual(negative["qualifiers"]["polarity"], "negative")
        self.assertFalse(negative["qualifiers"]["superlativeAsserted"])
        self.assertEqual(negative["qualifiers"]["validTime"]["precision"], "unknown")
        self.assertIsNone(negative["object"])
        self.assertEqual(negative["facets"]["people"], [])

        education = reextract_owner_corrected_memory_payload(
            kind=candidate.memory_kind,
            source_payload=candidate.content,
            corrected_payload={"statement": "我在2017年从B大学毕业。"},
        )
        self.assertEqual(education["factType"], "knowledge")
        self.assertEqual(education["qualifiers"]["validTime"], {
            "start": "2017",
            "end": "2017",
            "precision": "year",
            "expression": "2017年",
        })
        self.assertIsNone(education["object"])

        superlative = reextract_owner_corrected_memory_payload(
            kind=candidate.memory_kind,
            source_payload=candidate.content,
            corrected_payload={"statement": "我最喜欢面条"},
        )
        self.assertEqual(superlative["qualifiers"]["polarity"], "positive")
        self.assertTrue(superlative["qualifiers"]["superlativeAsserted"])

    def test_group_correction_uses_reextracted_value_for_preview_and_activation(self) -> None:
        store = _Store()
        first = self._candidate(statement="我最喜欢东坡肉")
        second = self._candidate(statement="毕业后我在上海从事软件研发工作")
        store.repository.seed(first)
        store.repository.seed(second)
        selections = (
            OwnerTruthMemoryChangeSetGroupSelection(
                candidate_id=first.candidate_id,
                expected_candidate_version=first.row_version,
                action=CandidateReviewAction.CORRECT,
                corrected_value={"statement": "我不喜欢面条"},
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                reason_code="ownerCorrectedRelatedGroup",
            ),
            OwnerTruthMemoryChangeSetGroupSelection(
                candidate_id=second.candidate_id,
                expected_candidate_version=second.row_version,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=None,
                reason_code="ownerReviewedRelatedGroup",
            ),
        )
        dependencies = (
            OwnerTruthMemoryChangeSetGroupDependency(
                before_candidate_id=first.candidate_id,
                after_candidate_id=second.candidate_id,
            ),
        )
        service = OwnerTruthMemoryChangeSetGroupReviewService(store)
        preview_command = OwnerTruthMemoryChangeSetGroupCommand(
            command_id="changeset-group-correction-preview-001",
            selections=selections,
            dependencies=dependencies,
        )
        preview = service.preview(command=preview_command, context=self.context)
        preview_after = preview.members[0].proposal.payload()["operations"][0]["factDiff"]["after"]
        self.assertEqual(preview_after["statement"], "我不喜欢面条")
        self.assertEqual(preview_after["qualifiers"]["polarity"], "negative")
        self.assertIsNone(preview_after["object"])
        self.assertEqual(preview_after["qualifiers"]["validTime"]["precision"], "unknown")

        result = service.confirm(
            command=OwnerTruthMemoryChangeSetGroupCommand(
                command_id="changeset-group-correction-confirm-001",
                selections=selections,
                dependencies=dependencies,
                expected_memory_revision=preview.base_memory_revision,
                expected_group_proposal_id=preview.proposal_id,
                expected_group_proposal_hash=preview.proposal_hash,
            ),
            context=self.context,
        )
        self.assertEqual(result.members[0].decision, "corrected")
        snapshot = store.repository.snapshot()
        activation = next(
            item
            for item in snapshot["memoryActivations"].values()
            if item.get("candidateId") == first.candidate_id
        )
        persisted = activation["payload"]["content"]
        self.assertEqual(persisted["statement"], "我不喜欢面条")
        self.assertEqual(persisted["qualifiers"]["polarity"], "negative")
        self.assertIsNone(persisted["object"])

    def test_second_formal_write_failure_rolls_back_every_member(self) -> None:
        effects = InMemoryEffectKernelRepository()
        repository = _FailSecondActivationRepository(
            memory_projection_rebuild_runnable_reader=effects.is_runnable
        )
        store, service, _proposal, command, first, second = self._prepare(
            repository=repository,
            effects=effects,
        )

        with self.assertRaisesRegex(RuntimeError, "second formal-memory write"):
            service.confirm(command=command, context=self.context)

        self._assert_group_rollback(store=store, first=first, second=second)

    def test_second_receipt_failure_rolls_back_every_member(self) -> None:
        effects = InMemoryEffectKernelRepository()
        repository = _FailSecondReceiptRepository(
            memory_projection_rebuild_runnable_reader=effects.is_runnable
        )
        store, service, _proposal, command, first, second = self._prepare(
            repository=repository,
            effects=effects,
        )

        with self.assertRaisesRegex(RuntimeError, "second receipt write"):
            service.confirm(command=command, context=self.context)

        self._assert_group_rollback(store=store, first=first, second=second)

    def test_second_outbox_failure_rolls_back_every_member_and_effect(self) -> None:
        effects = _FailSecondEffectRepository()
        repository = InMemoryOwnerTruthCandidateReviewRepository(
            memory_projection_rebuild_runnable_reader=effects.is_runnable
        )
        store, service, _proposal, command, first, second = self._prepare(
            repository=repository,
            effects=effects,
        )

        with self.assertRaisesRegex(RuntimeError, "second Outbox write"):
            service.confirm(command=command, context=self.context)

        self._assert_group_rollback(store=store, first=first, second=second)
        self.assertEqual(store.effects.record_count(), 0)

    def test_stale_group_preview_does_not_partially_decide_candidates(self) -> None:
        store, service, _proposal, command, first, second = self._prepare()
        foreign = self._candidate(statement="我在2016年杭州读书")
        store.repository.seed(foreign)

        # An unrelated current-formal write advances the group base revision.
        from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateReviewCommand
        from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService

        individual = OwnerTruthCandidateReviewService(store)
        individual_preview = individual.preview_changeset(
            candidate_id=foreign.candidate_id,
            context=self.context,
        )
        assert individual_preview is not None
        individual.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id="changeset-group-stale-prime-001",
                candidate_id=foreign.candidate_id,
                expected_candidate_version=foreign.row_version,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                reason_code="ownerReviewed",
                expected_memory_revision=individual_preview.change_set.base_memory_revision,
                expected_change_set_id=individual_preview.change_set.change_set_id,
                expected_proposal_hash=individual_preview.proposal_hash,
            ),
            context=self.context,
        )

        with self.assertRaises(OwnerTruthCandidateReviewConflict):
            service.confirm(command=command, context=self.context)
        snapshot = store.repository.snapshot()
        self.assertEqual(snapshot["candidates"][first.candidate_id]["decision"], "pending")
        self.assertEqual(snapshot["candidates"][second.candidate_id]["decision"], "pending")
        self.assertEqual(len(snapshot["memoryChangeSetGroupReceipts"]), 0)

    def _assert_group_rollback(self, *, store, first, second) -> None:
        snapshot = store.repository.snapshot()
        self.assertEqual(store.repository.memory_revision(context=self.context), 0)
        self.assertEqual(snapshot["candidates"][first.candidate_id]["decision"], "pending")
        self.assertEqual(snapshot["candidates"][second.candidate_id]["decision"], "pending")
        self.assertEqual(snapshot["receipts"], {})
        self.assertEqual(snapshot["memoryActivations"], {})
        self.assertEqual(snapshot["memoryChangeSetGroupReceipts"], {})
        self.assertEqual(store.effects.record_count(), 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
