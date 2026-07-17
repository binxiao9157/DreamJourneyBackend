import unittest

from app.services.archive_store import (
    ArchiveItemDeletionForbidden,
    ArchiveItemNotFound,
    ResourceOwnershipConflict,
    ResourceVersionConflict,
)
from app.services.in_memory_store import InMemoryStore
from app.services.knowledge_store import (
    KnowledgeOperationPayloadConflict,
    KnowledgeRevisionConflict,
)


PRIVACY_METADATA = {"privacyMetadata": {"scope": "generationAllowed"}}


def delete_fact_mutation():
    return {
        "upserts": {},
        "tombstones": [
            {
                "entityType": "facts",
                "entityId": "fact-1",
                "deletedAt": "2026-07-11T00:00:00Z",
            }
        ],
    }


class InMemoryArchiveDeletionTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.store.apply_kb_mutation(
            "u1",
            {"facts": [{"id": "fact-1", "statement": "source", **PRIVACY_METADATA}]},
            operation_id="seed",
            base_revision=0,
        )

    def add_archive(self, **overrides):
        return self.store.add_archive_item(
            "u1",
            {"id": "archive-1", "kind": "photo", **overrides},
        )

    def test_delete_archive_item_and_apply_kb_mutation_atomically(self):
        item = self.add_archive()

        result = self.store.delete_archive_item_with_kb_mutation(
            "u1",
            "archive-1",
            operation_id="delete-1",
            base_revision=1,
            mutation=delete_fact_mutation(),
        )

        self.assertEqual(
            set(result),
            {
                "item",
                "duplicate",
                "operationPayloadVerified",
                "revision",
                "graph",
                "mutationSchemaVersion",
                "mutation",
            },
        )
        self.assertEqual(result["item"], item)
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["graph"]["facts"], [])
        self.assertEqual(result["mutationSchemaVersion"], 2)
        self.assertEqual(self.store.list_archive_items("u1"), [])
        self.assertEqual(len(self.store.list_kb_changes("u1", 0)), 2)

    def test_stale_resource_version_keeps_archive_and_knowledge_unchanged(self):
        item = self.add_archive(resourceVersion=2)

        with self.assertRaises(ResourceVersionConflict) as context:
            self.store.delete_archive_item_with_kb_mutation(
                "u1",
                "archive-1",
                operation_id="stale-resource-delete",
                base_revision=1,
                expected_version=1,
                mutation=delete_fact_mutation(),
            )

        self.assertEqual(context.exception.expected_version, 1)
        self.assertEqual(context.exception.current_version, 2)
        self.assertEqual(self.store.list_archive_items("u1"), [item])
        self.assertEqual(self.store.get_kb_snapshot_record("u1")["revision"], 1)

    def test_delete_without_mutation_keeps_current_revision_and_change_feed(self):
        item = self.add_archive()

        result = self.store.delete_archive_item_with_kb_mutation(
            "u1",
            "archive-1",
            operation_id="delete-only",
            base_revision=1,
        )

        self.assertEqual(result["item"], item)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["graph"]["facts"][0]["id"], "fact-1")
        self.assertIsNone(result["mutationSchemaVersion"])
        self.assertIsNone(result["mutation"])
        self.assertEqual(len(self.store.list_kb_changes("u1", 0)), 1)

    def test_revision_conflict_keeps_archive_and_knowledge_unchanged(self):
        item = self.add_archive()

        with self.assertRaises(KnowledgeRevisionConflict):
            self.store.delete_archive_item_with_kb_mutation(
                "u1",
                "archive-1",
                operation_id="stale-delete",
                base_revision=0,
                mutation=delete_fact_mutation(),
            )

        self.assertEqual(self.store.list_archive_items("u1"), [item])
        self.assertEqual(self.store.get_kb_snapshot_record("u1")["revision"], 1)
        self.assertEqual(len(self.store.list_kb_changes("u1", 0)), 1)

    def test_sealed_time_letter_is_not_deleted(self):
        item = self.add_archive(
            kind="timeLetter",
            metadata={"deliveryState": "sealed"},
        )

        with self.assertRaisesRegex(
            ArchiveItemDeletionForbidden,
            "sealed timeLetter cannot be deleted",
        ):
            self.store.delete_archive_item_with_kb_mutation(
                "u1",
                "archive-1",
                operation_id="sealed-delete",
                base_revision=1,
            )

        self.assertEqual(self.store.list_archive_items("u1"), [item])

    def test_missing_archive_item_raises_recognizable_error(self):
        with self.assertRaisesRegex(ArchiveItemNotFound, "archive item not found"):
            self.store.delete_archive_item_with_kb_mutation(
                "u1",
                "missing",
                operation_id="missing-delete",
                base_revision=1,
            )

    def test_duplicate_mutation_returns_stored_result_without_new_revision(self):
        self.add_archive()
        first = self.store.delete_archive_item_with_kb_mutation(
            "u1",
            "archive-1",
            operation_id="duplicate-delete",
            base_revision=1,
            mutation=delete_fact_mutation(),
        )

        repeated = self.store.delete_archive_item_with_kb_mutation(
            "u1",
            "archive-1",
            operation_id="duplicate-delete",
            base_revision=0,
            mutation={"upserts": {}, "tombstones": []},
        )

        self.assertTrue(repeated["duplicate"])
        self.assertTrue(repeated["operationPayloadVerified"])
        self.assertIsNone(repeated["item"])
        self.assertEqual(repeated["revision"], first["revision"])
        self.assertEqual(repeated["graph"], first["graph"])
        self.assertEqual(repeated["mutation"], first["mutation"])
        self.assertEqual(len(self.store.list_kb_changes("u1", 0)), 2)

    def test_operation_id_cannot_be_reused_for_another_archive_item(self):
        self.add_archive()
        self.store.add_archive_item("u1", {"id": "archive-2", "kind": "photo"})
        self.store.delete_archive_item_with_kb_mutation(
            "u1",
            "archive-1",
            operation_id="shared-delete",
            base_revision=1,
        )

        with self.assertRaises(KnowledgeOperationPayloadConflict):
            self.store.delete_archive_item_with_kb_mutation(
                "u1",
                "archive-2",
                operation_id="shared-delete",
                base_revision=1,
            )

    def test_resource_ids_cannot_be_reused_to_transfer_owner(self):
        writers = (
            (self.store.add_memory, {"id": "shared-memory"}),
            (self.store.add_family_member, {"id": "shared-family", "name": "家人"}),
            (self.store.add_mailbox_letter, {"id": "shared-mailbox"}),
            (
                self.store.add_echo_delayed_reply,
                {"id": "shared-echo", "delayedReplyId": "shared-echo"},
            ),
            (
                self.store.save_push_device_token,
                {"id": "shared-push", "deviceTokenId": "shared-push"},
            ),
        )

        for writer, payload in writers:
            with self.subTest(resource_id=payload["id"]):
                writer("u1", payload)
                with self.assertRaisesRegex(ResourceOwnershipConflict, "another owner"):
                    writer("u2", payload)


if __name__ == "__main__":
    unittest.main()
