import unittest
from hashlib import sha256

from app.async_effects.contracts import AsyncEffectConflict
from app.async_effects.repository import InMemoryEffectKernelRepository
from app.services.time_letter_delivery_effects import (
    TimeLetterDeliveryContractError,
    TimeLetterDeliveryDisposition,
    build_time_letter_delivery_plan,
)


def _sealed_item(**overrides):
    item = {
        "id": "letter-001",
        "kind": "timeLetter",
        "ownerSubjectId": "owner-001",
        "vaultId": "vault-001",
        "authorityEpoch": 7,
        "sealedVersion": 3,
        "sealedPayloadHash": sha256(b"sealed-letter-v3").hexdigest(),
        "deliveryState": "sealed",
        "deliveryStatus": "scheduled",
        "openAt": "2026-07-20T09:00:00Z",
        "recipients": [
            {"id": "family-001", "name": "Alice", "type": "family", "subjectId": "member-001"},
            {"id": "family-001", "name": "Duplicate", "type": "family", "subjectId": "member-001"},
            {"id": "family-002", "name": "Missing subject", "type": "family"},
        ],
        "title": "This title must not enter the effect contract",
        "note": "This body must not enter the effect contract",
    }
    item.update(overrides)
    return item


class TimeLetterDeliveryEffectsTests(unittest.TestCase):
    def test_due_plan_has_one_owner_and_one_target_per_recipient(self):
        plan = build_time_letter_delivery_plan(
            _sealed_item(),
            now_iso="2026-07-20T09:00:01Z",
        )

        self.assertTrue(plan.is_due)
        self.assertEqual([target.recipient_id for target in plan.targets], ["self", "family-001", "family-002"])
        self.assertEqual(plan.targets[0].recipient_subject_id, "owner-001")
        self.assertEqual(plan.targets[1].recipient_subject_id, "member-001")
        self.assertIsNone(plan.targets[2].recipient_subject_id)
        self.assertEqual(len(plan.effect_intents), 3)
        self.assertEqual(
            [intent.target.resource_type for intent in plan.effect_intents],
            ["timeLetterDeliveryTarget"] * 3,
        )
        self.assertEqual(
            [intent.target.resource_version for intent in plan.effect_intents],
            [3, 3, 3],
        )
        self.assertEqual(
            [intent.target.purpose for intent in plan.effect_intents],
            ["timeLetterDelivery"] * 3,
        )

    def test_effect_target_is_stable_for_same_letter_version_and_recipient(self):
        first = build_time_letter_delivery_plan(
            _sealed_item(),
            now_iso="2026-07-20T09:00:01Z",
        )
        renamed = _sealed_item(
            title="Changed visible title",
            note="Changed body is invalid without a new sealed payload hash",
            recipients=[
                {"id": "family-001", "name": "Renamed", "type": "family", "subjectId": "member-001"},
                {"id": "family-002", "name": "Also renamed", "type": "family"},
            ],
        )
        second = build_time_letter_delivery_plan(
            renamed,
            now_iso="2026-07-20T09:00:01Z",
        )

        self.assertEqual(
            [target.stable_target_key for target in first.targets],
            [target.stable_target_key for target in second.targets],
        )
        self.assertEqual(
            [intent.stable_key for intent in first.effect_intents],
            [intent.stable_key for intent in second.effect_intents],
        )

    def test_changed_sealed_payload_hash_conflicts_with_existing_effect(self):
        first = build_time_letter_delivery_plan(
            _sealed_item(),
            now_iso="2026-07-20T09:00:01Z",
        )
        changed_hash = build_time_letter_delivery_plan(
            _sealed_item(sealedPayloadHash=sha256(b"changed-sealed-letter-v3").hexdigest()),
            now_iso="2026-07-20T09:00:01Z",
        )
        repository = InMemoryEffectKernelRepository()
        repository.accept(first.effect_intents[0])

        with self.assertRaisesRegex(AsyncEffectConflict, "stable effect key"):
            repository.accept(changed_hash.effect_intents[0])

    def test_not_due_plan_has_explicit_not_due_dispositions_and_no_effects(self):
        plan = build_time_letter_delivery_plan(
            _sealed_item(),
            now_iso="2026-07-20T08:59:59Z",
        )

        self.assertFalse(plan.is_due)
        self.assertEqual(plan.effect_intents, ())
        self.assertEqual(
            [admission.disposition for admission in plan.admissions],
            [TimeLetterDeliveryDisposition.NOT_DUE] * 3,
        )
        self.assertEqual(
            [admission.reason_code for admission in plan.admissions],
            ["timeLetterNotOpen"] * 3,
        )

    def test_missing_immutable_sealed_envelope_fails_closed(self):
        missing_version = _sealed_item(sealedVersion=None)
        missing_hash = _sealed_item(sealedPayloadHash=None)
        legacy_owner_only = _sealed_item(ownerSubjectId=None)

        for item in (missing_version, missing_hash, legacy_owner_only):
            with self.subTest(item=item):
                with self.assertRaises(TimeLetterDeliveryContractError):
                    build_time_letter_delivery_plan(item, now_iso="2026-07-20T09:00:01Z")

    def test_value_free_summary_does_not_contain_title_note_or_recipient_names(self):
        plan = build_time_letter_delivery_plan(
            _sealed_item(),
            now_iso="2026-07-20T09:00:01Z",
        )
        summary = plan.value_free_summary()
        serialized = str(summary)

        self.assertNotIn("This title", serialized)
        self.assertNotIn("This body", serialized)
        self.assertNotIn("Alice", serialized)
        self.assertEqual(summary["schemaVersion"], "time-letter-delivery-v1")
        self.assertEqual(summary["targetCount"], 3)
        self.assertEqual(summary["sealedVersion"], 3)


if __name__ == "__main__":
    unittest.main()
