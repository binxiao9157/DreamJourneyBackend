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
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_source import OwnerTruthSourceCommandService
from app.services.owner_truth_source_records import (
    OwnerTruthSourceRecordQuery,
    OwnerTruthSourceRecordService,
)
from app.main import app


client = TestClient(app)


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _facets() -> dict[str, object]:
    return {
        "people": [],
        "time": [],
        "places": [],
        "relationships": [],
        "emotions": [],
        "values": [],
        "personality": [],
        "confidence": 0.9,
    }


class OwnerTruthSourceRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.owner_id = "owner-source-record"
        self.vault_id = self.owner_id
        self.source_id = str(uuid4())
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        OwnerTruthSourceCommandService(self.store).create_text_source(
            context=self.context,
            command=CreateTextSourceCommand(
                command_id="source-record-test-create",
                source_id=self.source_id,
                expected_version=0,
                text="我小时候常和外公在河边散步。",
                metadata={"origin": "memoryArchiveTextCapture"},
            ),
        )

    def test_source_record_is_separate_from_formal_memory(self) -> None:
        page = OwnerTruthSourceRecordService(self.store).list(
            context=self.context,
            query=OwnerTruthSourceRecordQuery(limit=20),
        )

        self.assertEqual(len(page.items), 1)
        record = page.items[0]
        self.assertEqual(record.source_id, self.source_id)
        self.assertEqual(record.text, "我小时候常和外公在河边散步。")
        self.assertEqual(record.organization_status, "organizing")
        self.assertEqual(record.candidate_count, 0)

    def test_source_record_reports_pending_candidate_counts(self) -> None:
        content = {
            "event": "我小时候常和外公在河边散步。",
            "time": {"start": None, "end": None, "precision": "unknown"},
            "participants": ["外公"],
            "actions": ["散步"],
            "facets": _facets(),
        }
        self.store.owner_truth_candidate_review_repository().seed(
            OwnerTruthCandidateSnapshot(
                candidate_id=str(uuid4()),
                vault_id=self.vault_id,
                owner_subject_id=self.owner_id,
                source_id=self.source_id,
                memory_kind=MemoryKind.EXPERIENCE,
                perspective_type=PerspectiveType.FIRST_PERSON,
                epistemic_status=EpistemicStatus.RECALLED,
                sensitivity=SensitivityLevel.STANDARD,
                decision=CandidateDecision.PENDING,
                policy_version="owner-truth-v1",
                authority_epoch=0,
                row_version=1,
                content_hash=_digest(content),
                content_schema_version="owner-truth-v3",
                payload={
                    "content": content,
                    "evidenceRefs": [
                        {"sourceId": self.source_id, "sourceVersion": 1}
                    ],
                },
            )
        )

        record = OwnerTruthSourceRecordService(self.store).detail(
            context=self.context,
            source_id=self.source_id,
        )

        self.assertEqual(record.organization_status, "awaitingReview")
        self.assertEqual(record.candidate_count, 1)
        self.assertEqual(record.pending_count, 1)


class OwnerTruthSourceRecordAPITests(unittest.TestCase):
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
    def _login(phone: str) -> tuple[str, dict[str, str], str]:
        response = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "内容记录测试", "password": "password123"},
        )
        if response.status_code != 200:
            raise AssertionError(response.text)
        body = response.json()
        owner_id = str(body["user"]["id"])
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            set(main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS) | {owner_id}
        )
        return (
            owner_id,
            {"Authorization": f"Bearer {body['auth']['accessToken']}"},
            str(body["auth"]["sessionId"]),
        )

    @staticmethod
    def _policy_headers(headers: dict[str, str], *, session_id: str) -> dict[str, str]:
        return {
            **headers,
            "X-DreamJourney-Feature": "ownerTruthCandidateReview",
            "X-DreamJourney-Feature-Decision-Id": f"decision-{uuid4()}",
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": "release-policy-v1",
            "X-DreamJourney-Policy-Revision": "1",
            "X-DreamJourney-Account-Generation": sha256(session_id.encode("utf-8")).hexdigest()[:24],
        }

    @staticmethod
    def _create_source(*, vault_id: str, owner_id: str, text: str) -> str:
        source_id = str(uuid4())
        context = OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=owner_id,
        )
        OwnerTruthSourceCommandService(main_module.store).create_text_source(
            context=context,
            command=CreateTextSourceCommand(
                command_id=f"source-record-api-{source_id}",
                source_id=source_id,
                expected_version=0,
                text=text,
                metadata={"origin": "memoryArchiveTextCapture"},
            ),
        )
        return source_id

    def test_owner_can_list_and_read_original_submission(self) -> None:
        owner_id, headers, session_id = self._login("13800139971")
        vault_id = f"vault-{uuid4()}"
        source_id = self._create_source(
            vault_id=vault_id,
            owner_id=owner_id,
            text="我小时候常和外公在河边散步。",
        )

        listed = client.get(
            f"/v2/vaults/{vault_id}/source-records",
            headers=self._policy_headers(headers, session_id=session_id),
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.headers["cache-control"], "no-store")
        self.assertEqual(listed.json()["records"][0]["sourceId"], source_id)

        detail = client.get(
            f"/v2/vaults/{vault_id}/source-records/{source_id}",
            headers=self._policy_headers(headers, session_id=session_id),
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["record"]["text"], "我小时候常和外公在河边散步。")

    def test_cross_owner_cannot_read_submission_history(self) -> None:
        owner_id, headers, session_id = self._login("13800139972")
        _, foreign_headers, foreign_session_id = self._login("13800139973")
        vault_id = f"vault-{uuid4()}"
        source_id = self._create_source(
            vault_id=vault_id,
            owner_id=owner_id,
            text="只属于本人的原始记录。",
        )

        owner_response = client.get(
            f"/v2/vaults/{vault_id}/source-records",
            headers=self._policy_headers(headers, session_id=session_id),
        )
        self.assertEqual(owner_response.status_code, 200, owner_response.text)

        foreign_response = client.get(
            f"/v2/vaults/{vault_id}/source-records",
            headers=self._policy_headers(foreign_headers, session_id=foreign_session_id),
        )
        self.assertEqual(foreign_response.status_code, 200, foreign_response.text)
        self.assertEqual(foreign_response.json()["records"], [])

        foreign_detail = client.get(
            f"/v2/vaults/{vault_id}/source-records/{source_id}",
            headers=self._policy_headers(foreign_headers, session_id=foreign_session_id),
        )
        self.assertEqual(foreign_detail.status_code, 404, foreign_detail.text)


if __name__ == "__main__":
    unittest.main()
