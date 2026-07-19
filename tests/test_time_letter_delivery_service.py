import copy
from contextlib import contextmanager
from hashlib import sha256
import unittest

from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.repository import InMemoryEffectKernelRepository
from app.services.time_letter_delivery_effects import (
    TimeLetterDeliveryDisposition,
    build_time_letter_delivery_plan,
)
from app.services.time_letter_delivery_service import (
    TimeLetterAtomicDeliveryPersistenceError,
    TimeLetterAtomicDeliveryService,
)


def _sealed_item(**overrides):
    item = {
        "id": "letter-atomic-001",
        "kind": "timeLetter",
        "ownerSubjectId": "owner-001",
        "vaultId": "vault-001",
        "authorityEpoch": 7,
        "sealedVersion": 3,
        "sealedPayloadHash": sha256(b"sealed-letter-atomic-v3").hexdigest(),
        "deliveryState": "sealed",
        "deliveryStatus": "scheduled",
        "openAt": "2026-07-20T09:00:00Z",
        "recipients": [
            {"id": "family-001", "subjectId": "member-001", "type": "family"},
        ],
        "title": "This title must not enter delivery summary",
        "note": "This body must never enter mailbox payloads or receipts.",
    }
    item.update(overrides)
    return item


class _DeliveryStore:
    def __init__(self, item):
        self.item = copy.deepcopy(item)
        self.mailboxes = {}
        self.kernel = InMemoryEffectKernelRepository()
        self.consumer = InMemoryAsyncEffectConsumerRepository()
        self.family_members = {
            "family-001": {
                "id": "family-001",
                "accessStatus": "active",
                "invitationStatus": "accepted",
            }
        }
        self.relationships = {
            "family-001": {
                "id": "relationship-001",
                "status": "accepted",
                "memberSubjectId": "member-001",
            }
        }
        self.grants = [
            {
                "id": "grant-001",
                "status": "active",
                "grantorSubjectId": "owner-001",
                "granteeSubjectId": "member-001",
                "relationshipId": "relationship-001",
                "purpose": "timeLetter.read",
                "resourceType": "timeLetter",
                "resourceId": "letter-atomic-001",
                "operations": ["read"],
            }
        ]
        self.fail_mailbox_for = None
        self.summary_writes = 0

    @contextmanager
    def request_unit_of_work(self, **_kwargs):
        snapshot = {
            "item": copy.deepcopy(self.item),
            "mailboxes": copy.deepcopy(self.mailboxes),
            "kernel": copy.deepcopy(self.kernel._records),
            "consumer_inbox": copy.deepcopy(self.consumer._inbox),
            "consumer_receipts": copy.deepcopy(self.consumer._receipts),
            "summary_writes": self.summary_writes,
        }
        try:
            yield self
        except Exception:
            self.item = snapshot["item"]
            self.mailboxes = snapshot["mailboxes"]
            self.kernel._records = snapshot["kernel"]
            self.consumer._inbox = snapshot["consumer_inbox"]
            self.consumer._receipts = snapshot["consumer_receipts"]
            self.summary_writes = snapshot["summary_writes"]
            raise

    def effect_kernel_repository(self):
        return self.kernel

    def async_effect_consumer_repository(self):
        return self.consumer

    def get_time_letter_for_atomic_delivery(self, owner_subject_id, letter_id):
        if owner_subject_id != "owner-001" or letter_id != self.item["id"]:
            return None
        return copy.deepcopy(self.item)

    def add_mailbox_letter(self, user_id, payload):
        if user_id == self.fail_mailbox_for:
            raise TimeLetterAtomicDeliveryPersistenceError("injected mailbox failure")
        item = copy.deepcopy(payload)
        existing = self.mailboxes.get(item["id"])
        if existing is not None and existing != item:
            raise TimeLetterAtomicDeliveryPersistenceError("mailbox identity conflict")
        self.mailboxes[item["id"]] = item
        return copy.deepcopy(item)

    def update_time_letter_delivery_summary(
        self,
        owner_subject_id,
        letter_id,
        snapshot,
        expected_row_version,
        summary,
        delivered_at_iso,
    ):
        if owner_subject_id != "owner-001" or letter_id != self.item["id"]:
            return None
        if expected_row_version not in {0, int(self.item.get("rowVersion") or 0)}:
            return None
        current = self.item
        if (
            current.get("sealedVersion") != snapshot.sealed_version
            or current.get("authorityEpoch") != snapshot.authority_epoch
            or current.get("sealedPayloadHash") != snapshot.sealed_payload_hash
            or current.get("deliveryStatus") != "scheduled"
        ):
            return None
        current["deliveryStatus"] = summary["deliveryStatus"]
        current["deliveryExecutionState"] = summary["deliveryStatus"]
        current["deliverySummary"] = copy.deepcopy(summary)
        current["deliveredAt"] = delivered_at_iso
        current["updatedAt"] = delivered_at_iso
        self.summary_writes += 1
        return copy.deepcopy(current)

    def list_family_members(self, owner_subject_id):
        if owner_subject_id != "owner-001":
            return []
        return [copy.deepcopy(member) for member in self.family_members.values()]

    def get_family_relationship_by_member(self, owner_subject_id, family_member_id):
        if owner_subject_id != "owner-001":
            return None
        relationship = self.relationships.get(family_member_id)
        return None if relationship is None else copy.deepcopy(relationship)

    def list_access_grants(self, *, owner_subject_id, relationship_id):
        return [
            copy.deepcopy(grant)
            for grant in self.grants
            if grant["grantorSubjectId"] == owner_subject_id
            and grant["relationshipId"] == relationship_id
        ]


class TimeLetterAtomicDeliveryServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = _DeliveryStore(_sealed_item())
        self.service = TimeLetterAtomicDeliveryService(self.store)
        self.plan = build_time_letter_delivery_plan(
            self.store.item,
            now_iso="2026-07-20T09:00:01Z",
        )

    def test_delivers_each_authorized_target_and_writes_one_summary_after_receipts(self):
        result = self.service.dispatch(self.plan, now_iso="2026-07-20T09:00:01Z")

        self.assertEqual(result.outcome, "delivered")
        self.assertEqual(result.delivery_status, "delivered")
        self.assertEqual(len(result.completions), 2)
        self.assertEqual({completion.disposition for completion in result.completions}, {TimeLetterDeliveryDisposition.DELIVERED})
        self.assertEqual(len(self.store.mailboxes), 2)
        self.assertEqual(self.store.kernel.record_count(), 2)
        self.assertEqual(self.store.summary_writes, 1)
        self.assertEqual(self.store.item["deliveryStatus"], "delivered")
        self.assertNotIn("This body", repr(self.store.item["deliverySummary"]))
        self.assertTrue(all(payload["metadataOnly"] for payload in self.store.mailboxes.values()))
        self.assertTrue(all(payload["contentRedacted"] for payload in self.store.mailboxes.values()))
        self.assertNotIn("This title", repr(self.store.mailboxes))
        self.assertNotIn("This body", repr(self.store.mailboxes))

    def test_revoked_recipient_writes_skip_receipt_and_marks_partial_without_mailbox(self):
        self.store.grants[0]["status"] = "revoked"

        result = self.service.dispatch(self.plan, now_iso="2026-07-20T09:00:01Z")

        self.assertEqual(result.outcome, "partial")
        self.assertEqual(result.delivery_status, "partial")
        self.assertEqual(len(self.store.mailboxes), 1)
        recipient_completion = next(
            completion for completion in result.completions if completion.target.role == "recipient"
        )
        self.assertEqual(recipient_completion.disposition, TimeLetterDeliveryDisposition.SKIPPED_REVOKED)
        self.assertEqual(recipient_completion.reason_code, "grantRevoked")
        self.assertEqual(self.store.item["deliverySummary"]["skippedRevokedCount"], 1)

    def test_same_terminal_letter_replays_without_creating_extra_mailbox_or_receipts(self):
        first = self.service.dispatch(self.plan, now_iso="2026-07-20T09:00:01Z")
        second = self.service.dispatch(self.plan, now_iso="2026-07-20T09:00:02Z")

        self.assertEqual(first.outcome, "delivered")
        self.assertEqual(second.outcome, "already_terminal")
        self.assertEqual(len(self.store.mailboxes), 2)
        self.assertEqual(self.store.kernel.record_count(), 2)
        self.assertEqual(self.store.summary_writes, 1)

    def test_changed_sealed_version_blocks_all_targets_without_summary_or_mailbox(self):
        self.store.item["sealedVersion"] = 4

        result = self.service.dispatch(self.plan, now_iso="2026-07-20T09:00:01Z")

        self.assertEqual(result.outcome, "blocked")
        self.assertEqual(len(result.completions), 2)
        self.assertTrue(
            all(
                completion.disposition is TimeLetterDeliveryDisposition.BLOCKED
                and completion.reason_code == "sealedVersionChanged"
                for completion in result.completions
            )
        )
        self.assertEqual(self.store.mailboxes, {})
        self.assertEqual(self.store.item["deliveryStatus"], "scheduled")
        self.assertEqual(self.store.summary_writes, 0)

    def test_mid_delivery_mailbox_failure_rolls_back_effects_mailboxes_and_summary(self):
        self.store.fail_mailbox_for = "member-001"

        with self.assertRaisesRegex(TimeLetterAtomicDeliveryPersistenceError, "injected mailbox failure"):
            self.service.dispatch(self.plan, now_iso="2026-07-20T09:00:01Z")

        self.assertEqual(self.store.mailboxes, {})
        self.assertEqual(self.store.kernel.record_count(), 0)
        self.assertEqual(self.store.consumer._inbox, {})
        self.assertEqual(self.store.consumer._receipts, {})
        self.assertEqual(self.store.item["deliveryStatus"], "scheduled")
        self.assertEqual(self.store.summary_writes, 0)

    def test_not_due_plan_has_no_effect_or_persistence(self):
        not_due = build_time_letter_delivery_plan(
            self.store.item,
            now_iso="2026-07-20T08:59:59Z",
        )

        result = self.service.dispatch(not_due, now_iso="2026-07-20T08:59:59Z")

        self.assertEqual(result.outcome, "not_due")
        self.assertEqual(result.completions, ())
        self.assertEqual(self.store.mailboxes, {})
        self.assertEqual(self.store.kernel.record_count(), 0)
        self.assertEqual(self.store.item["deliveryStatus"], "scheduled")

    def test_due_plan_is_rechecked_against_execution_clock_before_delivery(self):
        result = self.service.dispatch(self.plan, now_iso="2026-07-20T08:59:59Z")

        self.assertEqual(result.outcome, "not_due")
        self.assertEqual(self.store.mailboxes, {})
        self.assertEqual(self.store.kernel.record_count(), 0)
        self.assertEqual(self.store.item["deliveryStatus"], "scheduled")


if __name__ == "__main__":
    unittest.main()
