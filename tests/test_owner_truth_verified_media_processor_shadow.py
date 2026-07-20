"""G0 contract tests for verified media processor admission planning."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.owner_truth_media_source_object_shadow import (
    MediaSourceObjectAdmissionContext,
)
from app.services.owner_truth_verified_media_processor_shadow import (
    VerifiedMediaExtractionResultStatus,
    VerifiedMediaExtractionSegmentRef,
    VerifiedMediaProcessorDescriptor,
    VerifiedMediaProcessorDisposition,
    build_verified_media_extraction_result_shadow,
    plan_verified_media_processor_admission,
)


def _context() -> MediaSourceObjectAdmissionContext:
    return MediaSourceObjectAdmissionContext(
        vault_id="vault-media-processor",
        owner_subject_id="owner-media-processor",
    )


def _verified_source_object() -> dict[str, object]:
    return {
        "protocolVersion": "owner-truth-source-object-v1",
        "sourceObjectId": "source-object-media-processor-001",
        "vaultId": "vault-media-processor",
        "ownerSubjectId": "owner-media-processor",
        "purpose": "candidateExtraction",
        "mediaKind": "image",
        "state": "verified",
        "objectVersion": 1,
        "sizeBytes": 1024,
        "sha256": "a" * 64,
        "magicMime": "image/jpeg",
        "storage": {
            "provider": "privateObjectStorage",
            "mode": "private",
            "objectKey": "env/region/vault-media-processor/image/source-object-media-processor-001",
        },
        "verificationReceipts": {
            "checksumVerified": True,
            "headVerified": True,
            "magicMimeVerified": True,
            "scanStatus": "clean",
        },
    }


def _descriptor(**overrides: object) -> VerifiedMediaProcessorDescriptor:
    values: dict[str, object] = {
        "processor_id": "imageVision",
        "processor_version": "v1",
        "policy_version": "mediaPolicyV1",
        "media_kind": "image",
        "enabled": True,
        "execution_mode": "synthetic",
    }
    values.update(overrides)
    return VerifiedMediaProcessorDescriptor(**values)


class VerifiedMediaProcessorAdmissionShadowTests(unittest.TestCase):
    def _plan(self, source_object: object = None, **kwargs: object):
        if source_object is None:
            source_object = _verified_source_object()
        return plan_verified_media_processor_admission(
            source_object,
            context=_context(),
            descriptor=kwargs.pop("descriptor", _descriptor()),
            enabled=kwargs.pop("enabled", True),
            prior_attempt=kwargs.pop("prior_attempt", None),
            **kwargs,
        )

    def test_disabled_returns_before_inputs_are_parsed(self) -> None:
        with patch(
            "app.services.owner_truth_verified_media_processor_shadow.build_media_source_object_admission_shadow"
        ) as admission:
            result = plan_verified_media_processor_admission(
                {"malformed": object()},
                context=_context(),
                descriptor=object(),
                enabled=False,
            )

        admission.assert_not_called()
        self.assertEqual(result.disposition, VerifiedMediaProcessorDisposition.SHADOW_DISABLED)
        self.assertFalse(result.would_enqueue_source_extraction)

    def test_verified_parent_generates_candidate_only_extraction_plan(self) -> None:
        result = self._plan()

        self.assertEqual(
            result.disposition,
            VerifiedMediaProcessorDisposition.WOULD_ENQUEUE_SOURCE_EXTRACTION,
        )
        self.assertTrue(result.would_enqueue_source_extraction)
        self.assertTrue(result.requires_separate_candidate_proposal)
        self.assertIsNotNone(result.extraction_request_fingerprint)
        summary = result.value_free_summary()
        self.assertTrue(summary["wouldEnqueueSourceExtraction"])
        self.assertTrue(summary["requiresSeparateCandidateProposal"])
        self.assertFalse(summary["sourceExtractionEnqueued"])
        self.assertFalse(summary["candidateProposalPerformed"])
        self.assertFalse(summary["extractionResultPersisted"])
        self.assertFalse(summary["confirmedMemoryWritten"])
        self.assertFalse(summary["personaWritten"])
        self.assertFalse(summary["providerCallPerformed"])
        self.assertFalse(summary["objectReadPerformed"])
        self.assertNotIn("vault-media-processor", repr(summary))
        self.assertNotIn("source-object-media-processor-001", repr(summary))

    def test_unverified_revoked_or_legacy_parent_never_reaches_processor(self) -> None:
        for mutation in (
            {"state": "uploaded_unverified"},
            {"state": "revoked"},
            {"temporaryURL": "https://provider.example/object"},
        ):
            with self.subTest(mutation=mutation):
                source_object = _verified_source_object()
                source_object.update(mutation)
                result = self._plan(source_object)
                self.assertEqual(
                    result.disposition,
                    VerifiedMediaProcessorDisposition.PARENT_NOT_ELIGIBLE,
                )
                self.assertFalse(result.would_enqueue_source_extraction)

    def test_processor_must_match_verified_media_kind_and_enabled_policy(self) -> None:
        mismatch = self._plan(descriptor=_descriptor(media_kind="audio"))
        disabled = self._plan(descriptor=_descriptor(enabled=False))
        unavailable = self._plan(descriptor=_descriptor(execution_mode="provider"))

        self.assertEqual(
            mismatch.disposition,
            VerifiedMediaProcessorDisposition.PROCESSOR_MEDIA_KIND_MISMATCH,
        )
        self.assertEqual(
            disabled.disposition,
            VerifiedMediaProcessorDisposition.PROCESSOR_DISABLED,
        )
        self.assertEqual(
            unavailable.disposition,
            VerifiedMediaProcessorDisposition.PROCESSOR_MODE_UNAVAILABLE,
        )

    def test_retry_terminal_unknown_and_stale_attempts_have_one_safe_next_action(self) -> None:
        first = self._plan()
        assert first.extraction_request_fingerprint is not None
        fingerprint = first.extraction_request_fingerprint

        cases = (
            ("succeeded", VerifiedMediaProcessorDisposition.WOULD_DEDUPLICATE),
            ("failed_retryable", VerifiedMediaProcessorDisposition.WOULD_RETRY_SOURCE_EXTRACTION),
            ("failed_terminal", VerifiedMediaProcessorDisposition.TERMINAL_FAILURE_RECORDED),
            ("unknown", VerifiedMediaProcessorDisposition.WOULD_QUERY_RECONCILE),
        )
        for outcome, disposition in cases:
            with self.subTest(outcome=outcome):
                result = self._plan(
                    prior_attempt={
                        "requestFingerprint": fingerprint,
                        "outcome": outcome,
                    }
                )
                self.assertEqual(result.disposition, disposition)

        stale = self._plan(
            prior_attempt={
                "requestFingerprint": "b" * 64,
                "outcome": "succeeded",
            }
        )
        self.assertEqual(
            stale.disposition,
            VerifiedMediaProcessorDisposition.STALE_OR_FOREIGN_ATTEMPT,
        )
        self.assertFalse(stale.would_enqueue_source_extraction)


class VerifiedMediaExtractionResultShadowTests(unittest.TestCase):
    def _admission(self):
        return plan_verified_media_processor_admission(
            _verified_source_object(),
            context=_context(),
            descriptor=_descriptor(),
            enabled=True,
        )

    def test_successful_media_result_is_value_minimized_and_requires_a_separate_proposal(self) -> None:
        admission = self._admission()
        result = build_verified_media_extraction_result_shadow(
            admission,
            status=VerifiedMediaExtractionResultStatus.SUCCEEDED,
            segments=(
                VerifiedMediaExtractionSegmentRef(
                    segment_id="segment-image-001",
                    locator_fingerprint="b" * 64,
                    content_fingerprint="c" * 64,
                    confidence=0.91,
                ),
            ),
        )

        self.assertTrue(result.requires_separate_candidate_proposal)
        summary = result.value_free_summary()
        self.assertEqual(summary["segmentCount"], 1)
        self.assertTrue(summary["requiresSeparateCandidateProposal"])
        self.assertFalse(summary["candidateProposalPerformed"])
        self.assertFalse(summary["extractionResultPersisted"])
        self.assertFalse(summary["confirmedMemoryWritten"])
        self.assertFalse(summary["personaWritten"])
        self.assertFalse(summary["providerCallPerformed"])
        self.assertFalse(summary["objectReadPerformed"])
        self.assertNotIn("vault-media-processor", repr(summary))
        self.assertNotIn("source-object-media-processor-001", repr(summary))
        self.assertNotIn("segment-image-001", repr(summary))
        self.assertNotIn("b" * 64, repr(summary))
        self.assertNotIn("c" * 64, repr(summary))

    def test_failed_retryable_or_empty_results_never_propose_candidates(self) -> None:
        admission = self._admission()
        empty = build_verified_media_extraction_result_shadow(
            admission,
            status=VerifiedMediaExtractionResultStatus.SUCCEEDED,
            segments=(),
        )
        retryable = build_verified_media_extraction_result_shadow(
            admission,
            status=VerifiedMediaExtractionResultStatus.FAILED,
            failure_code="providerUnavailable",
            retryable=True,
        )

        self.assertFalse(empty.requires_separate_candidate_proposal)
        self.assertFalse(retryable.requires_separate_candidate_proposal)
        self.assertTrue(retryable.retryable)
        self.assertEqual(retryable.value_free_summary()["status"], "failed")

    def test_result_requires_an_eligible_enqueue_or_retry_admission(self) -> None:
        rejected = plan_verified_media_processor_admission(
            _verified_source_object(),
            context=_context(),
            descriptor=_descriptor(enabled=False),
            enabled=True,
        )

        with self.assertRaises(ValueError):
            build_verified_media_extraction_result_shadow(
                rejected,
                status=VerifiedMediaExtractionResultStatus.SUCCEEDED,
            )


if __name__ == "__main__":
    unittest.main()
