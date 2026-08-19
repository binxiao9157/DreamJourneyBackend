import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.core.config import Settings
from app.services.runtime_capabilities import (
    RuntimeCapabilityComposer,
    RuntimeCapabilityInput,
)
from app.services.runtime_config import RuntimeConfigService
from app.services.provider_runtime import ProviderRuntimeInventory
from app.services.runtime_capability_control import (
    RuntimeCapabilityControlObservation,
    RuntimeCapabilityControlRegistry,
)


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
    def test_product_closed_time_letter_and_delayed_reply_capabilities(self):
        config = RuntimeConfigService(Settings()).public_config()
        snapshots = config["capabilitySnapshots"]

        for capability in ("timeLetters", "echoDelayedReplies"):
            snapshot = snapshots[capability]
            self.assertTrue(snapshot["implemented"], capability)
            self.assertFalse(snapshot["enabled"], capability)
            self.assertFalse(snapshot["providerReady"], capability)
            self.assertFalse(snapshot["releaseVisible"], capability)
            self.assertEqual(snapshot["reason"], "productClosed", capability)
            self.assertEqual(snapshot["fallbackMode"], "disabled", capability)
            self.assertFalse(config["capabilities"][capability], capability)

    def test_runtime_config_exposes_complete_route_authentication_inventory(self):
        development = RuntimeConfigService(Settings()).public_config()["auth"]["routeAuthentication"]
        production = RuntimeConfigService(
            Settings(environment="production", auth_route_mode="auto")
        ).public_config()["auth"]["routeAuthentication"]

        self.assertEqual(development["mode"], "shadow")
        self.assertEqual(production["mode"], "enforce")
        self.assertEqual(production["routeCount"], 232)
        self.assertEqual(production["unclassifiedCount"], 0)
        self.assertEqual(
            production["authModeCounts"],
            {"machine": 23, "public": 16, "user": 193},
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
            "kbliteUserSurface",
            "accountDataExport",
            "ownerTruthMediaStorage",
            "ownerTruthMediaProcessing",
            "identityChallenge",
            "timeLetters",
            "echoDelayedReplies",
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
            for field in (
                "providerKind",
                "operation",
                "dataClass",
                "region",
                "retentionPolicyVersion",
                "configurationStatus",
                "evidenceStatus",
            ):
                self.assertIsInstance(snapshot[field], str, f"{capability}.{field}")

        for capability in ("familyManagement", "familySpace"):
            family = snapshots[capability]
            self.assertTrue(family["implemented"], capability)
            self.assertTrue(family["enabled"], capability)
            self.assertTrue(family["providerReady"], capability)
            self.assertTrue(family["releaseVisible"], capability)
            self.assertTrue(family["externalVerified"], capability)
            self.assertEqual(family["reason"], "ready")

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
            self.assertFalse(media["enabled"])
            self.assertFalse(media["providerReady"])
            self.assertFalse(media["releaseVisible"])
            self.assertEqual(media["reason"], "productClosed")

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
        operation_matrix = config["voiceClone"]["operationMatrix"]
        self.assertEqual(operation_matrix["schemaVersion"], 1)
        self.assertEqual(
            set(operation_matrix["operations"]),
            {"train", "query", "preview", "accept", "synthesize", "pause", "delete"},
        )
        self.assertFalse(operation_matrix["operations"]["train"]["available"])
        self.assertEqual(
            operation_matrix["operations"]["train"]["reasonCode"],
            "identityLivenessProviderUnavailable",
        )
        self.assertTrue(operation_matrix["operations"]["query"]["available"])
        self.assertTrue(operation_matrix["operations"]["synthesize"]["available"])
        self.assertTrue(operation_matrix["operations"]["delete"]["available"])
        self.assertEqual(
            operation_matrix["operations"]["delete"]["providerCapability"],
            "unsupported",
        )
        self.assertFalse(
            operation_matrix["operations"]["delete"]["providerCompletionAvailable"]
        )

    def test_incomplete_provider_configuration_fails_closed_without_disabling_unrelated_runtime(self):
        settings = Settings(
            owner_truth_media_capture_enabled=True,
            owner_truth_media_storage_provider="s3",
            owner_truth_media_s3_bucket="fixture-private-media",
            owner_truth_media_s3_region="ap-shanghai",
            owner_truth_media_s3_access_key_id="fixture-storage-access",
            owner_truth_media_s3_secret_access_key=None,
            owner_truth_media_content_safety_provider="clamav",
            owner_truth_media_processing_worker_enabled=True,
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            identity_binding_hmac_key="h" * 32,
            identity_challenge_adapter="httpJson",
            identity_challenge_http_json_url="https://otp.example.test/challenge",
            identity_challenge_http_json_api_key=None,
        )

        config = RuntimeConfigService(
            settings,
            provider_inventory=ProviderRuntimeInventory(
                settings,
                clamav_scanner_ready=lambda: True,
            ),
        ).public_config()
        snapshots = config["capabilitySnapshots"]
        inventory = config["providerInventory"]["capabilities"]

        storage = snapshots["ownerTruthMediaStorage"]
        self.assertFalse(storage["enabled"])
        self.assertFalse(storage["providerReady"])
        self.assertEqual(storage["reason"], "providerConfigurationIncomplete")
        self.assertEqual(storage["configurationStatus"], "incomplete")
        self.assertFalse(config["capabilities"]["ownerTruthMediaCapture"])
        self.assertFalse(config["capabilities"]["ownerTruthMediaProcessing"])
        self.assertEqual(inventory["ownerTruthMediaStorage"]["reason"], storage["reason"])

        identity = snapshots["identityChallenge"]
        self.assertFalse(identity["enabled"])
        self.assertFalse(identity["providerReady"])
        self.assertEqual(identity["reason"], "providerConfigurationIncomplete")
        self.assertEqual(identity["configurationStatus"], "incomplete")
        self.assertFalse(config["capabilities"]["identityChallenge"])

        # The legacy metadata-only audio/video path is product closed; the
        # first-release source-object route remains independently described.
        self.assertFalse(config["capabilities"]["archiveMediaUploadIntent"])
        self.assertEqual(config["archive"]["providerMode"], "disabled")
        self.assertEqual(
            config["ownerTruthMedia"]["captureCapability"],
            "ownerTruthMediaStorage",
        )

    def test_complete_provider_configuration_is_value_free_and_stays_release_controlled(self):
        settings = Settings(
            owner_truth_media_capture_enabled=True,
            owner_truth_media_storage_provider="s3",
            owner_truth_media_s3_bucket="fixture-private-media",
            owner_truth_media_s3_region="ap-shanghai",
            owner_truth_media_s3_access_key_id="fixture-storage-access",
            owner_truth_media_s3_secret_access_key="fixture-storage-secret",
            owner_truth_media_s3_server_side_encryption="AES256",
            owner_truth_media_content_safety_provider="clamav",
            owner_truth_media_processing_worker_enabled=True,
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            identity_binding_hmac_key="h" * 32,
            identity_challenge_adapter="httpJson",
            identity_challenge_http_json_url="https://otp.example.test/challenge",
            identity_challenge_http_json_api_key="fixture-otp-secret",
            volcengine_voice_clone_api_key="fixture-voice-training-secret",
            volcengine_voice_clone_tts_api_key="fixture-voice-synthesis-secret",
            tencent_digital_human_app_key="fixture-dh-app-key",
            tencent_digital_human_access_token="fixture-dh-access-token",
            tencent_digital_human_asset_virtualman_key="fixture-dh-asset",
        )

        config = RuntimeConfigService(
            settings,
            provider_inventory=ProviderRuntimeInventory(
                settings,
                clamav_scanner_ready=lambda: True,
            ),
        ).public_config()
        snapshots = config["capabilitySnapshots"]
        inventory = config["providerInventory"]

        self.assertFalse(inventory["validatedAtStartup"])
        self.assertEqual(inventory["contractVersion"], 1)
        self.assertTrue(snapshots["ownerTruthMediaStorage"]["enabled"])
        self.assertTrue(snapshots["ownerTruthMediaStorage"]["providerReady"])
        self.assertEqual(snapshots["ownerTruthMediaStorage"]["providerKind"], "privateObjectStorage")
        self.assertEqual(snapshots["ownerTruthMediaStorage"]["dataClass"], "ownerPrivateMedia")
        self.assertTrue(snapshots["ownerTruthMediaProcessing"]["providerReady"])
        self.assertTrue(snapshots["identityChallenge"]["providerReady"])
        self.assertTrue(snapshots["voiceCloneShell"]["providerReady"])

        # Tencent's static project credentials still do not open a mobile
        # session until the scoped-session broker has a verified contract.
        digital_human = snapshots["digitalHumanLivePanel"]
        self.assertFalse(digital_human["enabled"])
        self.assertFalse(digital_human["providerReady"])
        self.assertEqual(digital_human["reason"], "productClosed")
        self.assertEqual(
            digital_human["configurationStatus"],
            "configuredButBrokerBlocked",
        )

        serialized = json.dumps(config, ensure_ascii=False)
        for secret in (
            "fixture-storage-access",
            "fixture-storage-secret",
            "fixture-otp-secret",
            "fixture-voice-training-secret",
            "fixture-voice-synthesis-secret",
            "fixture-dh-app-key",
            "fixture-dh-access-token",
            "fixture-private-media",
            "https://otp.example.test/challenge",
        ):
            self.assertNotIn(secret, serialized)

    def test_cos_storage_requires_https_endpoint_and_explicit_cos_encryption(self):
        base = {
            "owner_truth_media_capture_enabled": True,
            "owner_truth_media_storage_provider": "cos",
            "owner_truth_media_s3_bucket": "fixture-private-media-1250000000",
            "owner_truth_media_s3_region": "ap-shanghai",
            "owner_truth_media_s3_access_key_id": "fixture-storage-access",
            "owner_truth_media_s3_secret_access_key": "fixture-storage-secret",
            "owner_truth_media_content_safety_provider": "clamav",
        }

        incomplete_settings = Settings(
            **base,
            owner_truth_media_s3_endpoint_url="http://cos.ap-shanghai.myqcloud.com",
            owner_truth_media_s3_server_side_encryption="AES256",
        )
        incomplete = RuntimeConfigService(
            incomplete_settings,
            provider_inventory=ProviderRuntimeInventory(
                incomplete_settings,
                clamav_scanner_ready=lambda: True,
            ),
        ).public_config()["capabilitySnapshots"]["ownerTruthMediaStorage"]
        self.assertFalse(incomplete["providerReady"])
        self.assertEqual(incomplete["reason"], "providerConfigurationIncomplete")

        missing_sse_settings = Settings(
            **base,
            owner_truth_media_s3_endpoint_url="https://cos.ap-shanghai.myqcloud.com",
        )
        missing_sse = RuntimeConfigService(
            missing_sse_settings,
            provider_inventory=ProviderRuntimeInventory(
                missing_sse_settings,
                clamav_scanner_ready=lambda: True,
            ),
        ).public_config()["capabilitySnapshots"]["ownerTruthMediaStorage"]
        self.assertFalse(missing_sse["providerReady"])
        self.assertEqual(missing_sse["reason"], "providerConfigurationIncomplete")

        wrong_region_settings = Settings(
            **{
                **base,
                "owner_truth_media_s3_region": "ap-guangzhou",
                "owner_truth_media_s3_endpoint_url": "https://cos.ap-shanghai.myqcloud.com",
                "owner_truth_media_s3_server_side_encryption": "AES256",
            }
        )
        wrong_region = RuntimeConfigService(
            wrong_region_settings,
            provider_inventory=ProviderRuntimeInventory(
                wrong_region_settings,
                clamav_scanner_ready=lambda: True,
            ),
        ).public_config()["capabilitySnapshots"]["ownerTruthMediaStorage"]
        self.assertFalse(wrong_region["providerReady"])
        self.assertEqual(wrong_region["reason"], "providerConfigurationIncomplete")

        configured_settings = Settings(
            **base,
            owner_truth_media_s3_endpoint_url="https://cos.ap-shanghai.myqcloud.com",
            owner_truth_media_s3_server_side_encryption="cos/kms",
            owner_truth_media_s3_kms_key_id="fixture-kms-key",
        )
        configured = RuntimeConfigService(
            configured_settings,
            provider_inventory=ProviderRuntimeInventory(
                configured_settings,
                clamav_scanner_ready=lambda: True,
            ),
        ).public_config()["capabilitySnapshots"]["ownerTruthMediaStorage"]
        self.assertTrue(configured["enabled"])
        self.assertTrue(configured["providerReady"])
        self.assertEqual(configured["provider"], "cos")

    def test_startup_validated_inventory_is_the_runtime_authority(self):
        settings = Settings(
            owner_truth_media_capture_enabled=True,
            owner_truth_media_storage_provider="filesystem",
            owner_truth_media_storage_root="/var/lib/dreamjourney/private-media",
            owner_truth_media_content_safety_provider="clamav",
        )
        inventory = ProviderRuntimeInventory(
            settings,
            validated_at_startup=True,
            clamav_scanner_ready=lambda: True,
        )

        config = RuntimeConfigService(
            settings,
            provider_inventory=inventory,
        ).public_config()

        self.assertTrue(config["providerInventory"]["validatedAtStartup"])
        self.assertTrue(config["capabilitySnapshots"]["ownerTruthMediaStorage"]["providerReady"])
        self.assertEqual(
            config["providerInventory"]["capabilities"]["ownerTruthMediaStorage"],
            inventory.public_descriptor()["capabilities"]["ownerTruthMediaStorage"],
        )

    def test_media_storage_fails_closed_when_clamav_runtime_is_unavailable(self):
        settings = Settings(
            owner_truth_media_capture_enabled=True,
            owner_truth_media_storage_provider="filesystem",
            owner_truth_media_storage_root="/var/lib/dreamjourney/private-media",
            owner_truth_media_content_safety_provider="clamav",
        )
        inventory = ProviderRuntimeInventory(
            settings,
            clamav_scanner_ready=lambda: False,
        )

        storage = inventory.status_for("ownerTruthMediaStorage").public_descriptor()

        self.assertFalse(storage["enabled"])
        self.assertFalse(storage["providerReady"])
        self.assertEqual(storage["reason"], "contentSafetyScannerUnavailable")
        self.assertEqual(storage["configurationStatus"], "incomplete")

    def test_media_storage_uses_explicit_clamav_sidecar_runtime_probe(self):
        settings = Settings(
            owner_truth_media_capture_enabled=True,
            owner_truth_media_storage_provider="filesystem",
            owner_truth_media_storage_root="/var/lib/dreamjourney/private-media",
            owner_truth_media_content_safety_provider="clamav",
            owner_truth_media_clamav_host="clamav",
            owner_truth_media_clamav_port=3310,
            owner_truth_media_clamav_timeout_seconds=11,
        )
        with patch(
            "app.services.provider_runtime.clamav_daemon_runtime_ready",
            return_value=True,
        ) as daemon_ready:
            inventory = ProviderRuntimeInventory(settings)

        storage = inventory.status_for("ownerTruthMediaStorage").public_descriptor()
        self.assertTrue(storage["enabled"])
        daemon_ready.assert_called_once_with(host="clamav", port=3310, timeout_seconds=11)

    def test_controlled_media_capability_fails_closed_and_exposes_epoch_contract(self):
        now = datetime.now(timezone.utc)
        settings = Settings(
            owner_truth_media_capture_enabled=True,
            owner_truth_media_storage_provider="filesystem",
            owner_truth_media_storage_root="/var/lib/dreamjourney/private-media",
            owner_truth_media_content_safety_provider="clamav",
            release_policy_closed_pilot_features="ownerMediaCaptureV1",
        )
        inventory = ProviderRuntimeInventory(
            settings,
            clamav_scanner_ready=lambda: True,
        )
        registry = RuntimeCapabilityControlRegistry(epoch_factory=lambda: "rce-ready")
        registry.observe(
            RuntimeCapabilityControlObservation(
                capability="ownerTruthMediaStorage",
                observation_id="runtime-observation-blocked",
                observed_at=now,
                expires_at=now + timedelta(minutes=5),
                provider_ready=True,
                provider_reason="externalEvidenceMissing",
                scanner_ready=True,
                deletion_reconciliation_healthy=False,
            )
        )

        blocked_config = RuntimeConfigService(
            settings,
            provider_inventory=inventory,
            capability_control_registry=registry,
        ).public_config()
        blocked = blocked_config["capabilitySnapshots"]["ownerTruthMediaStorage"]

        self.assertTrue(blocked["enabled"])
        self.assertFalse(blocked["providerReady"])
        self.assertFalse(blocked_config["capabilities"]["ownerTruthMediaCapture"])
        self.assertEqual(blocked["controlState"], "blocked")
        self.assertIsNone(blocked["readinessEpoch"])
        self.assertEqual(
            blocked["reason"],
            "runtimeCapabilityDeletionReconciliationAnomaly",
        )
        self.assertEqual(
            blocked_config["runtimeCapabilityControl"]["capabilities"][
                "ownerTruthMediaStorage"
            ]["controlState"],
            "blocked",
        )

        registry.observe(
            RuntimeCapabilityControlObservation(
                capability="ownerTruthMediaStorage",
                observation_id="runtime-observation-ready",
                observed_at=now + timedelta(seconds=1),
                expires_at=now + timedelta(minutes=6),
                provider_ready=True,
                provider_reason="externalEvidenceMissing",
                scanner_ready=True,
                deletion_reconciliation_healthy=True,
            )
        )
        ready = RuntimeConfigService(
            settings,
            provider_inventory=inventory,
            capability_control_registry=registry,
        ).public_config()["capabilitySnapshots"]["ownerTruthMediaStorage"]
        self.assertTrue(ready["providerReady"])
        self.assertEqual(ready["controlState"], "ready")
        self.assertEqual(ready["readinessEpoch"], "rce-ready")


if __name__ == "__main__":
    unittest.main()
