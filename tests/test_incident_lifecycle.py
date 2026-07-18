import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.incident_lifecycle import (
    IncidentLifecycleError,
    IncidentLifecycleService,
)


BASE_TIME = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
client = TestClient(app)


class IncidentLifecycleServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.now = BASE_TIME
        self.service = IncidentLifecycleService(
            store=self.store,
            environment="test",
            build="test-build",
            ack_timeout_seconds=300,
            clock=lambda: self.now,
        )

    def open_critical_incident(self):
        return self.service.open(
            incident_id="inc-credential-leak-001",
            category="credentialLeak",
            severity="critical",
            owner="securityOnCall",
            runbook_id="runbook.credentialLeak",
            reason="credentialLeakDetected",
            required_fence_actions=(
                "releasePolicy.echo",
                "credentialRotation.stop",
                "readiness.degrade",
            ),
            command_id="cmd-open-001",
        )

    def test_open_ack_partial_fence_resolve_and_reopen_are_append_only(self):
        opened = self.open_critical_incident()

        self.assertEqual(opened["incident"]["state"], "open")
        self.assertTrue(opened["summary"]["stopTheLine"])
        self.assertEqual(
            self.service.release_policy_block("echo")["reason"],
            "incidentFenceIncomplete",
        )

        with self.assertRaisesRegex(IncidentLifecycleError, "incidentAcknowledgementRequired"):
            self.service.resolve(
                incident_id="inc-credential-leak-001",
                reason="mitigationVerified",
                evidence_ids=("evidence-resolution-001",),
                command_id="cmd-resolve-before-ack",
            )

        acknowledged = self.service.acknowledge(
            incident_id="inc-credential-leak-001",
            reason="securityAcknowledged",
            command_id="cmd-ack-001",
        )
        self.assertEqual(acknowledged["incident"]["state"], "acknowledged")

        partial = self.service.fence(
            incident_id="inc-credential-leak-001",
            reason="echoLaneFenced",
            fence_actions=("releasePolicy.echo",),
            command_id="cmd-fence-partial-001",
        )
        self.assertEqual(partial["incident"]["fenceStatus"], "partial")
        self.assertTrue(partial["summary"]["stopTheLine"])

        with self.assertRaisesRegex(IncidentLifecycleError, "incidentFenceIncomplete"):
            self.service.resolve(
                incident_id="inc-credential-leak-001",
                reason="mitigationVerified",
                evidence_ids=("evidence-resolution-001",),
                command_id="cmd-resolve-before-fence",
            )

        fenced = self.service.fence(
            incident_id="inc-credential-leak-001",
            reason="remainingLanesFenced",
            fence_actions=("credentialRotation.stop", "readiness.degrade"),
            command_id="cmd-fence-complete-001",
        )
        self.assertEqual(fenced["incident"]["fenceStatus"], "complete")
        self.assertEqual(fenced["incident"]["state"], "fenced")

        resolved = self.service.resolve(
            incident_id="inc-credential-leak-001",
            reason="mitigationVerified",
            evidence_ids=("evidence-resolution-001",),
            command_id="cmd-resolve-001",
        )
        self.assertEqual(resolved["incident"]["state"], "resolved")
        self.assertFalse(resolved["summary"]["stopTheLine"])
        self.assertIsNone(self.service.release_policy_block("echo"))
        self.assertNotIn("evidence-resolution-001", str(resolved))

        reopened = self.service.reopen(
            incident_id="inc-credential-leak-001",
            new_incident_id="inc-credential-leak-002",
            owner="securityOnCall",
            reason="recurrenceDetected",
            runbook_id="runbook.credentialLeak",
            required_fence_actions=("releasePolicy.echo",),
            command_id="cmd-reopen-001",
        )
        self.assertEqual(reopened["incident"]["state"], "open")
        self.assertEqual(
            reopened["incident"]["reopenedFrom"],
            "inc-credential-leak-001",
        )
        self.assertTrue(reopened["summary"]["stopTheLine"])

        records = self.store.list_evidence_events(
            event_type="incident",
            operation="incidentLifecycle",
        )
        self.assertEqual(len(records), 6)
        self.assertTrue(all(item["payload"]["type"] == "incident" for item in records))

    def test_commands_are_idempotent_but_conflicting_reuse_is_rejected(self):
        created = self.open_critical_incident()
        replayed = self.open_critical_incident()

        self.assertEqual(created["eventOutcome"], "appended")
        self.assertEqual(replayed["eventOutcome"], "deduplicated")

        with self.assertRaisesRegex(IncidentLifecycleError, "incidentAlreadyExists"):
            self.service.open(
                incident_id="inc-credential-leak-001",
                category="credentialLeak",
                severity="critical",
                owner="securityOnCall",
                runbook_id="runbook.credentialLeak",
                reason="credentialLeakDetected",
                required_fence_actions=("releasePolicy.familyManagement",),
                command_id="cmd-open-conflict",
            )

    def test_ack_timeout_and_cross_lane_isolation_are_explicit(self):
        self.open_critical_incident()
        self.now = BASE_TIME + timedelta(seconds=301)

        summary = self.service.summary()
        self.assertEqual(summary["ackOverdueCount"], 1)
        self.assertTrue(summary["stopTheLine"])
        self.assertIsNotNone(self.service.release_policy_block("echo"))
        self.assertIsNone(self.service.release_policy_block("familyManagement"))
        self.assertEqual(
            self.service.readiness_component()["reason"],
            "criticalIncidentAckOverdue",
        )

    def test_replay_after_service_recreation_preserves_current_state(self):
        self.open_critical_incident()
        self.service.acknowledge(
            incident_id="inc-credential-leak-001",
            reason="securityAcknowledged",
            command_id="cmd-ack-001",
        )

        recreated = IncidentLifecycleService(
            store=self.store,
            environment="test",
            build="test-build",
            ack_timeout_seconds=300,
            clock=lambda: self.now,
        )
        incident = recreated.get("inc-credential-leak-001")

        self.assertEqual(incident["state"], "acknowledged")
        self.assertEqual(incident["owner"], "securityOnCall")
        self.assertEqual(incident["eventCount"], 2)


class IncidentLifecycleAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_route_mode = main_module.AUTH_ROUTE_MODE
        self.previous_service = main_module.INCIDENT_LIFECYCLE_SERVICE
        self.previous_settings = main_module.settings
        self.store = InMemoryStore()
        self.now = BASE_TIME
        self.service = IncidentLifecycleService(
            store=self.store,
            environment="test",
            build="test-build",
            ack_timeout_seconds=300,
            clock=lambda: self.now,
        )
        main_module.store = self.store
        main_module.BACKEND_API_TOKEN = "incident-machine-token"
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.INCIDENT_LIFECYCLE_SERVICE = self.service
        main_module.settings = Settings(store_backend="memory", environment="development")

    def tearDown(self) -> None:
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_ROUTE_MODE = self.previous_route_mode
        main_module.INCIDENT_LIFECYCLE_SERVICE = self.previous_service
        main_module.settings = self.previous_settings

    @staticmethod
    def machine_headers() -> dict:
        return {"Authorization": "Bearer incident-machine-token"}

    def test_machine_only_api_drives_readiness_without_exposing_raw_evidence_ids(self):
        payload = {
            "incidentId": "inc-api-001",
            "category": "providerAvailability",
            "severity": "critical",
            "owner": "operationsOnCall",
            "runbookId": "runbook.providerAvailability",
            "reason": "providerUnavailable",
            "requiredFenceActions": [
                "releasePolicy.echoTextInput",
                "readiness.degrade",
            ],
            "commandId": "cmd-api-open-001",
        }
        anonymous = client.post("/ops/incidents", json=payload)
        self.assertEqual(anonymous.status_code, 401)

        opened = client.post("/ops/incidents", headers=self.machine_headers(), json=payload)
        self.assertEqual(opened.status_code, 200, opened.text)
        self.assertTrue(opened.json()["summary"]["stopTheLine"])
        self.assertEqual(opened.headers["Cache-Control"], "no-store")
        self.assertEqual(client.get("/ready").status_code, 503)

        release_blocked = client.post(
            "/echo/delayed-replies/dispatch-due",
            headers=self.machine_headers(),
            json={"now": "2026-07-18T12:05:00Z", "limit": 1},
        )
        self.assertEqual(release_blocked.status_code, 503, release_blocked.text)
        self.assertEqual(
            release_blocked.json()["detail"]["code"],
            "incident_stop_the_line",
        )
        self.assertEqual(
            release_blocked.headers["X-DreamJourney-Incident-Stop-Line"],
            "true",
        )

        observed = client.get(
            "/ops/incidents/readiness",
            headers=self.machine_headers(),
        )
        self.assertEqual(observed.status_code, 200, observed.text)
        self.assertTrue(observed.json()["stopTheLine"])
        self.assertNotIn("provider secret", observed.text)

        acknowledged = client.post(
            "/ops/incidents/inc-api-001/ack",
            headers=self.machine_headers(),
            json={"reason": "operationsAcknowledged", "commandId": "cmd-api-ack-001"},
        )
        self.assertEqual(acknowledged.status_code, 200, acknowledged.text)
        fenced = client.post(
            "/ops/incidents/inc-api-001/fence",
            headers=self.machine_headers(),
            json={
                "reason": "affectedLanesFenced",
                "fenceActions": ["releasePolicy.echoTextInput", "readiness.degrade"],
                "commandId": "cmd-api-fence-001",
            },
        )
        self.assertEqual(fenced.status_code, 200, fenced.text)
        resolved = client.post(
            "/ops/incidents/inc-api-001/resolve",
            headers=self.machine_headers(),
            json={
                "reason": "providerRecovered",
                "evidenceIds": ["evidence-api-001"],
                "commandId": "cmd-api-resolve-001",
            },
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertNotIn("evidence-api-001", resolved.text)
        self.assertEqual(client.get("/ready").status_code, 200)

        release_restored = client.post(
            "/echo/delayed-replies/dispatch-due",
            headers=self.machine_headers(),
            json={"now": "2026-07-18T12:05:00Z", "limit": 1},
        )
        self.assertEqual(release_restored.status_code, 200, release_restored.text)


if __name__ == "__main__":
    unittest.main()
