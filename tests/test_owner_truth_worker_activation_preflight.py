import json
import unittest

from app.async_effects.owner_truth_worker_activation import (
    OwnerTruthWorkerKind,
    evaluate_owner_truth_worker_activation,
)
from app.core.config import Settings
from app.services.provider_runtime import ProviderRuntimeInventory


class OwnerTruthWorkerActivationPreflightTests(unittest.TestCase):
    def settings(self, **overrides):
        values = {
            "environment": "production",
            "store_backend": "postgres",
            "async_effect_v1_enabled": True,
            "async_effect_worker_enabled": True,
            "owner_truth_candidate_extraction_worker_enabled": True,
            "owner_truth_memory_projection_worker_enabled": True,
        }
        values.update(overrides)
        return Settings(**values)

    def test_candidate_worker_requires_live_schema(self):
        decision = evaluate_owner_truth_worker_activation(
            worker=OwnerTruthWorkerKind.CANDIDATE_EXTRACTION,
            settings=self.settings(),
            schema_ready=False,
        )

        self.assertFalse(decision.ready)
        self.assertEqual(decision.reason, "asyncEffectSchemaNotReady")
        self.assertEqual(decision.blocking_dependency, "asyncEffectRuntime")

    def test_candidate_worker_is_ready_only_with_postgres_and_all_flags(self):
        ready = evaluate_owner_truth_worker_activation(
            worker=OwnerTruthWorkerKind.CANDIDATE_EXTRACTION,
            settings=self.settings(),
            schema_ready=True,
        )
        wrong_store = evaluate_owner_truth_worker_activation(
            worker=OwnerTruthWorkerKind.CANDIDATE_EXTRACTION,
            settings=self.settings(store_backend="memory"),
            schema_ready=True,
        )
        disabled = evaluate_owner_truth_worker_activation(
            worker=OwnerTruthWorkerKind.CANDIDATE_EXTRACTION,
            settings=self.settings(owner_truth_candidate_extraction_worker_enabled=False),
            schema_ready=True,
        )

        self.assertTrue(ready.ready)
        self.assertEqual(ready.reason, "ownerTruthWorkerActivationReady")
        self.assertFalse(wrong_store.ready)
        self.assertEqual(wrong_store.reason, "ownerTruthWorkerPostgresRequired")
        self.assertFalse(disabled.ready)
        self.assertEqual(disabled.reason, "ownerTruthCandidateExtractionWorkerDisabled")

    def test_memory_projection_requires_candidate_stage(self):
        decision = evaluate_owner_truth_worker_activation(
            worker=OwnerTruthWorkerKind.MEMORY_PROJECTION,
            settings=self.settings(owner_truth_candidate_extraction_worker_enabled=False),
            schema_ready=True,
        )

        self.assertFalse(decision.ready)
        self.assertEqual(decision.reason, "ownerTruthCandidateExtractionWorkerDisabled")
        self.assertEqual(decision.blocking_dependency, "candidateExtraction")

    def test_live_memory_organization_requires_deepseek_before_worker_start(self):
        missing = evaluate_owner_truth_worker_activation(
            worker=OwnerTruthWorkerKind.CANDIDATE_EXTRACTION,
            settings=self.settings(owner_truth_live_memory_organization_enabled=True),
            schema_ready=True,
        )
        ready = evaluate_owner_truth_worker_activation(
            worker=OwnerTruthWorkerKind.CANDIDATE_EXTRACTION,
            settings=self.settings(
                owner_truth_live_memory_organization_enabled=True,
                deepseek_api_key="deepseek-server-secret",
            ),
            schema_ready=True,
        )

        self.assertFalse(missing.ready)
        self.assertEqual(missing.reason, "ownerTruthLiveMemoryOrganizerNotConfigured")
        self.assertEqual(missing.blocking_dependency, "deepSeek")
        self.assertTrue(ready.ready)

    def test_media_worker_fails_closed_when_storage_is_incomplete(self):
        settings = self.settings(
            owner_truth_media_capture_enabled=True,
            owner_truth_media_processing_worker_enabled=True,
            owner_truth_media_storage_provider="cos",
            owner_truth_media_content_safety_provider="clamav",
        )
        inventory = ProviderRuntimeInventory(
            settings,
            clamav_scanner_ready=lambda: True,
        )

        decision = evaluate_owner_truth_worker_activation(
            worker=OwnerTruthWorkerKind.MEDIA_PROCESSING,
            settings=settings,
            schema_ready=True,
            provider_inventory=inventory,
        )

        self.assertFalse(decision.ready)
        self.assertEqual(decision.reason, "providerConfigurationIncomplete")
        self.assertEqual(decision.blocking_dependency, "ownerTruthMediaStorage")

    def test_media_worker_ready_descriptor_never_contains_credentials(self):
        settings = self.settings(
            owner_truth_media_capture_enabled=True,
            owner_truth_media_processing_worker_enabled=True,
            owner_truth_media_storage_provider="cos",
            owner_truth_media_s3_bucket="fixture-private-bucket-1250000000",
            owner_truth_media_s3_prefix="dreamjourney/private-media",
            owner_truth_media_s3_region="ap-shanghai",
            owner_truth_media_s3_endpoint_url="https://cos.ap-shanghai.myqcloud.com",
            owner_truth_media_s3_access_key_id="fixture-cos-access-id",
            owner_truth_media_s3_secret_access_key="fixture-cos-secret",
            owner_truth_media_s3_server_side_encryption="AES256",
            owner_truth_media_content_safety_provider="clamav",
        )
        inventory = ProviderRuntimeInventory(
            settings,
            clamav_scanner_ready=lambda: True,
        )

        decision = evaluate_owner_truth_worker_activation(
            worker=OwnerTruthWorkerKind.MEDIA_PROCESSING,
            settings=settings,
            schema_ready=True,
            provider_inventory=inventory,
        )
        serialized = json.dumps(decision.public_descriptor(), sort_keys=True)

        self.assertTrue(decision.ready)
        self.assertNotIn("fixture-cos-access-id", serialized)
        self.assertNotIn("fixture-cos-secret", serialized)
        self.assertNotIn("fixture-private-bucket", serialized)

    def test_media_deletion_has_an_independent_kill_switch(self):
        settings = self.settings(
            owner_truth_media_capture_enabled=True,
            owner_truth_media_deletion_worker_enabled=False,
            owner_truth_media_storage_provider="filesystem",
            owner_truth_media_storage_root="/var/lib/dreamjourney/private-media",
            owner_truth_media_content_safety_provider="clamav",
        )
        inventory = ProviderRuntimeInventory(
            settings,
            clamav_scanner_ready=lambda: True,
        )

        decision = evaluate_owner_truth_worker_activation(
            worker=OwnerTruthWorkerKind.MEDIA_DELETION,
            settings=settings,
            schema_ready=True,
            provider_inventory=inventory,
        )

        self.assertFalse(decision.ready)
        self.assertEqual(decision.reason, "ownerTruthMediaDeletionWorkerDisabled")


if __name__ == "__main__":
    unittest.main()
