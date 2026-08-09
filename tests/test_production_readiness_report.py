import json
import unittest
from datetime import datetime, timezone

from app.services.in_memory_store import InMemoryStore
from app.services.production_readiness_report import build_production_readiness_report


class ProductionReadinessReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observed_at = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)

    @staticmethod
    def _provider_inventory(*, ready: bool = True, identity_reason: str = "externalEvidenceVerified"):
        def capability(name: str, reason: str = "externalEvidenceVerified"):
            return {
                "capability": name,
                "enabled": ready,
                "providerReady": ready,
                "configurationStatus": "valid" if ready else "incomplete",
                "evidenceStatus": "verified" if ready else "notVerified",
                "reason": reason if ready else "providerConfigurationIncomplete",
                "secretValue": "must-never-be-copied",
            }

        identity = capability("identityChallenge", identity_reason)
        identity["reason"] = identity_reason
        return {
            "contractVersion": 1,
            "validatedAtStartup": True,
            "capabilities": {
                "identityChallenge": identity,
                "ownerTruthMediaStorage": capability("ownerTruthMediaStorage"),
                "ownerTruthMediaProcessing": capability("ownerTruthMediaProcessing"),
            },
        }

    @staticmethod
    def _runtime_controls(*, ready: bool = True):
        return {
            "contractVersion": 1,
            "capabilities": {
                name: {
                    "capability": name,
                    "controlState": "ready" if ready else "blocked",
                    "reason": (
                        "runtimeCapabilityReady"
                        if ready
                        else "runtimeCapabilityWorkerUnavailable"
                    ),
                    "operationalReady": ready,
                    "backlogCount": 0,
                    "openDeadLetterCount": 0,
                }
                for name in (
                    "ownerTruthMediaStorage",
                    "ownerTruthMediaProcessing",
                )
            },
        }

    @staticmethod
    def _workers(*, ready: bool = True):
        return [
            {
                "contractVersion": 1,
                "worker": name,
                "ready": ready,
                "reason": (
                    "ownerTruthWorkerActivationReady"
                    if ready
                    else "asyncEffectV1Disabled"
                ),
                "blockingDependency": None if ready else "asyncEffectRuntime",
            }
            for name in (
                "ownerTruthCandidateExtraction",
                "ownerTruthMemoryProjection",
                "ownerTruthMediaProcessing",
                "ownerTruthMediaDeletion",
            )
        ]

    @staticmethod
    def _core(*, ready: bool = True):
        return {
            "schemaVersion": 1,
            "status": "ready" if ready else "notReady",
            "components": [
                {
                    "component": "database",
                    "status": "ready" if ready else "notReady",
                    "reason": "readWriteProbeSucceeded" if ready else "databaseUnavailable",
                }
            ],
        }

    @staticmethod
    def _operation_metrics(*, ready: bool = True):
        return {
            "schemaVersion": 1,
            "evidenceSource": "persistent" if ready else "summaryUnavailable",
            "eventCount": 10 if ready else 0,
            "failedOperationCount": 0,
            "unknownOperationCount": 0,
            "sinkFailureCount": 0 if ready else 1,
            "sourceFailureCount": 0 if ready else 1,
            "metadata": {
                "windowStartedAt": "2026-08-09T09:00:00+00:00" if ready else None,
                "windowEndedAt": "2026-08-09T10:00:00+00:00" if ready else None,
            },
        }

    @staticmethod
    def _provider_metrics(*, ready: bool = True):
        return {
            "schemaVersion": 1,
            "evidenceSource": "persistent" if ready else "notConfigured",
            "eventCount": 4 if ready else 0,
            "stateCounts": {"succeeded": 4} if ready else {},
            "unknownCostEventCount": 0,
            "sinkFailureCount": 0 if ready else 1,
            "sourceFailureCount": 0 if ready else 1,
            "windowStartedAt": "2026-08-09T09:00:00+00:00" if ready else None,
            "windowEndedAt": "2026-08-09T10:00:00+00:00" if ready else None,
        }

    def _build(self, **overrides):
        values = {
            "core_readiness": self._core(),
            "provider_inventory": self._provider_inventory(),
            "runtime_capability_control": self._runtime_controls(),
            "worker_activations": self._workers(),
            "context_authority_enabled": True,
            "application_export_ready": True,
            "media_export_ready": True,
            "deletion_reconciliation_healthy": True,
            "scanner_evidence": {
                "ready": True,
                "reason": "clamavRuntimeReady",
                "engineVersion": "1.4.3",
                "signatureVersion": "27654",
            },
            "operation_metrics": self._operation_metrics(),
            "provider_metrics": self._provider_metrics(),
            "active_kill_switches": (),
            "observed_at": self.observed_at,
        }
        values.update(overrides)
        return build_production_readiness_report(**values)

    def test_all_required_lanes_ready_produces_go(self):
        report = self._build()

        self.assertEqual(report["schemaVersion"], 1)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["releaseDecision"], "go")
        self.assertEqual(report["summary"]["blockedCount"], 0)
        self.assertEqual(report["summary"]["degradedCount"], 0)
        self.assertEqual(
            set(report["lanes"]),
            {
                "coreService",
                "identity",
                "contentSafety",
                "mediaStorage",
                "mediaProcessing",
                "workers",
                "context",
                "export",
                "deletion",
                "operationTelemetry",
                "providerTelemetry",
            },
        )
        self.assertEqual(report["alerts"], [])

    def test_missing_external_dependencies_fail_closed_with_actionable_reasons(self):
        report = self._build(
            core_readiness=self._core(ready=False),
            provider_inventory=self._provider_inventory(
                ready=False,
                identity_reason="providerConfigurationIncomplete",
            ),
            runtime_capability_control=self._runtime_controls(ready=False),
            worker_activations=self._workers(ready=False),
            context_authority_enabled=False,
            media_export_ready=False,
            deletion_reconciliation_healthy=None,
            scanner_evidence={
                "ready": False,
                "reason": "clamavRuntimeUnavailable",
                "engineVersion": None,
                "signatureVersion": None,
            },
            operation_metrics=self._operation_metrics(ready=False),
            provider_metrics=self._provider_metrics(ready=False),
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["releaseDecision"], "noGo")
        self.assertEqual(report["lanes"]["identity"]["reason"], "providerConfigurationIncomplete")
        self.assertEqual(report["lanes"]["workers"]["reason"], "asyncEffectV1Disabled")
        self.assertEqual(report["lanes"]["context"]["reason"], "ownerTruthContextClosedPilotDisabled")
        self.assertEqual(report["lanes"]["export"]["state"], "degraded")
        self.assertEqual(report["lanes"]["deletion"]["state"], "blocked")
        self.assertGreater(report["summary"]["blockedCount"], 0)
        self.assertTrue(any(item["severity"] == "blocker" for item in report["alerts"]))

    def test_synthetic_identity_provider_is_not_production_ready(self):
        inventory = self._provider_inventory(identity_reason="syntheticProviderOnly")
        inventory["capabilities"]["identityChallenge"]["evidenceStatus"] = "syntheticOnly"

        report = self._build(provider_inventory=inventory)

        self.assertEqual(report["lanes"]["identity"]["state"], "blocked")
        self.assertEqual(report["lanes"]["identity"]["reason"], "syntheticProviderOnly")
        self.assertEqual(report["releaseDecision"], "noGo")

    def test_active_kill_switch_blocks_release_without_copying_unlisted_values(self):
        report = self._build(
            active_kill_switches=("ownerMediaCaptureV1",),
        )

        self.assertEqual(report["releaseDecision"], "noGo")
        self.assertEqual(report["killSwitches"], {
            "active": True,
            "activeFeatures": ["ownerMediaCaptureV1"],
        })
        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn("must-never-be-copied", serialized)
        self.assertNotIn("secretValue", serialized)


