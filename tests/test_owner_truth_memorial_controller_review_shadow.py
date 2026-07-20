"""G0 tests for the future Memorial controller-review boundary."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.owner_truth_memorial_controller_review_shadow import (
    MemorialControllerReviewCondition,
    MemorialControllerReviewContext,
    MemorialControllerReviewDisposition,
    observe_memorial_controller_review,
)


_PERSONA_ID = "39cb8c7d-31c0-4314-b813-2c01b5e2a7fb"


def _context(
    *,
    condition: MemorialControllerReviewCondition = MemorialControllerReviewCondition.ACTIVE,
    represented_login_subject_id: str | None = None,
) -> MemorialControllerReviewContext:
    return MemorialControllerReviewContext(
        vault_id="vault-controller-review-a",
        represented_persona_id=_PERSONA_ID,
        primary_controller_condition=condition,
        authority_epoch=11,
        represented_login_subject_id=represented_login_subject_id,
    )


class MemorialControllerReviewShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_context(self) -> None:
        with patch(
            "app.services.owner_truth_memorial_controller_review_shadow."
            "MemorialControllerReviewContext"
        ) as context_type:
            result = observe_memorial_controller_review(object())

        context_type.assert_not_called()
        self.assertEqual(result.disposition, MemorialControllerReviewDisposition.SHADOW_DISABLED)
        self.assertFalse(result.records_written)

    def test_active_controller_requires_no_review_or_side_effect(self) -> None:
        result = observe_memorial_controller_review(_context(), enabled=True)

        self.assertEqual(result.disposition, MemorialControllerReviewDisposition.REVIEW_NOT_REQUIRED)
        self.assertFalse(result.controller_review_required)
        self.assertFalse(result.records_written)
        self.assertFalse(result.vault_state_written)
        self.assertFalse(result.authority_epoch_changed)
        self.assertFalse(result.controller_appointment_activated)
        self.assertFalse(result.publication_allowed)
        self.assertFalse(result.provider_effect_allowed)

    def test_each_non_active_condition_fails_closed_to_review_scope_only(self) -> None:
        for condition in (
            MemorialControllerReviewCondition.CONTROLLER_UNREACHABLE,
            MemorialControllerReviewCondition.CONTROLLER_DECEASED,
            MemorialControllerReviewCondition.CONTROLLER_REVOKED,
            MemorialControllerReviewCondition.ACCOUNT_RECOVERY_FAILED,
            MemorialControllerReviewCondition.UNKNOWN,
        ):
            with self.subTest(condition=condition):
                result = observe_memorial_controller_review(
                    _context(condition=condition),
                    enabled=True,
                )
                summary = result.value_free_summary()

                self.assertEqual(
                    result.disposition,
                    MemorialControllerReviewDisposition.CONTROLLER_REVIEW_REQUIRED,
                )
                self.assertTrue(result.controller_review_required)
                self.assertEqual(
                    result.review_scope_categories,
                    ("minimum_operations", "necessary_preservation", "rights_request"),
                )
                self.assertEqual(result.blocked_categories, ("provider_effect", "publication"))
                self.assertFalse(result.records_written)
                self.assertFalse(result.vault_state_written)
                self.assertFalse(result.authority_epoch_changed)
                self.assertFalse(result.controller_appointment_activated)
                self.assertFalse(result.publication_allowed)
                self.assertFalse(result.provider_effect_allowed)
                self.assertFalse(result.provider_or_runtime_mutated)
                self.assertTrue(summary["shadowOnly"])
                self.assertEqual(summary["capturedAuthorityEpoch"], 11)

    def test_represented_login_principal_and_invalid_context_fail_closed(self) -> None:
        represented_login = observe_memorial_controller_review(
            _context(represented_login_subject_id="invalid-deceased-principal"),
            enabled=True,
        )
        invalid_context = observe_memorial_controller_review(object(), enabled=True)

        self.assertEqual(
            represented_login.disposition,
            MemorialControllerReviewDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN,
        )
        self.assertEqual(
            invalid_context.disposition,
            MemorialControllerReviewDisposition.INVALID_CONTEXT,
        )
        self.assertFalse(represented_login.records_written)
        self.assertFalse(invalid_context.records_written)

    def test_summary_never_leaks_vault_or_persona_identifiers(self) -> None:
        result = observe_memorial_controller_review(
            _context(condition=MemorialControllerReviewCondition.CONTROLLER_REVOKED),
            enabled=True,
        )
        rendered = repr(result.value_free_summary())

        self.assertNotIn("vault-controller-review-a", rendered)
        self.assertNotIn(_PERSONA_ID, rendered)
        self.assertIn("scopeHash", rendered)


if __name__ == "__main__":
    unittest.main()
