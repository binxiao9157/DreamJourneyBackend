"""G0 tests for deceased Memorial capability non-admission."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.owner_truth_memorial_capability_non_admission_shadow import (
    MemorialCapabilityDecisionState,
    MemorialCapabilityNonAdmissionClaims,
    MemorialCapabilityNonAdmissionContext,
    MemorialCapabilityNonAdmissionDisposition,
    MemorialCapabilityPurpose,
    MemorialCapabilitySubjectStatus,
    evaluate_memorial_capability_non_admission,
)


_PERSONA_ID = "39cb8c7d-31c0-4314-b813-2c01b5e2a7fb"


def _context(
    *,
    purpose: MemorialCapabilityPurpose = MemorialCapabilityPurpose.VOICE_TRAINING,
    subject_status: MemorialCapabilitySubjectStatus = MemorialCapabilitySubjectStatus.DECEASED,
    represented_login_subject_id: str | None = None,
) -> MemorialCapabilityNonAdmissionContext:
    return MemorialCapabilityNonAdmissionContext(
        vault_id="vault-memorial-capability-a",
        represented_persona_id=_PERSONA_ID,
        represented_subject_status=subject_status,
        purpose=purpose,
        authority_epoch=13,
        represented_login_subject_id=represented_login_subject_id,
    )


def _complete_claims() -> MemorialCapabilityNonAdmissionClaims:
    return MemorialCapabilityNonAdmissionClaims(
        memorial_vault_private_active=True,
        controller_appointment_active=True,
        death_and_kinship_verified=True,
        source_provenance_valid=True,
        deceased_intent_evidence_covers_exact_purpose=True,
        jurisdiction_policy_allowed=True,
        provider_contract_allowed=True,
        no_active_rights_claim_or_conflict_hold=True,
        ai_disclosure_and_labeling_ready=True,
        release_policy_enabled=True,
        m3_case_review_approved=True,
    )


class MemorialCapabilityNonAdmissionShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_context_or_claims(self) -> None:
        with patch(
            "app.services.owner_truth_memorial_capability_non_admission_shadow."
            "MemorialCapabilityNonAdmissionContext"
        ) as context_type:
            result = evaluate_memorial_capability_non_admission(object(), claims=object())

        context_type.assert_not_called()
        self.assertEqual(
            result.disposition,
            MemorialCapabilityNonAdmissionDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.records_written)

    def test_missing_exact_intent_evidence_stays_not_requested(self) -> None:
        result = evaluate_memorial_capability_non_admission(_context(), enabled=True)

        self.assertEqual(
            result.disposition,
            MemorialCapabilityNonAdmissionDisposition.NOT_REQUESTED_NO_INTENT_EVIDENCE,
        )
        self.assertEqual(result.proposed_decision_state, MemorialCapabilityDecisionState.NOT_REQUESTED)
        self.assertFalse(result.capability_admitted)
        self.assertFalse(result.voice_or_portrait_training_allowed)
        self.assertFalse(result.digital_human_session_allowed)

    def test_missing_gate_or_conflict_rejects_without_a_provider_or_fallback(self) -> None:
        claims = _complete_claims()
        claims = MemorialCapabilityNonAdmissionClaims(
            **{**claims.__dict__, "no_active_rights_claim_or_conflict_hold": False}
        )
        result = evaluate_memorial_capability_non_admission(
            _context(purpose=MemorialCapabilityPurpose.DIGITAL_HUMAN_PRIVATE),
            claims=claims,
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MemorialCapabilityNonAdmissionDisposition.REJECTED_PREREQUISITES,
        )
        self.assertEqual(result.proposed_decision_state, MemorialCapabilityDecisionState.REJECTED)
        self.assertIn("activeRightsClaimOrConflictHoldBlocksCapability", result.reason_codes)
        self.assertFalse(result.capability_decision_written)
        self.assertFalse(result.provider_effect_allowed)
        self.assertFalse(result.fallback_to_family_voice_allowed)
        self.assertFalse(result.default_system_voice_may_be_described_as_deceased)

    def test_all_synthetic_claims_only_plan_future_evidence_review(self) -> None:
        result = evaluate_memorial_capability_non_admission(
            _context(purpose=MemorialCapabilityPurpose.PUBLICATION_DIGITAL_HUMAN),
            claims=_complete_claims(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MemorialCapabilityNonAdmissionDisposition.EXTERNAL_M3_G2_G3_G4_REQUIRED,
        )
        self.assertEqual(result.proposed_decision_state, MemorialCapabilityDecisionState.EVIDENCE_REVIEW)
        self.assertFalse(result.capability_admitted)
        self.assertFalse(result.capability_decision_written)
        self.assertFalse(result.provider_effect_allowed)
        self.assertFalse(result.voice_or_portrait_training_allowed)
        self.assertFalse(result.digital_human_session_allowed)
        self.assertFalse(result.publication_allowed)

    def test_each_purpose_is_independent_and_summary_is_value_free(self) -> None:
        voice = evaluate_memorial_capability_non_admission(
            _context(purpose=MemorialCapabilityPurpose.VOICE_SYNTHESIS_PRIVATE),
            claims=_complete_claims(),
            enabled=True,
        )
        digital_human = evaluate_memorial_capability_non_admission(
            _context(purpose=MemorialCapabilityPurpose.DIGITAL_HUMAN_PRIVATE),
            claims=_complete_claims(),
            enabled=True,
        )
        rendered = repr(digital_human.value_free_summary())

        self.assertNotEqual(voice.scope_hash, digital_human.scope_hash)
        self.assertNotIn("vault-memorial-capability-a", rendered)
        self.assertNotIn(_PERSONA_ID, rendered)
        self.assertIn("scopeHash", rendered)

    def test_represented_login_non_deceased_and_invalid_context_fail_closed(self) -> None:
        represented_login = evaluate_memorial_capability_non_admission(
            _context(represented_login_subject_id="invalid-deceased-principal"),
            enabled=True,
        )
        living = evaluate_memorial_capability_non_admission(
            _context(subject_status=MemorialCapabilitySubjectStatus.LIVING),
            enabled=True,
        )
        invalid = evaluate_memorial_capability_non_admission(object(), enabled=True)

        self.assertEqual(
            represented_login.disposition,
            MemorialCapabilityNonAdmissionDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN,
        )
        self.assertEqual(
            living.disposition,
            MemorialCapabilityNonAdmissionDisposition.NON_DECEASED_CONTEXT_FORBIDDEN,
        )
        self.assertEqual(invalid.disposition, MemorialCapabilityNonAdmissionDisposition.INVALID_CONTEXT)
        self.assertFalse(represented_login.records_written)
        self.assertFalse(living.records_written)
        self.assertFalse(invalid.records_written)


if __name__ == "__main__":
    unittest.main()
