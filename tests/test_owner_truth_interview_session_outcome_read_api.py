from __future__ import annotations

import unittest
from hashlib import sha256
import json
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateSnapshot
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
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


client = TestClient(app)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthInterviewSessionOutcomeReadAPITests(unittest.TestCase):
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
        self.previous_outcome_qa = (
            main_module.OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_READ_QA_ENABLED
        )
        self.store = InMemoryStore()
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.OWNER_TRUTH_KNOWLEDGE_DIMENSION_CONFIRMATION_QA_ENABLED = True
        main_module.OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_READ_QA_ENABLED = True

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
        main_module.OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_READ_QA_ENABLED = (
            self.previous_outcome_qa
        )

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "会话结果 QA", "password": "password123"},
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
            json={"phone": phone, "nickname": "访谈回顾展示测试", "password": "password123"},
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
    def _outcome_presentation_policy_headers(
        headers: dict[str, str],
        *,
        session_id: str,
        decision_id: str,
    ) -> dict[str, str]:
        return {
            **headers,
            "X-DreamJourney-Feature": "ownerTruthInterviewOutcome",
            "X-DreamJourney-Feature-Decision-Id": decision_id,
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": "release-policy-v1",
            "X-DreamJourney-Policy-Revision": "1",
            "X-DreamJourney-Account-Generation": sha256(
                session_id.encode("utf-8")
            ).hexdigest()[:24],
        }

    def _seed_session(self, *, vault_id: str, owner_id: str) -> str:
        session_id = str(uuid4())
        source_id = str(uuid4())
        seed_content = {"claim": "outcome API vault seed"}
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        self.store.owner_truth_candidate_review_repository().seed(
            OwnerTruthCandidateSnapshot(
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
                content_hash=_hash(seed_content),
                content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                payload={
                    "content": seed_content,
                    "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                    "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                    "reviewMode": "single",
                    "schemaVersion": "owner-truth-candidate-proposal-v1",
                },
            )
        )
        with self.store.request_unit_of_work(
            correlation_id=f"session-outcome-api:{vault_id}:{session_id}",
            command_id="session-outcome-api-seed",
        ):
            OwnerTruthConversationService(
                self.store.owner_truth_conversation_repository()
            ).start_session(
                command=StartInterviewSessionCommand(
                    command_id="session-outcome-api-start",
                    thread_id=str(uuid4()),
                    session_id=session_id,
                    expected_thread_version=0,
                    entry_mode="naturalInput",
                ),
                context=context,
            )
        OwnerTruthMemoryProjectionService(self.store).rebuild(context=context)
        return session_id

    @staticmethod
    def _path(vault_id: str, session_id: str) -> str:
        return f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/outcome/read"

    def test_contract_is_hidden_when_its_separate_flag_is_disabled(self) -> None:
        _owner_id, headers = self._login("13800139641")
        main_module.OWNER_TRUTH_INTERVIEW_SESSION_OUTCOME_READ_QA_ENABLED = False

        response = client.post(
            self._path("vault-session-outcome-hidden", str(uuid4())),
            headers=headers,
            json={},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthInterviewSessionOutcomeReadUnavailable",
        )

    def test_owner_reads_value_free_session_outcome(self) -> None:
        owner_id, headers = self._login("13800139642")
        vault_id = "vault-session-outcome-api"
        session_id = self._seed_session(vault_id=vault_id, owner_id=owner_id)

        response = client.post(self._path(vault_id, session_id), headers=headers, json={})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(
            body["schemaVersion"],
            "owner-truth-interview-session-outcome-read-response-v1",
        )
        self.assertEqual(body["vaultId"], vault_id)
        outcome = body["sessionOutcome"]
        self.assertEqual(outcome["schemaVersion"], "owner-truth-interview-session-outcome-read-v1")
        self.assertEqual(outcome["sessionId"], session_id)
        self.assertEqual(outcome["presentation"], {
            "state": "readyForNarrative",
            "canContinue": True,
            "canContinueLater": True,
        })
        self.assertEqual(outcome["thisSession"], {
            "sessionState": "active",
            "sessionBoundary": "open",
            "reviewBatchCount": 0,
            "pendingReviewBatchCount": 0,
            "acknowledgedReviewBatchCount": 0,
            "admittedReviewBatchCount": 0,
            "confirmationState": "ready",
            "confirmedMemoryVersionCount": 0,
        })
        self.assertEqual(outcome["laterContinue"], {"eligibleSavedContinuationCueCount": 0})
        self.assertNotIn("message", str(outcome))
        self.assertNotIn("candidate", str(outcome).lower())
        self.assertNotIn("content", str(outcome).lower())

    def test_product_outcome_requires_its_own_policy_and_limits_response(self) -> None:
        owner_id, headers, auth_session_id = self._login_release_policy("13800139646")
        vault_id = "vault-session-outcome-presentation"
        session_id = self._seed_session(vault_id=vault_id, owner_id=owner_id)
        path = f"/v2/vaults/{vault_id}/interview-sessions/{session_id}/outcome"

        denied = client.get(
            path,
            headers={**headers, "X-DreamJourney-QA-Owner-Truth": "1"},
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "release_policy_denied")
        self.assertEqual(denied.json()["detail"]["feature"], "ownerTruthInterviewOutcome")

        policy_service = main_module.RELEASE_POLICY_SERVICE
        previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthInterviewOutcome"
        }
        try:
            response = client.get(
                path,
                headers=self._outcome_presentation_policy_headers(
                    headers,
                    session_id=auth_session_id,
                    decision_id="interview-outcome-presentation-owner",
                ),
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
            body = response.json()
            self.assertEqual(
                body["schemaVersion"],
                "owner-truth-interview-session-outcome-presentation-v1",
            )
            self.assertEqual(body["vaultId"], vault_id)
            outcome = body["sessionOutcome"]
            self.assertEqual(set(outcome), {"state", "thisSession", "laterContinue"})
            self.assertEqual(outcome["state"], "ready")
            self.assertEqual(
                outcome["thisSession"],
                {"confirmedMemoryCount": 0, "pendingReviewBatchCount": 0},
            )
            self.assertEqual(
                outcome["laterContinue"],
                {"canContinueLater": True, "eligibleCueCount": 0},
            )
            rendered = json.dumps(body, ensure_ascii=False)
            for forbidden in (
                "sessionId",
                "threadId",
                "memoryVersionId",
                "sourceId",
                "candidate",
                "message",
                "content",
                "reviewBatch",
                "authorityEpoch",
                "policyVersion",
            ):
                self.assertNotIn(forbidden, rendered)
        finally:
            policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible

    def test_owner_cannot_submit_extra_fields(self) -> None:
        owner_id, headers = self._login("13800139643")
        vault_id = "vault-session-outcome-invalid"
        session_id = self._seed_session(vault_id=vault_id, owner_id=owner_id)

        response = client.post(
            self._path(vault_id, session_id),
            headers=headers,
            json={"message": "must not become an input surface"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthInterviewSessionOutcomeReadInvalid",
        )

    def test_other_owner_cannot_read_session_outcome(self) -> None:
        owner_id, _headers = self._login("13800139644")
        vault_id = "vault-session-outcome-boundary"
        session_id = self._seed_session(vault_id=vault_id, owner_id=owner_id)
        _other_id, other_headers = self._login("13800139645")

        response = client.post(self._path(vault_id, session_id), headers=other_headers, json={})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ownerTruthInterviewSessionOutcomeReadDenied",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
