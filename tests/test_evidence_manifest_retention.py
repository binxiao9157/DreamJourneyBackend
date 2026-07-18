import unittest
from datetime import datetime, timezone

from app.services.in_memory_store import InMemoryStore
from scripts.evidence_manifest_retention import run_retention


def operation_event(event_id: str):
    return {
        "eventId": event_id,
        "type": "operation",
        "operationId": "evidenceManifestRetention",
        "correlationId": None,
        "principalHash": None,
        "resourceType": "evidenceManifest",
        "resourceIdHash": None,
        "state": "succeeded",
        "reason": "manifestExpired",
        "occurredAt": "2026-07-18T10:00:00+00:00",
        "env": "test",
        "build": "retention-test",
        "operation": "evidenceManifestRetention",
    }


class EvidenceManifestRetentionJobTests(unittest.TestCase):
    def test_expiry_job_only_reports_hashes_and_preserves_held_rows(self):
        store = InMemoryStore()
        store.append_evidence_event(
            operation_event("manifest-expired-row"),
            retention_class="verificationManifest",
            expires_at_iso="2026-07-18T10:01:00+00:00",
        )
        store.append_evidence_event(
            operation_event("manifest-held-row"),
            retention_class="legalHold",
            expires_at_iso="2026-07-18T10:01:00+00:00",
            legal_hold=True,
        )

        receipt = run_retention(
            store,
            now=datetime(2026, 7, 18, 10, 2, tzinfo=timezone.utc),
        )

        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["expiredCount"], 1)
        self.assertEqual(receipt["heldCount"], 1)
        self.assertEqual(len(receipt["expiredEventIdHashes"]), 1)
        self.assertNotIn("manifest-expired-row", str(receipt))
