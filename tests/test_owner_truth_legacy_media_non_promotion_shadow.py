"""G0 tests proving legacy Archive media cannot become verified SourceObjects."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.owner_truth_legacy_media_non_promotion_shadow import (
    LegacyArchiveMediaNonPromotionDisposition,
    inventory_legacy_archive_media_non_promotion,
)
from app.services.owner_truth_media_source_object_shadow import MediaSourceObjectAdmissionContext
from app.services.privacy import sanitize_archive_item_payload


def _context() -> MediaSourceObjectAdmissionContext:
    return MediaSourceObjectAdmissionContext(
        vault_id="owner-legacy-media",
        owner_subject_id="owner-legacy-media",
    )


def _photo() -> dict[str, object]:
    return {
        "id": "legacy-photo-001",
        "userId": "owner-legacy-media",
        "kind": "photo",
        "title": "一张旧照片",
    }


class LegacyArchiveMediaNonPromotionShadowTests(unittest.TestCase):
    def _classify(self, item: object):
        return inventory_legacy_archive_media_non_promotion(
            item,
            context=_context(),
            enabled=True,
        )

    def test_disabled_returns_before_legacy_item_is_inspected(self) -> None:
        with patch(
            "app.services.owner_truth_legacy_media_non_promotion_shadow._fingerprint"
        ) as fingerprint:
            result = inventory_legacy_archive_media_non_promotion(
                {"malformed": object()},
                context=_context(),
            )

        fingerprint.assert_not_called()
        self.assertEqual(
            result.disposition,
            LegacyArchiveMediaNonPromotionDisposition.SHADOW_DISABLED,
        )

    def test_non_mapping_and_text_are_never_media_source_candidates(self) -> None:
        invalid = self._classify(object())
        text = self._classify({"id": "legacy-text-001", "kind": "text"})

        self.assertEqual(
            invalid.disposition,
            LegacyArchiveMediaNonPromotionDisposition.INVALID_ENVELOPE,
        )
        self.assertEqual(
            text.disposition,
            LegacyArchiveMediaNonPromotionDisposition.NOT_MEDIA,
        )

    def test_foreign_owner_or_vault_is_rejected_before_media_state_is_considered(self) -> None:
        for field, value in (("userId", "other-owner"), ("vaultId", "other-vault")):
            with self.subTest(field=field):
                item = _photo()
                item[field] = value
                result = self._classify(item)
                self.assertEqual(
                    result.disposition,
                    LegacyArchiveMediaNonPromotionDisposition.OWNER_OR_VAULT_MISMATCH,
                )
                self.assertFalse(result.would_be_verified_source_object)

    def test_device_only_and_mock_media_remain_non_authoritative(self) -> None:
        local = _photo()
        local["localPath"] = "/private/var/mobile/archive/photo.jpg"
        local_result = self._classify(local)
        self.assertEqual(
            local_result.disposition,
            LegacyArchiveMediaNonPromotionDisposition.LOCAL_OR_DEVICE_ONLY,
        )

        mock = _photo()
        mock["metadata"] = {
            "storageProvider": "mockObjectStorage",
            "uploadURL": "mock://archive-media/legacy-photo-001",
            "uploadStatus": "uploaded",
        }
        mock_result = self._classify(mock)
        self.assertEqual(
            mock_result.disposition,
            LegacyArchiveMediaNonPromotionDisposition.MOCK_OR_METADATA_UPLOAD,
        )

        summary = mock_result.value_free_summary()
        self.assertFalse(summary["legacyMediaPromoted"])
        self.assertFalse(summary["sourceObjectCreated"])
        self.assertFalse(summary["objectStorageOperationPerformed"])
        self.assertFalse(summary["processorAdmissionPerformed"])
        self.assertNotIn("mock://", repr(summary))
        self.assertNotIn("legacy-photo-001", repr(summary))

    def test_temporary_public_locator_and_injected_receipts_do_not_establish_authority(self) -> None:
        temporary = _photo()
        temporary["temporaryURL"] = "https://provider.example/temporary/photo"
        temporary_result = self._classify(temporary)
        self.assertEqual(
            temporary_result.disposition,
            LegacyArchiveMediaNonPromotionDisposition.TEMPORARY_OR_PUBLIC_LOCATOR,
        )

        tainted = _photo()
        tainted["sourceObjectId"] = "claimed-source-object"
        tainted["verificationReceipts"] = {"scanStatus": "clean"}
        tainted_result = self._classify(tainted)
        self.assertEqual(
            tainted_result.disposition,
            LegacyArchiveMediaNonPromotionDisposition.LEGACY_AUTHORITY_TAINT,
        )
        self.assertFalse(tainted_result.would_be_verified_source_object)

    def test_current_sanitized_archive_media_is_metadata_only_even_when_upload_status_claims_uploaded(self) -> None:
        sanitized = sanitize_archive_item_payload(
            {
                "id": "legacy-audio-001",
                "userId": "owner-legacy-media",
                "kind": "audio",
                "title": "本地录音",
                "localPath": "/private/var/mobile/archive/raw.m4a",
                "privacyMetadata": {"scope": "generationAllowed"},
                "metadata": {
                    "localPath": "/private/var/mobile/archive/raw.m4a",
                    "uploadStatus": "uploaded",
                    "cloudStatus": "synced",
                },
            }
        )

        self.assertNotIn("localPath", sanitized)
        self.assertNotIn("localPath", sanitized["metadata"])
        result = self._classify(sanitized)
        self.assertEqual(
            result.disposition,
            LegacyArchiveMediaNonPromotionDisposition.METADATA_ONLY,
        )
        self.assertFalse(result.would_be_verified_source_object)
        self.assertNotIn("owner-legacy-media", repr(result.value_free_summary()))

    def test_legacy_cloud_claim_without_object_receipts_is_not_verified(self) -> None:
        cloud_claim = _photo()
        cloud_claim["metadata"] = {
            "uploadStatus": "uploaded",
            "cloudStatus": "synced",
            "fileStatus": "cloud",
        }
        result = self._classify(cloud_claim)

        self.assertEqual(
            result.disposition,
            LegacyArchiveMediaNonPromotionDisposition.UNBACKED_CLOUD_CLAIM,
        )
        self.assertFalse(result.would_be_verified_source_object)

    def test_unclassified_legacy_media_requires_explicit_owner_reupload(self) -> None:
        result = self._classify(_photo())

        self.assertEqual(
            result.disposition,
            LegacyArchiveMediaNonPromotionDisposition.LEGACY_MEDIA_UNCLASSIFIED,
        )
        self.assertEqual(result.reason_code, "legacyMediaRequiresOwnerReupload")
        self.assertFalse(result.would_be_verified_source_object)


if __name__ == "__main__":
    unittest.main()
