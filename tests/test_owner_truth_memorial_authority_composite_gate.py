"""Cross-boundary G0 regression for the future Memorial authority model."""

from __future__ import annotations

import unittest

from app.services.owner_truth_memorial_capability_non_admission_shadow import (
    MemorialCapabilityNonAdmissionClaims,
    MemorialCapabilityNonAdmissionContext,
    MemorialCapabilityNonAdmissionDisposition,
    MemorialCapabilityPurpose,
    MemorialCapabilitySubjectStatus,
    evaluate_memorial_capability_non_admission,
)
from app.services.owner_truth_memorial_conflict_hold_shadow import (
    MemorialConflictHoldClaims,
    MemorialConflictHoldContext,
    MemorialConflictHoldDisposition,
    MemorialConflictHoldScope,
    MemorialConflictHoldTrigger,
    observe_memorial_conflict_hold,
)
from app.services.owner_truth_memorial_controller_appointment_shadow import (
    MemorialControllerAppointmentClaims,
    MemorialControllerAppointmentCommandContext,
    MemorialControllerAppointmentCommandDisposition,
    plan_memorial_controller_appointment,
)
from app.services.owner_truth_memorial_controller_review_shadow import (
    MemorialControllerReviewCondition,
    MemorialControllerReviewContext,
    MemorialControllerReviewDisposition,
    observe_memorial_controller_review,
)
from app.services.owner_truth_memorial_family_contribution_shadow import (
    MemorialFamilyContributionClaims,
    MemorialFamilyContributionContext,
    MemorialFamilyContributionDisposition,
    MemorialFamilyContributionOperation,
    observe_memorial_family_contribution,
)


_PERSONA_ID = "39cb8c7d-31c0-4314-b813-2c01b5e2a7fb"
_VAULT_ID = "vault-memorial-composite-a"


class MemorialAuthorityCompositeGateTests(unittest.TestCase):
    def test_future_controller_transfer_does_not_activate_authority_or_capability(self) -> None:
        controller = plan_memorial_controller_appointment(
            {
                "commandId": "composite-controller-transfer-a",
                "expectedVersion": 2,
                "operation": "transfer",
            },
            context=MemorialControllerAppointmentCommandContext(
                vault_id=_VAULT_ID,
                represented_persona_id=_PERSONA_ID,
                actor_subject_id="current-controller",
                current_primary_controller_subject_id="current-controller",
                resolved_next_controller_subject_id="next-controller",
                current_appointment_version=2,
                authority_epoch=31,
            ),
            claims=MemorialControllerAppointmentClaims(
                actor_identity_verified=True,
                next_controller_identity_verified=True,
                death_and_kinship_verified=True,
                legal_policy_ready=True,
            ),
            enabled=True,
        )
        capability = evaluate_memorial_capability_non_admission(
            MemorialCapabilityNonAdmissionContext(
                vault_id=_VAULT_ID,
                represented_persona_id=_PERSONA_ID,
                represented_subject_status=MemorialCapabilitySubjectStatus.DECEASED,
                purpose=MemorialCapabilityPurpose.DIGITAL_HUMAN_PRIVATE,
                authority_epoch=31,
            ),
            claims=MemorialCapabilityNonAdmissionClaims(
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
            ),
            enabled=True,
        )

        self.assertEqual(
            controller.disposition,
            MemorialControllerAppointmentCommandDisposition.PLANNED_FOR_FUTURE_PERSISTENCE,
        )
        self.assertFalse(controller.records_written)
        self.assertFalse(controller.appointment_plan.authority_epoch_changed)
        self.assertEqual(
            capability.disposition,
            MemorialCapabilityNonAdmissionDisposition.EXTERNAL_M3_G2_G3_G4_REQUIRED,
        )
        self.assertFalse(capability.capability_admitted)
        self.assertFalse(capability.digital_human_session_allowed)
        self.assertFalse(capability.provider_effect_allowed)

    def test_controller_failure_rights_hold_and_family_role_remain_non_effecting(self) -> None:
        review = observe_memorial_controller_review(
            MemorialControllerReviewContext(
                vault_id=_VAULT_ID,
                represented_persona_id=_PERSONA_ID,
                primary_controller_condition=MemorialControllerReviewCondition.CONTROLLER_REVOKED,
                authority_epoch=31,
            ),
            enabled=True,
        )
        hold = observe_memorial_conflict_hold(
            MemorialConflictHoldContext(
                vault_id=_VAULT_ID,
                represented_persona_id=_PERSONA_ID,
                scope=MemorialConflictHoldScope.DIGITAL_HUMAN_PRIVATE,
                trigger=MemorialConflictHoldTrigger.VERIFIED_CLOSE_RELATIVE_RIGHTS_CLAIM,
                authority_epoch=31,
            ),
            claims=MemorialConflictHoldClaims(
                trigger_evidence_verified=True,
                scope_is_specific=True,
            ),
            enabled=True,
        )
        family = observe_memorial_family_contribution(
            MemorialFamilyContributionContext(
                vault_id=_VAULT_ID,
                represented_persona_id=_PERSONA_ID,
                contributor_subject_id="family-contributor",
                operation=MemorialFamilyContributionOperation.DIGITAL_HUMAN_PRIVATE,
                contribution_grant_version=1,
            ),
            claims=MemorialFamilyContributionClaims(
                active_contribution_grant=True,
                contributor_identity_verified=True,
                operation_is_own_contribution=True,
            ),
            enabled=True,
        )

        self.assertEqual(
            review.disposition,
            MemorialControllerReviewDisposition.CONTROLLER_REVIEW_REQUIRED,
        )
        self.assertFalse(review.publication_allowed)
        self.assertFalse(review.provider_effect_allowed)
        self.assertEqual(hold.disposition, MemorialConflictHoldDisposition.HOLD_REQUIRED)
        self.assertTrue(hold.conflict_hold_required)
        self.assertFalse(hold.authority_epoch_changed)
        self.assertFalse(hold.provider_generation_or_playback_stopped)
        self.assertEqual(
            family.disposition,
            MemorialFamilyContributionDisposition.OPERATION_NOT_ALLOWED,
        )
        self.assertFalse(family.controller_authority_granted)
        self.assertFalse(family.publication_or_high_risk_capability_allowed)


if __name__ == "__main__":
    unittest.main()
