from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main_module
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
from app.domain.owner_truth.source_commands import CreateTextSourceCommand, OwnerTruthCommandContext
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService
from app.services.owner_truth_source import OwnerTruthSourceCommandService
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


client = TestClient(app)


def _content_hash(content: dict[str, object]) -> str:
    return sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


class OwnerTruthContextAuthorityAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_release_policy_command_mode = main_module.RELEASE_POLICY_COMMAND_MODE
        self.previous_context_authority = (
            main_module.OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED
        )
        self.previous_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        self.previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self.previous_release_policy_command_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.RELEASE_POLICY_COMMAND_MODE = "observe"
        main_module.OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED = True

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.RELEASE_POLICY_COMMAND_MODE = self.previous_release_policy_command_mode
        main_module.OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED = (
            self.previous_context_authority
        )
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = self.previous_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE = self.previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self.previous_release_policy_command_gate

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "Context Authority", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return payload["user"]["id"], {"Authorization": f"Bearer {payload['auth']['accessToken']}"}

    @staticmethod
    def _set_closed_pilot(owner_id: str) -> None:
        service = ReleasePolicyService(
            policy_revision=1,
            min_client_build=1,
            ttl_seconds=300,
            closed_pilot_enabled_features={"ownerTruthCandidateReview"},
            shadow_mode=True,
        )
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset({owner_id})
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)

    @staticmethod
    def _seed_confirmed_memory(owner_id: str) -> OwnerTruthCandidateSnapshot:
        context = OwnerTruthCommandContext(
            vault_id=owner_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        source_id = str(uuid4())
        OwnerTruthSourceCommandService(main_module.store).create_text_source(
            command=CreateTextSourceCommand(
                command_id=f"context-authority-api-source-{source_id}",
                source_id=source_id,
                expected_version=0,
                text="本人主动存入的回忆来源。",
                metadata={"origin": "context-authority-api-test"},
            ),
            context=context,
        )
        content = {"summary": "已确认的个人回忆应进入闭环试点回响上下文。"}
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=owner_id,
            owner_subject_id=owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_content_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )
        main_module.store.owner_truth_candidate_review_repository().seed(candidate)
        OwnerTruthCandidateReviewService(main_module.store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=f"context-authority-api-accept-{candidate.candidate_id}",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=context,
        )
        OwnerTruthMemoryProjectionService(main_module.store).rebuild(context=context)
        return candidate

    @staticmethod
    def _payload(owner_id: str) -> dict[str, str]:
        return {
            "userId": owner_id,
            "intent": "echo_chat",
            "query": "请用已经确认的回忆陪我聊聊",
            "personaScope": "personal",
            "digitalHumanId": owner_id,
        }

    def test_server_allowlisted_owner_uses_v4_context_without_qa_header(self) -> None:
        owner_id, headers = self._login("13800139761")
        candidate = self._seed_confirmed_memory(owner_id)
        self._set_closed_pilot(owner_id)

        response = client.post("/context/build", headers=headers, json=self._payload(owner_id))

        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()["contextPacket"]
        self.assertEqual(packet["contextVersion"], "echo-context-v4-owner")
        self.assertEqual(packet["contextAuthority"]["mode"], "ownerTruthConfirmedProjection")
        self.assertFalse(packet["contextAuthority"]["legacyContextRead"])
        self.assertEqual(packet["selectedContext"][0]["citation"]["sourceId"], candidate.source_id)
        self.assertNotIn("X-DreamJourney-QA-Owner-Truth", headers)

    def test_non_allowlisted_owner_keeps_legacy_context_route(self) -> None:
        owner_id, headers = self._login("13800139762")
        self._set_closed_pilot("different-owner")
        headers["X-DreamJourney-Policy-Cohort"] = "closedPilotAdultSelf"

        response = client.post("/context/build", headers=headers, json=self._payload(owner_id))

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["contextPacket"]["contextVersion"], "echo-context-v2")
        self.assertNotIn("contextAuthority", response.json()["contextPacket"])

    def test_server_switch_defaults_off_even_for_an_allowlisted_owner(self) -> None:
        owner_id, headers = self._login("13800139764")
        self._seed_confirmed_memory(owner_id)
        self._set_closed_pilot(owner_id)
        main_module.OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED = False

        response = client.post("/context/build", headers=headers, json=self._payload(owner_id))

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["contextPacket"]["contextVersion"], "echo-context-v2")
        self.assertNotIn("contextAuthority", response.json()["contextPacket"])

    def test_allowlisted_owner_without_projection_fails_closed_to_empty_v4_context(self) -> None:
        owner_id, headers = self._login("13800139763")
        self._set_closed_pilot(owner_id)

        response = client.post("/context/build", headers=headers, json=self._payload(owner_id))

        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()["contextPacket"]
        self.assertEqual(packet["contextVersion"], "echo-context-v4-owner")
        self.assertEqual(packet["selectedContext"], [])
        self.assertEqual(packet["memory"]["archiveItems"], [])
        self.assertIn("owner_truth_context_unavailable_no_personal_memory", packet["fallbacks"])
