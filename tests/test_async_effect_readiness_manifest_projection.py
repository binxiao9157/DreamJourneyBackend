from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.async_effects.contracts import AsyncEffectRuntimeStatus
from app.async_effects.lease_repository import AsyncEffectJobPreview
from app.async_effects.readiness_evidence import (
    AsyncEffectReadinessObservationState,
    build_async_effect_worker_readiness_evidence,
)
from app.async_effects.readiness_manifest_projection import (
    ASYNC_EFFECT_READINESS_MANIFEST_PROJECTION_SCHEMA_VERSION,
    AsyncEffectReadinessManifestProjectionError,
    persist_async_effect_readiness_manifest,
)
from app.observability.evidence_manifest import (
    EvidenceManifestError,
    EvidenceManifestService,
)
from app.services.in_memory_store import InMemoryStore


class AsyncEffectReadinessManifestProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
        self.store = InMemoryStore()
        self.service = EvidenceManifestService(
            environment="test",
            build="backend-test",
            event_sink=self.store.append_evidence_event,
            event_source=self.store.list_evidence_events,
            retention_days=7,
            clock=lambda: self.now,
        )
        self.runtime_ready = AsyncEffectRuntimeStatus(
            enabled=True,
            worker_enabled=True,
            allowed=True,
            reason="asyncEffectRuntimeReady",
        )

    def _evidence(self, **overrides):
        payload = {
            "runtime_status": self.runtime_ready,
            "worker_id": "worker-readiness-projection-test",
            "previews": (),
            "runnable_handler_count": 1,
            "observed_at": self.now,
            "expires_at": self.now + timedelta(minutes=5),
        }
        payload.update(overrides)
        return build_async_effect_worker_readiness_evidence(**payload)

    def _persist(self, evidence, *, now=None):
        return persist_async_effect_readiness_manifest(
            evidence,
            manifest_service=self.service,
            source_commit="abcdef1234567",
            now=now,
        )

    def test_ready_observation_persists_a_value_free_passed_manifest(self):
        result = self._persist(
            self._evidence(
                previews=(
                    AsyncEffectJobPreview(
                        job_id="job-private-marker-001",
                        operation_id="operation-private-marker-001",
                        job_type="asyncEffect.synthetic.noop",
                        state="pending",
                        attempt=0,
                        available_at="2026-07-30T07:59:00+00:00",
                    ),
                ),
            )
        )
        plan = result["manifestPlan"]
        manifest = result["evidenceManifest"]
        listed = self.service.list_manifests(now=self.now)

        self.assertEqual(
            result["schemaVersion"],
            ASYNC_EFFECT_READINESS_MANIFEST_PROJECTION_SCHEMA_VERSION,
        )
        self.assertEqual(plan["observationState"], "ready")
        self.assertEqual(plan["manifestStatus"], "passed")
        self.assertEqual(manifest["outcome"], "appended")
        self.assertEqual(listed["manifestCount"], 1)
        self.assertEqual(listed["currentPassedCount"], 1)
        serialized = str({"result": result, "listed": listed}).lower()
        for forbidden in (
            "job-private-marker",
            "operation-private-marker",
            "raw-private-marker",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_same_effective_observation_is_deduplicated(self):
        evidence = self._evidence()

        first = self._persist(evidence)
        repeated = self._persist(evidence)

        self.assertEqual(first["evidenceManifest"]["outcome"], "appended")
        self.assertEqual(repeated["evidenceManifest"]["outcome"], "deduplicated")
        self.assertEqual(
            first["evidenceManifest"]["evidenceId"],
            repeated["evidenceManifest"]["evidenceId"],
        )
        self.assertEqual(self.service.list_manifests(now=self.now)["manifestCount"], 1)

    def test_blocked_skipped_unknown_and_expired_never_persist_as_passed(self):
        blocked = self._evidence(runnable_handler_count=0)
        skipped = self._evidence(store_supported=False)
        unknown = self._evidence(
            collection_error_code="asyncEffectBacklogObservationFailed"
        )
        expiring = self._evidence(expires_at=self.now + timedelta(seconds=1))

        persisted = (
            self._persist(blocked),
            self._persist(skipped),
            self._persist(unknown),
            self._persist(expiring, now=self.now + timedelta(seconds=2)),
        )

        self.assertEqual(
            persisted[0]["manifestPlan"]["observationState"],
            AsyncEffectReadinessObservationState.BLOCKED.value,
        )
        for result in persisted:
            self.assertNotEqual(result["manifestPlan"]["manifestStatus"], "passed")
        self.assertEqual(
            persisted[-1]["manifestPlan"]["observationState"],
            AsyncEffectReadinessObservationState.EXPIRED.value,
        )
        statuses = self.service.list_manifests(now=self.now)["statusCounts"]
        self.assertEqual(statuses, {"blocked": 1, "notRun": 3})

    def test_invalid_inputs_and_sink_failure_do_not_fabricate_a_receipt(self):
        evidence = self._evidence()
        unavailable = EvidenceManifestService(
            environment="test",
            build="backend-test",
            event_sink=None,
            event_source=None,
            retention_days=7,
            clock=lambda: self.now,
        )

        with self.assertRaises(AsyncEffectReadinessManifestProjectionError):
            persist_async_effect_readiness_manifest(
                object(),
                manifest_service=self.service,
                source_commit="abcdef1234567",
            )
        with self.assertRaises(EvidenceManifestError) as raised:
            persist_async_effect_readiness_manifest(
                evidence,
                manifest_service=unavailable,
                source_commit="abcdef1234567",
            )

        self.assertEqual(raised.exception.code, "evidenceManifestSinkUnavailable")
        self.assertEqual(self.service.list_manifests(now=self.now)["manifestCount"], 0)


if __name__ == "__main__":
    unittest.main()
