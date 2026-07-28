"""G0 contracts for C08 independent lane activation planning."""

from __future__ import annotations

from hashlib import sha256
import unittest

from app.domain.owner_truth.migration_lane_activation_shadow import (
    MigrationActivationLane,
    MigrationLaneActivationDisposition,
    MigrationLaneCohortKind,
    OwnerTruthMigrationLaneActivationContext,
    OwnerTruthMigrationLaneActivationScope,
    OwnerTruthMigrationLaneReadinessEvidence,
    plan_owner_truth_migration_lane_activation_shadow,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(*, vault_id: str = "vault-lane-a", owner_id: str = "owner-lane-a", epoch: int = 7):
    return OwnerTruthMigrationLaneActivationContext(
        vault_id=vault_id,
        owner_subject_id=owner_id,
        authority_epoch=epoch,
    )


def _scope(
    *,
    lane: MigrationActivationLane = MigrationActivationLane.PROJECTION,
    cohort_kind: MigrationLaneCohortKind = MigrationLaneCohortKind.INTERNAL_QA,
    vault_id: str = "vault-lane-a",
    owner_id: str = "owner-lane-a",
    epoch: int = 7,
):
    return OwnerTruthMigrationLaneActivationScope(
        vault_id=vault_id,
        owner_subject_id=owner_id,
        authority_epoch=epoch,
        lane=lane,
        cohort_kind=cohort_kind,
        cohort_reference_hash=_digest("cohort-lane-a"),
        policy_version="policy-v4-lane-a",
        c07_admission_reference_hash=_digest("c07-admission-a"),
    )


def _evidence():
    return OwnerTruthMigrationLaneReadinessEvidence(
        compatibility_receipt_hash=_digest("compatibility-a"),
        rollback_plan_hash=_digest("rollback-a"),
        readiness_report_hash=_digest("readiness-a"),
    )


class OwnerTruthMigrationLaneActivationShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_require_valid_inputs(self) -> None:
        result = plan_owner_truth_migration_lane_activation_shadow(
            scope=object(),
            current_context=object(),
            evidence=object(),
        )

        self.assertEqual(
            result.disposition,
            MigrationLaneActivationDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.lane_activation_allowed)
        self.assertFalse(result.global_activation_allowed)

    def test_valid_single_lane_never_activates_or_leaks_scope_values(self) -> None:
        result = plan_owner_truth_migration_lane_activation_shadow(
            scope=_scope(),
            current_context=_context(),
            evidence=_evidence(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MigrationLaneActivationDisposition.EXTERNAL_READINESS_REQUIRED,
        )
        self.assertFalse(result.lane_activation_allowed)
        self.assertFalse(result.global_activation_allowed)
        self.assertFalse(result.authority_epoch_changed)
        self.assertFalse(result.worker_or_provider_started)
        self.assertFalse(result.object_reference_promoted)
        summary = result.value_free_summary()
        self.assertEqual(summary["laneCount"], 1)
        self.assertEqual(summary["lane"], "projection")
        self.assertIn("G2", summary["requiredExternalGates"])
        self.assertIn("restoreCompatibilityRead", summary["rollbackFenceActionCodes"])
        rendered = str(summary)
        self.assertNotIn("vault-lane-a", rendered)
        self.assertNotIn("owner-lane-a", rendered)
        self.assertNotIn("cohort-lane-a", rendered)

    def test_public_cohort_is_rejected_before_any_lane_plan(self) -> None:
        result = plan_owner_truth_migration_lane_activation_shadow(
            scope=_scope(cohort_kind=MigrationLaneCohortKind.PUBLIC),
            current_context=_context(),
            evidence=_evidence(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            MigrationLaneActivationDisposition.PUBLIC_EXPOSURE_REJECTED,
        )
        self.assertIn("publicActivationForbidden", result.reason_codes)
        self.assertFalse(result.lane_activation_allowed)

    def test_context_mismatch_fails_closed(self) -> None:
        epoch_mismatch = plan_owner_truth_migration_lane_activation_shadow(
            scope=_scope(),
            current_context=_context(epoch=8),
            evidence=_evidence(),
            enabled=True,
        )
        owner_mismatch = plan_owner_truth_migration_lane_activation_shadow(
            scope=_scope(),
            current_context=_context(owner_id="owner-lane-b"),
            evidence=_evidence(),
            enabled=True,
        )

        self.assertEqual(
            epoch_mismatch.disposition,
            MigrationLaneActivationDisposition.CONTEXT_MISMATCH,
        )
        self.assertIn("authorityEpochMismatch", epoch_mismatch.reason_codes)
        self.assertEqual(
            owner_mismatch.disposition,
            MigrationLaneActivationDisposition.CONTEXT_MISMATCH,
        )
        self.assertIn("ownerMismatch", owner_mismatch.reason_codes)

    def test_optional_provider_has_its_own_provider_gate_and_fence(self) -> None:
        result = plan_owner_truth_migration_lane_activation_shadow(
            scope=_scope(lane=MigrationActivationLane.OPTIONAL_PROVIDER),
            current_context=_context(),
            evidence=_evidence(),
            enabled=True,
        )

        summary = result.value_free_summary()
        self.assertEqual(summary["lane"], "optionalProvider")
        self.assertIn("G3", summary["requiredExternalGates"])
        self.assertIn("pauseProviderRequests", summary["rollbackFenceActionCodes"])

    def test_every_lane_has_exactly_one_independent_fence(self) -> None:
        for lane in MigrationActivationLane:
            result = plan_owner_truth_migration_lane_activation_shadow(
                scope=_scope(lane=lane),
                current_context=_context(),
                evidence=_evidence(),
                enabled=True,
            )
            summary = result.value_free_summary()
            self.assertEqual(summary["laneCount"], 1)
            self.assertEqual(summary["lane"], lane.value)
            self.assertGreaterEqual(len(summary["rollbackFenceActionCodes"]), 3)


if __name__ == "__main__":
    unittest.main()
