"""C1 non-device lifecycle gate for VoiceProfile provider cleanup.

These tests keep the provider boundary fake on purpose.  They prove the
server state machine never treats local deletion as upstream deletion, while
leaving the current production adapter explicitly unsupported until a reviewed
provider deletion API is available.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.async_effects.lease_repository import InMemoryAsyncEffectLeaseRepository
from app.async_effects.voice_profile_deletion_worker import VoiceProfileDeletionWorkerRuntime
from app.core.config import settings
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService
from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)
from app.services.voice_clone import VoiceCloneProfileDeletionObservation
from app.services.voice_profile_lifecycle import (
    VoiceProfileLifecycleState,
    apply_voice_profile_lifecycle,
    make_voice_profile_consent,
)


class _LeaseSeedingEffectKernel:
    """Test-only bridge from accepted effects to the typed worker lease lane."""

    def __init__(self, delegate, lease_repository: InMemoryAsyncEffectLeaseRepository) -> None:
        self._delegate = delegate
        self._lease_repository = lease_repository

    def accept(self, intent):
        result = self._delegate.accept(intent)
        self._lease_repository.seed(intent)
        return result

    def __getattr__(self, name: str):
        return getattr(self._delegate, name)


class _VoiceDeletionWorkerStore(InMemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self._lease_repository = InMemoryAsyncEffectLeaseRepository()
        self._worker_effect_kernel = _LeaseSeedingEffectKernel(
            self._effect_kernel_repository,
            self._lease_repository,
        )

    def readiness_probe(self):
        return {"status": "ready"}

    def effect_kernel_repository(self):
        return self._worker_effect_kernel

    def async_effect_lease_repository(self):
        return self._lease_repository


def _accepted_profile(*, user_id: str, profile_id: str) -> dict:
    now = datetime.now(timezone.utc)
    return apply_voice_profile_lifecycle(
        {
            "userId": user_id,
            "voiceProfileId": profile_id,
            "providerSpeakerId": "S_c1_worker_slot",
            "realCloneProviderReady": True,
            "personaScope": "personal",
            "digitalHumanId": user_id,
        },
        state=VoiceProfileLifecycleState.ACCEPTED,
        consent=make_voice_profile_consent(
            purpose="private_synthesis",
            version="voice-clone-consent-v1",
            now=now,
        ),
        eligibility_decision=SubjectEligibilityDecision(
            capability=HighRiskCapability.CLONED_VOICE,
            allowed=True,
            decision="allow",
            reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
        ),
        eligibility_provenance="serverVerified",
        now=now,
    )


class _FakeDeletionProvider:
    provider_mode = "fakeVoiceCloneDeletion"
    is_configured = True

    def __init__(self, observation: VoiceCloneProfileDeletionObservation) -> None:
        self._observation = observation
        self.request_count = 0
        self.query_count = 0

    def request_profile_deletion(self, **_kwargs) -> VoiceCloneProfileDeletionObservation:
        self.request_count += 1
        return self._observation

    def query_profile_deletion(self, **_kwargs) -> VoiceCloneProfileDeletionObservation:
        self.query_count += 1
        return self._observation


class VoiceCloneC1DeletionWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self._previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
        self._previous_release_policy_gate = main_module.RELEASE_POLICY_COMMAND_GATE
        service = ReleasePolicyService(
            shadow_mode=True,
            enforce_default_closed_stages=False,
        )
        main_module.RELEASE_POLICY_SERVICE = service
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(service)

    def tearDown(self) -> None:
        main_module.RELEASE_POLICY_SERVICE = self._previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = self._previous_release_policy_gate
        super().tearDown()

    def _seed_deletion(self) -> tuple[_VoiceDeletionWorkerStore, str, str]:
        user_id = "voice-c1-owner"
        voice_profile_id = "voice-c1-profile"
        store = _VoiceDeletionWorkerStore()
        store.save_voice_profile(
            user_id,
            _accepted_profile(user_id=user_id, profile_id=voice_profile_id),
        )
        with patch("app.main.store", store):
            response = TestClient(app).delete(
                f"/voice/profiles/{user_id}/{voice_profile_id}"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "deletionPending")
        return store, user_id, voice_profile_id

    @staticmethod
    def _worker(
        store: _VoiceDeletionWorkerStore,
        provider: _FakeDeletionProvider,
    ) -> VoiceProfileDeletionWorkerRuntime:
        return VoiceProfileDeletionWorkerRuntime(
            settings=replace(
                settings,
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                voice_clone_deletion_worker_enabled=True,
            ),
            store=store,
            worker_id="voice-c1-test-worker",
            provider=provider,
        )

    def test_completed_provider_receipt_transitions_to_deleted_once(self) -> None:
        store, user_id, voice_profile_id = self._seed_deletion()
        provider = _FakeDeletionProvider(
            VoiceCloneProfileDeletionObservation.completed_from_reference(
                provider_mode="fakeVoiceCloneDeletion",
                provider_reference="provider-delete-complete",
            )
        )

        first = self._worker(store, provider).run_once()
        second = self._worker(store, provider).run_once()
        profile = store.get_voice_profile(user_id, voice_profile_id)

        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["lifecycleState"], "deleted")
        self.assertEqual(second["status"], "idle")
        self.assertEqual(provider.request_count, 1)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["lifecycleState"], "deleted")
        self.assertEqual(profile["deletionState"], "deleted")
        self.assertTrue(profile["providerEffectReceipt"]["providerReceiptPresent"])
        self.assertEqual(profile["providerEffectReceipt"]["state"], "completed")
        self.assertNotIn("S_c1_worker_slot", str(profile["providerEffectReceipt"]))

    def test_default_worker_switch_is_fail_closed_before_any_provider_call(self) -> None:
        store, user_id, voice_profile_id = self._seed_deletion()
        provider = _FakeDeletionProvider(
            VoiceCloneProfileDeletionObservation.completed_from_reference(
                provider_mode="fakeVoiceCloneDeletion",
                provider_reference="should-not-dispatch-while-disabled",
            )
        )
        worker = VoiceProfileDeletionWorkerRuntime(
            settings=replace(
                settings,
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                # Do not opt into VOICE_CLONE_DELETION_WORKER_ENABLED.
                voice_clone_deletion_worker_enabled=False,
            ),
            store=store,
            worker_id="voice-c1-disabled-worker",
            provider=provider,
        )

        result = worker.run_once()
        profile = store.get_voice_profile(user_id, voice_profile_id)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "voiceCloneDeletionWorkerDisabled")
        self.assertEqual(provider.request_count, 0)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["lifecycleState"], "deleting")
        self.assertFalse(profile["isEnabled"])

    def test_unsupported_provider_remains_partial_and_never_reenables_synthesis(self) -> None:
        store, user_id, voice_profile_id = self._seed_deletion()
        provider = _FakeDeletionProvider(
            VoiceCloneProfileDeletionObservation.unsupported(
                provider_mode="fakeVoiceCloneDeletion"
            )
        )

        result = self._worker(store, provider).run_once()
        profile = store.get_voice_profile(user_id, voice_profile_id)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "providerVoiceDeletionUnsupported")
        self.assertEqual(provider.request_count, 1)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["lifecycleState"], "deleting")
        self.assertEqual(profile["deletionState"], "unsupported")
        self.assertFalse(profile["providerEffectReceipt"]["providerReceiptPresent"])
        self.assertEqual(profile["providerEffectReceipt"]["state"], "unknown")

        with patch("app.main.store", store):
            synthesis = TestClient(app).post(
                "/voice/synthesis",
                json={
                    "userId": user_id,
                    "voiceProfileId": voice_profile_id,
                    "text": "cleanup uncertainty must not enable synthesis",
                    "outputMode": "tencentAudioDrive",
                    "roleSubjectId": user_id,
                    "roleKey": "personalOwner",
                    "personaScope": "personal",
                    "digitalHumanId": user_id,
                    "requestPurpose": "echo",
                },
            )
        self.assertEqual(synthesis.status_code, 409)
        self.assertEqual(synthesis.json()["detail"]["code"], "accepted_voice_profile_required")

    def test_known_provider_failure_records_receipt_without_default_voice_fallback(self) -> None:
        store, user_id, voice_profile_id = self._seed_deletion()
        provider = _FakeDeletionProvider(
            VoiceCloneProfileDeletionObservation.failed_from_reference(
                provider_mode="fakeVoiceCloneDeletion",
                provider_reference="provider-delete-failed",
            )
        )

        result = self._worker(store, provider).run_once()
        profile = store.get_voice_profile(user_id, voice_profile_id)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "providerVoiceDeletionFailed")
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["lifecycleState"], "deleting")
        self.assertEqual(profile["deletionState"], "failed")
        self.assertTrue(profile["providerEffectReceipt"]["providerReceiptPresent"])
        self.assertEqual(profile["providerEffectReceipt"]["state"], "failed")
        self.assertNotIn("provider-delete-failed", str(profile["providerEffectReceipt"]))

    def test_unknown_provider_receipt_stays_retryable_without_claiming_completion(self) -> None:
        store, user_id, voice_profile_id = self._seed_deletion()
        provider = _FakeDeletionProvider(
            VoiceCloneProfileDeletionObservation.unknown(
                provider_mode="fakeVoiceCloneDeletion"
            )
        )

        result = self._worker(store, provider).run_once()
        profile = store.get_voice_profile(user_id, voice_profile_id)

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "providerVoiceDeletionReceiptUnknown")
        self.assertEqual(provider.request_count, 1)
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile["lifecycleState"], "deleting")
        self.assertEqual(profile["deletionState"], "partial")
        self.assertTrue(profile["deletionRetryable"])
        self.assertFalse(profile["providerEffectReceipt"]["providerReceiptPresent"])
        self.assertEqual(profile["providerEffectReceipt"]["state"], "unknown")

    def test_stale_profile_generation_blocks_dispatch_before_provider_call(self) -> None:
        store, user_id, voice_profile_id = self._seed_deletion()
        profile = store.get_voice_profile(user_id, voice_profile_id)
        self.assertIsNotNone(profile)
        assert profile is not None
        profile["profileVersion"] = int(profile["profileVersion"]) + 1
        store.save_voice_profile(user_id, profile)
        provider = _FakeDeletionProvider(
            VoiceCloneProfileDeletionObservation.completed_from_reference(
                provider_mode="fakeVoiceCloneDeletion",
                provider_reference="must-not-run-for-stale-generation",
            )
        )

        result = self._worker(store, provider).run_once()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "voiceProfileDeletionProfileVersionMismatch")
        self.assertEqual(provider.request_count, 0)


if __name__ == "__main__":
    unittest.main()
