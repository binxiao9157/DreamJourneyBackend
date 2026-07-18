import unittest

from app.services.account_deletion_state import (
    AccountDeletionStateError,
    account_purge_block_reason,
    account_restore_block_reason,
    guard_account_upsert,
)
from app.services.in_memory_store import InMemoryStore


class AccountDeletionStateTests(unittest.TestCase):
    def soft_deleted_account(self, **overrides):
        account = {
            "id": "user_restore_state",
            "deletionState": "softDeleted",
            "accessState": "suspended_restorable",
            "restoreCount": 0,
            "restoreLimit": 1,
            "restoreDeadline": "2026-01-31T00:00:00+00:00",
            "retentionHolds": [],
        }
        account.update(overrides)
        return account

    def test_restore_allows_the_exact_deadline_but_rejects_after_deadline(self):
        account = self.soft_deleted_account()

        self.assertIsNone(
            account_restore_block_reason(account, "2026-01-31T00:00:00+00:00")
        )
        self.assertEqual(
            account_restore_block_reason(account, "2026-01-31T00:00:00.000001+00:00"),
            "restoreDeadlineExpired",
        )

    def test_restore_is_limited_by_persisted_count(self):
        account = self.soft_deleted_account(restoreCount=1)

        self.assertEqual(
            account_restore_block_reason(account, "2026-01-30T00:00:00+00:00"),
            "restoreLimitReached",
        )

    def test_active_or_malformed_retention_hold_blocks_only_physical_purge(self):
        active = self.soft_deleted_account(
            retentionHolds=[{"holdId": "hold_1", "state": "active"}]
        )
        malformed = self.soft_deleted_account(retentionHolds="invalid")
        released = self.soft_deleted_account(
            retentionHolds=[{"holdId": "hold_1", "state": "released"}]
        )

        self.assertEqual(
            account_purge_block_reason(active, "2026-01-31T00:00:00+00:00"),
            "retentionHoldActive",
        )
        self.assertEqual(
            account_purge_block_reason(malformed, "2026-01-31T00:00:00+00:00"),
            "retentionHoldInvalid",
        )
        self.assertIsNone(
            account_purge_block_reason(released, "2026-01-31T00:00:00+00:00")
        )
        self.assertIsNone(
            account_restore_block_reason(active, "2026-01-30T00:00:00+00:00")
        )

    def test_upsert_cannot_reactivate_soft_deleted_or_purged_accounts(self):
        for state in ("softDeleted", "purged"):
            with self.subTest(state=state):
                with self.assertRaises(AccountDeletionStateError) as raised:
                    guard_account_upsert({"deletionState": state})
                self.assertEqual(raised.exception.code, "accountLifecycleUpsertBlocked")

    def test_store_keeps_restore_count_and_refuses_generic_reactivation(self):
        store = InMemoryStore()
        user = store.upsert_user("13800138111", "恢复测试")
        user_id = user["id"]
        first_delete = store.soft_delete_user(
            user_id,
            phone="13800138111",
            requested_at_iso="2026-01-01T00:00:00+00:00",
        )

        restored = store.restore_user(
            user_id,
            phone="13800138111",
            restored_at_iso="2026-01-31T00:00:00+00:00",
        )
        second_delete = store.soft_delete_user(
            user_id,
            phone="13800138111",
            requested_at_iso="2026-02-01T00:00:00+00:00",
        )

        self.assertEqual(first_delete["restoreCount"], 0)
        self.assertEqual(restored["restoreCount"], 1)
        self.assertEqual(second_delete["restoreCount"], 1)
        self.assertIsNone(
            store.restore_user(
                user_id,
                phone="13800138111",
                restored_at_iso="2026-02-02T00:00:00+00:00",
            )
        )
        with self.assertRaises(AccountDeletionStateError):
            store.upsert_user("13800138111", "不应复活")

    def test_store_blocks_physical_purge_for_hold_and_keeps_redacted_receipt(self):
        store = InMemoryStore()
        phone = "13800138112"
        user = store.upsert_user(phone, "终态清除")
        user_id = user["id"]
        store.soft_delete_user(
            user_id,
            phone=phone,
            requested_at_iso="2026-01-01T00:00:00+00:00",
            deletion_request_id="rr_deletion_001",
        )
        store._users[user_id]["retentionHolds"] = [
            {"holdId": "hold_001", "state": "active"}
        ]

        self.assertEqual(
            store.purge_expired_deleted_users("2026-02-01T00:00:00+00:00"),
            [],
        )
        store._users[user_id]["retentionHolds"] = [
            {"holdId": "hold_001", "state": "released"}
        ]
        purged = store.purge_expired_deleted_users("2026-02-01T00:00:00+00:00")
        receipt = store.get_account_purge_receipt(user_id)
        tombstone = store.get_user(user_id)

        self.assertEqual(len(purged), 1)
        self.assertEqual(tombstone["deletionState"], "purged")
        self.assertEqual(tombstone["phone"], "")
        self.assertEqual(receipt["terminalState"], "purged")
        self.assertEqual(receipt["deletedAt"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(receipt["purgedAt"], "2026-02-01T00:00:00+00:00")
        self.assertNotIn(phone, str(receipt))
        self.assertNotIn(user_id, str(receipt))


if __name__ == "__main__":
    unittest.main()
