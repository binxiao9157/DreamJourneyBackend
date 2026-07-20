"""G0 contracts for the Owner Truth cohort-cutover admission boundary."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from unittest.mock import patch
import unittest

from app.domain.owner_truth.legacy_migration import (
    LegacyMigrationDomain,
    LegacyMigrationRecord,
    build_legacy_migration_inventory,
    build_legacy_shadow_parity_report,
)
from app.services.owner_truth_cutover_admission_shadow import (
    OwnerTruthCutoverAdmissionContext,
    OwnerTruthCutoverAdmissionDisposition,
    observe_owner_truth_cutover_admission,
)


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _context(*, authority_epoch: int = 0, vault_id: str = "vault-cutover-a") -> OwnerTruthCutoverAdmissionContext:
    return OwnerTruthCutoverAdmissionContext(
        vault_id=vault_id,
        owner_subject_id="owner-cutover-a",
        authority_epoch=authority_epoch,
    )


def _parity_report(*, vault_id: str = "vault-cutover-a", authority_epoch: int = 0):
    inventory = build_legacy_migration_inventory(
        vault_id=vault_id,
        classifier_version="cutover-admission-shadow-test-v1",
        records=(
            LegacyMigrationRecord(
                domain=LegacyMigrationDomain.MEMORY,
                legacy_id="legacy-memory-a",
                record_hash=_digest({"legacy": "memory-a"}),
                canonical_owner_subject_id="owner-cutover-a",
                observed_owner_subject_id="owner-cutover-a",
                source_evidence_id="source-evidence-a",
                decision_receipt_id="decision-receipt-a",
                decision_is_terminal=True,
                revision_evidence_id="revision-evidence-a",
            ),
        ),
    )
    return build_legacy_shadow_parity_report(
        inventory_run_id="cutover-admission-run-a",
        inventory=inventory,
        owner_subject_id="owner-cutover-a",
        projection_snapshot={
            "authorityEpoch": authority_epoch,
            "checkpoint": _digest({"checkpoint": "a"}),
            "entryCount": 1,
            "ownerSubjectId": "owner-cutover-a",
            "sourceHash": _digest({"source": "a"}),
            "state": "ready",
            "vaultId": vault_id,
        },
    )


class OwnerTruthCutoverAdmissionShadowTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_context_or_parity(self) -> None:
        with patch(
            "app.services.owner_truth_cutover_admission_shadow._scope_hash"
        ) as scope_hash:
            result = observe_owner_truth_cutover_admission(
                object(),
                context=object(),
            )

        scope_hash.assert_not_called()
        self.assertEqual(
            result.disposition,
            OwnerTruthCutoverAdmissionDisposition.SHADOW_DISABLED,
        )
        self.assertFalse(result.authority_epoch_changed)
        self.assertFalse(result.legacy_writer_retired)

    def test_current_parity_never_authorizes_epoch_or_legacy_writer_change(self) -> None:
        result = observe_owner_truth_cutover_admission(
            _parity_report(),
            context=_context(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            OwnerTruthCutoverAdmissionDisposition.EXTERNAL_GO_REQUIRED,
        )
        self.assertFalse(result.cutover_allowed)
        self.assertFalse(result.authority_epoch_changed)
        self.assertFalse(result.legacy_writer_retired)
        self.assertIn("legacyParityDoesNotAuthorizeCutover", result.reason_codes)
        self.assertIn("separateProductionGoRecordRequired", result.reason_codes)
        summary = result.value_free_summary()
        self.assertNotIn("vault-cutover-a", repr(summary))
        self.assertNotIn("owner-cutover-a", repr(summary))

    def test_even_a_tampered_parity_flag_cannot_self_authorize_cutover(self) -> None:
        parity = replace(
            _parity_report(),
            cutover_allowed=True,
            authority_epoch_changed=True,
            legacy_writer_retired=True,
        )

        result = observe_owner_truth_cutover_admission(
            parity,
            context=_context(),
            enabled=True,
        )

        self.assertEqual(
            result.disposition,
            OwnerTruthCutoverAdmissionDisposition.EXTERNAL_GO_REQUIRED,
        )
        self.assertFalse(result.cutover_allowed)
        self.assertFalse(result.authority_epoch_changed)
        self.assertFalse(result.legacy_writer_retired)
        self.assertIn("shadowParityFlagsCannotCommitCutover", result.reason_codes)
        self.assertIn("authorityEpochCasRequiresIndependentCommand", result.reason_codes)
        self.assertIn("legacyWriterRetirementRequiresIndependentCommand", result.reason_codes)

    def test_epoch_or_vault_mismatch_fails_closed_before_external_go_evaluation(self) -> None:
        epoch_mismatch = observe_owner_truth_cutover_admission(
            _parity_report(),
            context=_context(authority_epoch=1),
            enabled=True,
        )
        vault_mismatch = observe_owner_truth_cutover_admission(
            _parity_report(),
            context=_context(vault_id="vault-cutover-b"),
            enabled=True,
        )

        self.assertEqual(
            epoch_mismatch.disposition,
            OwnerTruthCutoverAdmissionDisposition.CONTEXT_MISMATCH,
        )
        self.assertIn("authorityEpochMismatch", epoch_mismatch.reason_codes)
        self.assertEqual(
            vault_mismatch.disposition,
            OwnerTruthCutoverAdmissionDisposition.CONTEXT_MISMATCH,
        )
        self.assertIn("vaultMismatch", vault_mismatch.reason_codes)


if __name__ == "__main__":
    unittest.main()
