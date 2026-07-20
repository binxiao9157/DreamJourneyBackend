"""G0-only tests for default-off future SourceObject intent/commit validation."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch
import unittest

from app.services.owner_truth_media_source_object_commit_shadow import (
    MEDIA_SOURCE_OBJECT_COMMIT_PROTOCOL_VERSION,
    MEDIA_SOURCE_OBJECT_INTENT_PROTOCOL_VERSION,
    MediaSourceObjectCommitShadowDisposition,
    build_media_source_object_intent_commit_shadow,
)
from app.services.owner_truth_media_source_object_shadow import MediaSourceObjectAdmissionContext


_DEFAULT = object()


def _context() -> MediaSourceObjectAdmissionContext:
    return MediaSourceObjectAdmissionContext(
        vault_id="vault-media-commit",
        owner_subject_id="owner-media-commit",
    )


def _intent() -> dict[str, object]:
    return {
        "protocolVersion": MEDIA_SOURCE_OBJECT_INTENT_PROTOCOL_VERSION,
        "intentId": "intent-media-commit-001",
        "sourceObjectId": "source-object-commit-001",
        "vaultId": "vault-media-commit",
        "ownerSubjectId": "owner-media-commit",
        "purpose": "candidateExtraction",
        "mediaKind": "audio",
        "storage": {
            "provider": "privateObjectStorage",
            "mode": "private",
            "objectKey": "env/region/vault-media-commit/audio/source-object-commit-001",
        },
        "maxSizeBytes": 1024,
        "declaredSizeBytes": 128,
        "declaredSha256": "b" * 64,
        "expiresAt": "2026-07-21T00:10:00Z",
    }


def _commit() -> dict[str, object]:
    return {
        "protocolVersion": MEDIA_SOURCE_OBJECT_COMMIT_PROTOCOL_VERSION,
        "intentId": "intent-media-commit-001",
        "sourceObjectId": "source-object-commit-001",
        "vaultId": "vault-media-commit",
        "ownerSubjectId": "owner-media-commit",
        "purpose": "candidateExtraction",
        "objectKey": "env/region/vault-media-commit/audio/source-object-commit-001",
        "objectVersion": 1,
        "sizeBytes": 128,
        "sha256": "b" * 64,
        "magicMime": "audio/mpeg",
        "observedHead": {
            "objectKey": "env/region/vault-media-commit/audio/source-object-commit-001",
            "objectVersion": 1,
            "sizeBytes": 128,
            "sha256": "b" * 64,
            "magicMime": "audio/mpeg",
        },
    }


class MediaSourceObjectCommitShadowTests(unittest.TestCase):
    def _shadow(self, intent=_DEFAULT, commit=_DEFAULT, **kwargs):
        return build_media_source_object_intent_commit_shadow(
            _intent() if intent is _DEFAULT else intent,
            _commit() if commit is _DEFAULT else commit,
            context=_context(),
            now_iso="2026-07-21T00:00:00Z",
            enabled=True,
            **kwargs,
        )

    def test_disabled_returns_before_malformed_inputs_are_parsed(self) -> None:
        with patch(
            "app.services.owner_truth_media_source_object_commit_shadow._parse_intent"
        ) as parse_intent:
            result = build_media_source_object_intent_commit_shadow(
                {"invalid": object()},
                {"invalid": object()},
                context=_context(),
                now_iso="not-a-time",
            )

        parse_intent.assert_not_called()
        self.assertEqual(
            result.disposition,
            MediaSourceObjectCommitShadowDisposition.SHADOW_DISABLED,
        )

    def test_valid_private_commit_only_would_create_quarantined_object(self) -> None:
        result = self._shadow()

        self.assertEqual(
            result.disposition,
            MediaSourceObjectCommitShadowDisposition.WOULD_COMMIT_QUARANTINED,
        )
        self.assertTrue(result.would_commit_quarantined)
        summary = result.value_free_summary()
        self.assertTrue(summary["shadowOnly"])
        self.assertFalse(summary["sourceObjectCreated"])
        self.assertFalse(summary["objectStorageOperationPerformed"])
        self.assertFalse(summary["providerHeadPerformed"])
        self.assertFalse(summary["effectAdmissionPerformed"])
        self.assertFalse(summary["candidateProposalPerformed"])
        self.assertNotIn("vault-media-commit", repr(summary))
        self.assertNotIn("source-object-commit-001", repr(summary))

    def test_expired_intent_fails_closed(self) -> None:
        result = build_media_source_object_intent_commit_shadow(
            _intent(),
            _commit(),
            context=_context(),
            now_iso="2026-07-21T00:10:00Z",
            enabled=True,
        )
        self.assertEqual(
            result.disposition,
            MediaSourceObjectCommitShadowDisposition.INTENT_EXPIRED,
        )

    def test_mock_or_unsafe_intent_cannot_be_admitted(self) -> None:
        mock_intent = _intent()
        mock_intent["storage"] = {
            "provider": "mockObjectStorage",
            "mode": "mock",
            "objectKey": "mock://archive-media/object",
        }
        mock_result = self._shadow(intent=mock_intent)
        self.assertEqual(
            mock_result.disposition,
            MediaSourceObjectCommitShadowDisposition.LEGACY_OR_MOCK_INTENT,
        )

        unsafe_intent = _intent()
        storage = deepcopy(unsafe_intent["storage"])
        assert isinstance(storage, dict)
        storage["objectKey"] = "https://evil.example/object"
        unsafe_intent["storage"] = storage
        unsafe_result = self._shadow(intent=unsafe_intent)
        self.assertEqual(
            unsafe_result.disposition,
            MediaSourceObjectCommitShadowDisposition.UNSAFE_OBJECT_KEY,
        )

    def test_owner_vault_purpose_and_commit_binding_mismatches_fail_closed(self) -> None:
        for field, value in (
            ("ownerSubjectId", "other-owner"),
            ("vaultId", "other-vault"),
            ("purpose", "unrelatedPurpose"),
        ):
            with self.subTest(field=field):
                commit = _commit()
                commit[field] = value
                result = self._shadow(commit=commit)
                self.assertEqual(
                    result.disposition,
                    MediaSourceObjectCommitShadowDisposition.INTENT_BINDING_MISMATCH,
                )

    def test_commit_path_size_hash_mime_and_head_mismatches_fail_closed(self) -> None:
        variants: list[dict[str, object]] = []
        path = _commit()
        path["objectKey"] = "../other-object"
        variants.append(path)
        size = _commit()
        size["sizeBytes"] = 129
        variants.append(size)
        digest = _commit()
        digest["sha256"] = "c" * 64
        variants.append(digest)
        mime = _commit()
        mime["magicMime"] = "image/jpeg"
        variants.append(mime)
        head = _commit()
        observed = deepcopy(head["observedHead"])
        assert isinstance(observed, dict)
        observed["sizeBytes"] = 129
        head["observedHead"] = observed
        variants.append(head)

        for commit in variants:
            with self.subTest(commit=commit):
                result = self._shadow(commit=commit)
                self.assertIn(
                    result.disposition,
                    {
                        MediaSourceObjectCommitShadowDisposition.UNSAFE_OBJECT_KEY,
                        MediaSourceObjectCommitShadowDisposition.COMMIT_VERIFICATION_MISMATCH,
                    },
                )
                self.assertFalse(result.would_commit_quarantined)

    def test_identical_retry_would_deduplicate_but_changed_retry_conflicts(self) -> None:
        first = self._shadow()
        prior = {"commitFingerprint": first.commit_fingerprint}
        duplicate = self._shadow(prior_receipt=prior)
        self.assertEqual(
            duplicate.disposition,
            MediaSourceObjectCommitShadowDisposition.WOULD_DEDUPLICATE,
        )

        conflict = self._shadow(prior_receipt={"commitFingerprint": "d" * 64})
        self.assertEqual(
            conflict.disposition,
            MediaSourceObjectCommitShadowDisposition.DUPLICATE_CONFLICT,
        )

    def test_uncommitted_private_object_is_only_an_orphan_candidate(self) -> None:
        observed = {
            "objectKey": "env/region/vault-media-commit/audio/source-object-commit-001",
            "objectVersion": 1,
            "sha256": "b" * 64,
        }
        result = self._shadow(commit=None, observed_uncommitted_object=observed)

        self.assertEqual(
            result.disposition,
            MediaSourceObjectCommitShadowDisposition.ORPHAN_CANDIDATE,
        )
        self.assertFalse(result.value_free_summary()["orphanCleanupPerformed"])


if __name__ == "__main__":
    unittest.main()
