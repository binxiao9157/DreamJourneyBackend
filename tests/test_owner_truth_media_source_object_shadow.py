"""G0-only tests for the disabled future media SourceObject admission shadow."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch
import unittest

from app.services.owner_truth_media_source_object_shadow import (
    MEDIA_SOURCE_OBJECT_PROTOCOL_VERSION,
    MediaSourceObjectAdmissionContext,
    MediaSourceObjectAdmissionShadowDisposition,
    build_media_source_object_admission_shadow,
)


def _context() -> MediaSourceObjectAdmissionContext:
    return MediaSourceObjectAdmissionContext(
        vault_id="vault-media-shadow",
        owner_subject_id="owner-media-shadow",
    )


def _verified_image() -> dict[str, object]:
    return {
        "protocolVersion": MEDIA_SOURCE_OBJECT_PROTOCOL_VERSION,
        "sourceObjectId": "source-object-media-001",
        "vaultId": "vault-media-shadow",
        "ownerSubjectId": "owner-media-shadow",
        "purpose": "candidateExtraction",
        "mediaKind": "image",
        "state": "verified",
        "objectVersion": 1,
        "sizeBytes": 128,
        "sha256": "a" * 64,
        "magicMime": "image/jpeg",
        "storage": {
            "provider": "privateObjectStorage",
            "mode": "private",
            "objectKey": "vault-media-shadow/image/source-object-media-001",
        },
        "verificationReceipts": {
            "headVerified": True,
            "checksumVerified": True,
            "magicMimeVerified": True,
            "scanStatus": "clean",
        },
    }


class MediaSourceObjectAdmissionShadowTests(unittest.TestCase):
    def test_disabled_returns_before_malformed_envelope_is_inspected(self) -> None:
        with patch(
            "app.services.owner_truth_media_source_object_shadow._safe_storage_locator"
        ) as locator:
            result = build_media_source_object_admission_shadow(
                {"this": object()},
                context=_context(),
            )

        locator.assert_not_called()
        self.assertFalse(result.enabled)
        self.assertEqual(
            result.disposition,
            MediaSourceObjectAdmissionShadowDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.would_be_processor_eligible)

    def test_verified_private_fixture_is_only_future_processor_eligible(self) -> None:
        result = build_media_source_object_admission_shadow(
            _verified_image(),
            context=_context(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MediaSourceObjectAdmissionShadowDisposition.WOULD_BE_PROCESSOR_ELIGIBLE,
        )
        self.assertTrue(result.would_be_processor_eligible)
        summary = result.value_free_summary()
        self.assertTrue(summary["shadowOnly"])
        self.assertFalse(summary["sourceObjectCreated"])
        self.assertFalse(summary["objectStorageOperationPerformed"])
        self.assertFalse(summary["effectAdmissionPerformed"])
        self.assertFalse(summary["candidateProposalPerformed"])
        self.assertNotIn("vault-media-shadow", repr(summary))
        self.assertNotIn("owner-media-shadow", repr(summary))
        self.assertNotIn("source-object-media-001", repr(summary))
        self.assertNotIn("privateObjectStorage", repr(summary))

    def test_verified_result_fingerprint_is_deterministic_and_value_free(self) -> None:
        first = build_media_source_object_admission_shadow(_verified_image(), context=_context(), enabled=True)
        second = build_media_source_object_admission_shadow(_verified_image(), context=_context(), enabled=True)

        self.assertEqual(first.source_object_fingerprint, second.source_object_fingerprint)
        self.assertEqual(len(first.source_object_fingerprint or ""), 64)
        self.assertNotEqual(first.source_object_fingerprint, "a" * 64)

    def test_mock_local_or_temporary_locators_cannot_become_processor_eligible(self) -> None:
        fixtures = []
        mock = _verified_image()
        mock["storage"] = {
            "provider": "mockObjectStorage",
            "mode": "mock",
            "objectKey": "mock://archive-media/object",
        }
        fixtures.append(mock)

        local = _verified_image()
        local["localPath"] = "/private/var/mobile/Media/photo.jpg"
        fixtures.append(local)

        temporary = _verified_image()
        temporary["temporaryURL"] = "https://provider.example/temporary/object"
        fixtures.append(temporary)

        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                result = build_media_source_object_admission_shadow(
                    fixture,
                    context=_context(),
                    enabled=True,
                )
                self.assertEqual(
                    result.disposition,
                    MediaSourceObjectAdmissionShadowDisposition.UNTRUSTED_STORAGE_LOCATOR,
                )
                self.assertFalse(result.would_be_processor_eligible)
                self.assertNotIn("mock://", repr(result.value_free_summary()))
                self.assertNotIn("/private/var", repr(result.value_free_summary()))

    def test_unverified_or_inactive_object_is_blocked_before_future_processor(self) -> None:
        for state, disposition in (
            ("uploaded_unverified", MediaSourceObjectAdmissionShadowDisposition.OBJECT_NOT_VERIFIED),
            ("quarantined", MediaSourceObjectAdmissionShadowDisposition.OBJECT_NOT_VERIFIED),
            ("revoked", MediaSourceObjectAdmissionShadowDisposition.OBJECT_INACTIVE),
            ("deleted", MediaSourceObjectAdmissionShadowDisposition.OBJECT_INACTIVE),
        ):
            with self.subTest(state=state):
                fixture = _verified_image()
                fixture["state"] = state
                result = build_media_source_object_admission_shadow(
                    fixture,
                    context=_context(),
                    enabled=True,
                )
                self.assertEqual(result.disposition, disposition)
                self.assertFalse(result.would_be_processor_eligible)

    def test_owner_vault_or_purpose_mismatch_fails_closed(self) -> None:
        for field, value in (
            ("ownerSubjectId", "other-owner"),
            ("vaultId", "other-vault"),
            ("purpose", "unrelatedPurpose"),
        ):
            with self.subTest(field=field):
                fixture = _verified_image()
                fixture[field] = value
                result = build_media_source_object_admission_shadow(
                    fixture,
                    context=_context(),
                    enabled=True,
                )
                self.assertEqual(
                    result.disposition,
                    MediaSourceObjectAdmissionShadowDisposition.OWNER_OR_PURPOSE_MISMATCH,
                )
                self.assertFalse(result.would_be_processor_eligible)

    def test_missing_verification_receipt_or_wrong_magic_mime_is_not_eligible(self) -> None:
        incomplete = _verified_image()
        receipts = deepcopy(incomplete["verificationReceipts"])
        assert isinstance(receipts, dict)
        receipts["scanStatus"] = "pending"
        incomplete["verificationReceipts"] = receipts

        failed_receipt = build_media_source_object_admission_shadow(
            incomplete,
            context=_context(),
            enabled=True,
        )
        self.assertEqual(
            failed_receipt.disposition,
            MediaSourceObjectAdmissionShadowDisposition.INCOMPLETE_VERIFICATION_RECEIPTS,
        )

        wrong_mime = _verified_image()
        wrong_mime["magicMime"] = "audio/mpeg"
        failed_mime = build_media_source_object_admission_shadow(
            wrong_mime,
            context=_context(),
            enabled=True,
        )
        self.assertEqual(
            failed_mime.disposition,
            MediaSourceObjectAdmissionShadowDisposition.UNSUPPORTED_MEDIA,
        )

    def test_source_object_envelope_cannot_carry_candidate_or_confirmed_authority(self) -> None:
        fixture = _verified_image()
        fixture["confirmedMemoryId"] = "memory-should-not-be-here"

        result = build_media_source_object_admission_shadow(
            fixture,
            context=_context(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MediaSourceObjectAdmissionShadowDisposition.INVALID_ENVELOPE,
        )
        self.assertEqual(result.reason_code, "sourceObjectCarriesAuthorityField")


if __name__ == "__main__":
    unittest.main()