class ProductionReadinessFastAPITests(unittest.TestCase):
    def test_machine_observations_include_no_store_readiness_report(self):
        from fastapi.testclient import TestClient

        import app.main as main_module

        previous_token = main_module.BACKEND_API_TOKEN
        previous_store = main_module.store
        main_module.BACKEND_API_TOKEN = "production-readiness-machine-token"
        main_module.store = InMemoryStore()
        try:
            response = TestClient(main_module.app).get(
                "/ops/release-policy/observations",
                headers={
                    "Authorization": "Bearer production-readiness-machine-token"
                },
            )
        finally:
            main_module.BACKEND_API_TOKEN = previous_token
            main_module.store = previous_store

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        report = response.json()["productionReadiness"]
        self.assertEqual(report["schemaVersion"], 1)
        self.assertIn(report["status"], {"ready", "degraded", "blocked"})
        self.assertIn(report["releaseDecision"], {"go", "noGo"})
        self.assertEqual(report["summary"]["laneCount"], 11)

    def test_anonymous_observations_remain_denied(self):
        from fastapi.testclient import TestClient

        import app.main as main_module

        previous_store = main_module.store
        main_module.store = InMemoryStore()
        try:
            response = TestClient(main_module.app).get(
                "/ops/release-policy/observations"
            )
        finally:
            main_module.store = previous_store

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
