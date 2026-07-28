"""G0 contracts for the C06 non-authoritative canary boundary."""

from __future__ import annotations

from hashlib import sha256
from unittest.mock import patch
import unittest

from app.domain.owner_truth.migration_canary_shadow import (
    MigrationCanaryCohortKind,
    MigrationCanaryDisposition,
    MigrationCanaryLane,
    MigrationCanaryRollbackPlane,
    OwnerTruthMigrationCanaryContext,
    OwnerTruthMigrationCanaryEvidence,
    OwnerTruthMigrationCanaryScope,
    plan_owner_truth_migration_canary_shadow,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(*, vault_id: str = "vault-canary-a", owner_id: str = "owner-canary-a", epoch: int = 4):
    return OwnerTruthMigrationCanaryContext(
        vault_id=vault_id,
        owner_subject_id=owner_id,
        authority_epoch=epoch,
    )


def _scope(
    *,
    vault_id: str = "vault-canary-a",
    owner_id: str = "owner-canary-a",
    epoch: int = 4,
    cohort_kind: MigrationCanaryCohortKind = MigrationCanaryCohortKind.INTERNAL_QA,
):
    return OwnerTruthMigrationCanaryScope(
        vault_id=vault_id,
        owner_subject_id=owner_id,
        authority_epoch=epoch,
        lane=MigrationCanaryLane.OWNER_TEXT_CORE,
        cohort_id="cohort-canary-a",
        cohort_kind=cohort_kind,
        policy_version="policy-v4-a",
        ios_build_hash=_digest("ios-build-a"),
        backend_build_hash=_digest("backend-build-a"),
        schema_head_hash=_digest("schema-head-a"),
        c04_tail_report_hash=_digest("c04-a"),
        c05_parity_report_hash=_digest("c05-a"),
    )


def _evidence():
    return OwnerTruthMigrationCanaryEvidence(
        threshold_set_id="threshold-set-a",
        observation_window_id="window-a",
        rollback_drill_plan_id="rollback-plan-a",
        max_recovery_time_reference="mrt-c06-a",
        evidence_bundle_id="evidence-bundle-a",
        approval_reference_hash=_digest("approval-a"),
    )


class OwnerTruthMigrationCanaryShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_read_or_validate_any_envelope(self) -> None:
        with patch(
            "app.domain.owner_truth.migration_canary_shadow.OwnerTruthMigrationCanaryScope.scope_hash"
        ) as scope_hash:
            result = plan_owner_truth_migration_canary_shadow(
                scope=object(),
                current_context=object(),
                evidence=object(),
            )

        scope_hash.assert_not_called()
        self.assertEqual(result.disposition, MigrationCanaryDisposition.SHADOW_DISABLED)
        self.assertFalse(result.canary_execution_allowed)
        self.assertFalse(result.authority_epoch_changed)
        self.assertFalse(result.legacy_writer_retired)

    def test_internal_plan_never_self_authorizes_execution(self) -> None:
        result = plan_owner_truth_migration_canary_shadow(
            scope=_scope(),
            current_context=_context(),
            evidence=_evidence(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MigrationCanaryDisposition.EXTERNAL_APPROVAL_REQUIRED,
        )
        self.assertFalse(result.canary_execution_allowed)
        self.assertFalse(result.authority_epoch_changed)
        self.assertFalse(result.legacy_writer_retired)
        summary = result.value_free_summary()
        self.assertFalse(summary["publicTrafficAllowed"])
        self.assertEqual(summary["providerCallCount"], 0)
        self.assertEqual(summary["objectOperationCount"], 0)
        self.assertEqual(summary["commandEffectExecutionCount"], 0)
        self.assertEqual(
            {item["plane"] for item in summary["rollbackPlanes"]},
            {plane.value for plane in MigrationCanaryRollbackPlane},
        )
        self.assertIn("nonAuthoritativeCanaryOnly", summary["reasonCodes"])
        rendered = str(summary)
        self.assertNotIn("vault-canary-a", rendered)
        self.assertNotIn("owner-canary-a", rendered)
        self.assertNotIn("cohort-canary-a", rendered)

    def test_public_cohort_is_rejected_before_any_external_approval_claim(self) -> None:
        result = plan_owner_truth_migration_canary_shadow(
            scope=_scope(cohort_kind=MigrationCanaryCohortKind.PUBLIC),
            current_context=_context(),
            evidence=_evidence(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MigrationCanaryDisposition.PUBLIC_EXPOSURE_REJECTED,
        )
        self.assertIn("internalQaCohortRequired", result.reason_codes)
        self.assertFalse(result.canary_execution_allowed)

    def test_scope_context_mismatch_fails_closed(self) -> None:
        epoch_mismatch = plan_owner_truth_migration_canary_shadow(
            scope=_scope(epoch=5),
            current_context=_context(epoch=4),
            evidence=_evidence(),
            enabled=True,
        )
        owner_mismatch = plan_owner_truth_migration_canary_shadow(
            scope=_scope(owner_id="owner-canary-b"),
            current_context=_context(),
            evidence=_evidence(),
            enabled=True,
        )

        self.assertEqual(epoch_mismatch.disposition, MigrationCanaryDisposition.CONTEXT_MISMATCH)
        self.assertIn("authorityEpochMismatch", epoch_mismatch.reason_codes)
        self.assertEqual(owner_mismatch.disposition, MigrationCanaryDisposition.CONTEXT_MISMATCH)
        self.assertIn("ownerMismatch", owner_mismatch.reason_codes)

    def test_invalid_envelope_fails_closed(self) -> None:
        result = plan_owner_truth_migration_canary_shadow(
            scope=object(),
            current_context=_context(),
            evidence=_evidence(),
            enabled=True,
        )

        self.assertEqual(result.disposition, MigrationCanaryDisposition.INVALID_ENVELOPE)
        self.assertFalse(result.canary_execution_allowed)
        self.assertEqual(result.scope_hash, None)


if __name__ == "__main__":
    unittest.main()
