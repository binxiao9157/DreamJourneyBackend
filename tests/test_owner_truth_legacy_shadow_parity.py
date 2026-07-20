from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateSnapshot
from app.domain.owner_truth.legacy_migration import (
    LegacyMigrationDomain,
    LegacyMigrationRecord,
    build_legacy_migration_inventory,
    build_legacy_shadow_parity_report,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_legacy_migration import (
    OwnerTruthLegacyMigrationAccessDenied,
    OwnerTruthLegacyMigrationUnavailable,
)
from app.services.owner_truth_legacy_shadow_parity import (
    OwnerTruthLegacyShadowParityService,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthLegacyShadowParityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.vault_id = "vault-legacy-parity"
        self.owner_id = "owner-legacy-parity"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self._seed_active_vault()
        self.service = OwnerTruthLegacyShadowParityService(self.store, enabled=True)

    def _seed_active_vault(self) -> None:
        source_id = str(uuid4())
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash({"summary": "projection seed"}),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": {"summary": "projection seed"},
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [
                    {
                        "sourceId": source_id,
                        "sourceVersion": 1,
                        "span": {"start": 0, "end": 1},
                    }
                ],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )
        self.store.owner_truth_candidate_review_repository().seed(candidate)

    def test_ready_projection_with_unproven_legacy_data_is_deterministic_and_blocks_cutover(self) -> None:
        raw_archive_body = "旧档案正文不得出现在 parity 输出"
        raw_memory_body = "旧记忆正文不得出现在 parity 输出"
        self.store.add_archive_item(
            self.owner_id,
            {"id": "legacy-archive-parity", "kind": "text", "note": raw_archive_body},
        )
        self.store.add_memory(
            self.owner_id,
            {"id": "legacy-memory-parity", "summary": raw_memory_body},
        )
        OwnerTruthMemoryProjectionService(self.store).rebuild(context=self.context)

        first = self.service.observe(context=self.context)
        second = self.service.observe(context=self.context)
        summary = first.public_summary()

        self.assertEqual(first.inventory_outcome, "created")
        self.assertEqual(second.inventory_outcome, "deduplicated")
        self.assertEqual(first.inventory_run_id, second.inventory_run_id)
        self.assertEqual(first.report.report_hash, second.report.report_hash)
        self.assertEqual(summary["comparisonStatus"], "legacyEvidenceIncomplete")
        self.assertEqual(summary["legacyEntryCount"], 2)
        self.assertEqual(summary["legacyEligibleEntryCount"], 0)
        self.assertEqual(summary["projection"]["state"], "ready")
        self.assertFalse(summary["cutoverAllowed"])
        self.assertFalse(summary["authorityEpochChanged"])
        self.assertFalse(summary["legacyWriterRetired"])
        self.assertEqual(summary["cutoverAdmission"]["status"], "external_go_required")
        self.assertFalse(summary["cutoverAdmission"]["cutoverAllowed"])
        self.assertFalse(summary["cutoverAdmission"]["authorityEpochChanged"])
        self.assertFalse(summary["cutoverAdmission"]["legacyWriterRetired"])
        self.assertIn(
            "separateProductionGoRecordRequired",
            summary["cutoverAdmission"]["reasonCodes"],
        )
        self.assertIn("legacyEvidenceIncomplete", summary["reasonCodes"])
        self.assertIn("authorityEpochCutoverRequiresSeparateGate", summary["reasonCodes"])
        self.assertNotIn(raw_archive_body, str(summary))
        self.assertNotIn(raw_memory_body, str(summary))
        self.assertNotIn("legacy-archive-parity", str(summary))
        self.assertNotIn("legacy-memory-parity", str(summary))

    def test_missing_projection_checkpoint_fails_closed_without_cutover(self) -> None:
        observation = self.service.observe(context=self.context)
        summary = observation.public_summary()

        self.assertEqual(summary["comparisonStatus"], "projectionRebuilding")
        self.assertEqual(summary["projection"]["state"], "rebuilding")
        self.assertFalse(summary["cutoverAllowed"])
        self.assertEqual(summary["cutoverAdmission"]["status"], "external_go_required")
        self.assertIn("projectionRebuilding", summary["reasonCodes"])

    def test_cross_owner_is_rejected_before_legacy_inventory_is_written(self) -> None:
        attacker_context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id="attacker-owner",
            actor_subject_id="attacker-owner",
        )

        with self.assertRaises(OwnerTruthLegacyMigrationAccessDenied):
            self.service.observe(context=attacker_context)

        snapshot = self.store.owner_truth_legacy_migration_repository().snapshot()
        self.assertEqual(snapshot["runCount"], 0)

    def test_disabled_service_remains_unavailable(self) -> None:
        disabled = OwnerTruthLegacyShadowParityService(self.store, enabled=False)

        with self.assertRaises(OwnerTruthLegacyMigrationUnavailable):
            disabled.observe(context=self.context)

    def test_proven_legacy_record_still_requires_explicit_v4_lineage_mapping(self) -> None:
        inventory = build_legacy_migration_inventory(
            vault_id=self.vault_id,
            classifier_version="legacy-shadow-parity-test-v1",
            records=(
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.MEMORY,
                    legacy_id="legacy-proven-memory",
                    record_hash="a" * 64,
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                    source_evidence_id="source-evidence",
                    decision_receipt_id="decision-receipt",
                    decision_is_terminal=True,
                    revision_evidence_id="revision-evidence",
                ),
            ),
        )
        projection = {
            "authorityEpoch": 0,
            "checkpoint": "b" * 64,
            "entryCount": 1,
            "ownerSubjectId": self.owner_id,
            "sourceHash": "c" * 64,
            "state": "ready",
            "vaultId": self.vault_id,
        }

        report = build_legacy_shadow_parity_report(
            inventory_run_id="parity-run-proven",
            inventory=inventory,
            owner_subject_id=self.owner_id,
            projection_snapshot=projection,
        )

        self.assertEqual(report.summary()["comparisonStatus"], "legacyRecordMappingRequired")
        self.assertEqual(report.summary()["legacyEligibleEntryCount"], 1)
        self.assertEqual(report.summary()["mappedRecordCount"], 0)
        self.assertFalse(report.summary()["cutoverAllowed"])
        self.assertFalse(report.summary()["authorityEpochChanged"])
        self.assertFalse(report.summary()["legacyWriterRetired"])
        self.assertIn("legacyRecordMappingUnavailable", report.summary()["reasonCodes"])


if __name__ == "__main__":
    unittest.main()
