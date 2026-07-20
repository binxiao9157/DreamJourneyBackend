"""G0 contracts for future Memorial Persona authority admission."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.owner_truth_memorial_authority_admission_shadow import (
    MemorialPersonaAuthorityAdmissionClaims,
    MemorialPersonaAuthorityAdmissionContext,
    MemorialPersonaAuthorityAdmissionDisposition,
    MemorialPersonaAuthorityCommandOrigin,
    observe_memorial_persona_authority_admission,
)


_PERSONA_ID = "39cb8c7d-31c0-4314-b813-2c01b5e2a7fb"


def _context(
    *,
    origin: MemorialPersonaAuthorityCommandOrigin = (
        MemorialPersonaAuthorityCommandOrigin.MEMORIAL_CONTROLLER_INTERACTIVE
    ),
    represented_login_subject_id: str | None = None,
) -> MemorialPersonaAuthorityAdmissionContext:
    return MemorialPersonaAuthorityAdmissionContext(
        vault_id="vault-memorial-authority-a",
        represented_persona_id=_PERSONA_ID,
        actor_subject_id="memorial-controller-a",
        represented_login_subject_id=represented_login_subject_id,
        command_origin=origin,
    )


def _all_claims() -> MemorialPersonaAuthorityAdmissionClaims:
    return MemorialPersonaAuthorityAdmissionClaims(
        controller_appointment_verified=True,
        death_and_kinship_verified=True,
        legal_policy_ready=True,
        rights_claim_active=False,
        conflict_hold_active=False,
    )


class MemorialPersonaAuthorityAdmissionShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_context_or_claims(self) -> None:
        with patch(
            "app.services.owner_truth_memorial_authority_admission_shadow."
            "MemorialPersonaAuthorityAdmissionClaims"
        ) as claims_type:
            result = observe_memorial_persona_authority_admission(
                object(),
                claims=object(),
            )

        claims_type.assert_not_called()
        self.assertEqual(
            result.disposition,
            MemorialPersonaAuthorityAdmissionDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.memorial_authority_admitted)
        self.assertFalse(result.records_written)

    def test_represented_persona_cannot_be_a_login_principal(self) -> None:
        result = observe_memorial_persona_authority_admission(
            _context(represented_login_subject_id="deceased-login-subject"),
            claims=_all_claims(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MemorialPersonaAuthorityAdmissionDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN,
        )
        self.assertFalse(result.memorial_authority_admitted)
        self.assertIn("representedPersonaCannotBeLoginPrincipal", result.reason_codes)

    def test_family_assistant_provider_and_runtime_cannot_write_memorial_persona(self) -> None:
        expected_reason = {
            MemorialPersonaAuthorityCommandOrigin.FAMILY_CONTRIBUTOR: (
                "familyContributorMayOnlySubmitSourceOrCandidate"
            ),
            MemorialPersonaAuthorityCommandOrigin.ASSISTANT: "assistantCannotWriteMemorialPersona",
            MemorialPersonaAuthorityCommandOrigin.PROVIDER: "providerCannotWriteMemorialPersona",
            MemorialPersonaAuthorityCommandOrigin.RUNTIME: "runtimeCannotWriteMemorialPersona",
            MemorialPersonaAuthorityCommandOrigin.UNKNOWN: "unknownOriginCannotWriteMemorialPersona",
        }
        for origin, reason_code in expected_reason.items():
            with self.subTest(origin=origin.value):
                result = observe_memorial_persona_authority_admission(
                    _context(origin=origin),
                    claims=_all_claims(),
                    enabled=True,
                )

                self.assertEqual(
                    result.disposition,
                    MemorialPersonaAuthorityAdmissionDisposition.ORIGIN_NOT_ALLOWED,
                )
                self.assertFalse(result.memorial_authority_admitted)
                self.assertIn(reason_code, result.reason_codes)

    def test_missing_verification_rights_claim_or_conflict_hold_fails_closed(self) -> None:
        missing_verification = observe_memorial_persona_authority_admission(
            _context(),
            claims=MemorialPersonaAuthorityAdmissionClaims(),
            enabled=True,
        )
        rights_claim = observe_memorial_persona_authority_admission(
            _context(),
            claims=MemorialPersonaAuthorityAdmissionClaims(
                controller_appointment_verified=True,
                death_and_kinship_verified=True,
                legal_policy_ready=True,
                rights_claim_active=True,
            ),
            enabled=True,
        )
        conflict_hold = observe_memorial_persona_authority_admission(
            _context(),
            claims=MemorialPersonaAuthorityAdmissionClaims(
                controller_appointment_verified=True,
                death_and_kinship_verified=True,
                legal_policy_ready=True,
                conflict_hold_active=True,
            ),
            enabled=True,
        )

        self.assertEqual(
            missing_verification.disposition,
            MemorialPersonaAuthorityAdmissionDisposition.VERIFICATION_REQUIRED,
        )
        self.assertIn("verifiedControllerAppointmentRequired", missing_verification.reason_codes)
        self.assertEqual(
            rights_claim.disposition,
            MemorialPersonaAuthorityAdmissionDisposition.RIGHTS_OR_CONFLICT_HOLD,
        )
        self.assertIn("activeRightsClaimBlocksMemorialAuthorityMutation", rights_claim.reason_codes)
        self.assertEqual(
            conflict_hold.disposition,
            MemorialPersonaAuthorityAdmissionDisposition.RIGHTS_OR_CONFLICT_HOLD,
        )
        self.assertIn("activeConflictHoldBlocksMemorialAuthorityMutation", conflict_hold.reason_codes)

    def test_all_synthetic_claims_still_require_independent_g2_and_g4_evidence(self) -> None:
        result = observe_memorial_persona_authority_admission(
            _context(),
            claims=_all_claims(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MemorialPersonaAuthorityAdmissionDisposition.EXTERNAL_G2_G4_REQUIRED,
        )
        self.assertFalse(result.memorial_authority_admitted)
        self.assertFalse(result.records_written)
        self.assertFalse(result.persona_version_written)
        self.assertFalse(result.controller_appointment_written)
        self.assertFalse(result.provider_or_runtime_mutated)
        self.assertIn("shadowMemorialClaimsCannotAuthorizeAuthority", result.reason_codes)

        summary = result.value_free_summary()
        self.assertTrue(summary["shadowOnly"])
        self.assertEqual(summary["requiredExternalGates"], ["G2", "G4"])
        self.assertNotIn("vault-memorial-authority-a", repr(summary))
        self.assertNotIn("memorial-controller-a", repr(summary))
        self.assertNotIn(_PERSONA_ID, repr(summary))


if __name__ == "__main__":
    unittest.main()
