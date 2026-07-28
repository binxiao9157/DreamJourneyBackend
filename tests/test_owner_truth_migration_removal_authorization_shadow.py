"""C11 authorization planning tests; no irreversible work is exercised."""

from __future__ import annotations

from hashlib import sha256
import unittest

from app.domain.owner_truth.migration_removal_authorization_shadow import (
    OldBinaryObservation,
    OwnerTruthRemovalAuthorizationEvidence,
    OwnerTruthRemovalAuthorizationScope,
    RemovalAuthorizationDisposition,
    RemovalAuthorizationPhase,
    plan_owner_truth_migration_removal_authorization_shadow,
)
from app.domain.owner_truth.migration_retirement_candidate_shadow import (
    EvidenceVerificationState,
    InFlightObservation,
    RetirementCandidateLifecycleState,
    RetirementSurfaceKind,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _scope(
    *,
    kind: RetirementSurfaceKind = RetirementSurfaceKind.TIMER,
    candidate_lifecycle: RetirementCandidateLifecycleState = (
        RetirementCandidateLifecycleState.CANDIDATE_APPROVED
    ),
    authorization_hash: str | None = _digest("c11-independent-authorization"),
    reference: str = "legacy-time-letter-dispatch",
) -> OwnerTruthRemovalAuthorizationScope:
    return OwnerTruthRemovalAuthorizationScope(
        surface_kind=kind,
        surface_reference=reference,
        candidate_lifecycle=candidate_lifecycle,
        c10_candidate_manifest_hash=_digest("c10-candidate-manifest"),
        independent_authorization_hash=authorization_hash,
        policy_version="c11-shadow-v1",
    )


def _evidence(**overrides: object) -> OwnerTruthRemovalAuthorizationEvidence:
    values: dict[str, object] = {
        "contract_dry_run": EvidenceVerificationState.VERIFIED,
        "final_restore_replay": EvidenceVerificationState.VERIFIED,
        "old_binary": OldBinaryObservation.ZERO,
        "in_flight": InFlightObservation.TERMINAL,
        "credential_owner": EvidenceVerificationState.VERIFIED,
        "post_monitor_plan_hash": _digest("c11-post-monitor-plan"),
        "evidence_bundle_hash": _digest("c11-evidence-bundle"),
        "approval_reference_hashes": (_digest("security-ops-approval"),),
    }
    values.update(overrides)
    return OwnerTruthRemovalAuthorizationEvidence(**values)


class OwnerTruthMigrationRemovalAuthorizationShadowTests(unittest.TestCase):
    def test_disabled_mode_does_not_inspect_inputs(self) -> None:
        result = plan_owner_truth_migration_removal_authorization_shadow(
            scope=object(),
            evidence=object(),
            enabled=False,
        )

        self.assertEqual(result.disposition, RemovalAuthorizationDisposition.SHADOW_DISABLED)
        self.assertFalse(result.contract_migrated)
        self.assertFalse(result.removal_execution_allowed)
        self.assertFalse(result.legacy_artifact_removed)
        self.assertFalse(result.credential_revoked)
        self.assertFalse(result.post_monitor_started)

    def test_c10_zero_use_observation_cannot_authorize_c11(self) -> None:
        result = plan_owner_truth_migration_removal_authorization_shadow(
            scope=_scope(
                candidate_lifecycle=RetirementCandidateLifecycleState.ZERO_USE_OBSERVED
            ),
            evidence=_evidence(),
            enabled=True,
        )

        self.assertEqual(result.phase, RemovalAuthorizationPhase.REOPENED)
        self.assertEqual(
            result.disposition,
            RemovalAuthorizationDisposition.CANDIDATE_NOT_APPROVED,
        )
        self.assertIn("retirementCandidateNotApproved", result.reason_codes)

    def test_missing_independent_approval_rejects_candidate(self) -> None:
        result = plan_owner_truth_migration_removal_authorization_shadow(
            scope=_scope(authorization_hash=None),
            evidence=_evidence(approval_reference_hashes=()),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            RemovalAuthorizationDisposition.CANDIDATE_NOT_APPROVED,
        )
        self.assertIn("independentAuthorizationMissing", result.reason_codes)

    def test_unknown_or_nonterminal_prerequisite_fails_closed(self) -> None:
        result = plan_owner_truth_migration_removal_authorization_shadow(
            scope=_scope(),
            evidence=_evidence(
                old_binary=OldBinaryObservation.UNKNOWN,
                in_flight=InFlightObservation.ACTIVE,
                final_restore_replay=EvidenceVerificationState.UNKNOWN,
            ),
            enabled=True,
        )

        self.assertEqual(result.phase, RemovalAuthorizationPhase.REOPENED)
        self.assertEqual(
            result.disposition,
            RemovalAuthorizationDisposition.EVIDENCE_INCOMPLETE,
        )
        self.assertIn("oldBinaryNotZeroOrUnknown", result.reason_codes)
        self.assertIn("inFlightNotTerminalOrUnknown", result.reason_codes)
        self.assertIn("finalRestoreReplayEvidenceMissingOrUnknown", result.reason_codes)

    def test_complete_synthetic_envelope_still_requires_external_execution(self) -> None:
        result = plan_owner_truth_migration_removal_authorization_shadow(
            scope=_scope(),
            evidence=_evidence(),
            enabled=True,
        )

        self.assertEqual(result.phase, RemovalAuthorizationPhase.AUTHORIZATION)
        self.assertEqual(
            result.disposition,
            RemovalAuthorizationDisposition.EXTERNAL_EXECUTION_REQUIRED,
        )
        summary = result.value_free_summary()
        self.assertFalse(summary["contractMigrated"])
        self.assertFalse(summary["removalExecutionAllowed"])
        self.assertFalse(summary["legacyArtifactRemoved"])
        self.assertFalse(summary["credentialRevoked"])
        self.assertFalse(summary["postMonitorStarted"])
        self.assertNotIn("legacy-time-letter-dispatch", str(summary))

    def test_rights_and_reconciliation_surfaces_are_never_authorized(self) -> None:
        for kind in (
            RetirementSurfaceKind.RIGHTS_ROUTE,
            RetirementSurfaceKind.RECONCILIATION_ROUTE,
        ):
            with self.subTest(kind=kind):
                result = plan_owner_truth_migration_removal_authorization_shadow(
                    scope=_scope(kind=kind, reference=f"legacy-{kind.value}"),
                    evidence=_evidence(),
                    enabled=True,
                )
                self.assertEqual(
                    result.disposition,
                    RemovalAuthorizationDisposition.PROTECTED_SURFACE_REJECTED,
                )

    def test_provider_and_credential_surfaces_require_g3(self) -> None:
        for kind in (RetirementSurfaceKind.PROVIDER_ADAPTER, RetirementSurfaceKind.CREDENTIAL):
            with self.subTest(kind=kind):
                result = plan_owner_truth_migration_removal_authorization_shadow(
                    scope=_scope(kind=kind, reference=f"legacy-{kind.value}"),
                    evidence=_evidence(),
                    enabled=True,
                )
                self.assertIn("G3", result.value_free_summary()["requiredExternalGates"])


if __name__ == "__main__":
    unittest.main()
