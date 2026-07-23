"""G0 tests for the default-deny Voice/DH purpose consent policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import MappingProxyType
from unittest.mock import patch
import unittest

from pydantic import ValidationError

from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityEvidence,
    SubjectEligibilityReason,
    evaluate_subject_eligibility,
)
from app.services.voice_dh_consent_policy import (
    ConsentReceipt,
    ProcessingBasis,
    VoiceDHPurpose,
    VoiceDHPurposeConsentDisposition,
    VoiceDHPurposeConsentRequest,
    VoicePurposeGrant,
    observe_voice_dh_purpose_consent,
)


_NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _eligibility(purpose: VoiceDHPurpose, *, allowed: bool = True, reason: SubjectEligibilityReason | None = None) -> SubjectEligibilityDecision:
    capability = (
        HighRiskCapability.DIGITAL_HUMAN
        if purpose is VoiceDHPurpose.DH_AUDIO_DRIVE
        else HighRiskCapability.CLONED_VOICE
    )
    if allowed:
        return SubjectEligibilityDecision(
            capability=capability,
            allowed=True,
            decision="allow",
            reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
        )
    return SubjectEligibilityDecision(
        capability=capability,
        allowed=False,
        decision="hardDeny",
        reason=reason or SubjectEligibilityReason.SUBJECT_MISMATCH,
    )


def _receipt(
    purpose: VoiceDHPurpose,
    **changes: object,
) -> ConsentReceipt:
    values: dict[str, object] = {
        "receiptHash": _digest(f"receipt:{purpose.value}"),
        "subjectId": "subject-owner-a",
        "actorId": "subject-owner-a",
        "purpose": purpose,
        "basis": ProcessingBasis(policyVersion="voice-policy-v1"),
        "policyVersion": "voice-policy-v1",
        "provider": "volcengineVoiceClone",
        "region": "cn-mainland",
        "issuedAt": _NOW - timedelta(minutes=1),
        "expiresAt": _NOW + timedelta(hours=1),
    }
    values.update(changes)
    return ConsentReceipt(**values)  # type: ignore[arg-type]


def _grant(receipt: ConsentReceipt, **changes: object) -> VoicePurposeGrant:
    values: dict[str, object] = {
        "grantHash": _digest(f"grant:{receipt.purpose.value}"),
        "receiptHash": receipt.receiptHash,
        "subjectId": receipt.subjectId,
        "actorId": receipt.actorId,
        "purpose": receipt.purpose,
        "policyVersion": receipt.policyVersion,
        "provider": receipt.provider,
        "region": receipt.region,
        "issuedAt": receipt.issuedAt,
        "expiresAt": receipt.expiresAt,
    }
    values.update(changes)
    return VoicePurposeGrant(**values)  # type: ignore[arg-type]


def _request(purpose: VoiceDHPurpose, **changes: object) -> VoiceDHPurposeConsentRequest:
    provider = "tencentDigitalHuman" if purpose is VoiceDHPurpose.DH_AUDIO_DRIVE else "volcengineVoiceClone"
    receipt = _receipt(purpose, provider=provider)
    values: dict[str, object] = {
        "purpose": purpose,
        "provider": provider,
        "region": "cn-mainland",
        "evaluationMode": "syntheticG0",
        "online": True,
        "consentReceipt": receipt,
        "purposeGrant": _grant(receipt),
        "subjectEligibility": _eligibility(purpose),
    }
    values.update(changes)
    return VoiceDHPurposeConsentRequest(**values)  # type: ignore[arg-type]


class VoiceDHPurposeConsentPolicyTests(unittest.TestCase):
    def _observe(self, request: object, *, enabled: bool = True):
        return observe_voice_dh_purpose_consent(request, enabled=enabled, now=_NOW)

    def test_disabled_path_does_not_validate_context(self) -> None:
        with patch(
            "app.services.voice_dh_consent_policy._evaluation_time"
        ) as evaluation_time:
            result = observe_voice_dh_purpose_consent(object())

        evaluation_time.assert_not_called()
        self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.SHADOW_DISABLED)
        self.assertFalse(result.effectAllowed)
        self.assertFalse(result.providerEffectAllowed)

    def test_non_boolean_enabled_value_remains_disabled(self) -> None:
        result = observe_voice_dh_purpose_consent(
            _request(VoiceDHPurpose.TRAINING),
            enabled="false",
            now=_NOW,
        )

        self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.SHADOW_DISABLED)
        self.assertFalse(result.effectAllowed)

    def test_valid_synthetic_m1_purposes_remain_denied_without_effect(self) -> None:
        for purpose in (
            VoiceDHPurpose.TRAINING,
            VoiceDHPurpose.PREVIEW,
            VoiceDHPurpose.PRIVATE_SYNTHESIS,
            VoiceDHPurpose.MEMOIR,
            VoiceDHPurpose.DH_AUDIO_DRIVE,
        ):
            with self.subTest(purpose=purpose):
                result = self._observe(_request(purpose))
                self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.DENIED)
                self.assertTrue(result.syntheticPreconditionsSatisfied)
                self.assertFalse(result.would_be_eligible_for_future_promotion)
                self.assertFalse(result.effectAllowed)
                self.assertFalse(result.providerEffectAllowed)
                self.assertFalse(result.releaseVisible)

    def test_every_purpose_rejects_mismatched_receipt(self) -> None:
        purposes = tuple(VoiceDHPurpose)
        for index, purpose in enumerate(purposes):
            with self.subTest(purpose=purpose):
                mismatch = purposes[(index + 1) % len(purposes)]
                request = _request(purpose, consentReceipt=_receipt(mismatch))
                result = self._observe(request)
                self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.DENIED)
                self.assertIn("consentReceiptPurposeMismatch", result.reasonCodes)
                self.assertFalse(result.effectAllowed)

    def test_legacy_boolean_never_becomes_authority(self) -> None:
        result = self._observe(_request(VoiceDHPurpose.TRAINING, legacyConsentObserved=True))

        self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.DENIED)
        self.assertIn("legacyBooleanConsentNotAuthority", result.reasonCodes)
        self.assertFalse(result.legacyConsentPromoted)

    def test_missing_expired_revoked_offline_and_unknown_state_deny(self) -> None:
        base = _request(VoiceDHPurpose.PRIVATE_SYNTHESIS)
        cases = {
            "missing": _request(
                VoiceDHPurpose.PRIVATE_SYNTHESIS,
                consentReceipt=None,
                purposeGrant=None,
                subjectEligibility=None,
            ),
            "expired": _request(
                VoiceDHPurpose.PRIVATE_SYNTHESIS,
                consentReceipt=_receipt(
                    VoiceDHPurpose.PRIVATE_SYNTHESIS,
                    issuedAt=_NOW - timedelta(hours=2),
                    expiresAt=_NOW - timedelta(minutes=1),
                ),
            ),
            "revoked": _request(
                VoiceDHPurpose.PRIVATE_SYNTHESIS,
                consentReceipt=_receipt(
                    VoiceDHPurpose.PRIVATE_SYNTHESIS,
                    revokedAt=_NOW - timedelta(seconds=1),
                ),
            ),
            "offline": _request(VoiceDHPurpose.PRIVATE_SYNTHESIS, online=False),
            "unknownProvider": _request(VoiceDHPurpose.PRIVATE_SYNTHESIS, provider="unknownProvider"),
            "unknownRegion": _request(VoiceDHPurpose.PRIVATE_SYNTHESIS, region="unknownRegion"),
        }

        self.assertIsInstance(base, VoiceDHPurposeConsentRequest)
        for name, request in cases.items():
            with self.subTest(name=name):
                result = self._observe(request)
                self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.DENIED)
                self.assertFalse(result.effectAllowed)
                self.assertFalse(result.providerEffectAllowed)

    def test_future_receipts_and_grants_are_not_yet_effective(self) -> None:
        future_receipt = _receipt(
            VoiceDHPurpose.TRAINING,
            issuedAt=_NOW + timedelta(minutes=1),
            expiresAt=_NOW + timedelta(hours=1),
        )
        future_grant = _grant(
            _receipt(VoiceDHPurpose.TRAINING),
            issuedAt=_NOW + timedelta(minutes=1),
            expiresAt=_NOW + timedelta(hours=1),
        )
        receipt_result = self._observe(
            _request(
                VoiceDHPurpose.TRAINING,
                consentReceipt=future_receipt,
                purposeGrant=_grant(future_receipt),
            )
        )
        grant_result = self._observe(
            _request(VoiceDHPurpose.TRAINING, purposeGrant=future_grant)
        )

        self.assertEqual(receipt_result.status, VoiceDHPurposeConsentDisposition.DENIED)
        self.assertIn("consentReceiptNotYetEffective", receipt_result.reasonCodes)
        self.assertEqual(grant_result.status, VoiceDHPurposeConsentDisposition.DENIED)
        self.assertIn("purposeGrantNotYetEffective", grant_result.reasonCodes)

    def test_naive_evaluation_time_fails_closed(self) -> None:
        result = observe_voice_dh_purpose_consent(
            _request(VoiceDHPurpose.TRAINING),
            enabled=True,
            now=datetime(2026, 7, 23, 12, 0),
        )

        self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.INVALID_CONTEXT)
        self.assertIn("invalidEvaluationTime", result.reasonCodes)
        self.assertFalse(result.effectAllowed)

    def test_existing_subject_eligibility_hard_denials_are_reused(self) -> None:
        reasons = (
            SubjectEligibilityReason.MINOR,
            SubjectEligibilityReason.FAMILY_SUBJECT,
            SubjectEligibilityReason.DECEASED_SUBJECT,
            SubjectEligibilityReason.SUBJECT_MISMATCH,
        )
        for reason in reasons:
            with self.subTest(reason=reason):
                result = self._observe(
                    _request(
                        VoiceDHPurpose.TRAINING,
                        subjectEligibility=_eligibility(
                            VoiceDHPurpose.TRAINING,
                            allowed=False,
                            reason=reason,
                        ),
                    )
                )
                self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.DENIED)
                self.assertIn(f"subjectEligibilityHardDeny:{reason.value}", result.reasonCodes)

    def test_legacy_boolean_derived_positive_eligibility_never_promotes(self) -> None:
        legacy_derived_decision = evaluate_subject_eligibility(
            SubjectEligibilityEvidence(
                capability=HighRiskCapability.CLONED_VOICE,
                subjectKind="self",
                ageStatus="adult",
                livingStatus="living",
                ageVerified=True,
                livenessVerified=True,
                subjectMatchesActor=True,
                consentVerified=True,
                consentPurpose=HighRiskCapability.CLONED_VOICE,
            )
        )
        result = self._observe(
            _request(VoiceDHPurpose.TRAINING, subjectEligibility=legacy_derived_decision)
        )

        self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.DENIED)
        self.assertTrue(result.syntheticPreconditionsSatisfied)
        self.assertFalse(result.legacyConsentPromoted)
        self.assertFalse(result.would_be_eligible_for_future_promotion)

    def test_visitor_public_voice_remains_external_denied(self) -> None:
        result = self._observe(_request(VoiceDHPurpose.VISITOR_PUBLIC_VOICE))

        self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.DENIED)
        self.assertIn("visitorPublicVoiceRequiresM2G4Approval", result.reasonCodes)
        self.assertFalse(result.releaseVisible)

    def test_receipt_grant_binding_and_capability_mismatch_deny(self) -> None:
        receipt = _receipt(VoiceDHPurpose.TRAINING)
        wrong_grant = _grant(receipt, receiptHash=_digest("other-receipt"))
        wrong_capability = SubjectEligibilityDecision(
            capability=HighRiskCapability.DIGITAL_HUMAN,
            allowed=True,
            decision="allow",
            reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
        )
        result = self._observe(
            _request(
                VoiceDHPurpose.TRAINING,
                consentReceipt=receipt,
                purposeGrant=wrong_grant,
                subjectEligibility=wrong_capability,
            )
        )

        self.assertEqual(result.status, VoiceDHPurposeConsentDisposition.DENIED)
        self.assertIn("purposeGrantReceiptBindingMismatch", result.reasonCodes)
        self.assertIn("subjectEligibilityCapabilityMismatch", result.reasonCodes)

    def test_contracts_are_immutable_strict_and_summary_is_value_free(self) -> None:
        request = _request(VoiceDHPurpose.MEMOIR)
        result = self._observe(request)

        with self.assertRaises(ValidationError):
            request.consentReceipt.subjectId = "subject-owner-b"  # type: ignore[misc]
        with self.assertRaises(ValidationError):
            ProcessingBasis(policyVersion="voice-policy-v1", unexpected=True)
        with self.assertRaises(ValidationError):
            ConsentReceipt(
                **{
                    **request.consentReceipt.model_dump(),
                    "issuedAt": _NOW,
                    "expiresAt": _NOW,
                }
            )
        with self.assertRaises(ValidationError):
            ConsentReceipt(
                **{
                    **request.consentReceipt.model_dump(),
                    "receiptHash": "not-a-sha256-hash",
                }
            )
        with self.assertRaises(ValidationError):
            VoiceDHPurposeConsentRequest(
                **{
                    **request.model_dump(),
                    "unexpected": "not allowed",
                }
            )
        with self.assertRaises(ValidationError):
            VoiceDHPurposeConsentRequest(
                **{
                    **request.model_dump(),
                    "online": "yes",
                }
            )
        with self.assertRaises(ValidationError):
            VoiceDHPurposeConsentRequest(
                **{
                    **request.model_dump(),
                    "legacyConsentObserved": "no",
                }
            )

        summary = result.value_free_summary()
        rendered = repr(MappingProxyType(summary))
        self.assertNotIn("subject-owner-a", rendered)
        self.assertNotIn(request.consentReceipt.receiptHash, rendered)
        self.assertTrue(summary["shadowOnly"])
        self.assertFalse(summary["consentReceiptWritten"])
        self.assertFalse(summary["purposeGrantWritten"])
        self.assertTrue(summary["syntheticPreconditionsSatisfied"])


if __name__ == "__main__":
    unittest.main()
