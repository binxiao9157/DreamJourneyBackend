"""G0 tests for scoped Memorial Family Contributor non-admission."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.owner_truth_memorial_family_contribution_shadow import (
    MemorialFamilyContributionClaims,
    MemorialFamilyContributionContext,
    MemorialFamilyContributionDisposition,
    MemorialFamilyContributionOperation,
    observe_memorial_family_contribution,
)


_PERSONA_ID = "39cb8c7d-31c0-4314-b813-2c01b5e2a7fb"


def _context(
    *,
    operation: MemorialFamilyContributionOperation = (
        MemorialFamilyContributionOperation.SUBMIT_SOURCE
    ),
    represented_login_subject_id: str | None = None,
) -> MemorialFamilyContributionContext:
    return MemorialFamilyContributionContext(
        vault_id="vault-family-contribution-a",
        represented_persona_id=_PERSONA_ID,
        contributor_subject_id="family-contributor-a",
        operation=operation,
        contribution_grant_version=4,
        represented_login_subject_id=represented_login_subject_id,
    )


def _claims(*, own: bool = True) -> MemorialFamilyContributionClaims:
    return MemorialFamilyContributionClaims(
        active_contribution_grant=True,
        contributor_identity_verified=True,
        operation_is_own_contribution=own,
    )


class MemorialFamilyContributionShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_context_or_claims(self) -> None:
        with patch(
            "app.services.owner_truth_memorial_family_contribution_shadow."
            "MemorialFamilyContributionContext"
        ) as context_type:
            result = observe_memorial_family_contribution(object(), claims=object())

        context_type.assert_not_called()
        self.assertEqual(result.disposition, MemorialFamilyContributionDisposition.SHADOW_DISABLED)
        self.assertFalse(result.records_written)

    def test_permitted_operations_require_scoped_grant_and_identity(self) -> None:
        for operation in (
            MemorialFamilyContributionOperation.SUBMIT_SOURCE,
            MemorialFamilyContributionOperation.SUBMIT_CANDIDATE,
            MemorialFamilyContributionOperation.WITHDRAW_OWN_CONTRIBUTION,
        ):
            with self.subTest(operation=operation):
                result = observe_memorial_family_contribution(
                    _context(operation=operation),
                    enabled=True,
                )
                self.assertEqual(
                    result.disposition,
                    MemorialFamilyContributionDisposition.CONTRIBUTION_GRANT_REQUIRED,
                )
                self.assertFalse(result.contribution_admitted)
                self.assertFalse(result.records_written)

    def test_forbidden_operations_never_upgrade_family_to_authority(self) -> None:
        for operation in (
            MemorialFamilyContributionOperation.CONFIRM_MEMORY,
            MemorialFamilyContributionOperation.PRIVATE_QUERY,
            MemorialFamilyContributionOperation.PUBLICATION,
            MemorialFamilyContributionOperation.VOICE_TRAINING,
            MemorialFamilyContributionOperation.DIGITAL_HUMAN_PRIVATE,
            MemorialFamilyContributionOperation.CONTROLLER_APPOINTMENT,
        ):
            with self.subTest(operation=operation):
                result = observe_memorial_family_contribution(
                    _context(operation=operation),
                    claims=_claims(),
                    enabled=True,
                )
                self.assertEqual(
                    result.disposition,
                    MemorialFamilyContributionDisposition.OPERATION_NOT_ALLOWED,
                )
                self.assertFalse(result.private_query_allowed)
                self.assertFalse(result.publication_or_high_risk_capability_allowed)
                self.assertFalse(result.controller_authority_granted)

    def test_complete_synthetic_contribution_remains_external_and_zero_write(self) -> None:
        result = observe_memorial_family_contribution(
            _context(operation=MemorialFamilyContributionOperation.WITHDRAW_OWN_CONTRIBUTION),
            claims=_claims(own=True),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MemorialFamilyContributionDisposition.EXTERNAL_G2_G4_REQUIRED,
        )
        self.assertFalse(result.contribution_admitted)
        self.assertFalse(result.contribution_grant_written)
        self.assertFalse(result.source_or_candidate_written)
        self.assertFalse(result.confirmed_memory_written)
        self.assertFalse(result.records_written)

    def test_withdrawal_must_be_own_and_summary_is_value_free(self) -> None:
        denied = observe_memorial_family_contribution(
            _context(operation=MemorialFamilyContributionOperation.WITHDRAW_OWN_CONTRIBUTION),
            claims=_claims(own=False),
            enabled=True,
        )
        source = observe_memorial_family_contribution(
            _context(operation=MemorialFamilyContributionOperation.SUBMIT_SOURCE),
            claims=_claims(),
            enabled=True,
        )
        candidate = observe_memorial_family_contribution(
            _context(operation=MemorialFamilyContributionOperation.SUBMIT_CANDIDATE),
            claims=_claims(),
            enabled=True,
        )
        rendered = repr(candidate.value_free_summary())

        self.assertIn("contributorMayWithdrawOnlyOwnContribution", denied.reason_codes)
        self.assertNotEqual(source.scope_hash, candidate.scope_hash)
        self.assertNotIn("vault-family-contribution-a", rendered)
        self.assertNotIn("family-contributor-a", rendered)
        self.assertNotIn(_PERSONA_ID, rendered)

    def test_represented_login_and_invalid_context_fail_closed(self) -> None:
        represented_login = observe_memorial_family_contribution(
            _context(represented_login_subject_id="invalid-deceased-principal"),
            claims=_claims(),
            enabled=True,
        )
        invalid = observe_memorial_family_contribution(object(), enabled=True)

        self.assertEqual(
            represented_login.disposition,
            MemorialFamilyContributionDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN,
        )
        self.assertEqual(invalid.disposition, MemorialFamilyContributionDisposition.INVALID_CONTEXT)
        self.assertFalse(represented_login.records_written)
        self.assertFalse(invalid.records_written)


if __name__ == "__main__":
    unittest.main()
