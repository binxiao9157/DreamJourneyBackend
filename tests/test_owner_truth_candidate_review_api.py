from __future__ import annotations

from hashlib import sha256
import json
import unittest
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
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


client = TestClient(app)


def _content_hash(content: dict[str, object]) -> str:
    return sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


class OwnerTruthCandidateReviewAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_qa_enabled = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
        self.previous_closed_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        self.previous_closed_pilot_features = set(
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features
        )
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = {
            "ownerTruthCandidateReview"
        }

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = self.previous_qa_enabled
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = self.previous_closed_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = (
            self.previous_closed_pilot_features
        )

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str]]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "候选审核测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return payload["user"]["id"], {
            "Authorization": f"Bearer {payload['auth']['accessToken']}",
            "X-DreamJourney-QA-Owner-Truth": "1",
        }

    @staticmethod
    def _formal_login(phone: str) -> tuple[str, dict[str, str], str]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "正式候选审核测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        payload = response.json()
        return (
            str(payload["user"]["id"]),
            {"Authorization": f"Bearer {payload['auth']['accessToken']}"},
            str(payload["auth"]["sessionId"]),
        )

    @staticmethod
    def _formal_policy_headers(
        headers: dict[str, str],
        *,
        session_id: str,
    ) -> dict[str, str]:
        captured = dict(headers)
        captured.update(
            {
                "X-DreamJourney-Feature": "ownerTruthCandidateReview",
                "X-DreamJourney-Feature-Decision-Id": f"decision-{uuid4()}",
                "X-DreamJourney-Feature-Allowed": "true",
                "X-DreamJourney-Policy-Version": "release-policy-v1",
                "X-DreamJourney-Policy-Revision": "1",
                "X-DreamJourney-Account-Generation": sha256(
                    session_id.encode("utf-8")
                ).hexdigest()[:24],
            }
        )
        return captured

    @staticmethod
    def _allow_closed_pilot_owner(owner_id: str) -> None:
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            set(main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS) | {owner_id}
        )

    @staticmethod
    def _candidate(*, vault_id: str, owner_subject_id: str) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        content = {"summary": "小时候在院子里听雨"}
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
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

    def _seed(self, candidate: OwnerTruthCandidateSnapshot) -> None:
        main_module.store.owner_truth_candidate_review_repository().seed(candidate)

    def test_contract_is_default_hidden_even_for_an_authenticated_owner(self) -> None:
        owner_id, headers = self._login("13800139101")
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False

        response = client.get(f"/v2/vaults/vault-hidden/candidates", headers=headers)
        projection = client.get(
            "/v2/vaults/vault-hidden/memory-projection",
            headers=headers,
        )
        compatibility = client.get(
            "/v2/vaults/vault-hidden/kblite-compatibility",
            headers=headers,
        )
        compatibility_envelope = client.get(
            "/v2/vaults/vault-hidden/kblite-compatibility/read-envelope",
            headers=headers,
        )
        context_shadow = client.get(
            "/v2/vaults/vault-hidden/context-shadow",
            headers=headers,
        )
        context_shadow_build = client.post(
            "/v2/vaults/vault-hidden/context-shadow/build",
            headers=headers,
            json={"query": "default hidden"},
        )
        context_shadow_compare = client.post(
            "/v2/vaults/vault-hidden/context-shadow/compare",
            headers=headers,
            json={"query": "default hidden"},
        )
        context_materialization = client.post(
            "/v2/vaults/vault-hidden/context-shadow/materialize",
            headers=headers,
            json={"query": "default hidden"},
        )
        answer_citation = client.post(
            "/v2/vaults/vault-hidden/answer-citation-receipts",
            headers=headers,
            json={
                "commandId": "answer-citation-hidden-001",
                "query": "default hidden",
                "answerText": "不得公开。",
            },
        )
        citation_read = client.get(
            f"/v2/vaults/vault-hidden/answers/{uuid4()}/citations",
            headers=headers,
        )
        answer_feedback = client.post(
            f"/v2/vaults/vault-hidden/answers/{uuid4()}/feedback",
            headers=headers,
            json={"commandId": "answer-feedback-hidden-001", "helpful": True},
        )
        correction = client.post(
            "/v2/vaults/vault-hidden/memories/00000000-0000-0000-0000-000000000001/corrections",
            headers=headers,
            json={},
        )
        correction_resolution = client.post(
            f"/v2/vaults/vault-hidden/correction-requests/{uuid4()}/resolve",
            headers=headers,
            json={},
        )

        self.assertEqual(owner_id.startswith("user_"), True)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["code"], "ownerTruthCandidateReviewUnavailable")
        self.assertEqual(projection.status_code, 404)
        self.assertEqual(
            projection.json()["detail"]["code"],
            "ownerTruthMemoryProjectionUnavailable",
        )
        self.assertEqual(compatibility.status_code, 404)
        self.assertEqual(
            compatibility.json()["detail"]["code"],
            "ownerTruthKBLiteCompatibilityUnavailable",
        )
        self.assertEqual(compatibility_envelope.status_code, 404)
        self.assertEqual(
            compatibility_envelope.json()["detail"]["code"],
            "ownerTruthKBLiteCompatibilityUnavailable",
        )
        self.assertEqual(context_shadow.status_code, 404)
        self.assertEqual(
            context_shadow.json()["detail"]["code"],
            "ownerTruthContextShadowUnavailable",
        )
        self.assertEqual(context_shadow_build.status_code, 404)
        self.assertEqual(
            context_shadow_build.json()["detail"]["code"],
            "ownerTruthContextShadowUnavailable",
        )
        self.assertEqual(context_shadow_compare.status_code, 404)
        self.assertEqual(
            context_shadow_compare.json()["detail"]["code"],
            "ownerTruthContextShadowUnavailable",
        )
        self.assertEqual(context_materialization.status_code, 404)
        self.assertEqual(
            context_materialization.json()["detail"]["code"],
            "ownerTruthContextShadowUnavailable",
        )
        self.assertEqual(answer_citation.status_code, 404)
        self.assertEqual(
            answer_citation.json()["detail"]["code"],
            "ownerTruthAnswerCitationUnavailable",
        )
        self.assertEqual(citation_read.status_code, 404)
        self.assertEqual(
            citation_read.json()["detail"]["code"],
            "ownerTruthAnswerCitationUnavailable",
        )
        self.assertEqual(answer_feedback.status_code, 404)
        self.assertEqual(
            answer_feedback.json()["detail"]["code"],
            "ownerTruthAnswerCitationUnavailable",
        )
        self.assertEqual(correction.status_code, 404)
        self.assertEqual(
            correction.json()["detail"]["code"],
            "ownerTruthCorrectionRequestUnavailable",
        )
        self.assertEqual(correction_resolution.status_code, 404)
        self.assertEqual(
            correction_resolution.json()["detail"]["code"],
            "ownerTruthCorrectionRequestUnavailable",
        )

    def test_owner_can_list_decide_activate_memory_and_replay(self) -> None:
        owner_id, headers = self._login("13800139102")
        vault_id = "vault-api-owner-review"
        candidate = self._candidate(vault_id=vault_id, owner_subject_id=owner_id)
        self._seed(candidate)

        inbox = client.get(f"/v2/vaults/{vault_id}/candidates", headers=headers)
        self.assertEqual(inbox.status_code, 200)
        self.assertEqual(inbox.headers["cache-control"], "no-store")
        self.assertEqual(inbox.json()["schemaVersion"], "owner-truth-candidate-inbox-v1")
        self.assertEqual(inbox.json()["candidates"][0]["candidateId"], candidate.candidate_id)
        self.assertEqual(inbox.json()["candidates"][0]["candidateVersion"], 1)

        command = {
            "commandId": "candidate-api-accept-001",
            "expectedCandidateVersion": 1,
            "action": "accept",
            "reasonCode": "ownerReviewed",
        }
        created = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=headers,
            json=command,
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["status"], "created")
        self.assertEqual(
            created.json()["schemaVersion"],
            "owner-truth-candidate-decision-memory-v1",
        )
        self.assertEqual(created.json()["memoryActivation"]["status"], "created")
        self.assertTrue(created.json()["memoryActivation"]["memoryId"])
        self.assertTrue(created.json()["memoryActivation"]["memoryVersionId"])
        self.assertEqual(created.json()["receipt"]["decision"], "accepted")

        replay = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=headers,
            json=command,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["status"], "deduplicated")
        self.assertEqual(
            replay.json()["receipt"]["receiptId"],
            created.json()["receipt"]["receiptId"],
        )
        self.assertEqual(replay.json()["memoryActivation"]["status"], "deduplicated")
        self.assertEqual(
            client.get(f"/v2/vaults/{vault_id}/candidates", headers=headers).json()["candidates"],
            [],
        )

    def test_server_authorized_owner_reviews_candidate_without_qa_header(self) -> None:
        owner_id, auth_headers, session_id = self._formal_login("13800139105")
        self._allow_closed_pilot_owner(owner_id)
        vault_id = "vault-api-formal-owner-review"
        candidate = self._candidate(vault_id=vault_id, owner_subject_id=owner_id)
        self._seed(candidate)
        headers = self._formal_policy_headers(auth_headers, session_id=session_id)

        inbox = client.get(f"/v2/vaults/{vault_id}/candidates", headers=headers)
        self.assertEqual(inbox.status_code, 200, inbox.text)
        self.assertEqual([item["candidateId"] for item in inbox.json()["candidates"]], [candidate.candidate_id])

        command = {
            "commandId": "candidate-api-formal-review-001",
            "expectedCandidateVersion": 1,
            "action": "accept",
            "reasonCode": "ownerReviewed",
        }
        created = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=headers,
            json=command,
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["receipt"]["decision"], "accepted")

        snapshot = main_module.store.owner_truth_candidate_review_repository().snapshot()
        receipt = next(iter(snapshot["receipts"].values()))
        evidence = receipt["authorizationCapture"]
        self.assertEqual(evidence["feature"], "ownerTruthCandidateReview")
        self.assertEqual(evidence["schemaVersion"], "owner-truth-command-authorization-capture-v1")
        rendered = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("X-DreamJourney-QA-Owner-Truth", rendered)
        self.assertNotIn(auth_headers["Authorization"], rendered)

        replay = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=self._formal_policy_headers(auth_headers, session_id=session_id),
            json=command,
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["status"], "deduplicated")
        self.assertEqual(
            replay.json()["receipt"]["receiptId"],
            created.json()["receipt"]["receiptId"],
        )

    def test_server_authorized_owner_records_citation_and_resolves_correction_without_qa_header(
        self,
    ) -> None:
        owner_id, auth_headers, session_id = self._formal_login("13800139106")
        self._allow_closed_pilot_owner(owner_id)
        vault_id = "vault-api-formal-correction"
        candidate = self._candidate(vault_id=vault_id, owner_subject_id=owner_id)
        self._seed(candidate)

        accepted = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=self._formal_policy_headers(auth_headers, session_id=session_id),
            json={
                "commandId": "candidate-api-formal-correction-accept-001",
                "expectedCandidateVersion": 1,
                "action": "accept",
                "reasonCode": "ownerReviewed",
            },
        )
        self.assertEqual(accepted.status_code, 201, accepted.text)
        memory_activation = accepted.json()["memoryActivation"]
        OwnerTruthMemoryProjectionService(main_module.store).rebuild(
            context=OwnerTruthCommandContext(
                vault_id=vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            )
        )

        answer_text = "我只根据你已经确认的记忆回答。"
        citation_response = client.post(
            f"/v2/vaults/{vault_id}/answer-citation-receipts",
            headers=self._formal_policy_headers(auth_headers, session_id=session_id),
            json={
                "commandId": "answer-citation-formal-correction-001",
                "intent": "echo_chat",
                "query": "请说说这段经历。",
                "answerText": answer_text,
            },
        )
        self.assertEqual(citation_response.status_code, 201, citation_response.text)
        citation_summary = citation_response.json()["answerCitation"]
        self.assertEqual(citation_summary["citationCount"], 1)
        self.assertEqual(citation_summary["contextVersion"], "echo-context-v4-owner-qa")
        self.assertNotIn(answer_text, str(citation_summary))

        citation_read = client.get(
            f"/v2/vaults/{vault_id}/answers/{citation_summary['answerId']}/citations",
            headers=self._formal_policy_headers(auth_headers, session_id=session_id),
        )
        self.assertEqual(citation_read.status_code, 200, citation_read.text)
        citation = citation_read.json()["answerCitation"]["citations"][0]

        correction_text = "不是父亲，是外祖父讲了这段故事。"
        requested = client.post(
            f"/v2/vaults/{vault_id}/memories/{citation['citation']['memoryId']}/corrections",
            headers=self._formal_policy_headers(auth_headers, session_id=session_id),
            json={
                "commandId": "correction-api-formal-001",
                "answerId": citation_summary["answerId"],
                "citationId": citation["citationId"],
                "expectedMemoryVersionId": citation["citation"]["memoryVersionId"],
                "correctionText": correction_text,
                "reasonCode": "ownerReportedCorrection",
            },
        )
        self.assertEqual(requested.status_code, 201, requested.text)
        request_summary = requested.json()["correctionRequest"]
        self.assertNotIn(correction_text, str(request_summary))

        resolved = client.post(
            f"/v2/vaults/{vault_id}/correction-requests/{request_summary['correctionRequestId']}/resolve",
            headers=self._formal_policy_headers(auth_headers, session_id=session_id),
            json={
                "commandId": "correction-resolution-formal-001",
                "expectedCandidateVersion": request_summary["candidateVersion"],
                "expectedMemoryVersionId": request_summary["expectedMemoryVersionId"],
                "action": "correct",
                "correctedValue": {"summary": "外祖父在院子里讲故事"},
                "correctedValueSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "reasonCode": "ownerConfirmedCorrection",
            },
        )
        self.assertEqual(resolved.status_code, 201, resolved.text)
        self.assertEqual(resolved.json()["correctionResolution"]["decision"], "corrected")
        self.assertEqual(
            resolved.json()["correctionResolution"]["supersededMemoryVersionId"],
            memory_activation["memoryVersionId"],
        )

        receipts = main_module.store.owner_truth_candidate_review_repository().snapshot()["receipts"]
        correction_receipt = next(
            receipt
            for receipt in receipts.values()
            if receipt["candidateId"] == request_summary["candidateId"]
        )
        evidence = correction_receipt["authorizationCapture"]
        self.assertEqual(evidence["feature"], "ownerTruthCandidateReview")
        self.assertNotIn("X-DreamJourney-QA-Owner-Truth", json.dumps(receipts, sort_keys=True))

        feedback = client.post(
            f"/v2/vaults/{vault_id}/answers/{citation_summary['answerId']}/feedback",
            headers=self._formal_policy_headers(auth_headers, session_id=session_id),
            json={"commandId": "answer-feedback-formal-hidden-001", "helpful": True},
        )
        self.assertEqual(feedback.status_code, 404)
        self.assertEqual(
            feedback.json()["detail"]["code"],
            "ownerTruthAnswerCitationUnavailable",
        )

    def test_cross_vault_stale_and_corrected_value_boundaries(self) -> None:
        owner_id, headers = self._login("13800139103")
        vault_id = "vault-api-correct-review"
        candidate = self._candidate(vault_id=vault_id, owner_subject_id=owner_id)
        self._seed(candidate)

        denied = client.get("/v2/vaults/vault-other-owner/candidates", headers=headers)
        shadow_denied = client.post(
            "/v2/vaults/vault-other-owner/context-shadow/build",
            headers=headers,
            json={"query": "跨 Vault 读取不得构建个人上下文"},
        )
        shadow_compare_denied = client.post(
            "/v2/vaults/vault-other-owner/context-shadow/compare",
            headers=headers,
            json={"query": "跨 Vault 读取不得对照旧上下文"},
        )
        answer_citation_denied = client.post(
            "/v2/vaults/vault-other-owner/answer-citation-receipts",
            headers=headers,
            json={
                "commandId": "answer-citation-cross-vault-001",
                "query": "跨 Vault 读取不得记录答案引用",
                "answerText": "不应创建。",
            },
        )
        correction_denied = client.post(
            f"/v2/vaults/vault-other-owner/memories/{uuid4()}/corrections",
            headers=headers,
            json={
                "commandId": "correction-cross-vault-001",
                "answerId": str(uuid4()),
                "citationId": str(uuid4()),
                "expectedMemoryVersionId": str(uuid4()),
                "correctionText": "跨 Vault 不得写入纠错请求。",
                "reasonCode": "ownerReportedCorrection",
            },
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["detail"]["code"], "ownerTruthCandidateReviewDenied")
        self.assertEqual(shadow_denied.status_code, 403)
        self.assertEqual(
            shadow_denied.json()["detail"]["code"],
            "ownerTruthMemoryProjectionDenied",
        )
        self.assertEqual(shadow_compare_denied.status_code, 403)
        self.assertEqual(
            shadow_compare_denied.json()["detail"]["code"],
            "ownerTruthMemoryProjectionDenied",
        )
        self.assertEqual(answer_citation_denied.status_code, 403)
        self.assertEqual(
            answer_citation_denied.json()["detail"]["code"],
            "ownerTruthAnswerCitationDenied",
        )
        self.assertEqual(correction_denied.status_code, 403)
        self.assertEqual(
            correction_denied.json()["detail"]["code"],
            "ownerTruthCorrectionRequestDenied",
        )

        stale = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=headers,
            json={
                "commandId": "candidate-api-stale-001",
                "expectedCandidateVersion": 2,
                "action": "reject",
                "reasonCode": "ownerReviewed",
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["code"], "ownerTruthCandidateVersionConflict")

        corrected_value = {"summary": "小时候在院子里听父亲讲故事"}
        corrected = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=headers,
            json={
                "commandId": "candidate-api-correct-001",
                "expectedCandidateVersion": 1,
                "action": "correct",
                "correctedValue": corrected_value,
                "correctedValueSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "reasonCode": "ownerCorrected",
            },
        )
        self.assertEqual(corrected.status_code, 201)
        body = corrected.json()
        self.assertEqual(body["receipt"]["decision"], "corrected")
        self.assertIsNotNone(body["receipt"]["correctedValueId"])
        self.assertNotIn("correctedValue", body["receipt"])
        self.assertNotIn(corrected_value["summary"], str(body))

    def test_owner_can_rebuild_and_read_projection_without_raw_content_echo(self) -> None:
        owner_id, headers = self._login("13800139104")
        vault_id = "vault-api-memory-projection"
        candidate = self._candidate(vault_id=vault_id, owner_subject_id=owner_id)
        self._seed(candidate)

        decision = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate.candidate_id}/decisions",
            headers=headers,
            json={
                "commandId": "candidate-api-projection-001",
                "expectedCandidateVersion": 1,
                "action": "accept",
                "reasonCode": "ownerReviewed",
            },
        )
        self.assertEqual(decision.status_code, 201)

        before = client.get(
            f"/v2/vaults/{vault_id}/memory-projection",
            headers=headers,
        )
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.json()["projection"]["state"], "rebuilding")

        rebuilt = client.post(
            f"/v2/vaults/{vault_id}/memory-projection/rebuild",
            headers=headers,
        )
        self.assertEqual(rebuilt.status_code, 200)
        self.assertEqual(
            rebuilt.json()["schemaVersion"],
            "owner-truth-memory-projection-rebuild-v1",
        )
        self.assertEqual(rebuilt.json()["outcome"], "rebuilt")
        self.assertEqual(rebuilt.json()["projection"]["state"], "ready")
        self.assertEqual(rebuilt.json()["projection"]["entryCount"], 1)
        self.assertNotIn(
            candidate.content["summary"],
            str(rebuilt.json()["projection"]),
        )

        replay = client.post(
            f"/v2/vaults/{vault_id}/memory-projection/rebuild",
            headers=headers,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["outcome"], "unchanged")

        compatibility = client.get(
            f"/v2/vaults/{vault_id}/kblite-compatibility",
            headers=headers,
        )
        self.assertEqual(compatibility.status_code, 200)
        self.assertEqual(
            compatibility.json()["schemaVersion"],
            "owner-truth-kblite-compatibility-read-v1",
        )
        self.assertEqual(compatibility.json()["compatibility"]["state"], "ready")
        self.assertEqual(compatibility.json()["compatibility"]["factCount"], 0)
        self.assertEqual(compatibility.json()["compatibility"]["filteredEntries"][0]["reason"], "memory_kind_not_compatibility_fact")
        self.assertNotIn(candidate.content["summary"], str(compatibility.json()))

        compatibility_envelope = client.get(
            f"/v2/vaults/{vault_id}/kblite-compatibility/read-envelope",
            headers=headers,
        )
        self.assertEqual(compatibility_envelope.status_code, 200)
        self.assertEqual(compatibility_envelope.headers["cache-control"], "no-store")
        self.assertEqual(
            compatibility_envelope.json()["schemaVersion"],
            "owner-truth-kblite-read-envelope-v1",
        )
        self.assertEqual(compatibility_envelope.json()["state"], "ready")
        self.assertEqual(compatibility_envelope.json()["cacheDisposition"], "replace")
        self.assertTrue(compatibility_envelope.json()["contentHash"])
        self.assertEqual(compatibility_envelope.json()["graph"]["facts"], [])
        self.assertNotIn(candidate.content["summary"], str(compatibility_envelope.json()))

        context_shadow = client.get(
            f"/v2/vaults/{vault_id}/context-shadow",
            headers=headers,
        )
        self.assertEqual(context_shadow.status_code, 200)
        self.assertEqual(
            context_shadow.json()["schemaVersion"],
            "owner-truth-context-shadow-read-v1",
        )
        shadow = context_shadow.json()["contextShadow"]
        self.assertEqual(shadow["state"], "ready")
        self.assertTrue(shadow["shadowOnly"])
        self.assertTrue(shadow["legacyContextUnchanged"])
        self.assertEqual(len(shadow["selectedContext"]), 1)
        self.assertEqual(
            shadow["selectedContext"][0]["citation"]["sourceId"],
            candidate.source_id,
        )
        self.assertNotIn(candidate.content["summary"], str(shadow))

        raw_query = "只允许在 QA trace 中留下哈希的原始问题"
        context_shadow_build = client.post(
            f"/v2/vaults/{vault_id}/context-shadow/build",
            headers=headers,
            json={"intent": "echo_chat", "query": raw_query},
        )
        self.assertEqual(context_shadow_build.status_code, 200)
        self.assertEqual(
            context_shadow_build.json()["schemaVersion"],
            "owner-truth-context-shadow-build-response-v1",
        )
        build = context_shadow_build.json()["contextShadow"]
        self.assertEqual(build["contextVersion"], "echo-context-v4-shadow")
        self.assertFalse(build["legacyContextRead"])
        self.assertEqual(build["fallbacks"], [])
        self.assertEqual(build["citationProof"][0]["resolution"], "current_confirmed_projection_entry")
        self.assertEqual(build["citationProof"][0]["citation"]["sourceId"], candidate.source_id)
        self.assertNotIn(raw_query, str(build))
        self.assertNotIn(candidate.content["summary"], str(build))

        context_shadow_compare = client.post(
            f"/v2/vaults/{vault_id}/context-shadow/compare",
            headers=headers,
            json={"intent": "echo_chat", "query": raw_query},
        )
        self.assertEqual(context_shadow_compare.status_code, 200)
        self.assertEqual(context_shadow_compare.headers["cache-control"], "no-store")
        self.assertEqual(
            context_shadow_compare.json()["schemaVersion"],
            "owner-truth-context-shadow-compare-response-v1",
        )
        comparison = context_shadow_compare.json()["contextComparison"]
        self.assertTrue(comparison["shadowOnly"])
        self.assertTrue(comparison["legacyContextRead"])
        self.assertTrue(comparison["requestCorrelationMatches"])
        self.assertTrue(comparison["v4"]["allSelectedItemsHaveTypedCitation"])
        self.assertNotIn(raw_query, str(comparison))
        self.assertNotIn(candidate.content["summary"], str(comparison))

        context_materialization = client.post(
            f"/v2/vaults/{vault_id}/context-shadow/materialize",
            headers=headers,
            json={"intent": "echo_chat", "query": raw_query},
        )
        self.assertEqual(context_materialization.status_code, 200)
        self.assertEqual(context_materialization.headers["cache-control"], "no-store")
        self.assertEqual(
            context_materialization.json()["schemaVersion"],
            "owner-truth-context-materialization-response-v1",
        )
        materialization = context_materialization.json()["contextMaterialization"]
        self.assertEqual(materialization["state"], "ready")
        self.assertFalse(materialization["legacyContextRead"])
        self.assertEqual(materialization["generationContext"]["sourceCount"], 1)
        self.assertTrue(materialization["generationContext"]["contentHash"])
        self.assertEqual(len(materialization["typedCitations"]), 1)
        self.assertNotIn(raw_query, str(materialization))
        self.assertNotIn(candidate.content["summary"], str(materialization))

        main_module.store.owner_truth_memory_search_document_projection_repository().rebuild(
            context=OwnerTruthCommandContext(
                vault_id=vault_id,
                owner_subject_id=owner_id,
                actor_subject_id=owner_id,
            )
        )
        query_ranked_build = client.post(
            f"/v2/vaults/{vault_id}/context-shadow/build",
            headers=headers,
            json={
                "intent": "echo_chat",
                "query": "院子",
                "selectionMode": "deterministicTextFallback",
            },
        )
        self.assertEqual(query_ranked_build.status_code, 200)
        query_ranked_shadow = query_ranked_build.json()["contextShadow"]
        self.assertEqual(
            query_ranked_shadow["request"]["selectionMode"],
            "deterministicTextFallback",
        )
        self.assertEqual(len(query_ranked_shadow["selectedContext"]), 1)
        self.assertEqual(
            query_ranked_shadow["selectedContext"][0]["rank"]["strategy"],
            "deterministicTextFallback",
        )
        self.assertNotIn("院子", str(query_ranked_shadow))
        self.assertNotIn(candidate.content["summary"], str(query_ranked_shadow))

        raw_answer = "我会只依据已确认的个人记忆回答。"
        answer_citation = client.post(
            f"/v2/vaults/{vault_id}/answer-citation-receipts",
            headers=headers,
            json={
                "commandId": "answer-citation-api-001",
                "intent": "echo_chat",
                "query": raw_query,
                "answerText": raw_answer,
            },
        )
        self.assertEqual(answer_citation.status_code, 201)
        self.assertEqual(
            answer_citation.json()["schemaVersion"],
            "owner-truth-answer-citation-receipt-response-v1",
        )
        evidence = answer_citation.json()["answerCitation"]
        self.assertEqual(evidence["citationCount"], 1)
        self.assertTrue(evidence["citations"][0]["citationId"])
        self.assertTrue(evidence["contextHash"])
        self.assertNotIn(raw_query, str(evidence))
        self.assertNotIn(raw_answer, str(evidence))
        self.assertNotIn(candidate.content["summary"], str(evidence))

        citation_read = client.get(
            f"/v2/vaults/{vault_id}/answers/{evidence['answerId']}/citations",
            headers=headers,
        )
        self.assertEqual(citation_read.status_code, 200)
        self.assertEqual(citation_read.headers["cache-control"], "no-store")
        self.assertEqual(
            citation_read.json()["schemaVersion"],
            "owner-truth-answer-citation-read-response-v1",
        )
        currentness = citation_read.json()["answerCitation"]
        self.assertEqual(currentness["citationCount"], 1)
        self.assertEqual(currentness["currentCitationCount"], 1)
        self.assertTrue(currentness["citations"][0]["current"])
        self.assertNotIn(raw_query, str(currentness))
        self.assertNotIn(raw_answer, str(currentness))
        self.assertNotIn(candidate.content["summary"], str(currentness))

        answer_feedback = client.post(
            f"/v2/vaults/{vault_id}/answers/{evidence['answerId']}/feedback",
            headers=headers,
            json={"commandId": "answer-feedback-api-001", "helpful": True},
        )
        self.assertEqual(answer_feedback.status_code, 201)
        self.assertEqual(
            answer_feedback.json()["schemaVersion"],
            "owner-truth-answer-feedback-receipt-response-v1",
        )
        feedback = answer_feedback.json()["answerFeedback"]
        self.assertTrue(feedback["metricEligible"])
        self.assertEqual(feedback["eligibilityReason"], "eligible")
        self.assertNotIn(raw_query, str(feedback))
        self.assertNotIn(raw_answer, str(feedback))
        self.assertNotIn(candidate.content["summary"], str(feedback))

        query_ranked_answer_citation = client.post(
            f"/v2/vaults/{vault_id}/answer-citation-receipts",
            headers=headers,
            json={
                "commandId": "answer-citation-api-query-ranked-001",
                "intent": "echo_chat",
                "query": "院子",
                "selectionMode": "deterministicTextFallback",
                "answerText": "我只会引用与当前问题匹配的已确认记忆。",
            },
        )
        self.assertEqual(query_ranked_answer_citation.status_code, 201)
        query_ranked_evidence = query_ranked_answer_citation.json()["answerCitation"]
        self.assertEqual(query_ranked_evidence["citationCount"], 1)
        self.assertEqual(
            query_ranked_evidence["contextHash"],
            query_ranked_shadow["contextHash"],
        )
        self.assertNotIn("院子", str(query_ranked_evidence))
        self.assertNotIn(candidate.content["summary"], str(query_ranked_evidence))

        correction_text = "不是父亲，是外祖父在院子里讲故事。"
        citation = evidence["citations"][0]
        correction = client.post(
            f"/v2/vaults/{vault_id}/memories/{citation['citation']['memoryId']}/corrections",
            headers=headers,
            json={
                "commandId": "correction-api-001",
                "answerId": evidence["answerId"],
                "citationId": citation["citationId"],
                "expectedMemoryVersionId": citation["citation"]["memoryVersionId"],
                "correctionText": correction_text,
                "reasonCode": "ownerReportedCorrection",
            },
        )
        self.assertEqual(correction.status_code, 201)
        self.assertEqual(
            correction.json()["schemaVersion"],
            "owner-truth-correction-request-response-v1",
        )
        request_summary = correction.json()["correctionRequest"]
        self.assertEqual(request_summary["status"], "pendingReview")
        self.assertEqual(request_summary["answerId"], evidence["answerId"])
        self.assertEqual(request_summary["citationId"], citation["citationId"])
        self.assertNotIn(correction_text, str(request_summary))
        self.assertNotIn(candidate.content["summary"], str(request_summary))

        corrected_value = {"summary": "小时候在院子里听外祖父讲故事"}
        resolution = client.post(
            f"/v2/vaults/{vault_id}/correction-requests/{request_summary['correctionRequestId']}/resolve",
            headers=headers,
            json={
                "commandId": "correction-resolution-api-001",
                "expectedCandidateVersion": request_summary["candidateVersion"],
                "expectedMemoryVersionId": request_summary["expectedMemoryVersionId"],
                "action": "correct",
                "correctedValue": corrected_value,
                "correctedValueSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "reasonCode": "ownerConfirmedCorrection",
            },
        )
        self.assertEqual(resolution.status_code, 201)
        self.assertEqual(
            resolution.json()["schemaVersion"],
            "owner-truth-correction-resolution-response-v1",
        )
        resolved = resolution.json()["correctionResolution"]
        self.assertEqual(resolved["decision"], "corrected")
        self.assertEqual(
            resolved["supersededMemoryVersionId"],
            request_summary["expectedMemoryVersionId"],
        )
        self.assertEqual(resolved["replacementMemoryVersion"], 2)
        self.assertTrue(resolved["replacementMemoryVersionId"])
        self.assertTrue(resolved["answerOutdatedEventId"])
        self.assertNotIn(correction_text, str(resolved))
        self.assertNotIn(corrected_value["summary"], str(resolved))
        self.assertNotIn(candidate.content["summary"], str(resolved))

        resolution_replay = client.post(
            f"/v2/vaults/{vault_id}/correction-requests/{request_summary['correctionRequestId']}/resolve",
            headers=headers,
            json={
                "commandId": "correction-resolution-api-001",
                "expectedCandidateVersion": request_summary["candidateVersion"],
                "expectedMemoryVersionId": request_summary["expectedMemoryVersionId"],
                "action": "correct",
                "correctedValue": corrected_value,
                "correctedValueSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "reasonCode": "ownerConfirmedCorrection",
            },
        )
        self.assertEqual(resolution_replay.status_code, 200)
        self.assertEqual(resolution_replay.json()["status"], "deduplicated")
        self.assertEqual(
            resolution_replay.json()["correctionResolution"]["receiptId"],
            resolved["receiptId"],
        )

        stale_projection = client.get(
            f"/v2/vaults/{vault_id}/memory-projection",
            headers=headers,
        )
        self.assertEqual(stale_projection.status_code, 200)
        self.assertEqual(stale_projection.json()["projection"]["state"], "rebuilding")

        rebuilt_projection = client.post(
            f"/v2/vaults/{vault_id}/memory-projection/rebuild",
            headers=headers,
        )
        self.assertEqual(rebuilt_projection.status_code, 200)
        self.assertEqual(rebuilt_projection.json()["projection"]["entryCount"], 1)
        self.assertNotIn(correction_text, str(rebuilt_projection.json()))
        self.assertNotIn(corrected_value["summary"], str(rebuilt_projection.json()))


if __name__ == "__main__":
    unittest.main()
