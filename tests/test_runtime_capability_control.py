import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from app.async_effects.lease_repository import AsyncEffectJobPreview
from app.core.config import Settings
from app.services.owner_truth_media_processing import (
    OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE,
)
from app.services.runtime_capability_control import (
    RuntimeCapabilityBudgetState,
    RuntimeCapabilityControlObservation,
    RuntimeCapabilityControlRegistry,
    RuntimeCapabilityControlState,
)
from app.services.runtime_capability_control_collection import (
    RuntimeCapabilityControlCollector,
)


class _LeaseRepository:
    def __init__(self, previews):
        self.previews = previews

    def preview_eligible(self, *, limit, job_types=None):
        allowed = None if job_types is None else set(job_types)
        values = [
            item
            for item in self.previews
            if allowed is None or item.job_type in allowed
        ]
        return values[:limit]


class _DeadLetterRepository:
    def __init__(self, count):
        self.count = count

    def count_open(self, *, job_type=None):
        self.job_type = job_type
        return self.count


class _RuntimeStore:
    def __init__(self, *, previews=(), dead_letters=0, reconciliation_healthy=True):
        self.lease_repository = _LeaseRepository(list(previews))
        self.dead_letter_repository = _DeadLetterRepository(dead_letters)
        self.reconciliation_healthy = reconciliation_healthy

    def readiness_probe(self):
        return {"status": "ready"}

    @contextmanager
    def request_unit_of_work(self, **_):
        yield self

    def async_effect_lease_repository(self):
        return self.lease_repository

    def async_effect_dead_letter_repository(self):
        return self.dead_letter_repository

    def summarize_rights_external_effect_reconciliation(self, *, domains=None):
        self.reconciliation_domains = domains
        return {"healthy": self.reconciliation_healthy}


class RuntimeCapabilityControlRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 9, 8, 0, tzinfo=timezone.utc)
        epochs = iter(("rce-first", "rce-second", "rce-third"))
        self.registry = RuntimeCapabilityControlRegistry(
            epoch_factory=lambda: next(epochs),
        )

    def observation(self, **overrides):
        values = {
            "capability": "ownerTruthMediaProcessing",
            "observation_id": "runtime-observation-1",
            "observed_at": self.now,
            "expires_at": self.now + timedelta(minutes=5),
            "provider_ready": True,
            "provider_reason": "externalEvidenceMissing",
            "scanner_ready": True,
            "worker_ready": True,
            "worker_evidence_id": "aer-fixture",
            "backlog_count": 0,
            "backlog_limit": 10,
            "open_dead_letter_count": 0,
            "dead_letter_limit": 0,
            "deletion_reconciliation_healthy": True,
            "budget_state": RuntimeCapabilityBudgetState.NOT_APPLICABLE,
            "budget_required": False,
            "kill_switch_active": False,
        }
        values.update(overrides)
        return RuntimeCapabilityControlObservation(**values)

    def test_recovery_requires_a_new_readiness_epoch(self):
        first = self.registry.observe(self.observation())
        still_ready = self.registry.observe(
            self.observation(
                observation_id="runtime-observation-2",
                observed_at=self.now + timedelta(seconds=30),
                expires_at=self.now + timedelta(minutes=6),
            )
        )
        blocked = self.registry.observe(
            self.observation(
                observation_id="runtime-observation-3",
                observed_at=self.now + timedelta(minutes=1),
                expires_at=self.now + timedelta(minutes=7),
                backlog_count=11,
            )
        )
        recovered = self.registry.observe(
            self.observation(
                observation_id="runtime-observation-4",
                observed_at=self.now + timedelta(minutes=2),
                expires_at=self.now + timedelta(minutes=8),
            )
        )

        self.assertEqual(first.state, RuntimeCapabilityControlState.READY)
        self.assertEqual(first.readiness_epoch, "rce-first")
        self.assertEqual(still_ready.readiness_epoch, first.readiness_epoch)
        self.assertEqual(blocked.state, RuntimeCapabilityControlState.BLOCKED)
        self.assertEqual(blocked.reason, "runtimeCapabilityBacklogExceeded")
        self.assertIsNone(blocked.readiness_epoch)
        self.assertEqual(recovered.state, RuntimeCapabilityControlState.READY)
        self.assertEqual(recovered.readiness_epoch, "rce-second")

    def test_expired_observation_fails_closed_and_fresh_recovery_rotates_epoch(self):
        ready = self.registry.observe(self.observation())
        expired = self.registry.decision(
            "ownerTruthMediaProcessing",
            now=self.now + timedelta(minutes=5),
        )
        recovered = self.registry.observe(
            self.observation(
                observation_id="runtime-observation-2",
                observed_at=self.now + timedelta(minutes=6),
                expires_at=self.now + timedelta(minutes=11),
            )
        )

        self.assertEqual(ready.readiness_epoch, "rce-first")
        self.assertIsNotNone(expired)
        self.assertEqual(expired.state, RuntimeCapabilityControlState.STALE)
        self.assertEqual(expired.reason, "runtimeCapabilityObservationExpired")
        self.assertFalse(expired.operational_ready)
        self.assertEqual(recovered.readiness_epoch, "rce-second")

    def test_older_observation_cannot_overwrite_newer_blocked_state(self):
        blocked = self.registry.observe(
            self.observation(
                observation_id="runtime-observation-newer",
                observed_at=self.now + timedelta(minutes=2),
                expires_at=self.now + timedelta(minutes=7),
                provider_ready=False,
                provider_reason="contentSafetyScannerUnavailable",
            )
        )
        ignored = self.registry.observe(
            self.observation(
                observation_id="runtime-observation-older",
                observed_at=self.now + timedelta(minutes=1),
                expires_at=self.now + timedelta(minutes=6),
            )
        )

        self.assertEqual(ignored, blocked)
        self.assertEqual(ignored.reason, "contentSafetyScannerUnavailable")

    def test_conflicting_same_timestamp_observation_keeps_the_safer_state(self):
        ready = self.registry.observe(self.observation())
        blocked = self.registry.observe(
            self.observation(
                observation_id="runtime-observation-conflict",
                provider_ready=False,
                provider_reason="contentSafetyScannerUnavailable",
            )
        )
        attempted_recovery = self.registry.observe(
            self.observation(observation_id="runtime-observation-ready-again")
        )

        self.assertTrue(ready.operational_ready)
        self.assertFalse(blocked.operational_ready)
        self.assertEqual(blocked.reason, "contentSafetyScannerUnavailable")
        self.assertEqual(attempted_recovery, blocked)

    def test_each_operational_blocker_fails_closed(self):
        cases = {
            "killSwitch": ({"kill_switch_active": True}, "runtimeCapabilityKillSwitchActive"),
            "provider": (
                {
                    "provider_ready": False,
                    "provider_reason": "contentSafetyScannerUnavailable",
                },
                "contentSafetyScannerUnavailable",
            ),
            "scanner": ({"scanner_ready": False}, "runtimeCapabilityScannerUnavailable"),
            "worker": ({"worker_ready": False}, "runtimeCapabilityWorkerUnavailable"),
            "backlog": ({"backlog_count": 11}, "runtimeCapabilityBacklogExceeded"),
            "deadLetter": (
                {"open_dead_letter_count": 1},
                "runtimeCapabilityDeadLetterThresholdExceeded",
            ),
            "deletion": (
                {"deletion_reconciliation_healthy": False},
                "runtimeCapabilityDeletionReconciliationAnomaly",
            ),
            "budgetExceeded": (
                {"budget_state": RuntimeCapabilityBudgetState.EXCEEDED},
                "runtimeCapabilityBudgetExceeded",
            ),
            "budgetUnknown": (
                {
                    "budget_state": RuntimeCapabilityBudgetState.UNKNOWN,
                    "budget_required": True,
                },
                "runtimeCapabilityBudgetUnknown",
            ),
        }

        for index, (name, (overrides, reason)) in enumerate(cases.items(), start=1):
            with self.subTest(name=name):
                registry = RuntimeCapabilityControlRegistry(
                    epoch_factory=lambda: "rce-fixture",
                )
                decision = registry.observe(
                    self.observation(
                        observation_id=f"runtime-observation-{index}",
                        **overrides,
                    )
                )
                self.assertEqual(decision.state, RuntimeCapabilityControlState.BLOCKED)
                self.assertFalse(decision.operational_ready)
                self.assertEqual(decision.reason, reason)
                self.assertIsNone(decision.readiness_epoch)

    def test_public_descriptor_is_value_free(self):
        decision = self.registry.observe(self.observation())
        descriptor = decision.public_descriptor()

        self.assertEqual(descriptor["controlState"], "ready")
        self.assertEqual(descriptor["readinessEpoch"], "rce-first")
        self.assertEqual(descriptor["backlogCount"], 0)
        self.assertEqual(descriptor["openDeadLetterCount"], 0)
        self.assertNotIn("providerReceipt", descriptor)
        self.assertNotIn("owner", descriptor)


