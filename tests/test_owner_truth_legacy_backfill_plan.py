from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import unittest
from uuid import uuid4

from app.domain.owner_truth.legacy_backfill import (
    LegacyBackfillAdmissionAction,
    OwnerTruthLegacyBackfillPlanError,
    build_legacy_backfill_admission_plan,
)
from app.domain.owner_truth.legacy_migration import (
    LegacyMigrationDomain,
    LegacyMigrationRecord,
    build_legacy_migration_inventory,
)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class OwnerTruthLegacyBackfillAdmissionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-backfill-plan"
        self.vault_id = "vault-backfill-plan"
        self.inventory_run_id = str(uuid4())
        self.private_legacy_id = "legacy-private-source-identifier"
        self.inventory = build_legacy_migration_inventory(
            vault_id=self.vault_id,
            classifier_version="legacy-backfill-plan-test-v1",
            records=(
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.MEMORY,
                    legacy_id="legacy-proven-memory",
                    record_hash=digest("proven legacy memory body"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                    source_evidence_id="source-evidence",
                    decision_receipt_id="decision-receipt",
                    decision_is_terminal=True,
                    revision_evidence_id="revision-evidence",
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.ARCHIVE_ITEM,
                    legacy_id=self.private_legacy_id,
                    record_hash=digest("legacy photo metadata"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.MEMORY,
                    legacy_id="legacy-review-memory",
                    record_hash=digest("legacy memory lacks evidence"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.ARCHIVE_ITEM,
                    legacy_id="legacy-owner-conflict",
                    record_hash=digest("legacy owner conflict body"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id="different-owner",
                ),
                LegacyMigrationRecord(
                    domain=LegacyMigrationDomain.CONVERSATION_CACHE,
                    legacy_id="legacy-conversation-private-id",
                    record_hash=digest("assistant conversation must stay excluded"),
                    canonical_owner_subject_id=self.owner_id,
                    observed_owner_subject_id=self.owner_id,
                ),
            ),
            unavailable_domains=(LegacyMigrationDomain.CONVERSATION_CACHE,),
        )

    def build(self):
        return build_legacy_backfill_admission_plan(
            inventory_run_id=self.inventory_run_id,
            inventory=self.inventory,
            owner_subject_id=self.owner_id,
            authority_epoch=7,
        )

    def test_every_inventory_entry_has_exactly_one_non_authorizing_action(self) -> None:
        plan = self.build()

        self.assertEqual(len(plan.entries), len(self.inventory.entries))
        self.assertEqual(
            plan.action_counts,
            {
                "excluded": 1,
                "quarantined": 1,
                "requireEvidenceReview": 1,
                "requireIndependentLineageReplay": 1,
                "requireOwnerCandidateReview": 1,
            },
        )
        self.assertEqual(sum(plan.action_counts.values()), len(self.inventory.entries))
        actions = {entry.action for entry in plan.entries}
        self.assertEqual(
            actions,
            {
                LegacyBackfillAdmissionAction.EXCLUDED,
                LegacyBackfillAdmissionAction.QUARANTINED,
                LegacyBackfillAdmissionAction.REQUIRE_EVIDENCE_REVIEW,
                LegacyBackfillAdmissionAction.REQUIRE_INDEPENDENT_LINEAGE_REPLAY,
                LegacyBackfillAdmissionAction.REQUIRE_OWNER_CANDIDATE_REVIEW,
            },
        )
        self.assertFalse(plan.summary()["cutoverAllowed"])
        self.assertFalse(plan.summary()["legacyWriterRetired"])
        self.assertEqual(plan.summary()["targetState"], "notCreated")

    def test_replay_is_deterministic_and_value_free(self) -> None:
        first = self.build()
        replay = self.build()

        self.assertEqual(first.plan_id, replay.plan_id)
        self.assertEqual(first.plan_hash, replay.plan_hash)
        self.assertEqual(first.scope_hash, replay.scope_hash)
        rendered = str(first.summary()) + str([entry.summary() for entry in first.entries])
        self.assertNotIn(self.private_legacy_id, rendered)
        self.assertNotIn("proven legacy memory body", rendered)
        self.assertNotIn("assistant conversation must stay excluded", rendered)

    def test_scope_or_plan_tampering_fails_closed(self) -> None:
        plan = self.build()

        with self.assertRaises(OwnerTruthLegacyBackfillPlanError):
            replace(plan, authority_epoch=8)
        with self.assertRaises(OwnerTruthLegacyBackfillPlanError):
            replace(plan, plan_hash="0" * 64)

    def test_non_uuid_inventory_run_is_rejected(self) -> None:
        with self.assertRaises(OwnerTruthLegacyBackfillPlanError):
            build_legacy_backfill_admission_plan(
                inventory_run_id="not-a-run-id",
                inventory=self.inventory,
                owner_subject_id=self.owner_id,
                authority_epoch=0,
            )


if __name__ == "__main__":
    unittest.main()
