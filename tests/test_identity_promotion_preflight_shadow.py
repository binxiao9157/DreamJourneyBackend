"""G0 tests for the fail-closed legacy identity promotion preflight."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256
from unittest.mock import patch
import unittest

from app.services.identity_promotion_preflight_shadow import (
    IdentityPromotionDecision,
    IdentityPromotionPreflightContext,
    IdentityPromotionPreflightContractError,
    IdentityPromotionPreflightDisposition,
    IdentityPromotionPrincipalSource,
    IdentityPromotionSessionState,
    LegacyAliasClaim,
    LegacyAliasClaimState,
    observe_identity_promotion_preflight,
)


def _digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _claim(
    *,
    state: LegacyAliasClaimState = LegacyAliasClaimState.EXPLICIT_OWNER_CLAIM,
    subject_id: str | None = "subject-owner-a",
) -> LegacyAliasClaim:
    if state is not LegacyAliasClaimState.EXPLICIT_OWNER_CLAIM:
        return LegacyAliasClaim(
            legacy_alias_hash=_digest(f"alias:{state.value}"),
            state=state,
        )
    return LegacyAliasClaim(
        legacy_alias_hash=_digest("alias:owner-a"),
        state=state,
        explicit_claim_subject_id=subject_id,
        explicit_claim_evidence_hash=_digest("claim-proof:owner-a"),
    )


def _context(**changes: object) -> IdentityPromotionPreflightContext:
    values: dict[str, object] = {
        "vault_id": "vault-owner-a",
        "server_subject_id": "subject-owner-a",
        "session_subject_id": "subject-owner-a",
        "resource_owner_subject_id": "subject-owner-a",
        "payload_owner_subject_id": "subject-owner-a",
        "session_vault_id": "vault-owner-a",
        "resource_vault_id": "vault-owner-a",
        "payload_vault_id": "vault-owner-a",
        "account_generation": "lease-generation-a",
        "session_account_generation": "lease-generation-a",
        "session_state": IdentityPromotionSessionState.ACTIVE,
        "principal_source": IdentityPromotionPrincipalSource.SERVER_DERIVED,
        "route_decision": IdentityPromotionDecision.ALLOW,
        "resource_decision": IdentityPromotionDecision.ALLOW,
        "route_policy_version": "route-policy-v4",
        "route_evidence_hash": _digest("route-evidence:a"),
        "resource_evidence_hash": _digest("resource-evidence:a"),
    }
    values.update(changes)
    return IdentityPromotionPreflightContext(**values)  # type: ignore[arg-type]


class IdentityPromotionPreflightShadowTests(unittest.TestCase):
    def _observe(self, *, claim: object | None = None, context: object | None = None):
        return observe_identity_promotion_preflight(
            _claim() if claim is None else claim,
            context=_context() if context is None else context,
            enabled=True,
        )

    def test_disabled_path_does_not_validate_or_hash_input(self) -> None:
        with patch(
            "app.services.identity_promotion_preflight_shadow._scope_hash"
        ) as scope_hash:
            result = observe_identity_promotion_preflight(
                object(),
                context=object(),
            )

        scope_hash.assert_not_called()
        self.assertEqual(
            result.disposition,
            IdentityPromotionPreflightDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.promotion_written)
        self.assertFalse(result.alias_claim_committed)

    def test_verified_owner_envelope_is_only_shadow_eligible(self) -> None:
        result = self._observe()

        self.assertEqual(
            result.disposition,
            IdentityPromotionPreflightDisposition.SHADOW_ELIGIBLE,
        )
        self.assertTrue(result.would_be_eligible_for_future_promotion)
        self.assertFalse(result.promotion_written)
        self.assertFalse(result.alias_claim_committed)
        self.assertFalse(result.session_issued)
        self.assertFalse(result.route_policy_changed)
        self.assertFalse(result.visitor_enumeration_allowed)
        self.assertFalse(result.cutover_allowed)

        summary = result.value_free_summary()
        self.assertTrue(summary["shadowOnly"])
        self.assertFalse(summary["promotionWritten"])
        self.assertTrue(summary["wouldBeEligibleForFuturePromotion"])
        self.assertNotIn("subject-owner-a", repr(summary))
        self.assertNotIn("vault-owner-a", repr(summary))
        self.assertNotIn("lease-generation-a", repr(summary))

    def test_same_claim_and_context_produce_stable_value_free_evidence(self) -> None:
        first = self._observe()
        second = self._observe()

        self.assertEqual(first.alias_hash, second.alias_hash)
        self.assertEqual(first.scope_hash, second.scope_hash)
        self.assertEqual(first.evidence_hash, second.evidence_hash)
        with self.assertRaises(FrozenInstanceError):
            first.enabled = False  # type: ignore[misc]

    def test_shared_alias_is_quarantined_without_automatic_claim(self) -> None:
        result = self._observe(claim=_claim(state=LegacyAliasClaimState.SHARED))

        self.assertEqual(
            result.disposition,
            IdentityPromotionPreflightDisposition.QUARANTINED,
        )
        self.assertIn("legacyAliasShared", result.reason_codes)
        self.assertIn("explicitOwnerClaimRequired", result.reason_codes)
        self.assertFalse(result.would_be_eligible_for_future_promotion)
        self.assertFalse(result.alias_claim_committed)

    def test_unknown_and_pending_aliases_never_authorize_login_or_enumeration(self) -> None:
        for state in (
            LegacyAliasClaimState.UNKNOWN,
            LegacyAliasClaimState.CLAIM_PENDING,
            LegacyAliasClaimState.QUARANTINED,
        ):
            with self.subTest(state=state):
                result = self._observe(claim=_claim(state=state))
                self.assertIn(
                    result.disposition,
                    {
                        IdentityPromotionPreflightDisposition.CLAIM_PENDING,
                        IdentityPromotionPreflightDisposition.QUARANTINED,
                    },
                )
                self.assertFalse(result.session_issued)
                self.assertFalse(result.visitor_enumeration_allowed)
                self.assertFalse(result.alias_claim_committed)

    def test_cross_vault_payload_owner_and_generation_mismatches_fail_closed(self) -> None:
        result = self._observe(
            context=_context(
                resource_vault_id="vault-owner-b",
                payload_owner_subject_id="subject-owner-b",
                session_account_generation="lease-generation-b",
            )
        )

        self.assertEqual(
            result.disposition,
            IdentityPromotionPreflightDisposition.DENIED,
        )
        self.assertIn("resourceVaultMismatch", result.reason_codes)
        self.assertIn("payloadOwnerSubjectMismatch", result.reason_codes)
        self.assertIn("accountGenerationMismatch", result.reason_codes)
        self.assertFalse(result.would_be_eligible_for_future_promotion)

    def test_stale_or_revoked_refresh_session_fails_closed(self) -> None:
        for session_state in (
            IdentityPromotionSessionState.STALE,
            IdentityPromotionSessionState.REVOKED,
            IdentityPromotionSessionState.MISSING,
        ):
            with self.subTest(session_state=session_state):
                result = self._observe(context=_context(session_state=session_state))
                self.assertEqual(
                    result.disposition,
                    IdentityPromotionPreflightDisposition.DENIED,
                )
                self.assertIn("sessionNotActive", result.reason_codes)

    def test_payload_principal_or_denied_route_cannot_be_promoted(self) -> None:
        result = self._observe(
            context=_context(
                principal_source=IdentityPromotionPrincipalSource.PAYLOAD,
                route_decision=IdentityPromotionDecision.DENY,
                resource_decision=IdentityPromotionDecision.DENY,
            )
        )

        self.assertEqual(
            result.disposition,
            IdentityPromotionPreflightDisposition.DENIED,
        )
        self.assertIn("principalNotServerDerived", result.reason_codes)
        self.assertIn("routeDecisionDenied", result.reason_codes)
        self.assertIn("resourceDecisionDenied", result.reason_codes)

    def test_claim_subject_mismatch_fails_closed(self) -> None:
        result = self._observe(claim=_claim(subject_id="subject-owner-b"))

        self.assertEqual(
            result.disposition,
            IdentityPromotionPreflightDisposition.DENIED,
        )
        self.assertIn("explicitClaimSubjectMismatch", result.reason_codes)

    def test_invalid_envelope_and_unredacted_alias_are_rejected(self) -> None:
        invalid = self._observe(claim=object())
        self.assertEqual(
            invalid.disposition,
            IdentityPromotionPreflightDisposition.INVALID_ENVELOPE,
        )
        with self.assertRaises(IdentityPromotionPreflightContractError):
            LegacyAliasClaim(
                legacy_alias_hash="13800138000",
                state=LegacyAliasClaimState.UNKNOWN,
            )


if __name__ == "__main__":
    unittest.main()