class RuntimeCapabilityControlCollectorTests(unittest.TestCase):
    def settings(self, **overrides):
        values = {
            "environment": "development",
            "owner_truth_media_capture_enabled": True,
            "owner_truth_media_storage_provider": "filesystem",
            "owner_truth_media_storage_root": "/tmp/dreamjourney-runtime-control",
            "owner_truth_media_content_safety_provider": "testclean",
            "owner_truth_media_processing_worker_enabled": True,
            "async_effect_v1_enabled": True,
            "async_effect_worker_enabled": True,
            "runtime_capability_backlog_limit": 1,
            "runtime_capability_dead_letter_limit": 0,
        }
        values.update(overrides)
        return Settings(**values)

    def test_collector_reuses_worker_backlog_and_reconciliation_evidence(self):
        now = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
        preview = AsyncEffectJobPreview(
            job_id="job-runtime-1",
            operation_id="operation-runtime-1",
            job_type=OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE,
            state="pending",
            attempt=0,
            available_at=(now - timedelta(minutes=2)).isoformat(),
        )
        collector = RuntimeCapabilityControlCollector(
            settings=self.settings(),
            store=_RuntimeStore(previews=(preview,)),
        )

        _, observations = collector.collect(now=now)
        by_capability = {item.capability: item for item in observations}
        processing = by_capability["ownerTruthMediaProcessing"]

        self.assertTrue(processing.provider_ready)
        self.assertTrue(processing.worker_ready)
        self.assertEqual(processing.backlog_count, 1)
        self.assertEqual(processing.backlog_limit, 1)
        self.assertEqual(processing.open_dead_letter_count, 0)
        self.assertTrue(processing.deletion_reconciliation_healthy)

    def test_collector_maps_kill_switch_and_operational_anomalies_to_blockers(self):
        now = datetime(2026, 8, 9, 9, 0, tzinfo=timezone.utc)
        previews = tuple(
            AsyncEffectJobPreview(
                job_id=f"job-runtime-{index}",
                operation_id=f"operation-runtime-{index}",
                job_type=OWNER_TRUTH_MEDIA_PROCESSING_JOB_TYPE,
                state="pending",
                attempt=0,
                available_at=now.isoformat(),
            )
            for index in range(2)
        )
        settings = self.settings(
            release_policy_emergency_disabled_features="ownerMediaCaptureV1"
        )
        collector = RuntimeCapabilityControlCollector(
            settings=settings,
            store=_RuntimeStore(
                previews=previews,
                dead_letters=1,
                reconciliation_healthy=False,
            ),
        )
        registry = RuntimeCapabilityControlRegistry(epoch_factory=lambda: "rce-ready")

        _, observations = collector.collect(now=now)
        decisions = {
            item.capability: registry.observe(item)
            for item in observations
        }

        self.assertEqual(
            decisions["ownerTruthMediaStorage"].reason,
            "runtimeCapabilityKillSwitchActive",
        )
        # Backlog is evaluated before dead-letter and reconciliation blockers.
        self.assertEqual(
            decisions["ownerTruthMediaProcessing"].reason,
            "runtimeCapabilityBacklogExceeded",
        )


if __name__ == "__main__":
    unittest.main()
