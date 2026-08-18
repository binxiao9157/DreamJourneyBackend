import unittest
from hashlib import sha256
from tempfile import TemporaryDirectory
from uuid import uuid4

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.core.config import Settings
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_media_source_object import (
    FilesystemPrivateMediaObjectStore,
    MediaUploadIntentCommand,
    OwnerTruthMediaIngestionService,
    OwnerTruthMediaKindProductClosed,
    TestOnlyCleanMediaContentSafetyScanner,
)
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService
from app.services.runtime_config import RuntimeConfigService


class ProductConfirmedFirstReleaseScopeTests(unittest.TestCase):
    def test_product_closed_features_override_every_rollout_input(self):
        service = ReleasePolicyService(
            authenticated_owner_v4_enabled=True,
            enforce_default_closed_stages=False,
        )

        for feature in (
            "archiveAudioUpload",
            "archiveVideoUpload",
            "kbliteUserSurface",
            "accountDataExport",
        ):
            for audience, cohort in (
                ("owner", "authenticatedOwner"),
                ("owner", "closedPilotAdultSelf"),
                ("qa", "internalQA"),
            ):
                decision = service.build_snapshot(
                    audience=audience,
                    cohort=cohort,
                    client_build=999,
                    requested_feature=feature,
                ).features[0]
                self.assertFalse(decision.enabled, (feature, audience, cohort))
                self.assertFalse(decision.releaseVisible, (feature, audience, cohort))
                self.assertEqual(decision.reason, "productClosed")
            self.assertEqual(service.command_mode_for(feature), "enforce")

    def test_v2_upload_intents_classify_closed_media_without_blocking_image_or_document(self):
        gate = ReleasePolicyCommandGate(ReleasePolicyService())
        path = "/v2/vaults/vault-a/source-objects/upload-intents"

        self.assertEqual(
            gate.feature_for_request("POST", path, {"mediaKind": "audio"}),
            "archiveAudioUpload",
        )
        self.assertEqual(
            gate.feature_for_request("POST", path, {"mediaKind": "video"}),
            "archiveVideoUpload",
        )
        for media_kind in ("image", "document"):
            self.assertEqual(
                gate.feature_for_request("POST", path, {"mediaKind": media_kind}),
                "ownerMediaCaptureV1",
            )

    def test_runtime_exposes_only_confirmed_first_release_media_and_keeps_kb_sync_internal(self):
        config = RuntimeConfigService(Settings()).public_config()
        capabilities = config["capabilities"]
        snapshots = config["capabilitySnapshots"]

        self.assertTrue(capabilities["kbSync"])
        for capability in (
            "archiveAudioUpload",
            "archiveVideoUpload",
            "kbliteUserSurface",
            "accountDataExport",
        ):
            self.assertFalse(capabilities[capability], capability)
            snapshot = snapshots[capability]
            self.assertTrue(snapshot["implemented"], capability)
            self.assertFalse(snapshot["enabled"], capability)
            self.assertFalse(snapshot["providerReady"], capability)
            self.assertFalse(snapshot["releaseVisible"], capability)
            self.assertEqual(snapshot["reason"], "productClosed", capability)

        self.assertEqual(config["archive"]["supportedMediaKinds"], [])
        self.assertEqual(
            config["ownerTruthMedia"]["supportedMediaKinds"],
            ["document", "image"],
        )

    def test_ingestion_service_rejects_closed_media_before_creating_storage_state(self):
        with TemporaryDirectory() as directory:
            store = InMemoryStore()
            service = OwnerTruthMediaIngestionService(
                store=store,
                object_store=FilesystemPrivateMediaObjectStore(root=directory),
                safety_scanner=TestOnlyCleanMediaContentSafetyScanner(),
                enabled=True,
                max_upload_bytes=1024 * 1024,
                upload_intent_ttl_seconds=900,
                allowed_media_kinds={"image", "document"},
            )
            context = OwnerTruthCommandContext(
                vault_id="vault-first-release",
                owner_subject_id="owner-first-release",
                actor_subject_id="owner-first-release",
            )

            for media_kind, content_type, file_name, payload in (
                ("audio", "audio/mpeg", "closed.mp3", b"ID3closed-audio"),
                ("video", "video/mp4", "closed.mp4", b"\x00\x00\x00\x18ftypmp42"),
            ):
                command = MediaUploadIntentCommand.from_payload(
                    {
                        "commandId": str(uuid4()),
                        "expectedAuthorityEpoch": 0,
                        "mediaKind": media_kind,
                        "fileName": file_name,
                        "contentType": content_type,
                        "fileSizeBytes": len(payload),
                        "contentSha256": sha256(payload).hexdigest(),
                        "purpose": "memoryCapture",
                        "clientCreatedAt": "2026-08-18T00:00:00Z",
                    }
                )
                with self.assertRaises(OwnerTruthMediaKindProductClosed):
                    service.create_upload_intent(context=context, command=command)


if __name__ == "__main__":
    unittest.main()
