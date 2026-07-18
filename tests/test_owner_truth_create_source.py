import uuid
import unittest
from typing import Optional

from fastapi.testclient import TestClient

from app import main as main_module
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
    OwnerTruthSourceCommandConflict,
    OwnerTruthSourceVersionConflict,
)
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_source import (
    ArchiveOwnerTruthCompatibilityFacade,
    OwnerTruthSourceCommandService,
)


class OwnerTruthCreateSourceCommandTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.service = OwnerTruthSourceCommandService(self.store)
        self.context = OwnerTruthCommandContext(
            vault_id="owner-source-vault",
            owner_subject_id="owner-source",
            actor_subject_id="owner-source",
            policy_version="owner-truth-v1",
        )

    def command(
        self,
        *,
        command_id: str = "command-source-1",
        source_id: Optional[str] = None,
        expected_version: int = 0,
        text: str = "我小时候常在河边听外公讲故事。",
    ) -> CreateTextSourceCommand:
        return CreateTextSourceCommand(
            command_id=command_id,
            source_id=source_id or str(uuid.uuid4()),
            expected_version=expected_version,
            text=text,
            metadata={"title": "一段回忆"},
        )

    def test_same_command_replays_the_original_receipt_without_duplicate_source(self):
        command = self.command()

        created = self.service.create_text_source(command=command, context=self.context)
        replayed = self.service.create_text_source(command=command, context=self.context)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(replayed.source_id, created.source_id)
        self.assertEqual(replayed.receipt_id, created.receipt_id)
        self.assertEqual(replayed.source_version, 1)
        self.assertEqual(self.store.owner_truth_source_count(self.context.vault_id), 1)

    def test_reusing_command_id_with_changed_payload_is_an_explicit_conflict(self):
        command = self.command(command_id="command-source-conflict")
        self.service.create_text_source(command=command, context=self.context)

        with self.assertRaises(OwnerTruthSourceCommandConflict):
            self.service.create_text_source(
                command=self.command(
                    command_id="command-source-conflict",
                    source_id=command.source_id,
                    text="同一个 commandId 不能替换另一段内容。",
                ),
                context=self.context,
            )

    def test_new_source_rejects_nonzero_expected_version(self):
        with self.assertRaises(OwnerTruthSourceVersionConflict):
            self.service.create_text_source(
                command=self.command(expected_version=1),
                context=self.context,
            )

    def test_compatibility_facade_only_shadows_text_archive_items(self):
        facade = ArchiveOwnerTruthCompatibilityFacade(self.store)
        text_item = {
            "id": "legacy-text-archive-1",
            "kind": "text",
            "title": "河边故事",
            "note": "我小时候常在河边听外公讲故事。",
        }

        created = facade.shadow_archive_item(owner_subject_id="owner-source", item=text_item)
        replayed = facade.shadow_archive_item(owner_subject_id="owner-source", item=text_item)
        photo = facade.shadow_archive_item(
            owner_subject_id="owner-source",
            item={"id": "legacy-photo-1", "kind": "photo", "title": "本地照片"},
        )

        self.assertEqual(created.status, "created")
        self.assertEqual(replayed.status, "deduplicated")
        self.assertEqual(photo.status, "skipped")
        self.assertEqual(photo.reason, "localOnlyMedia")
        self.assertEqual(self.store.owner_truth_source_count("owner-source"), 1)


class ArchiveOwnerTruthCompatibilityAPITests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        main_module.store = InMemoryStore()
        self.client = TestClient(app)

    def tearDown(self):
        main_module.store = self.previous_store

    def test_text_archive_save_returns_shadow_receipt_and_photo_is_explicitly_not_shadowed(self):
        text_payload = {
            "userId": "archive-owner-source",
            "id": "archive-text-source-1",
            "kind": "text",
            "title": "河边故事",
            "note": "我小时候常在河边听外公讲故事。",
            "privacyMetadata": {"scope": "generationAllowed"},
        }

        created = self.client.post("/archive/items", json=text_payload)
        replayed = self.client.post("/archive/items", json=text_payload)
        photo = self.client.post(
            "/archive/items",
            json={
                "userId": "archive-owner-source",
                "id": "archive-photo-local-only-1",
                "kind": "photo",
                "title": "本地照片",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["ownerTruthShadow"]["status"], "created")
        self.assertEqual(replayed.status_code, 200)
        self.assertEqual(replayed.json()["ownerTruthShadow"]["status"], "deduplicated")
        self.assertEqual(photo.status_code, 200)
        self.assertEqual(photo.json()["ownerTruthShadow"], {
            "status": "skipped",
            "reason": "localOnlyMedia",
        })


if __name__ == "__main__":
    unittest.main()
