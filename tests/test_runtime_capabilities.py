import unittest
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.services.runtime_capabilities import (
    RuntimeCapabilityComposer,
    RuntimeCapabilityInput,
)
from app.services.runtime_config import RuntimeConfigService


class RuntimeCapabilityComposerTests(unittest.TestCase):
    def test_external_verification_requires_current_timestamp(self):
        now = datetime(2026, 7, 16, tzinfo=timezone.utc)
        composer = RuntimeCapabilityComposer(now=now, external_evidence_ttl_days=30)
        base = RuntimeCapabilityInput(
            capability="fixture",
            implemented=True,
            enabled=True,
            provider_ready=True,
            release_visible=True,
            external_verified=True,
            provider="fixture-provider",
            fallback_mode="none",
            reason="ready",
        )

        missing = composer.compose(base)
        stale = composer.compose(
            RuntimeCapabilityInput(
                **{
                    **base.__dict__,
                    "evidence_timestamp": now - timedelta(days=31),
                }
            )
        )
        current = composer.compose(
            RuntimeCapabilityInput(
                **{
                    **base.__dict__,
                    "evidence_timestamp": now - timedelta(days=1),
                }
            )
        )

        self.assertFalse(missing.externalVerified)
        self.assertEqual(missing.reason, "externalEvidenceMissing")
        self.assertFalse(stale.externalVerified)
        self.assertEqual(stale.reason, "externalEvidenceStale")
        self.assertTrue(current.externalVerified)
        self.assertEqual(current.reason, "ready")

    def test_capability_axes_remain_independent_across_failure_modes(self):
        composer = RuntimeCapabilityComposer(
            now=datetime(2026, 7, 16, tzinfo=timezone.utc)
        )
        fixtures = (
            ("notImplemented", False, False, False, False, False),
            ("runtimeDisabled", True, False, False, False, False),
            ("mockProviderOnly", True, True, False, False, False),
            ("providerQuotaExhausted", True, True, False, False, False),
            ("policyDeny", True, True, True, False, False),
        )

        for reason, implemented, enabled, provider_ready, release_visible, external_verified in fixtures:
            with self.subTest(reason=reason):
                snapshot = composer.compose(
                    RuntimeCapabilityInput(
                        capability="fixture",
                        implemented=implemented,
                        enabled=enabled,
                        provider_ready=provider_ready,
                        release_visible=release_visible,
                        external_verified=external_verified,
                        provider="fixture",
                        fallback_mode="text",
                        reason=reason,
                    )
                )
                self.assertEqual(snapshot.implemented, implemented)
                self.assertEqual(snapshot.enabled, enabled)
                self.assertEqual(snapshot.providerReady, provider_ready)
                self.assertEqual(snapshot.releaseVisible, release_visible)
                self.assertEqual(snapshot.externalVerified, external_verified)
                self.assertEqual(snapshot.reason, reason)


class RuntimeCapabilityConfigTests(unittest.TestCase):
    def test_runtime_config_exposes_complete_route_authentication_inventory(self):
        development = RuntimeConfigService(Settings()).public_config()["auth"]["routeAuthentication"]
        production = RuntimeConfigService(
            Settings(environment="production", auth_route_mode="auto")
        ).public_config()["auth"]["routeAuthentication"]

        self.assertEqual(development["mode"], "shadow")
        self.assertEqual(production["mode"], "enforce")
        self.assertEqual(production["routeCount"], 87)
        self.assertEqual(production["unclassifiedCount"], 0)
        self.assertEqual(
            production["authModeCounts"],
            {"machine": 14, "public": 10, "user": 63},
        )
        self.assertTrue(production["productionEnforceReady"])

    def test_runtime_config_exposes_complete_five_axis_snapshots(self):
        settings = Settings(
            deepseek_api_key="fixture-deepseek-key",
            volcengine_voice_clone_api_key="fixture-clone-key",
            volcengine_voice_clone_tts_api_key="fixture-clone-tts-key",
        )

        config = RuntimeConfigService(settings).public_config()
        snapshots = config["capabilitySnapshots"]

        self.assertEqual(config["capabilitySnapshotSchemaVersion"], 1)
        for capability in (
            "archiveImageAnalysis",
            "archiveAudioUpload",
            "archiveVideoUpload",
            "timeLetters",
            "familyManagement",
            "familySpace",
            "voiceCloneShell",
            "digitalHumanLivePanel",
        ):
            snapshot = snapshots[capability]
            self.assertEqual(snapshot["schemaVersion"], 1, capability)
            self.assertEqual(snapshot["capability"], capability)
            for axis in (
                "implemented",
                "enabled",
                "providerReady",
                "releaseVisible",
                "externalVerified",
            ):
                self.assertIsInstance(snapshot[axis], bool, f"{capability}.{axis}")
            self.assertIn("provider", snapshot)
            self.assertIn("fallbackMode", snapshot)
            self.assertIn("reason", snapshot)
            self.assertIn("evidenceTimestamp", snapshot)

    def test_text_only_image_provider_and_mock_storage_are_not_provider_ready(self):
        config = RuntimeConfigService(
            Settings(deepseek_api_key="fixture-deepseek-key")
        ).public_config()
        snapshots = config["capabilitySnapshots"]

        image = snapshots["archiveImageAnalysis"]
        self.assertTrue(image["implemented"])
        self.assertTrue(image["enabled"])
        self.assertFalse(image["providerReady"])
        self.assertFalse(image["releaseVisible"])
        self.assertFalse(image["externalVerified"])
        self.assertEqual(image["reason"], "providerVisionUnsupported")

        for capability in ("archiveAudioUpload", "archiveVideoUpload"):
            media = snapshots[capability]
            self.assertTrue(media["implemented"])
            self.assertTrue(media["enabled"])
            self.assertFalse(media["providerReady"])
            self.assertEqual(media["reason"], "mockProviderOnly")

    def test_configured_voice_provider_does_not_imply_release_or_external_verification(self):
        config = RuntimeConfigService(
            Settings(
                volcengine_voice_clone_api_key="fixture-clone-key",
                volcengine_voice_clone_tts_api_key="fixture-clone-tts-key",
            )
        ).public_config()

        voice = config["capabilitySnapshots"]["voiceCloneShell"]
        self.assertTrue(voice["implemented"])
        self.assertTrue(voice["enabled"])
        self.assertTrue(voice["providerReady"])
        self.assertFalse(voice["releaseVisible"])
        self.assertFalse(voice["externalVerified"])
        self.assertEqual(voice["reason"], "externalEvidenceMissing")
        self.assertTrue(config["voiceClone"]["realProviderReady"])


if __name__ == "__main__":
    unittest.main()
