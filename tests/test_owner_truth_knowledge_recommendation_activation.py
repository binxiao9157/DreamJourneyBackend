from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
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
from app.domain.owner_truth.conversation import StartInterviewSessionCommand
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_knowledge_dimension_confirmation import (
    OwnerTruthKnowledgeDimensionConfirmationCommand,
    OwnerTruthKnowledgeDimensionConfirmationService,
)
from app.services.owner_truth_knowledge_recommendation_activation import (
    OwnerTruthKnowledgeRecommendationActivationCommand,
    OwnerTruthKnowledgeRecommendationActivationConflict,
    OwnerTruthKnowledgeRecommendationActivationService,
    OwnerTruthKnowledgeRecommendationActivationStale,
    OwnerTruthKnowledgeRecommendationActivationUnavailable,
    knowledge_recommendation_activation_summary,
)
from app.services.owner_truth_knowledge_recommendation_read import (
    OwnerTruthKnowledgeRecommendationReadService,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthKnowledgeRecommendationActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()

    def _context(self, *, vault_id: str, owner_id: str) -> OwnerTruthCommandContext:
        return OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )

    def _activate_memory(
        self,
        *,
        vault_id: str,
        owner_id: str,
        content: dict[str, object],
        command_id: str,
    ) -> tuple[str, str]:
        source_id = str(uuid4())
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=vault_id,
            owner_subject_id=owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.KNOWLEDGE,
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
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )
        self.store.owner_truth_candidate_review_repository().seed(candidate)
        context = self._context(vault_id=vault_id, owner_id=owner_id)
        OwnerTruthCandidateReviewService(self.store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=command_id,
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action="accept",
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=context,
        )
        OwnerTruthMemoryProjectionService(self.store).rebuild(context=context)
        snapshot = self.store.owner_truth_memory_projection_repository().read(context=context)
        entry = next(
            item
            for item in snapshot["entries"]
            if item["citation"]["contentHash"] == _hash(content)
        )
        return str(entry["citation"]["memoryVersionId"]), str(entry["citation"]["contentHash"])

    def _confirm(
        self,
        *,
        vault_id: str,
        owner_id: str,
        memory_version_id: str,
        content_hash: str,
        dimension: str,
        facets: tuple[str, ...],
        command_id: str,
    ) -> None:
        OwnerTruthKnowledgeDimensionConfirmationService(self.store, enabled=True).confirm(
            context=self._context(vault_id=vault_id, owner_id=owner_id),
            memory_version_id=memory_version_id,
            command=OwnerTruthKnowledgeDimensionConfirmationCommand(
                command_id=command_id,
                expected_content_hash=content_hash,
                dimension=dimension,
                covered_facets=facets,
            ),
        )

    def _seed_thread(self, *, vault_id: str, owner_id: str, command_id: str) -> tuple[str, str]:
        thread_id = str(uuid4())
        session_id = str(uuid4())
        context = self._context(vault_id=vault_id, owner_id=owner_id)
        with self.store.request_unit_of_work(
            correlation_id=f"recommendation-activation-thread:{vault_id}:{thread_id}",
            command_id=command_id,
        ):
            OwnerTruthConversationService(
                self.store.owner_truth_conversation_repository()
            ).start_session(
                command=StartInterviewSessionCommand(
                    command_id=command_id,
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_thread_version=0,
                    entry_mode="recommendation",
                ),
                context=context,
            )
        return thread_id, session_id

    def _plan_breadth(
        self,
        *,
        vault_id: str,
        owner_id: str,
    ):
        result = OwnerTruthKnowledgeRecommendationReadService(self.store).plan(
            context=self._context(vault_id=vault_id, owner_id=owner_id)
        )
        self.assertEqual(result.state.value, "ready")
        assert result.selection is not None
        selected = [item for item in result.selection.selected if item.slot.value == "breadth"]
        self.assertEqual(len(selected), 1)
        return selected[0]

    def test_acceptance_replans_current_authority_is_idempotent_and_value_free(self) -> None:
        vault_id = "vault-recommendation-activation"
        owner_id = "owner-recommendation-activation"
        decision_memory_id, decision_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "This private decision must not appear in the activation receipt."},
            command_id="recommendation-activation-memory-001",
        )
        values_memory_id, values_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "This second private value must not appear in the receipt either."},
            command_id="recommendation-activation-memory-002",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=decision_memory_id,
            content_hash=decision_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="recommendation-activation-confirm-001",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=values_memory_id,
            content_hash=values_hash,
            dimension="values",
            facets=("priority",),
            command_id="recommendation-activation-confirm-002",
        )
        thread_id, session_id = self._seed_thread(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="recommendation-activation-thread-001",
        )
        del thread_id, session_id
        decision = self._plan_breadth(vault_id=vault_id, owner_id=owner_id)
        context = self._context(vault_id=vault_id, owner_id=owner_id)

        disabled = OwnerTruthKnowledgeRecommendationActivationService(self.store, enabled=False)
        with self.assertRaises(OwnerTruthKnowledgeRecommendationActivationUnavailable):
            disabled.accept(
                context=context,
                command=OwnerTruthKnowledgeRecommendationActivationCommand(
                    command_id="recommendation-activation-disabled",
                    expected_candidate_id=decision.candidate_id,
                    slot="breadth",
                    expected_session_version=1,
                ),
            )

        service = OwnerTruthKnowledgeRecommendationActivationService(self.store, enabled=True)
        with self.assertRaises(OwnerTruthKnowledgeRecommendationActivationStale):
            service.accept(
                context=context,
                command=OwnerTruthKnowledgeRecommendationActivationCommand(
                    command_id="recommendation-activation-forged",
                    expected_candidate_id="server-plan-breadth-forged",
                    slot="breadth",
                    expected_session_version=1,
                ),
            )
        with self.assertRaises(OwnerTruthKnowledgeRecommendationActivationStale):
            service.accept(
                context=context,
                command=OwnerTruthKnowledgeRecommendationActivationCommand(
                    command_id="recommendation-activation-stale-session",
                    expected_candidate_id=decision.candidate_id,
                    slot="breadth",
                    expected_session_version=2,
                ),
            )

        command = OwnerTruthKnowledgeRecommendationActivationCommand(
            command_id="recommendation-activation-accepted",
            expected_candidate_id=decision.candidate_id,
            slot="breadth",
            expected_session_version=1,
        )
        created = service.accept(context=context, command=command)
        replayed = service.accept(context=context, command=command)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.candidate_id, decision.candidate_id)
        self.assertEqual(created.next_action.value, "broaden")
        self.assertEqual(created.thread_id, decision.thread_id)
        self.assertEqual(created.expected_session_version, 1)
        self.assertEqual(
            self.store.owner_truth_knowledge_recommendation_activation_repository().list_accepted_candidate_ids(
                context=context,
                authority_epoch=0,
            ),
            frozenset((decision.candidate_id,)),
        )
        summary = knowledge_recommendation_activation_summary(created)
        rendered = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("private decision", rendered)
        self.assertNotIn("second private value", rendered)
        self.assertNotIn("questionTemplateId", summary)
        self.assertNotIn("evidenceRefs", summary)

        with self.assertRaises(OwnerTruthKnowledgeRecommendationActivationConflict):
            service.accept(
                context=context,
                command=OwnerTruthKnowledgeRecommendationActivationCommand(
                    command_id="recommendation-activation-duplicate-candidate",
                    expected_candidate_id=decision.candidate_id,
                    slot="breadth",
                    expected_session_version=1,
                ),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
