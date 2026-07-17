import json
import unittest
from datetime import datetime, timezone

from app.db.recovery import (
    RecoveryContractError,
    build_recovery_record,
    build_replay_plan,
    build_restore_evidence,
    finalize_replay_plan,
    validate_recovery_target,
    verify_integrity_metrics,
)


class RecoveryRecordTests(unittest.TestCase):
    def setUp(self):
        self.backup_id = "dj-20260717T010203Z-a1b2c3d4"
        self.cutoff_lsn = "0/16B6A40"
        self.metrics = {
            "schemaVersion": 1,
            "schemaHead": "0001",
            "targetSchemaHead": "0001",
            "relationCount": 19,
            "rowCounts": {"users": 3, "archive_items": 4},
            "orphanOwnerCount": 0,
            "invalidPayloadHashCount": 0,
            "purgedOwnerViolationCount": 0,
            "migrationState": "ready",
        }

    def complete_bundle(self):
        return {
            "schemaVersion": 1,
            "backupId": self.backup_id,
            "cutoffLSN": self.cutoff_lsn,
            "rangeEndLSN": "0/16B6B00",
            "sourceEvidenceId": "a" * 64,
            "coverage": {
                "commandReceipts": True,
                "outboxReceipts": True,
                "deletionReceipts": True,
                "providerReceipts": True,
            },
            "receipts": [
                {
                    "receiptId": "delete-1",
                    "kind": "deletion",
                    "lsn": "0/16B6A80",
                    "ownerIdHash": "b" * 64,
                    "payloadHash": "c" * 64,
                    "status": "applied",
                }
            ],
        }

    def test_target_must_be_isolated_and_cannot_equal_production(self):
        self.assertEqual(
            validate_recovery_target("dj_recovery_drill_1234", "dreamjourney"),
            "dj_recovery_drill_1234",
        )
        for target in ("dreamjourney", "postgres", "dj_restore", "bad-name"):
            with self.assertRaises(RecoveryContractError):
                validate_recovery_target(target, "dreamjourney")

    def test_integrity_requires_head_owner_hash_and_purged_owner_invariants(self):
        report = self.verified_integrity()
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["integrityDigest"].__len__(), 64)

        for field in (
            "orphanOwnerCount",
            "invalidPayloadHashCount",
            "purgedOwnerViolationCount",
        ):
            invalid = dict(self.metrics)
            invalid[field] = 1
            self.assertEqual(
                self.verified_integrity(metrics=invalid)["status"],
                "failed",
            )

        stale = dict(self.metrics)
        stale["schemaHead"] = "0000"
        self.assertEqual(
            self.verified_integrity(metrics=stale)["status"],
            "failed",
        )

    def verified_integrity(self, *, metrics=None):
        return verify_integrity_metrics(
            metrics or self.metrics,
            expected_schema_head="0001",
            backup_id=self.backup_id,
            cutoff_lsn=self.cutoff_lsn,
            target_database="dj_recovery_drill_1234",
            production_database="dreamjourney",
        )

    def application_evidence(self, plan):
        return {
            "schemaVersion": 1,
            "status": "applied",
            "backupId": self.backup_id,
            "cutoffLSN": self.cutoff_lsn,
            "rangeEndLSN": plan["rangeEndInclusive"],
            "sourceEvidenceId": plan["sourceEvidenceId"],
            "planDigest": plan["replayDigest"],
            "applicationEvidenceId": "f" * 64,
            "appliedReceiptCounts": {
                "command": 0,
                "outbox": 0,
                "deletion": 1,
                "provider": 0,
            },
        }

    def restore_evidence(self):
        return build_restore_evidence(
            backup_id=self.backup_id,
            backup_checksum="1" * 64,
            backup_completed_at="2026-07-17T01:02:04+00:00",
            schema_head="0001",
            cutoff_lsn=self.cutoff_lsn,
            started_at="2026-07-17T02:03:04+00:00",
            completed_at="2026-07-17T02:04:34+00:00",
            target_database="dj_recovery_drill_1234",
            production_database="dreamjourney",
            source_manifest_digest="2" * 64,
            migration_evidence_id="3" * 64,
        )

    def test_replay_is_idempotent_but_conflicting_duplicates_fail_closed(self):
        bundle = self.complete_bundle()
        bundle["receipts"].append(dict(bundle["receipts"][0]))
        plan = build_replay_plan(
            bundle,
            backup_id=self.backup_id,
            cutoff_lsn=self.cutoff_lsn,
        )
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["uniqueReceiptCount"], 1)
        self.assertEqual(plan["duplicateReceiptCount"], 1)

        conflicting = self.complete_bundle()
        duplicate = dict(conflicting["receipts"][0])
        duplicate["payloadHash"] = "d" * 64
        conflicting["receipts"].append(duplicate)
        with self.assertRaisesRegex(RecoveryContractError, "receiptConflict"):
            build_replay_plan(
                conflicting,
                backup_id=self.backup_id,
                cutoff_lsn=self.cutoff_lsn,
            )

    def test_missing_coverage_or_unknown_provider_is_no_go(self):
        missing = self.complete_bundle()
        missing["coverage"]["outboxReceipts"] = False
        plan = build_replay_plan(
            missing,
            backup_id=self.backup_id,
            cutoff_lsn=self.cutoff_lsn,
        )
        self.assertEqual(plan["status"], "incomplete")
        self.assertIn("outboxCoverageMissing", plan["blockers"])

        unknown = self.complete_bundle()
        unknown["receipts"].append(
            {
                "receiptId": "provider-1",
                "kind": "provider",
                "lsn": "0/16B6A90",
                "ownerIdHash": "e" * 64,
                "payloadHash": "f" * 64,
                "status": "unknown",
            }
        )
        plan = build_replay_plan(
            unknown,
            backup_id=self.backup_id,
            cutoff_lsn=self.cutoff_lsn,
        )
        self.assertEqual(plan["status"], "incomplete")
        self.assertIn("providerReceiptUnknown", plan["blockers"])

    def test_unknown_or_inverted_lsn_and_out_of_range_receipts_fail_closed(self):
        with self.assertRaisesRegex(RecoveryContractError, "unknownCutoffLSN"):
            build_replay_plan(
                self.complete_bundle(),
                backup_id=self.backup_id,
                cutoff_lsn="unknown",
            )

        inverted = self.complete_bundle()
        inverted["rangeEndLSN"] = "0/16B6A00"
        with self.assertRaisesRegex(RecoveryContractError, "invalidReplayRange"):
            build_replay_plan(
                inverted,
                backup_id=self.backup_id,
                cutoff_lsn=self.cutoff_lsn,
            )

        outside = self.complete_bundle()
        outside["receipts"][0]["lsn"] = self.cutoff_lsn
        with self.assertRaisesRegex(RecoveryContractError, "receiptOutsideReplayRange"):
            build_replay_plan(
                outside,
                backup_id=self.backup_id,
                cutoff_lsn=self.cutoff_lsn,
            )

    def test_record_is_value_free_and_only_goes_with_integrity_and_replay(self):
        integrity = self.verified_integrity()
        ready = build_replay_plan(
            self.complete_bundle(),
            backup_id=self.backup_id,
            cutoff_lsn=self.cutoff_lsn,
        )
        complete = finalize_replay_plan(
            ready,
            application_evidence=self.application_evidence(ready),
        )
        record = build_recovery_record(
            recovery_id="recovery-20260717T020304Z-a1b2c3d4",
            backup_id=self.backup_id,
            cutoff_lsn=self.cutoff_lsn,
            backup_completed_at="2026-07-17T01:02:04+00:00",
            started_at="2026-07-17T02:03:04+00:00",
            completed_at="2026-07-17T02:04:34+00:00",
            target_database="dj_recovery_drill_1234",
            production_database="dreamjourney",
            backup_checksum="1" * 64,
            schema_head="0001",
            restore=self.restore_evidence(),
            integrity=integrity,
            replay=complete,
        )
        self.assertEqual(record["cutoverDecision"], "GO")
        self.assertEqual(record["status"], "verified")
        self.assertEqual(record["observedRtoSeconds"], 90)
        serialized = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("dj_recovery_drill_1234", serialized)
        self.assertNotIn("phone", serialized.lower())
        self.assertEqual(len(record["evidenceId"]), 64)

        incomplete = build_replay_plan(
            None,
            backup_id=self.backup_id,
            cutoff_lsn=self.cutoff_lsn,
        )
        no_go = build_recovery_record(
            recovery_id="recovery-20260717T020304Z-a1b2c3d4",
            backup_id=self.backup_id,
            cutoff_lsn=self.cutoff_lsn,
            backup_completed_at="2026-07-17T01:02:04+00:00",
            started_at="2026-07-17T02:03:04+00:00",
            completed_at="2026-07-17T02:04:34+00:00",
            target_database="dj_recovery_drill_1234",
            production_database="dreamjourney",
            backup_checksum="1" * 64,
            schema_head="0001",
            restore=self.restore_evidence(),
            integrity=integrity,
            replay=incomplete,
        )
        self.assertEqual(no_go["cutoverDecision"], "NO_GO")
        self.assertEqual(no_go["status"], "replayPending")

    def test_fabricated_or_mismatched_evidence_cannot_produce_go(self):
        ready = build_replay_plan(
            self.complete_bundle(),
            backup_id=self.backup_id,
            cutoff_lsn=self.cutoff_lsn,
        )
        evidence = self.application_evidence(ready)
        evidence["planDigest"] = "0" * 64
        with self.assertRaisesRegex(RecoveryContractError, "replayApplicationPlanMismatch"):
            finalize_replay_plan(ready, application_evidence=evidence)

        complete = finalize_replay_plan(
            ready,
            application_evidence=self.application_evidence(ready),
        )
        fabricated_integrity = {"schemaVersion": 1, "status": "verified"}
        with self.assertRaisesRegex(RecoveryContractError, "invalidIntegrityDigest"):
            build_recovery_record(
                recovery_id="recovery-20260717T020304Z-a1b2c3d4",
                backup_id=self.backup_id,
                cutoff_lsn=self.cutoff_lsn,
                backup_completed_at="2026-07-17T01:02:04+00:00",
                started_at="2026-07-17T02:03:04+00:00",
                completed_at="2026-07-17T02:04:34+00:00",
                target_database="dj_recovery_drill_1234",
                production_database="dreamjourney",
                backup_checksum="1" * 64,
                schema_head="0001",
                restore=self.restore_evidence(),
                integrity=fabricated_integrity,
                replay=complete,
            )

        mismatched = dict(self.verified_integrity())
        mismatched["backupId"] = "dj-20260717T010203Z-deadbeef"
        mismatched["integrityDigest"] = "0" * 64
        with self.assertRaises(RecoveryContractError):
            build_recovery_record(
                recovery_id="recovery-20260717T020304Z-a1b2c3d4",
                backup_id=self.backup_id,
                cutoff_lsn=self.cutoff_lsn,
                backup_completed_at="2026-07-17T01:02:04+00:00",
                started_at="2026-07-17T02:03:04+00:00",
                completed_at="2026-07-17T02:04:34+00:00",
                target_database="dj_recovery_drill_1234",
                production_database="dreamjourney",
                backup_checksum="1" * 64,
                schema_head="0001",
                restore=self.restore_evidence(),
                integrity=mismatched,
                replay=complete,
            )


if __name__ == "__main__":
    unittest.main()
