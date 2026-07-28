"""C10 retirement candidate shadow tests; no live process or deletion work."""

from __future__ import annotations

from hashlib import sha256
import unittest

from app.domain.owner_truth.migration_retirement_candidate_shadow import (
    EvidenceVerificationState,
    InFlightObservation,
    MinimumClientObservation,
    OwnerTruthRetirementCandidateEvidence,
    OwnerTruthRetirementCandidateScope,
    RetirementCandidateDisposition,
    RetirementCandidateLifecycleState,
    RetirementSurfaceKind,
    RuntimeUsageObservation,
    plan_owner_truth_migration_retirement_candidate_shadow,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _scope(
    *,
    kind: RetirementSurfaceKind = RetirementSurfaceKind.TIMER,
    reference: str = "legacy-time-letter-dispatch",
) -> OwnerTruthRetirementCandidateScope:
    return OwnerTruthRetirementCandidateScope(
        surface_kind=kind,
        surface_reference=reference,
        source_inventory_hash=_digest("c10-source-inventory"),
        policy_version="c10-shadow-v1",
    )


def _evidence(**overrides: object) -> OwnerTruthRetirementCandidateEvidence:
    values: dict[str, object] = {
        "zero_use_window_hash": _digest("c10-zero-use-window"),
        "runtime_usage": RuntimeUsageObservation.ZERO,
        "runtime_use_count": 0,
        "in_flight": InFlightObservation.TERMINAL,
        "minimum_client": MinimumClientObservation.ZERO_OLD_CLIENTS,
        "restore_replay": EvidenceVerificationState.VERIFIED,
        "receipt": EvidenceVerificationState.VERIFIED,
        "owner": EvidenceVerificationState.VERIFIED,
        "evidence_bundle_hash": _digest("c10-evidence-bundle"),
        "approver_hashes": (_digest("security-owner"),),
    }
    values.update(overrides)
    return OwnerTruthRetirementCandidateEvidence(**values)


class OwnerTruthMigrationRetirementCandidateShadowTests(unittest.TestCase):
    def test_disabled_mode_does_not_inspect_inputs(self) -> None:
        result = plan_owner_truth_migration_retirement_candidate_shadow(
            scope=object(),
            evidence=object(),
            enabled=False,
        )

        self.assertEqual(result.lifecycle_state, RetirementCandidateLifecycleState.DISCOVERED)
        self.assertEqual(result.disposition, RetirementCandidateDisposition.SHADOW_DISABLED)
        self.assertFalse(result.candidate_approval_allowed)
        self.assertFalse(result.legacy_implementation_deleted)
        self.assertFalse(result.credential_revoked)

    def test_complete_synthetic_evidence_only_observes_zero_use(self) -> None:
        result = plan_owner_truth_migration_retirement_candidate_shadow(
            scope=_scope(),
            evidence=_evidence(),
            enabled=True,
        )

        self.assertEqual(result.lifecycle_state, RetirementCandidateLifecycleState.ZERO_USE_OBSERVED)
        self.assertEqual(
            result.disposition,
            RetirementCandidateDisposition.EXTERNAL_APPROVAL_REQUIRED,
        )
        self.assertFalse(result.candidate_approval_allowed)
        self.assertFalse(result.legacy_implementation_deleted)
        self.assertFalse(result.credential_revoked)
        summary = result.value_free_summary()
        self.assertEqual(summary["runtimeUseCount"], 0)
        self.assertEqual(summary["inFlightState"], "terminal")
        self.assertEqual(summary["minimumClientState"], "zeroOldClients")
        self.assertEqual(summary["approverCount"], 1)
        self.assertEqual(summary["approverReferenceHashes"], [_digest("security-owner")])
        self.assertIn("G2", summary["requiredExternalGates"])
        self.assertIn("G4", summary["requiredExternalGates"])
        self.assertNotIn("legacy-time-letter-dispatch", str(summary))

    def test_runtime_hit_reopens_the_zero_use_window(self) -> None:
        result = plan_owner_truth_migration_retirement_candidate_shadow(
            scope=_scope(),
            evidence=_evidence(
                runtime_usage=RuntimeUsageObservation.POSITIVE,
                runtime_use_count=1,
            ),
            enabled=True,
        )

        self.assertEqual(result.lifecycle_state, RetirementCandidateLifecycleState.REOPENED)
        self.assertEqual(result.disposition, RetirementCandidateDisposition.REOPEN_REQUIRED)
        self.assertIn("runtimeUsageObserved", result.reason_codes)
        self.assertIn("zeroUseWindowReset", result.reason_codes)

    def test_active_inflight_work_stays_draining(self) -> None:
        result = plan_owner_truth_migration_retirement_candidate_shadow(
            scope=_scope(),
            evidence=_evidence(in_flight=InFlightObservation.ACTIVE),
            enabled=True,
        )

        self.assertEqual(result.lifecycle_state, RetirementCandidateLifecycleState.DRAINING)
        self.assertEqual(result.disposition, RetirementCandidateDisposition.DRAIN_REQUIRED)
        self.assertIn("inFlightDrainRequired", result.reason_codes)

    def test_unknown_evidence_fails_closed_and_reopens(self) -> None:
        result = plan_owner_truth_migration_retirement_candidate_shadow(
            scope=_scope(),
            evidence=_evidence(
                in_flight=InFlightObservation.UNKNOWN,
                restore_replay=EvidenceVerificationState.UNKNOWN,
            ),
            enabled=True,
        )

        self.assertEqual(result.lifecycle_state, RetirementCandidateLifecycleState.REOPENED)
        self.assertIn("inFlightUnknown", result.reason_codes)
        self.assertIn("restoreReplayEvidenceMissingOrUnknown", result.reason_codes)
        self.assertIn("zeroUseWindowReset", result.reason_codes)

    def test_rights_and_reconciliation_surfaces_never_become_candidates(self) -> None:
        for kind in (
            RetirementSurfaceKind.RIGHTS_ROUTE,
            RetirementSurfaceKind.RECONCILIATION_ROUTE,
        ):
            with self.subTest(kind=kind):
                result = plan_owner_truth_migration_retirement_candidate_shadow(
                    scope=_scope(kind=kind, reference=f"legacy-{kind.value}"),
                    evidence=_evidence(),
                    enabled=True,
                )
                self.assertEqual(result.lifecycle_state, RetirementCandidateLifecycleState.REOPENED)
                self.assertEqual(
                    result.disposition,
                    RetirementCandidateDisposition.PROTECTED_SURFACE_REJECTED,
                )
                self.assertIn("protectedRightsOrReconcileSurface", result.reason_codes)

    def test_provider_and_credential_surfaces_declare_g3(self) -> None:
        for kind in (RetirementSurfaceKind.PROVIDER_ADAPTER, RetirementSurfaceKind.CREDENTIAL):
            with self.subTest(kind=kind):
                result = plan_owner_truth_migration_retirement_candidate_shadow(
                    scope=_scope(kind=kind, reference=f"legacy-{kind.value}"),
                    evidence=_evidence(),
                    enabled=True,
                )
                self.assertIn("G3", result.value_free_summary()["requiredExternalGates"])


if __name__ == "__main__":
    unittest.main()
