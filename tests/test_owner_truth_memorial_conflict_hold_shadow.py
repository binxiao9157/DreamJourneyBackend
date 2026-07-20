"""G0 tests for future Memorial rights claims and ConflictHold non-admission."""

from __future__ import annotations

from unittest.mock import patch
import unittest

from app.services.owner_truth_memorial_conflict_hold_shadow import (
    MemorialConflictHoldClaims,
    MemorialConflictHoldContext,
    MemorialConflictHoldDisposition,
    MemorialConflictHoldScope,
    MemorialConflictHoldTrigger,
    observe_memorial_conflict_hold,
)


_PERSONA_ID = "39cb8c7d-31c0-4314-b813-2c01b5e2a7fb"


def _context(
    *,
    scope: MemorialConflictHoldScope = MemorialConflictHoldScope.DIGITAL_HUMAN_PRIVATE,
    trigger: MemorialConflictHoldTrigger = (
        MemorialConflictHoldTrigger.VERIFIED_CLOSE_RELATIVE_RIGHTS_CLAIM
    ),
    represented_login_subject_id: str | None = None,
) -> MemorialConflictHoldContext:
    return MemorialConflictHoldContext(
        vault_id="vault-conflict-hold-a",
        represented_persona_id=_PERSONA_ID,
        scope=scope,
        trigger=trigger,
        authority_epoch=19,
        represented_login_subject_id=represented_login_subject_id,
    )


def _claims() -> MemorialConflictHoldClaims:
    return MemorialConflictHoldClaims(
        trigger_evidence_verified=True,
        scope_is_specific=True,
    )


class MemorialConflictHoldShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_context_or_claims(self) -> None:
        with patch(
            "app.services.owner_truth_memorial_conflict_hold_shadow."
            "MemorialConflictHoldContext"
        ) as context_type:
            result = observe_memorial_conflict_hold(object(), claims=object())

        context_type.assert_not_called()
        self.assertEqual(result.disposition, MemorialConflictHoldDisposition.SHADOW_DISABLED)
        self.assertFalse(result.records_written)

    def test_missing_proof_or_scope_requires_verification_without_a_hold(self) -> None:
        result = observe_memorial_conflict_hold(_context(), enabled=True)

        self.assertEqual(result.disposition, MemorialConflictHoldDisposition.VERIFICATION_REQUIRED)
        self.assertFalse(result.conflict_hold_required)
        self.assertFalse(result.conflict_hold_written)
        self.assertFalse(result.authority_epoch_changed)

    def test_verified_triggers_require_future_atomic_hold_without_any_effect(self) -> None:
        for trigger in MemorialConflictHoldTrigger:
            with self.subTest(trigger=trigger):
                result = observe_memorial_conflict_hold(
                    _context(trigger=trigger),
                    claims=_claims(),
                    enabled=True,
                )
                summary = result.value_free_summary()

                self.assertEqual(result.disposition, MemorialConflictHoldDisposition.HOLD_REQUIRED)
                self.assertTrue(result.conflict_hold_required)
                self.assertTrue(summary["authorityEpochIncrementRequired"])
                self.assertFalse(result.records_written)
                self.assertFalse(result.conflict_hold_written)
                self.assertFalse(result.authority_epoch_changed)
                self.assertFalse(result.affected_capability_suspended)
                self.assertFalse(result.publication_blocked)
                self.assertFalse(result.provider_generation_or_playback_stopped)
                self.assertFalse(result.provider_cleanup_executed)

    def test_each_scope_is_independent_and_summary_is_value_free(self) -> None:
        voice = observe_memorial_conflict_hold(
            _context(scope=MemorialConflictHoldScope.VOICE_TRAINING),
            claims=_claims(),
            enabled=True,
        )
        digital_human = observe_memorial_conflict_hold(
            _context(scope=MemorialConflictHoldScope.DIGITAL_HUMAN_PRIVATE),
            claims=_claims(),
            enabled=True,
        )
        rendered = repr(digital_human.value_free_summary())

        self.assertNotEqual(voice.scope_hash, digital_human.scope_hash)
        self.assertNotIn("vault-conflict-hold-a", rendered)
        self.assertNotIn(_PERSONA_ID, rendered)
        self.assertIn("scopeHash", rendered)

    def test_represented_login_and_invalid_context_fail_closed(self) -> None:
        represented_login = observe_memorial_conflict_hold(
            _context(represented_login_subject_id="invalid-deceased-principal"),
            claims=_claims(),
            enabled=True,
        )
        invalid = observe_memorial_conflict_hold(object(), enabled=True)

        self.assertEqual(
            represented_login.disposition,
            MemorialConflictHoldDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN,
        )
        self.assertEqual(invalid.disposition, MemorialConflictHoldDisposition.INVALID_CONTEXT)
        self.assertFalse(represented_login.records_written)
        self.assertFalse(invalid.records_written)


if __name__ == "__main__":
    unittest.main()
