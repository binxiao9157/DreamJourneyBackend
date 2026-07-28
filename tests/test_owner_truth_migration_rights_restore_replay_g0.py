"""Composite G0 checks for C09 rights, restore, and replay boundaries."""

from __future__ import annotations

from hashlib import sha256
import unittest

from app.db.recovery import build_replay_plan
from app.services.account_deletion_state import (
    AccountDeletionStateError,
    account_purge_block_reason,
    account_restore_block_reason,
    guard_account_upsert,
)
from app.services.data_rights_evidence_projection import (
    build_data_rights_evidence_projection,
)
from app.services.recovery_access import RecoveryAccessPolicy


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _soft_deleted_account(**overrides: object) -> dict[str, object]:
    account: dict[str, object] = {
        "deletionState": "softDeleted",
        "restoreCount": 0,
        "restoreLimit": 1,
        "restoreDeadline": "2026-01-31T00:00:00+00:00",
        "retentionHolds": [],
    }
    account.update(overrides)
    return account


def _replay_bundle(*, deletion_coverage: bool = True, deletion_status: str = "applied"):
    return {
        "schemaVersion": 1,
        "backupId": "dj-20260717T010203Z-a1b2c3d4",
        "cutoffLSN": "0/16B6A40",
        "rangeEndLSN": "0/16B6B00",
        "sourceEvidenceId": "a" * 64,
        "coverage": {
            "commandReceipts": True,
            "outboxReceipts": True,
            "deletionReceipts": deletion_coverage,
            "providerReceipts": True,
        },
        "receipts": [
            {
                "receiptId": "deletion-c09-a",
                "kind": "deletion",
                "lsn": "0/16B6A80",
                "ownerIdHash": "b" * 64,
                "payloadHash": "c" * 64,
                "status": deletion_status,
            }
        ],
    }


class OwnerTruthMigrationRightsRestoreReplayG0Tests(unittest.TestCase):
    def test_access_first_and_lifecycle_guards_block_resurrection(self) -> None:
        account = _soft_deleted_account(restoreCount=1)

        with self.assertRaises(AccountDeletionStateError) as raised:
            guard_account_upsert(account)
        self.assertEqual(raised.exception.code, "accountLifecycleUpsertBlocked")
        self.assertEqual(
            account_restore_block_reason(account, "2026-01-30T00:00:00+00:00"),
            "restoreLimitReached",
        )
        self.assertEqual(
            account_restore_block_reason(
                _soft_deleted_account(), "2026-02-01T00:00:00+00:00"
            ),
            "restoreDeadlineExpired",
        )
        self.assertEqual(
            account_purge_block_reason(
                _soft_deleted_account(
                    retentionHolds=[{"holdId": "hold-c09", "state": "active"}]
                ),
                "2026-02-01T00:00:00+00:00",
            ),
            "retentionHoldActive",
        )

    def test_missing_rights_receipt_is_unknown_not_completed(self) -> None:
        projection = build_data_rights_evidence_projection(
            {
                "request": {
                    "id": "rr-c09-a",
                    "status": "completed",
                    "createdAt": "2026-07-29T00:00:00+00:00",
                    "updatedAt": "2026-07-29T00:01:00+00:00",
                },
                "executions": [
                    {
                        "moduleId": "providerVoice",
                        "resourceType": "voiceCloneAsset",
                        "executionIdHash": _digest("execution-c09-a"),
                        "outcome": "completed",
                        "updatedAt": "2026-07-29T00:01:00+00:00",
                    }
                ],
                "receipts": [],
            },
            now="2026-07-29T00:02:00+00:00",
        )

        self.assertEqual(projection["accessRevocation"]["status"], "unknown")
        self.assertEqual(projection["physicalCleanup"]["status"], "unknown")
        self.assertEqual(projection["resources"][0]["status"], "unknown")
        self.assertIn(
            "terminalExecutionMissingReceipt",
            projection["resources"][0]["reasonCodes"],
        )

    def test_restore_mode_blocks_writes_before_any_replay_or_cleanup(self) -> None:
        policy = RecoveryAccessPolicy(mode="readOnly", authority_epoch="epoch-c09-a")

        self.assertTrue(policy.evaluate(method="GET", path="/archive/items").allowed)
        decision = policy.evaluate(method="POST", path="/archive/items")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "recoveryWriteBlocked")
        self.assertFalse(policy.public_descriptor()["writesAllowed"])

    def test_replay_refuses_missing_or_nonterminal_deletion_evidence(self) -> None:
        missing_coverage = build_replay_plan(
            _replay_bundle(deletion_coverage=False),
            backup_id="dj-20260717T010203Z-a1b2c3d4",
            cutoff_lsn="0/16B6A40",
        )
        unknown_deletion = build_replay_plan(
            _replay_bundle(deletion_status="unknown"),
            backup_id="dj-20260717T010203Z-a1b2c3d4",
            cutoff_lsn="0/16B6A40",
        )

        self.assertEqual(missing_coverage["status"], "incomplete")
        self.assertIn("deletionCoverageMissing", missing_coverage["blockers"])
        self.assertEqual(unknown_deletion["status"], "incomplete")
        self.assertIn("deletionReceiptNotApplied", unknown_deletion["blockers"])


if __name__ == "__main__":
    unittest.main()
