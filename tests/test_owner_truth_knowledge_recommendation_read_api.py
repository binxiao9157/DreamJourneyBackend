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
        self.previous_recommendation_plan_qa = (
            main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED
        )
        self.previous_recommendation_activation_qa = (
            main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_QA_ENABLED
        )
        self.previous_recommendation_feedback_qa = (
            main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_QA_ENABLED
        )
        self.previous_saved_continuation_cue_qa = (
            main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED
        )
        self.previous_thread_preference_qa = main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_READ_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_QA_ENABLED = False
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_QA_ENABLED = False
        main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED = False
        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = False

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
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED = (
            self.previous_recommendation_plan_qa
        )
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_QA_ENABLED = (
            self.previous_recommendation_activation_qa
        )
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_QA_ENABLED = (
            self.previous_recommendation_feedback_qa
        )
        main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED = (
            self.previous_saved_continuation_cue_qa
        )
        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = (
            self.previous_thread_preference_qa
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

    @staticmethod
    def _login_release_policy(phone: str) -> tuple[str, dict[str, str], str]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "推荐展示测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        return (
            str(body["user"]["id"]),
            {"Authorization": f"Bearer {body['auth']['accessToken']}"},
            str(body["auth"]["sessionId"]),
        )

    @staticmethod
    def _guided_presentation_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/guided-recommendations"

    @staticmethod
    def _guided_presentation_feedback_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/guided-recommendations/feedback"

    @staticmethod
    def _guided_presentation_activation_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/guided-recommendations/activate"

    @staticmethod
    def _guided_presentation_policy_headers(
        headers: dict[str, str],
        *,
        session_id: str,
        decision_id: str,
    ) -> dict[str, str]:
        return {
            **headers,
            "X-DreamJourney-Feature": "echoGuidedRecommendations",
            "X-DreamJourney-Feature-Decision-Id": decision_id,
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": "release-policy-v1",
            "X-DreamJourney-Policy-Revision": "1",
            "X-DreamJourney-Account-Generation": sha256(
                session_id.encode("utf-8")
            ).hexdigest()[:24],
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
    def _plan_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/knowledge-recommendations/plan"

    @staticmethod
    def _activation_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/knowledge-recommendations/activate"

    @staticmethod
    def _feedback_path(vault_id: str) -> str:
        return f"/v2/vaults/{vault_id}/knowledge-recommendations/feedback"

    @staticmethod
    def _saved_continuation_cue_path(vault_id: str, session_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-sessions/{session_id}"
            "/saved-continuation-cues"
        )

    @staticmethod
    def _defer_with_continuation_path(vault_id: str, session_id: str) -> str:
        return (
            f"/v2/vaults/{vault_id}/interview-sessions/{session_id}"
            "/defer-with-continuation"
        )

    def test_defer_with_continuation_is_hidden_atomic_and_replay_safe(self) -> None:
        owner_id, headers = self._login("13800139416")
        vault_id = "vault-recommendation-defer-with-continuation"
        memory_version_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "This text must not become a continuation payload."},
            command_id="defer-with-continuation-activate",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=memory_version_id,
            content_hash=content_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="defer-with-continuation-confirm",
        )
        thread_id, session_id = self._seed_thread_with_session(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="defer-with-continuation-thread",
        )
        path = self._defer_with_continuation_path(vault_id, session_id)
        payload = {
            "commandId": "defer-with-continuation-001",
            "threadId": thread_id,
            "expectedSessionVersion": 1,
            "memoryVersionId": memory_version_id,
            "targetDimension": "keyDecisions",
            "missingFacet": "outcome",
        }

        hidden = client.post(path, headers=headers, json=payload)
        self.assertEqual(hidden.status_code, 404, hidden.text)
        self.assertEqual(
            hidden.json()["detail"]["code"],
            "ownerTruthSavedContinuationCueUnavailable",
        )

        main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED = True
        main_module.OWNER_TRUTH_THREAD_PREFERENCE_QA_ENABLED = True
        injected = client.post(
            path,
            headers=headers,
            json={**payload, "continuationText": "must be rejected"},
        )
        self.assertEqual(injected.status_code, 400, injected.text)
        self.assertEqual(
            injected.json()["detail"]["code"],
            "ownerTruthSavedContinuationCueInvalid",
        )

        created = client.post(path, headers=headers, json=payload)
        replayed = client.post(path, headers=headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(replayed.status_code, 200, replayed.text)
        self.assertEqual(created.headers["cache-control"], "no-store")
        self.assertEqual(created.json()["cue"]["status"], "created")
        self.assertEqual(created.json()["receipt"]["boundary"], "cooldown")
        self.assertEqual(replayed.json()["cue"]["status"], "deduplicated")
        self.assertEqual(replayed.json()["receipt"]["status"], "deduplicated")
        self.assertEqual(replayed.json()["receipt"]["sessionVersion"], 2)
        self.assertNotIn("continuation payload", created.text)
        self.assertNotIn("claim", created.text)

        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        preference = self.store.owner_truth_thread_preference_repository().read(
            context=context,
            thread_id=thread_id,
        )
        self.assertIsNotNone(preference)
        assert preference is not None
        self.assertEqual(preference.preference.value, "cooldown")
        self.assertEqual(
            len(
                self.store.owner_truth_saved_continuation_cue_repository().list_for_recommendation(
                    context=context
                )
            ),
            1,
        )
        planned = client.post(self._plan_path(vault_id), headers=headers, json={})
        self.assertEqual(planned.status_code, 200, planned.text)
        self.assertEqual(planned.json()["recommendations"]["selected"], [])

        invalid_thread_id, invalid_session_id = self._seed_thread_with_session(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="defer-with-continuation-invalid-thread",
        )
        invalid = client.post(
            self._defer_with_continuation_path(vault_id, invalid_session_id),
            headers=headers,
            json={
                "commandId": "defer-with-continuation-invalid-001",
                "threadId": invalid_thread_id,
                "expectedSessionVersion": 1,
                "memoryVersionId": memory_version_id,
                "targetDimension": "keyDecisions",
                "missingFacet": "not-a-facet",
            },
        )
        self.assertEqual(invalid.status_code, 400, invalid.text)
        current = self.store.owner_truth_conversation_repository().get_interview_session(
            session_id=invalid_session_id,
            context=context,
        )
        self.assertEqual(current.state.value, "active")
        self.assertEqual(current.boundary.value, "open")

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

    def test_server_planned_contract_has_its_own_default_off_gate(self) -> None:
        _owner_id, headers = self._login("13800139420")
        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_PLAN_QA_ENABLED = False

        response = client.post(
            self._plan_path("vault-hidden-recommendation-plan"),
            headers=headers,
            json={},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationPlanUnavailable",
        )

    def test_product_guided_recommendations_require_own_policy_and_hide_planner_metadata(self) -> None:
        owner_id, headers, session_id = self._login_release_policy("13800139418")
        vault_id = "vault-guided-recommendation-presentation"
        decision_id, decision_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "Owner text must never be returned by the guided prompt."},
            command_id="guided-presentation-activate-decision",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=decision_id,
            content_hash=decision_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="guided-presentation-confirm-decision",
        )
        self._seed_thread(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="guided-presentation-thread",
        )
        path = self._guided_presentation_path(vault_id)

        qa_header_only = client.get(
            path,
            headers={**headers, "X-DreamJourney-QA-Owner-Truth": "1"},
        )
        self.assertEqual(qa_header_only.status_code, 403, qa_header_only.text)
        self.assertEqual(
            qa_header_only.json()["detail"]["code"],
            "release_policy_denied",
        )
        self.assertEqual(
            qa_header_only.json()["detail"]["feature"],
            "echoGuidedRecommendations",
        )

        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "echoGuidedRecommendations"
        }
        try:
            response = client.get(
                path,
                headers=self._guided_presentation_policy_headers(
                    headers,
                    session_id=session_id,
                    decision_id="guided-recommendation-owner",
                ),
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            body = response.json()
            self.assertEqual(
                set(body),
                {
                    "schemaVersion",
                    "vaultId",
                    "state",
                    "recommendationSetId",
                    "recommendations",
                },
            )
            self.assertEqual(
                body["schemaVersion"],
                "owner-truth-guided-recommendation-presentation-response-v2",
            )
            self.assertEqual(body["vaultId"], vault_id)
            self.assertEqual(body["state"], "ready")
            self.assertRegex(body["recommendationSetId"], r"^[a-f0-9]{64}$")
            self.assertEqual(len(body["recommendations"]), 1)
            self.assertEqual(
                set(body["recommendations"][0]),
                {"slot", "label", "question"},
            )
            self.assertEqual(body["recommendations"][0]["slot"], "breadth")
            self.assertTrue(body["recommendations"][0]["question"].endswith("？"))
            self.assertEqual(
                len({item["slot"] for item in body["recommendations"]}),
                len(body["recommendations"]),
            )
            rendered = json.dumps(body, ensure_ascii=False)
            for forbidden in (
                "candidateId",
                "evidenceRef",
                "reasonCode",
                "targetDimension",
                "policyVersion",
                "questionTemplate",
                "Owner text must never",
                "claim",
            ):
                self.assertNotIn(forbidden, rendered)
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_product_guided_recommendation_feedback_is_scope_bound_replay_safe_and_value_free(self) -> None:
        owner_id, headers, session_id = self._login_release_policy("13800139421")
        vault_id = "vault-guided-recommendation-feedback-presentation"
        decision_id, decision_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "A selected prompt must remain private to the owner."},
            command_id="guided-feedback-activate-decision",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=decision_id,
            content_hash=decision_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="guided-feedback-confirm-decision",
        )
        self._seed_thread(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="guided-feedback-thread",
        )
        presentation_path = self._guided_presentation_path(vault_id)
        feedback_path = self._guided_presentation_feedback_path(vault_id)
        payload = {
            "commandId": "guided-feedback-replay-safe",
            "recommendationSetId": "0" * 64,
            "slot": "breadth",
            "feedbackAction": "replace",
            "feedbackReason": "questionWording",
        }

        qa_header_only = client.post(
            feedback_path,
            headers={**headers, "X-DreamJourney-QA-Owner-Truth": "1"},
            json=payload,
        )
        self.assertEqual(qa_header_only.status_code, 403, qa_header_only.text)
        self.assertEqual(
            qa_header_only.json()["detail"]["code"],
            "release_policy_denied",
        )
        self.assertEqual(
            qa_header_only.json()["detail"]["feature"],
            "echoGuidedRecommendations",
        )

        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "echoGuidedRecommendations"
        }
        policy_headers = self._guided_presentation_policy_headers(
            headers,
            session_id=session_id,
            decision_id="guided-feedback-owner",
        )
        try:
            presentation = client.get(presentation_path, headers=policy_headers)
            self.assertEqual(presentation.status_code, 200, presentation.text)
            presentation_body = presentation.json()
            self.assertEqual(presentation_body["state"], "ready")
            self.assertEqual([item["slot"] for item in presentation_body["recommendations"]], ["breadth"])
            payload["recommendationSetId"] = presentation_body["recommendationSetId"]

            created = client.post(feedback_path, headers=policy_headers, json=payload)
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.headers["cache-control"], "no-store")
            self.assertEqual(
                created.json(),
                {
                    "schemaVersion": "owner-truth-guided-recommendation-feedback-response-v1",
                    "vaultId": vault_id,
                    "feedback": {"status": "created"},
                },
            )
            rendered = json.dumps(created.json(), ensure_ascii=False)
            for forbidden in (
                "candidateId",
                "evidenceRef",
                "reasonCode",
                "targetDimension",
                "policyVersion",
                "questionTemplate",
                "threadId",
                "sessionId",
                "A selected prompt must remain private",
            ):
                self.assertNotIn(forbidden, rendered)

            replayed = client.post(feedback_path, headers=policy_headers, json=payload)
            self.assertEqual(replayed.status_code, 200, replayed.text)
            self.assertEqual(replayed.json()["feedback"], {"status": "deduplicated"})

            stale = client.post(
                feedback_path,
                headers=policy_headers,
                json={**payload, "commandId": "guided-feedback-stale-selection"},
            )
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(
                stale.json()["detail"]["code"],
                "ownerTruthGuidedRecommendationFeedbackStale",
            )
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_product_guided_recommendation_activation_binds_slot_without_writing_prompt_as_owner_input(self) -> None:
        owner_id, headers, session_id = self._login_release_policy("13800139424")
        vault_id = "vault-guided-recommendation-activation-presentation"
        memory_version_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "The displayed assistant question is never an Owner narrative."},
            command_id="guided-activation-activate-decision",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=memory_version_id,
            content_hash=content_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="guided-activation-confirm-decision",
        )
        self._seed_thread(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="guided-activation-thread",
        )
        presentation_path = self._guided_presentation_path(vault_id)
        activation_path = self._guided_presentation_activation_path(vault_id)
        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "echoGuidedRecommendations"
        }
        policy_headers = self._guided_presentation_policy_headers(
            headers,
            session_id=session_id,
            decision_id="guided-activation-owner",
        )
        try:
            presentation = client.get(presentation_path, headers=policy_headers)
            self.assertEqual(presentation.status_code, 200, presentation.text)
            presentation_body = presentation.json()
            self.assertEqual(presentation_body["state"], "ready")
            self.assertEqual(len(presentation_body["recommendations"]), 1)
            prompt = presentation_body["recommendations"][0]
            payload = {
                "commandId": "guided-activation-replay-safe",
                "recommendationSetId": presentation_body["recommendationSetId"],
                "slot": prompt["slot"],
            }
            message_count_before = len(
                self.store.owner_truth_conversation_repository()._messages
            )

            unsupported = client.post(
                activation_path,
                headers=policy_headers,
                json={**payload, "question": prompt["question"]},
            )
            self.assertEqual(unsupported.status_code, 400, unsupported.text)
            self.assertEqual(
                unsupported.json()["detail"]["code"],
                "ownerTruthGuidedRecommendationActivationInvalid",
            )

            created = client.post(activation_path, headers=policy_headers, json=payload)
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.headers["cache-control"], "no-store")
            self.assertEqual(
                created.json(),
                {
                    "schemaVersion": "owner-truth-guided-recommendation-activation-response-v1",
                    "vaultId": vault_id,
                    "activation": {
                        "status": "created",
                        "slot": prompt["slot"],
                        "nextAction": "broaden",
                        "inputState": "awaitingOwnerNarrative",
                    },
                },
            )
            self.assertEqual(
                len(self.store.owner_truth_conversation_repository()._messages),
                message_count_before,
            )
            rendered = json.dumps(created.json(), ensure_ascii=False)
            for forbidden in (
                "candidateId",
                "evidenceRef",
                "reasonCode",
                "targetDimension",
                "threadId",
                "sessionId",
                "The displayed assistant question",
                prompt["question"],
            ):
                self.assertNotIn(forbidden, rendered)

            replayed = client.post(activation_path, headers=policy_headers, json=payload)
            self.assertEqual(replayed.status_code, 200, replayed.text)
            self.assertEqual(replayed.json()["activation"]["status"], "deduplicated")

            conflicting_replay = client.post(
                activation_path,
                headers=policy_headers,
                json={**payload, "slot": "continuity"},
            )
            self.assertEqual(conflicting_replay.status_code, 409, conflicting_replay.text)
            self.assertEqual(
                conflicting_replay.json()["detail"]["code"],
                "ownerTruthGuidedRecommendationActivationConflict",
            )

            stale = client.post(
                activation_path,
                headers=policy_headers,
                json={
                    **payload,
                    "commandId": "guided-activation-stale-selection",
                    "recommendationSetId": "0" * 64,
                },
            )
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(
                stale.json()["detail"]["code"],
                "ownerTruthGuidedRecommendationActivationStale",
            )
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_product_guided_recommendation_timing_feedback_defers_with_safe_continuation(self) -> None:
        owner_id, headers, session_id = self._login_release_policy("13800139422")
        vault_id = "vault-guided-recommendation-timing-feedback"
        memory_version_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "Deferred prompts must not expose private source text."},
            command_id="guided-timing-feedback-activate",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=memory_version_id,
            content_hash=content_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="guided-timing-feedback-confirm",
        )
        thread_id, _interview_session_id = self._seed_thread_with_session(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="guided-timing-feedback-thread",
        )
        presentation_path = self._guided_presentation_path(vault_id)
        feedback_path = self._guided_presentation_feedback_path(vault_id)
        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "echoGuidedRecommendations"
        }
        policy_headers = self._guided_presentation_policy_headers(
            headers,
            session_id=session_id,
            decision_id="guided-timing-feedback-owner",
        )
        try:
            presentation = client.get(presentation_path, headers=policy_headers)
            self.assertEqual(presentation.status_code, 200, presentation.text)
            body = presentation.json()
            self.assertEqual(body["state"], "ready")
            self.assertEqual(len(body["recommendations"]), 1)
            payload = {
                "commandId": "guided-timing-feedback-replay-safe",
                "recommendationSetId": body["recommendationSetId"],
                "slot": body["recommendations"][0]["slot"],
                "feedbackAction": "defer",
                "feedbackReason": "timing",
            }

            injected = client.post(
                feedback_path,
                headers=policy_headers,
                json={**payload, "threadId": thread_id},
            )
            self.assertEqual(injected.status_code, 400, injected.text)
            self.assertEqual(
                injected.json()["detail"]["code"],
                "ownerTruthGuidedRecommendationFeedbackInvalid",
            )

            created = client.post(feedback_path, headers=policy_headers, json=payload)
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(
                created.json(),
                {
                    "schemaVersion": "owner-truth-guided-recommendation-feedback-response-v1",
                    "vaultId": vault_id,
                    "feedback": {"status": "created"},
                },
            )
            rendered = json.dumps(created.json(), ensure_ascii=False)
            for forbidden in (
                "candidateId",
                "evidenceRef",
                "reasonCode",
                "targetDimension",
                "policyVersion",
                "questionTemplate",
                "threadId",
                "sessionId",
                "Deferred prompts must not",
            ):
                self.assertNotIn(forbidden, rendered)

            context = OwnerTruthCommandContext(
                vault_id=vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            )
            preference = self.store.owner_truth_thread_preference_repository().read(
                context=context,
                thread_id=thread_id,
            )
            self.assertIsNotNone(preference)
            assert preference is not None
            self.assertEqual(preference.preference.value, "cooldown")
            cues = self.store.owner_truth_saved_continuation_cue_repository().list_for_recommendation(
                context=context
            )
            self.assertEqual(len(cues), 1)
            self.assertEqual(cues[0].thread_id, thread_id)
            self.assertEqual(cues[0].memory_version_id, memory_version_id)

            replayed = client.post(feedback_path, headers=policy_headers, json=payload)
            self.assertEqual(replayed.status_code, 200, replayed.text)
            self.assertEqual(replayed.json()["feedback"], {"status": "deduplicated"})

            stale = client.post(
                feedback_path,
                headers=policy_headers,
                json={**payload, "commandId": "guided-timing-feedback-stale"},
            )
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(
                stale.json()["detail"]["code"],
                "ownerTruthGuidedRecommendationFeedbackStale",
            )
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_explicit_saved_continuation_cue_is_hidden_then_plans_only_current_private_state(self) -> None:
        owner_id, headers = self._login("13800139417")
        vault_id = "vault-recommendation-saved-continuation"
        decision_id, decision_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "This text must never become an inferred continuation topic."},
            command_id="saved-continuation-activate-decision",
        )
        values_id, values_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "A second confirmed gap provides a breadth recommendation."},
            command_id="saved-continuation-activate-values",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=decision_id,
            content_hash=decision_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="saved-continuation-confirm-decision",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=values_id,
            content_hash=values_hash,
            dimension="values",
            facets=("priority",),
            command_id="saved-continuation-confirm-values",
        )
        thread_id, session_id = self._seed_thread_with_session(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="saved-continuation-thread",
        )
        cue_path = self._saved_continuation_cue_path(vault_id, session_id)
        payload = {
            "commandId": "saved-continuation-cue-001",
            "threadId": thread_id,
            "expectedSessionVersion": 1,
            "memoryVersionId": decision_id,
            "targetDimension": "keyDecisions",
            "missingFacet": "outcome",
        }

        hidden = client.post(cue_path, headers=headers, json=payload)
        self.assertEqual(hidden.status_code, 404, hidden.text)
        self.assertEqual(
            hidden.json()["detail"]["code"],
            "ownerTruthSavedContinuationCueUnavailable",
        )

        main_module.OWNER_TRUTH_SAVED_CONTINUATION_CUE_QA_ENABLED = True
        created = client.post(cue_path, headers=headers, json=payload)
        replay = client.post(cue_path, headers=headers, json=payload)
        plan = client.post(self._plan_path(vault_id), headers=headers, json={})
        injected = client.post(
            cue_path,
            headers=headers,
            json={**payload, "continuationText": "must be rejected"},
        )

        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(created.headers["cache-control"], "no-store")
        self.assertEqual(created.json()["cue"]["status"], "created")
        self.assertEqual(replay.json()["cue"]["status"], "deduplicated")
        self.assertNotIn("inferred continuation", created.text)
        self.assertEqual(injected.status_code, 400, injected.text)
        self.assertEqual(
            injected.json()["detail"]["code"],
            "ownerTruthSavedContinuationCueInvalid",
        )
        self.assertEqual(plan.status_code, 200, plan.text)
        selected = plan.json()["recommendations"]["selected"]
        self.assertEqual([item["slot"] for item in selected], ["continuity", "breadth"])
        self.assertEqual(selected[0]["questionTemplateId"], "continueSavedOwnerCue")
        self.assertEqual(selected[0]["reasonCode"], "explicitOwnerSavedContinuation")
        self.assertNotIn("inferred continuation", plan.text)
        self.assertNotIn("claim", plan.text)

        self._set_thread_boundary(
            vault_id=vault_id,
            owner_id=owner_id,
            thread_id=thread_id,
            session_id=session_id,
            boundary=InterviewBoundary.DO_NOT_ASK,
            command_id="saved-continuation-do-not-ask",
        )
        stale_plan = client.post(self._plan_path(vault_id), headers=headers, json={})
        self.assertEqual(stale_plan.status_code, 200, stale_plan.text)
        self.assertEqual(stale_plan.json()["recommendations"]["selected"], [])

    def test_owner_can_read_server_planned_value_free_breadth_without_candidate_input(self) -> None:
        owner_id, headers = self._login("13800139419")
        vault_id = "vault-recommendation-plan-api"
        decision_id, decision_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "I left a role to spend more time with family."},
            command_id="recommendation-plan-activate-001",
        )
        values_id, values_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "I value taking time to reflect before commitments."},
            command_id="recommendation-plan-activate-002",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=decision_id,
            content_hash=decision_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="recommendation-plan-confirm-001",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=values_id,
            content_hash=values_hash,
            dimension="values",
            facets=("priority",),
            command_id="recommendation-plan-confirm-002",
        )
        self._seed_thread(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="recommendation-plan-thread-001",
        )

        response = client.post(self._plan_path(vault_id), headers=headers, json={})
        injected = client.post(
            self._plan_path(vault_id),
            headers=headers,
            json={"candidates": []},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(
            body["schemaVersion"],
            "owner-truth-knowledge-recommendation-plan-response-v1",
        )
        self.assertEqual(body["recommendations"]["candidateSource"], "serverPlanned")
        self.assertEqual(
            [item["slot"] for item in body["recommendations"]["selected"]],
            ["breadth"],
        )
        presentation = body["recommendations"]["selected"][0]["presentation"]
        self.assertEqual(presentation["label"], "换个角度")
        self.assertTrue(str(presentation["question"]).endswith("？"))
        self.assertEqual(presentation["questionSource"], "policyTemplate")
        self.assertRegex(
            str(body["recommendations"]["selected"][0]["expiresAt"]),
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$",
        )
        coverage = body["recommendations"]["dimensionRead"]["coverage"]
        self.assertEqual(coverage["knowledgeGapCount"], len(coverage["knowledgeGaps"]))
        self.assertTrue(coverage["knowledgeGaps"])
        self.assertTrue(
            all(
                item["reasonCode"] == "confirmedDimensionIncomplete"
                and item["evidenceRefCount"] > 0
                for item in coverage["knowledgeGaps"]
            )
        )
        self.assertNotIn("spend more time", response.text)
        self.assertNotIn("taking time to reflect", response.text)
        self.assertNotIn("claim", response.text)
        self.assertEqual(injected.status_code, 400, injected.text)
        self.assertEqual(
            injected.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationPlanInvalid",
        )

    def test_server_verified_recommendation_activation_is_hidden_then_replay_safe(self) -> None:
        owner_id, headers = self._login("13800139415")
        vault_id = "vault-recommendation-activation-api"
        decision_id, decision_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "This private source must not enter an activation response."},
            command_id="recommendation-activation-api-memory-001",
        )
        values_id, values_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "This second private source stays out of activation output."},
            command_id="recommendation-activation-api-memory-002",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=decision_id,
            content_hash=decision_hash,
            dimension="keyDecisions",
            facets=("choice", "reason"),
            command_id="recommendation-activation-api-confirm-001",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=values_id,
            content_hash=values_hash,
            dimension="values",
            facets=("priority",),
            command_id="recommendation-activation-api-confirm-002",
        )
        self._seed_thread_with_session(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="recommendation-activation-api-thread-001",
        )
        planned = client.post(self._plan_path(vault_id), headers=headers, json={})
        self.assertEqual(planned.status_code, 200, planned.text)
        breadth = next(
            item
            for item in planned.json()["recommendations"]["selected"]
            if item["slot"] == "breadth"
        )
        payload = {
            "commandId": "recommendation-activation-api-001",
            "expectedCandidateId": breadth["candidateId"],
            "slot": "breadth",
            "expectedSessionVersion": 1,
        }

        hidden = client.post(self._activation_path(vault_id), headers=headers, json=payload)
        self.assertEqual(hidden.status_code, 404, hidden.text)
        self.assertEqual(
            hidden.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationActivationUnavailable",
        )

        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_ACTIVATION_QA_ENABLED = True
        injected = client.post(
            self._activation_path(vault_id),
            headers=headers,
            json={**payload, "questionText": "must be rejected"},
        )
        stale = client.post(
            self._activation_path(vault_id),
            headers=headers,
            json={**payload, "commandId": "recommendation-activation-api-stale", "expectedSessionVersion": 2},
        )
        created = client.post(self._activation_path(vault_id), headers=headers, json=payload)
        replayed = client.post(self._activation_path(vault_id), headers=headers, json=payload)

        self.assertEqual(injected.status_code, 400, injected.text)
        self.assertEqual(
            injected.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationActivationInvalid",
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationActivationStale",
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(replayed.status_code, 200, replayed.text)
        self.assertEqual(created.headers["cache-control"], "no-store")
        activation = created.json()["activation"]
        self.assertEqual(activation["status"], "created")
        self.assertEqual(activation["candidateId"], breadth["candidateId"])
        self.assertEqual(activation["slot"], "breadth")
        self.assertEqual(activation["nextAction"], "broaden")
        self.assertEqual(replayed.json()["activation"]["status"], "deduplicated")
        self.assertNotIn("private source", created.text)
        self.assertNotIn("evidenceRefs", created.text)
        self.assertNotIn("questionText", created.text)

        after_acceptance = client.post(self._plan_path(vault_id), headers=headers, json={})
        self.assertEqual(after_acceptance.status_code, 200, after_acceptance.text)
        selected_after_acceptance = after_acceptance.json()["recommendations"]["selected"]
        self.assertEqual(len(selected_after_acceptance), 1)
        self.assertEqual(selected_after_acceptance[0]["slot"], "breadth")
        self.assertNotEqual(selected_after_acceptance[0]["candidateId"], breadth["candidateId"])
        self.assertEqual(
            [
                (item["candidateId"], item["reasonCode"])
                for item in after_acceptance.json()["recommendations"]["filtered"]
            ],
            [(breadth["candidateId"], "acceptedAlready")],
        )

    def test_recommendation_feedback_is_hidden_then_replaces_current_candidate(self) -> None:
        owner_id, headers = self._login("13800139426")
        vault_id = "vault-recommendation-feedback-api"
        fixtures = (
            (
                {"claim": "Private decision source must not enter feedback output."},
                "keyDecisions",
                ("choice", "reason"),
                "feedback-api-decision",
            ),
            (
                {"claim": "Private values source must not enter feedback output."},
                "values",
                ("priority",),
                "feedback-api-values",
            ),
            (
                {"claim": "Private life source must not enter feedback output."},
                "lifeStage",
                ("timeContext",),
                "feedback-api-life",
            ),
        )
        for content, dimension, facets, suffix in fixtures:
            memory_version_id, content_hash = self._activate_memory(
                vault_id=vault_id,
                owner_id=owner_id,
                content=content,
                command_id=f"{suffix}-activate",
            )
            self._confirm(
                vault_id=vault_id,
                owner_id=owner_id,
                memory_version_id=memory_version_id,
                content_hash=content_hash,
                dimension=dimension,
                facets=facets,
                command_id=f"{suffix}-confirm",
            )
        self._seed_thread_with_session(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="feedback-api-thread",
        )
        planned = client.post(self._plan_path(vault_id), headers=headers, json={})
        self.assertEqual(planned.status_code, 200, planned.text)
        breadth = next(
            item
            for item in planned.json()["recommendations"]["selected"]
            if item["slot"] == "breadth"
        )
        payload = {
            "commandId": "recommendation-feedback-api-001",
            "expectedCandidateId": breadth["candidateId"],
            "feedbackAction": "replace",
            "feedbackReason": "questionWording",
            "expectedSessionVersion": 1,
        }
        path = self._feedback_path(vault_id)

        hidden = client.post(path, headers=headers, json=payload)
        self.assertEqual(hidden.status_code, 404, hidden.text)
        self.assertEqual(
            hidden.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationFeedbackUnavailable",
        )

        main_module.OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_QA_ENABLED = True
        injected = client.post(
            path,
            headers=headers,
            json={**payload, "questionText": "must be rejected"},
        )
        stale = client.post(
            path,
            headers=headers,
            json={
                **payload,
                "commandId": "recommendation-feedback-api-stale",
                "expectedSessionVersion": 2,
            },
        )
        created = client.post(path, headers=headers, json=payload)
        replayed = client.post(path, headers=headers, json=payload)

        self.assertEqual(injected.status_code, 400, injected.text)
        self.assertEqual(
            injected.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationFeedbackInvalid",
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(
            stale.json()["detail"]["code"],
            "ownerTruthKnowledgeRecommendationFeedbackStale",
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(replayed.status_code, 200, replayed.text)
        self.assertEqual(created.headers["cache-control"], "no-store")
        feedback = created.json()["feedback"]
        self.assertEqual(feedback["status"], "created")
        self.assertEqual(feedback["candidateId"], breadth["candidateId"])
        self.assertEqual(feedback["feedbackAction"], "replace")
        self.assertEqual(feedback["feedbackScope"], "candidate")
        self.assertEqual(replayed.json()["feedback"]["status"], "deduplicated")
        self.assertNotIn("Private decision", created.text)
        self.assertNotIn("questionText", created.text)
        self.assertNotIn("evidenceRefs", created.text)

        replanned = client.post(self._plan_path(vault_id), headers=headers, json={})
        self.assertEqual(replanned.status_code, 200, replanned.text)
        next_breadth = next(
            item
            for item in replanned.json()["recommendations"]["selected"]
            if item["slot"] == "breadth"
        )
        self.assertNotEqual(next_breadth["candidateId"], breadth["candidateId"])
        self.assertIn(
            (breadth["candidateId"], "userRequestedReplacement"),
            [
                (item["candidateId"], item["reasonCode"])
                for item in replanned.json()["recommendations"]["filtered"]
            ],
        )

    def test_server_plan_returns_empty_after_session_boundary_blocks_recommendations(self) -> None:
        owner_id, headers = self._login("13800139418")
        vault_id = "vault-recommendation-plan-boundary"
        memory_id, content_hash = self._activate_memory(
            vault_id=vault_id,
            owner_id=owner_id,
            content={"claim": "This gap must not be planned after a user boundary."},
            command_id="recommendation-plan-activate-boundary",
        )
        self._confirm(
            vault_id=vault_id,
            owner_id=owner_id,
            memory_version_id=memory_id,
            content_hash=content_hash,
            dimension="keyDecisions",
            facets=("choice",),
            command_id="recommendation-plan-confirm-boundary",
        )
        thread_id, session_id = self._seed_thread_with_session(
            vault_id=vault_id,
            owner_id=owner_id,
            command_id="recommendation-plan-thread-boundary",
        )
        self._set_thread_boundary(
            vault_id=vault_id,
            owner_id=owner_id,
            thread_id=thread_id,
            session_id=session_id,
            boundary=InterviewBoundary.DO_NOT_ASK,
            command_id="recommendation-plan-boundary-do-not-ask",
        )

        response = client.post(self._plan_path(vault_id), headers=headers, json={})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["recommendations"]["selected"], [])

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
