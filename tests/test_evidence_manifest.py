import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main_module
from app.observability.evidence_manifest import EvidenceManifestService
from app.observability.events import EvidenceManifestEvent, validate_evidence_event
from app.services.in_memory_store import InMemoryStore


def digest(character: str) -> str:
    return character * 64


def issue_payload(*, artifact_hashes=None, status="passed"):
    return {
        "manifestType": "echoQaEvidenceBundle",
        "sourceCommit": "abcdef1234567",
        "commandId": "runEchoTraceEvidencePackageExportSmoke",
        "sampleCount": 2,
        "sampleSetHash": digest("a"),
        "exclusionCodes": ["rawAudio", "providerSecret"],
        "sourceSchemaVersions": ["echoQaBundle-v2", "manifest-v1"],
        "artifactHashes": artifact_hashes or [digest("b")],
        "windowStartedAt": "2026-07-18T10:00:00+00:00",
        "windowEndedAt": "2026-07-18T10:02:00+00:00",
        "issuer": "qaHarness",
        "manifestStatus": status,
        "build": "ios-qa-build-42",
        "ownerLeaseHash": digest("c"),
    }


class EvidenceManifestServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.now = datetime(2026, 7, 18, 10, 5, tzinfo=timezone.utc)
        self.service = EvidenceManifestService(
            environment="test",
            build="backend-test",
            event_sink=self.store.append_evidence_event,
            event_source=self.store.list_evidence_events,
            retention_days=2,
            clock=lambda: self.now,
        )

    def issue(self, **overrides):
        payload = issue_payload()
        payload.update(overrides)
        return self.service.issue(
            manifest_type=payload["manifestType"],
            source_commit=payload["sourceCommit"],
            command_id=payload["commandId"],
            sample_count=payload["sampleCount"],
            sample_set_hash=payload["sampleSetHash"],
            exclusion_codes=payload["exclusionCodes"],
            source_schema_versions=payload["sourceSchemaVersions"],
            artifact_hashes=payload["artifactHashes"],
            window_started_at=payload["windowStartedAt"],
            window_ended_at=payload["windowEndedAt"],
            issuer=payload["issuer"],
            manifest_status=payload["manifestStatus"],
            build=payload["build"],
            owner_lease_hash=payload["ownerLeaseHash"],
        )

    def test_manifest_is_value_free_current_and_artifact_hash_bound(self):
        issued = self.issue()
        summary = self.service.list_manifests()
        verified = self.service.verify_artifacts(
            evidence_id=issued["evidenceId"],
            artifact_hashes=[digest("b")],
        )
        tampered = self.service.verify_artifacts(
            evidence_id=issued["evidenceId"],
            artifact_hashes=[digest("d")],
        )

        self.assertEqual(issued["outcome"], "appended")
        self.assertEqual(issued["retentionClass"], "verificationManifest")
        self.assertEqual(summary["manifestCount"], 1)
        self.assertEqual(summary["currentPassedCount"], 1)
        self.assertTrue(verified["valid"])
        self.assertEqual(tampered["reason"], "artifactHashMismatch")
        serialized = str(summary).lower()
        for forbidden in ["raw audio", "private", "token", "prompt", "user_", "phone"]:
            self.assertNotIn(forbidden, serialized)

    def test_expiry_reissue_and_retention_do_not_promote_old_evidence(self):
        first = self.issue()
        self.now += timedelta(minutes=1)
        second = self.issue()
        expired_at = self.now + timedelta(days=3)

        before_delete = self.service.list_manifests(now=expired_at)
        expired = self.service.verify_artifacts(
            evidence_id=first["evidenceId"],
            artifact_hashes=[digest("b")],
            now=expired_at,
        )
        receipt = self.store.expire_evidence_events(expired_at.isoformat())
        after_delete = self.service.list_manifests(now=expired_at)

        self.assertNotEqual(first["evidenceId"], second["evidenceId"])
        self.assertEqual(before_delete["validityCounts"], {"expired": 2})
        self.assertFalse(expired["valid"])
        self.assertEqual(expired["reason"], "evidenceManifestExpired")
        self.assertEqual(receipt["expiredCount"], 2)
        self.assertEqual(after_delete["manifestCount"], 0)

    def test_legacy_evidence_without_a_manifest_cannot_verify_a_gate(self):
        self.store.append_evidence_event(
            {
                "eventId": "legacy-observation-001",
                "type": "operation",
                "operationId": "legacyEvidence",
                "correlationId": None,
                "principalHash": None,
                "resourceType": "legacyReport",
                "resourceIdHash": None,
                "state": "succeeded",
                "reason": "legacyReportObserved",
                "occurredAt": "2026-07-18T10:00:00+00:00",
                "env": "test",
                "build": "legacy-build",
                "operation": "legacyEvidence",
            },
            retention_class="operationalTemporary",
            expires_at_iso="2026-08-01T00:00:00+00:00",
        )

        verification = self.service.verify_artifacts(
            evidence_id="legacy-observation-001",
            artifact_hashes=[digest("b")],
        )

        self.assertFalse(verification["valid"])
        self.assertEqual(verification["reason"], "evidenceManifestMissing")

    def test_schema_rejects_content_fields_invalid_window_and_duplicate_hashes(self):
        common = {
            "eventId": "evm-schema-test",
            "operationId": "evidenceManifestIssue",
            "correlationId": None,
            "principalHash": None,
            "resourceType": "evidenceManifest",
            "resourceIdHash": digest("d"),
            "state": "succeeded",
            "reason": "manifestPassed",
            "occurredAt": "2026-07-18T10:05:00+00:00",
            "env": "test",
            "build": "backend-test",
            "type": "evidenceManifest",
            "manifestType": "echoQaEvidenceBundle",
            "sourceCommit": "abcdef1234567",
            "commandId": "runEchoTraceEvidencePackageExportSmoke",
            "sampleCount": 1,
            "sampleSetHash": digest("a"),
            "exclusionCodes": ["rawAudio"],
            "sourceSchemaVersions": ["echoQaBundle-v2"],
            "artifactHashes": [digest("b")],
            "windowStartedAt": "2026-07-18T10:00:00+00:00",
            "windowEndedAt": "2026-07-18T10:02:00+00:00",
            "issuedAt": "2026-07-18T10:05:00+00:00",
            "expiresAt": "2026-07-20T10:05:00+00:00",
            "issuer": "qaHarness",
            "manifestStatus": "passed",
        }

        self.assertIsInstance(validate_evidence_event(common), EvidenceManifestEvent)
        for invalid in [
            {**common, "prompt": "private memory"},
            {**common, "artifactHashes": [digest("b"), digest("b")]},
            {**common, "windowStartedAt": "2026-07-18T10:03:00+00:00"},
            {**common, "state": "failed"},
        ]:
            with self.subTest(invalid=invalid.keys()):
                with self.assertRaises(ValidationError):
                    validate_evidence_event(invalid)


class EvidenceManifestAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_token = main_module.BACKEND_API_TOKEN
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = "evidence-manifest-machine-token"
        self.client = TestClient(main_module.app)
        self.headers = {"Authorization": "Bearer evidence-manifest-machine-token"}

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_token

    def test_machine_only_issue_and_query_are_no_store_and_value_free(self):
        anonymous = self.client.post("/ops/evidence-manifests", json=issue_payload())
        issued = self.client.post(
            "/ops/evidence-manifests",
            headers=self.headers,
            json=issue_payload(),
        )
        queried = self.client.get("/ops/evidence-manifests", headers=self.headers)
        rejected_body = self.client.post(
            "/ops/evidence-manifests",
            headers=self.headers,
            json={**issue_payload(), "reportBody": "must-not-store"},
        )

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(issued.status_code, 200, issued.text)
        self.assertEqual(issued.headers.get("cache-control"), "no-store")
        self.assertEqual(queried.status_code, 200, queried.text)
        self.assertEqual(queried.json()["currentPassedCount"], 1)
        self.assertEqual(rejected_body.status_code, 422)
        self.assertEqual(
            rejected_body.json()["detail"]["code"],
            "evidenceManifestUnexpectedField",
        )
        self.assertNotIn("provider secret", queried.text.lower())
