from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.candidate_decisions import (
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    PauseInterviewForTopicSwitchCommand,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
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
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_knowledge_dimension_confirmation import (
    OwnerTruthKnowledgeDimensionConfirmationCommand,
    OwnerTruthKnowledgeDimensionConfirmationService,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


client = TestClient(app)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthKnowledgeRecommendationReadAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_candidate_qa = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.previous_confirmation_qa = (
            main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED
        )
        self.previous_recommendation_qa = (
            main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED
        )
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = self.previous_candidate_qa
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = (
            self.previous_confirmation_qa
        )
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED = (
            self.previous_recommendation_qa
        )

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "推荐读取测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        return str(body["user"]["id"]), {
            "Authorization": f"Bearer {body['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

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
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
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
            context=OwnerTruthCommandContext(
                vault_id=vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            ),
            memory_version_id=memory_version_id,
            command=OwnerTruthKnowledgeDimensionConfirmationCommand(
                command_id=command_id,
                expected_content_hash=content_hash,
                dimension=dimension,
                covered_facets=facets,
            ),
        )

    def _seed_thread_with_session(
        self,
        *,
        vault_id: str,
        owner_id: str,
        command_id: str,
    ) -> tuple[str, str]:
        thread_id = str(uuid4())
        session_id = str(uuid4())
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        with self.store.request_unit_of_work(
            correlation_id=f"recommendation-read-thread:{vault_id}:{thread_id}",
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

    def _seed_thread(
        self,
        *,
        vault_id: str,
        owner_id: str,
        command_id: str,
    ) -> str:
        thread_id, _session_id = self._seed_thread_with_session(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id=command_id,
        )
        return thread_id

    def _pause_thread(
        self,
        *,
        vault_id: str,
        owner_id: str,
        thread_id: str,
        session_id: str,
        command_id: str,
    ) -> None:
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        with self.store.request_unit_of_work(
            correlation_id=f"recommendation-read-thread-pause:{vault_id}:{thread_id}",
            command_id=command_id,
        ):
            result = OwnerTruthConversationService(
                self.store.owner_truth_conversation_repository()
            ).pause_for_topic_switch(
                command=PauseInterviewForTopicSwitchCommand(
                    command_id=command_id,
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_thread_version=1,
                    expected_session_version=1,
                ),
                context=context,
            )
        self.assertEqual(result.state.value, "paused")

    def _set_thread_boundary(
        self,
        *,
        vault_id: str,
        owner_id: str,
        thread_id: str,
        session_id: str,
        boundary: InterviewBoundary,
        command_id: str,
    ) -> None:
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        with self.store.request_unit_of_work(
            correlation_id=f"recommendation-read-thread-boundary:{vault_id}:{thread_id}",
            command_id=command_id,
        ):
            result = OwnerTruthConversationService(
                self.store.owner_truth_conversation_repository()
            ).set_boundary(
                command=SetInterviewBoundaryCommand(
                    command_id=command_id,
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_session_version=1,
                    boundary=boundary,
                ),
                context=context,
            )
        self.assertEqual(result.boundary, boundary)

    @staticmethod
    def _path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/knowledge-recommendations/read"

    @staticmethod
    def _candidate(
        *,
        candidate_id: str,
        slot: str,
        thread_id: str,
        dimension: str,
        missing_facet: str,
        memory_version_id: str,
    ) -> dict[str, object]:
        return {
            "candidateId": candidate_id,
            "slot": slot,
            "threadId": thread_id,
            "targetDimension": dimension,
            "missingFacet": missing_facet,
            "questionTemplateId": f"template-{candidate_id}",
            "evidenceKind": "confirmedMemory",
            "evidenceRefs": [memory_version_id],
            "reasonCode": "qaConfirmedMemory",
        }

    def test_contract_is_default_hidden_when_its_separate_flag_is_disabled(self) -> None:
        _owner_id, headers = self._login("13800139421")
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED = False

        response = client.post(
            self._path("vault-hidden-recommendation"),
            headers=headers,
            json={"candidates": []},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationReadUnavailable",
        )

    def test_owner_can_read_value_free_selection_only_from_confirmed_memory(self) -> None:
        owner_id, headers = self._login("13800139422")
        vault_id = "vault-recommendation-read-api"
        decision_id, decision_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "I left a role to be closer to family."},
            command_id="recommendation-api-activate-001",
        )
        values_id, values_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "I value thoughtful commitments."},
            command_id="recommendation-api-activate-002",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=decision_id,
            content_hash=decision_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="recommendation-api-confirm-001",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=values_id,
            content_hash=values_hash,
            dimension="values",
            facets=("priority",),
            command_id="recommendation-api-confirm-002",
        )
        thread_id = self._seed_thread(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="recommendation-api-thread-001",
        )

        response = client.post(
            self._path(vault_id),
            headers=headers,
            json={
                "candidates": [
                    self._candidate(
                        candidate_id="api-continuity",
                        slot="continuity",
                        thread_id=thread_id,
                        dimension="keyDecisions",
                        missing_facet="outcome",
                        memory_version_id=decision_id,
                    ),
                    self._candidate(
                        candidate_id="api-breadth",
                        slot="breadth",
                        thread_id=thread_id,
                        dimension="values",
                        missing_facet="reflection",
                        memory_version_id=values_id,
                    ),
                ]
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(
            body["schemaVersion"],
            "owner-truth-knowledge-recommendation-read-response-v1",
        )
        self.assertEqual(body["recommendations"]["selectionState"], "ready")
        self.assertEqual(
            [item["slot"] for item in body["recommendations"]["selected"]],
            ["continuity", "breadth"],
        )
        self.assertNotIn("closer to family", response.text)
        self.assertNotIn("thoughtful commitments", response.text)
        self.assertNotIn("claim", response.text)

    def test_other_owner_and_unbound_evidence_cannot_read_a_selection(self) -> None:
        owner_id, owner_headers = self._login("13800139423")
        vault_id = "vault-recommendation-read-owner-boundary"
        memory_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "The Owner selected a boundary."},
            command_id="recommendation-api-activate-003",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=memory_id,
            content_hash=content_hash,
            dimension="keyDecisions",
            facets=("choice",),
            command_id="recommendation-api-confirm-003",
        )
        thread_id = self._seed_thread(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="recommendation-api-thread-002",
        )
        candidate = self._candidate(
            candidate_id="api-owner-boundary",
            slot="continuity",
            thread_id=thread_id,
            dimension="keyDecisions",
            missing_facet="reason",
            memory_version_id=memory_id,
        )
        _other_id, other_headers = self._login("13800139424")

        denied = client.post(self._path(vault_id), headers=other_headers, json={"candidates": [candidate]})
        invalid = client.post(
            self._path(vault_id),
            headers=owner_headers,
            json={
                "candidates": [
                    {**candidate, "evidenceRefs": [str(uuid4())]},
                ]
            },
        )

        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "ownerTruthKnowledgeRecommendationReadDenied")
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertEqual(invalid.json()["detail"]["code"], "ownerTruthKnowledgeRecommendationReadInvalid")

    def test_scope_or_raw_content_fields_are_rejected(self) -> None:
        owner_id, headers = self._login("13800139425")
        vault_id = "vault-recommendation-read-strict-envelope"
        memory_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "This text must never become recommendation input."},
            command_id="recommendation-api-activate-004",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=memory_id,
            content_hash=content_hash,
            dimension="keyDecisions",
            facets=("choice",),
            command_id="recommendation-api-confirm-004",
        )
        thread_id = self._seed_thread(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="recommendation-api-thread-003",
        )
        candidate = self._candidate(
            candidate_id="api-strict-envelope",
            slot="continuity",
            thread_id=thread_id,
            dimension="keyDecisions",
            missing_facet="reason",
            memory_version_id=memory_id,
        )

        injected_scope = client.post(
            self._path(vault_id),
            headers=headers,
            json={"candidates": [{**candidate, "ownerSubjectId": "another-owner"}]},
        )
        injected_text = client.post(
            self._path(vault_id),
            headers=headers,
            json={"candidates": [{**candidate, "questionText": "leak me"}]},
        )

        self.assertEqual(injected_scope.status_code, 400, injected_scope.text)
        self.assertEqual(injected_text.status_code, 400, injected_text.text)
        self.assertEqual(
            injected_scope.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationReadInvalid",
        )
        self.assertEqual(
            injected_text.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationReadInvalid",
        )

    def test_unknown_thread_is_rejected_after_owner_confirmed_coverage_is_ready(self) -> None:
        owner_id, headers = self._login("13800139426")
        vault_id = "vault-recommendation-read-thread-boundary"
        memory_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "Only a persisted private thread may carry a recommendation."},
            command_id="recommendation-api-activate-005",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=memory_id,
            content_hash=content_hash,
            dimension="keyDecisions",
            facets=("choice",),
            command_id="recommendation-api-confirm-005",
        )

        response = client.post(
            self._path(vault_id),
            headers=headers,
            json={
                "candidates": [
                    self._candidate(
                        candidate_id="api-unknown-thread",
                        slot="continuity",
                        thread_id=str(uuid4()),
                        dimension="keyDecisions",
                        missing_facet="reason",
                        memory_version_id=memory_id,
                    )
                ]
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationReadInvalid",
        )

    def test_paused_thread_is_rejected_after_owner_confirmed_coverage_is_ready(self) -> None:
        owner_id, headers = self._login("13800139427")
        vault_id = "vault-recommendation-read-paused-thread"
        memory_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "A paused private thread must not receive a new recommendation."},
            command_id="recommendation-api-activate-006",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=memory_id,
            content_hash=content_hash,
            dimension="keyDecisions",
            facets=("choice",),
            command_id="recommendation-api-confirm-006",
        )
        thread_id, session_id = self._seed_thread_with_session(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="recommendation-api-thread-004",
        )
        self._pause_thread(
            vault_id=vault_id,
            owner_id=owner_id,
            thread_id=thread_id,
            session_id=session_id,
            command_id="recommendation-api-thread-pause-004",
        )

        response = client.post(
            self._path(vault_id),
            headers=headers,
            json={
                "candidates": [
                    self._candidate(
                        candidate_id="api-paused-thread",
                        slot="continuity",
                        thread_id=thread_id,
                        dimension="keyDecisions",
                        missing_facet="reason",
                        memory_version_id=memory_id,
                    )
                ]
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationReadInvalid",
        )

    def test_paused_or_non_open_session_rejects_owner_confirmed_candidate(self) -> None:
        cases = (
            ("cooldown", InterviewBoundary.COOLDOWN, "13800139431"),
            ("do-not-ask", InterviewBoundary.DO_NOT_ASK, "13800139432"),
            ("skip-once", InterviewBoundary.SKIP_ONCE, "13800139433"),
        )
        for suffix, boundary, phone in cases:
            with self.subTest(boundary=boundary.value):
                owner_id, headers = self._login(phone)
                vault_id = f"vault-recommendation-read-{suffix}"
                memory_id, content_hash = self._activate_memory(
                    vault_id=vault_id,
                    owner_id=owner_id,
                    content={"claim": f"The {suffix} boundary blocks recommendation reuse."},
                    command_id=f"recommendation-api-activate-{suffix}",
                )
                self._confirm(
                    vault_id=vault_id,
                    owner_id=owner_id,
                    memory_version_id=memory_id,
                    content_hash=content_hash,
                    dimension="keyDecisions",
                    facets=("choice",),
                    command_id=f"recommendation-api-confirm-{suffix}",
                )
                thread_id, session_id = self._seed_thread_with_session(
                    vault_id=vault_id,
                    owner_id=owner_id,
                    command_id=f"recommendation-api-thread-{suffix}",
                )
                self._set_thread_boundary(
                    vault_id=vault_id,
                    owner_id=owner_id,
                    thread_id=thread_id,
                    session_id=session_id,
                    boundary=boundary,
                    command_id=f"recommendation-api-boundary-{suffix}",
                )

                response = client.post(
                    self._path(vault_id),
                    headers=headers,
                    json={
                        "candidates": [
                            self._candidate(
                                candidate_id=f"api-{suffix}-thread",
                                slot="continuity",
                                thread_id=thread_id,
                                dimension="keyDecisions",
                                missing_facet="reason",
                                memory_version_id=memory_id,
                            )
                        ]
                    },
                )

                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    "ownerTruthKnowledgeRecommendationReadInvalid",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
