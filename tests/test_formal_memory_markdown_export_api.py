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
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION_V2
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService


client = TestClient(app)


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _content(summary: str) -> dict[str, object]:
    return {
        "summary": summary,
        "facets": {
            "people": [{"value": "外祖父", "evidenceMode": "ownerStated", "confidence": 1.0}],
            "time": [],
            "places": [{"value": "老院子", "evidenceMode": "ownerStated", "confidence": 1.0}],
            "relationships": [],
            "emotions": [],
            "values": [],
            "personality": [],
            "confidence": 1.0,
        },
    }


class FormalMemoryMarkdownExportAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_closed_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
        self.previous_closed_pilot_features = set(
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features
        )
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
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
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = self.previous_closed_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = (
            self.previous_closed_pilot_features
        )

    @staticmethod
    def _login(phone: str) -> tuple[str, dict[str, str], str]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "正式记忆导出测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        owner_id = str(body["user"]["id"])
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            set(main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS) | {owner_id}
        )
        return owner_id, {"Authorization": f"Bearer {body['auth']['accessToken']}"}, str(body["auth"]["sessionId"])

    @staticmethod
    def _policy_headers(
        headers: dict[str, str],
        *,
        session_id: str,
        feature: str,
    ) -> dict[str, str]:
        return {
            **headers,
            "X-DreamJourney-Feature": feature,
            "X-DreamJourney-Feature-Decision-Id": f"decision-{uuid4()}",
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": "release-policy-v1",
            "X-DreamJourney-Policy-Revision": "1",
            "X-DreamJourney-Account-Generation": sha256(session_id.encode("utf-8")).hexdigest()[:24],
        }

    @staticmethod
    def _activate(*, vault_id: str, owner_id: str, summary: str) -> str:
        source_id = str(uuid4())
        candidate_id = str(uuid4())
        content = _content(summary)
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=candidate_id,
            vault_id=vault_id,
            owner_subject_id=owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=context.policy_version,
            authority_epoch=0,
            row_version=1,
            content_hash=_digest(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
            payload={
                "schemaVersion": "owner-truth-candidate-proposal-v1",
                "candidateKind": "experience",
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V2,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
            },
        )
        main_module.store.owner_truth_candidate_review_repository().seed(candidate)
        result = OwnerTruthCandidateReviewService(main_module.store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=f"accept-{candidate_id}",
                candidate_id=candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                reason_code="ownerReviewed",
            ),
            context=context,
        )
        return str(result.memory_activation.memory_id)

    def test_owner_downloads_only_current_memory_with_one_time_credential(self) -> None:
        owner_id, headers, session_id = self._login("13800139971")
        other_id, other_headers, other_session_id = self._login("13800139972")
        vault_id = f"vault-{uuid4()}"
        memory_id = self._activate(
            vault_id=vault_id,
            owner_id=owner_id,
            summary="旧版正文不得导出",
        )
        owner_truth_headers = self._policy_headers(
            headers,
            session_id=session_id,
            feature="ownerTruthCandidateReview",
        )
        current = client.get(
            f"/v2/vaults/{vault_id}/memories/{memory_id}",
            headers=owner_truth_headers,
        ).json()["memory"]["currentVersion"]
        revised = client.post(
            f"/v2/vaults/{vault_id}/memories/{memory_id}/revisions",
            headers=self._policy_headers(
                headers,
                session_id=session_id,
                feature="ownerTruthCandidateReview",
            ),
            json={
                "commandId": "formal-memory-export-revision-001",
                "expectedVersion": current["versionNumber"],
                "expectedContentHash": current["contentHash"],
                "expectedContentSchemaVersion": current["contentSchemaVersion"],
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V2,
                "correctedContent": _content("新版 #正式记忆 <只读>"),
                "secondConfirmation": True,
            },
        )
        self.assertEqual(revised.status_code, 201, revised.text)

        export_headers = self._policy_headers(
            headers,
            session_id=session_id,
            feature="formalMemoryMarkdownExport",
        )
        created = client.post(
            f"/v2/vaults/{vault_id}/memory-exports/jobs",
            headers=export_headers,
            json={
                "requestKey": "formal-memory-export-001",
                "exportType": "formalMemoryMarkdown",
            },
        )
        self.assertEqual(created.status_code, 202, created.text)
        self.assertEqual(created.json()["exportType"], "formalMemoryMarkdown")
        self.assertEqual(created.json()["scopeId"], vault_id)
        job_id = created.json()["jobId"]

        status = client.get(
            f"/v2/vaults/{vault_id}/memory-exports/jobs/{job_id}",
            headers=self._policy_headers(
                headers,
                session_id=session_id,
                feature="formalMemoryMarkdownExport",
            ),
        )
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "ready")
        self.assertEqual(status.json()["manifest"]["memoryCount"], 1)
        self.assertEqual(status.json()["manifest"]["mimeType"], "text/markdown; charset=utf-8")

        foreign = client.get(
            f"/v2/vaults/{vault_id}/memory-exports/jobs/{job_id}",
            headers=self._policy_headers(
                other_headers,
                session_id=other_session_id,
                feature="formalMemoryMarkdownExport",
            ),
        )
        self.assertEqual(foreign.status_code, 404, foreign.text)
        self.assertNotEqual(owner_id, other_id)

        credential = client.post(
            f"/v2/vaults/{vault_id}/memory-exports/jobs/{job_id}/download-credential",
            headers=self._policy_headers(
                headers,
                session_id=session_id,
                feature="formalMemoryMarkdownExport",
            ),
            json={},
        )
        self.assertEqual(credential.status_code, 200, credential.text)
        download_headers = self._policy_headers(
            headers,
            session_id=session_id,
            feature="formalMemoryMarkdownExport",
        )
        download_headers["X-DreamJourney-Export-Token"] = credential.json()["downloadToken"]
        downloaded = client.get(
            f"/v2/vaults/{vault_id}/memory-exports/jobs/{job_id}/download",
            headers=download_headers,
        )
        replay = client.get(
            f"/v2/vaults/{vault_id}/memory-exports/jobs/{job_id}/download",
            headers=download_headers,
        )

        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        self.assertEqual(downloaded.headers["content-type"], "text/markdown; charset=utf-8")
        self.assertIn(".md", downloaded.headers["content-disposition"])
        self.assertEqual(len(downloaded.headers["x-content-sha256"]), 64)
        self.assertIn(r"新版 \#正式记忆 \<只读\>", downloaded.text)
        self.assertNotIn("旧版正文不得导出", downloaded.text)
        self.assertNotIn(memory_id, downloaded.text)
        self.assertNotIn("source", downloaded.text.lower())
        self.assertEqual(replay.status_code, 401, replay.text)

    def test_empty_export_can_cancel_and_full_account_type_is_rejected(self) -> None:
        owner_id, headers, session_id = self._login("13800139973")
        vault_id = f"vault-{uuid4()}"
        export_headers = self._policy_headers(
            headers,
            session_id=session_id,
            feature="formalMemoryMarkdownExport",
        )
        rejected = client.post(
            f"/v2/vaults/{vault_id}/memory-exports/jobs",
            headers=export_headers,
            json={"requestKey": "wrong-type", "exportType": "fullAccountArchive"},
        )
        self.assertEqual(rejected.status_code, 400, rejected.text)

        created = client.post(
            f"/v2/vaults/{vault_id}/memory-exports/jobs",
            headers=self._policy_headers(
                headers,
                session_id=session_id,
                feature="formalMemoryMarkdownExport",
            ),
            json={"requestKey": "empty-export"},
        )
        self.assertEqual(created.status_code, 202, created.text)
        job_id = created.json()["jobId"]
        cancelled = client.post(
            f"/v2/vaults/{vault_id}/memory-exports/jobs/{job_id}/cancel",
            headers=self._policy_headers(
                headers,
                session_id=session_id,
                feature="formalMemoryMarkdownExport",
            ),
            json={},
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        blocked_credential = client.post(
            f"/v2/vaults/{vault_id}/memory-exports/jobs/{job_id}/download-credential",
            headers=self._policy_headers(
                headers,
                session_id=session_id,
                feature="formalMemoryMarkdownExport",
            ),
            json={},
        )
        self.assertEqual(blocked_credential.status_code, 409, blocked_credential.text)
        self.assertTrue(owner_id)


if __name__ == "__main__":
    unittest.main()
