import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.observability.events import EvidenceEventConflict
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyDecisionRecorder


def operation_event(
    event_id: str,
    *,
    operation_id: str = "op_release_policy",
    reason: str = "policyAllowed",
    occurred_at: str = "2026-07-16T12:00:00+00:00",
):
    return {
        "eventId": event_id,
        "schemaVersion": 1,
        "type": "operation",
        "operationId": operation_id,
        "correlationId": None,
        "principalHash": None,
        "resourceType": "releasePolicy",
        "resourceIdHash": None,
        "state": "succeeded",
        "reason": reason,
        "attempt": 1,
        "occurredAt": occurred_at,
        "env": "test",
        "build": "42",
        "redactionVersion": 1,
        "operation": "releasePolicyDecision",
        "route": "GET /config/runtime",
        "latencyMs": 0,
        "policyVersion": "release-policy-v1",
        "clientBuild": 42,
        "feature": "runtimeConfig",
        "decision": "typedRuntimeContract",
    }


class InMemoryEvidenceStoreTests(unittest.TestCase):
    def test_append_is_idempotent_and_rejects_same_id_with_different_payload(self):
        store = InMemoryStore()
        event = operation_event("evt_release_policy_01")

        created = store.append_evidence_event(
            event,
            retention_class="rolloutObservation",
            expires_at_iso="2026-08-15T12:00:00+00:00",
        )
        duplicate = store.append_evidence_event(
            event,
            retention_class="rolloutObservation",
            expires_at_iso="2026-08-15T12:00:00+00:00",
        )

        self.assertEqual(created["outcome"], "appended")
        self.assertEqual(duplicate["outcome"], "deduplicated")
        self.assertEqual(created["payloadHash"], duplicate["payloadHash"])

        with self.assertRaises(EvidenceEventConflict):
            store.append_evidence_event(
                operation_event("evt_release_policy_01", reason="tamperedReason"),
                retention_class="rolloutObservation",
                expires_at_iso="2026-08-15T12:00:00+00:00",
            )
        with self.assertRaises(ValidationError):
            store.append_evidence_event(
                operation_event("evt_unknown_retention"),
                retention_class="inventedRetentionClass",
                expires_at_iso="2026-08-15T12:00:00+00:00",
            )
        with self.assertRaises(ValueError):
            store.append_evidence_event(
                operation_event("evt_naive_expiry"),
                retention_class="rolloutObservation",
                expires_at_iso="2026-08-15T12:00:00",
            )

    def test_retention_expiry_removes_normal_event_but_preserves_legal_hold(self):
        store = InMemoryStore()
        store.append_evidence_event(
            operation_event("evt_expired_normal"),
            retention_class="operationalTemporary",
            expires_at_iso="2026-07-16T12:30:00+00:00",
        )
        store.append_evidence_event(
            operation_event("evt_expired_hold", operation_id="op_hold"),
            retention_class="legalHold",
            expires_at_iso="2026-07-16T12:30:00+00:00",
            legal_hold=True,
        )

        before_expiry = store.summarize_evidence_events(
            operation="releasePolicyDecision",
            now_iso="2026-07-16T12:15:00+00:00",
        )
        after_expiry = store.summarize_evidence_events(
            operation="releasePolicyDecision",
            now_iso="2026-07-16T13:00:00+00:00",
        )
        receipt = store.expire_evidence_events("2026-07-16T13:00:00+00:00")

        self.assertEqual(before_expiry["eventCount"], 2)
        self.assertEqual(after_expiry["eventCount"], 1)
        self.assertEqual(receipt["expiredCount"], 1)
        self.assertEqual(receipt["heldCount"], 1)
        self.assertEqual(len(receipt["expiredEventIdHashes"]), 1)
        self.assertEqual(
            store.summarize_evidence_events(
                operation="releasePolicyDecision",
                now_iso="2026-07-16T13:00:00+00:00",
            )["eventCount"],
            1,
        )

    def test_account_purge_does_not_delete_evidence(self):
        store = InMemoryStore()
        store.append_evidence_event(
            operation_event("evt_account_purge_boundary"),
            retention_class="rolloutObservation",
            expires_at_iso="2026-08-15T12:00:00+00:00",
        )
        user = store.upsert_user("13800000000", "owner")
        store.soft_delete_user(
            user["id"],
            phone="13800000000",
            requested_at_iso="2026-01-01T00:00:00+00:00",
        )
        store.purge_expired_deleted_users("2026-02-01T00:00:00+00:00")

        summary = store.summarize_evidence_events(
            operation="releasePolicyDecision",
            now_iso="2026-07-16T13:00:00+00:00",
        )
        self.assertEqual(summary["eventCount"], 1)

    def test_release_policy_summary_survives_recorder_recreation(self):
        store = InMemoryStore()

        def make_recorder():
            return ReleasePolicyDecisionRecorder(
                max_events=8,
                environment="test",
                event_sink=store.append_evidence_event,
                event_summary_source=lambda: store.summarize_evidence_events(
                    operation="releasePolicyDecision",
                    now_iso="2026-07-16T13:00:00+00:00",
                ),
                retention_days=30,
            )

        first = make_recorder()
        first.record_runtime_contract(
            client_build=42,
            contract_version=2,
            occurred_at=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        )
        recreated = make_recorder()
        summary = recreated.summary()

        self.assertEqual(summary["evidenceSource"], "persistent")
        self.assertEqual(summary["eventCount"], 1)
        self.assertEqual(summary["typedRuntimeContractHitCount"], 1)
        self.assertEqual(summary["sinkFailureCount"], 0)
        self.assertEqual(summary["operationEvents"][0]["type"], "operation")


if __name__ == "__main__":
    unittest.main()
