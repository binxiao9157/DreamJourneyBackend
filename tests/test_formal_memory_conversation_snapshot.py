import unittest

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.formal_memory_conversation_snapshot import (
    FormalMemoryConversationSnapshotError,
    FormalMemoryConversationSnapshotService,
)


class _ProjectionStore:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def owner_truth_memory_projection_repository(self):
        return self

    def read(self, *, context):
        return self.snapshot


def _ready_projection():
    return {
        "state": "ready",
        "authorityEpoch": 7,
        "checkpoint": "checkpoint-7",
        "entries": [
            {
                "memoryVersionId": "memory-version-school",
                "memoryKind": "knowledge",
                "content": {"claim": "本科毕业于 A 大学计算机专业。"},
            },
            {
                "memoryVersionId": "memory-version-home",
                "memoryKind": "experience",
                "content": {"summary": "小时候住在河边的小村庄。"},
            },
        ],
        "personMemoryModel": {"semanticConsolidation": {"groups": []}},
    }


class FormalMemoryConversationSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.context = OwnerTruthCommandContext(
            vault_id="vault-1",
            owner_subject_id="subject-1",
            actor_subject_id="subject-1",
        )

    def test_snapshot_is_deterministic_for_same_projection_checkpoint(self):
        service = FormalMemoryConversationSnapshotService(_ProjectionStore(_ready_projection()))

        first = service.build(context=self.context)
        second = service.build(context=self.context)

        self.assertEqual(first["contextHash"], second["contextHash"])
        self.assertEqual(first["coreFacts"], second["coreFacts"])
        self.assertEqual(first["projectionCheckpoint"], "checkpoint-7")
        self.assertEqual(first["coreFacts"][0]["statement"], "本科毕业于 A 大学计算机专业。")

    def test_snapshot_hash_excludes_generation_time_but_keeps_authority_binding(self):
        snapshot = FormalMemoryConversationSnapshotService(
            _ProjectionStore(_ready_projection())
        ).build(context=self.context, persona_scope="personal")

        self.assertEqual(snapshot["schemaVersion"], "formal-memory-conversation-v1")
        self.assertEqual(snapshot["subjectId"], "subject-1")
        self.assertEqual(snapshot["authorityEpoch"], 7)
        self.assertTrue(snapshot["contextHash"].startswith("sha256:"))
        self.assertIn("generatedAt", snapshot)

    def test_snapshot_contains_only_current_formal_fact_fields(self):
        snapshot = FormalMemoryConversationSnapshotService(
            _ProjectionStore(_ready_projection())
        ).build(context=self.context)

        serialized = str(snapshot)
        self.assertNotIn("sourceId", serialized)
        self.assertNotIn("candidate", serialized.lower())
        self.assertNotIn("review", serialized.lower())
        self.assertEqual(
            [item["sourceMemoryVersionIds"] for item in snapshot["coreFacts"]],
            [["memory-version-school"], ["memory-version-home"]],
        )

    def test_unavailable_projection_fails_closed(self):
        projection = _ready_projection()
        projection["state"] = "rebuilding"

        with self.assertRaises(FormalMemoryConversationSnapshotError) as raised:
            FormalMemoryConversationSnapshotService(
                _ProjectionStore(projection)
            ).build(context=self.context)

        self.assertEqual(raised.exception.code, "formalMemorySnapshotUnavailable")

    def test_oversized_projection_is_not_silently_truncated(self):
        projection = _ready_projection()
        projection["entries"][0]["content"]["claim"] = "事实 " * 2_000

        with self.assertRaises(FormalMemoryConversationSnapshotError) as raised:
            FormalMemoryConversationSnapshotService(
                _ProjectionStore(projection),
                max_chars=1_024,
            ).build(context=self.context)

        self.assertEqual(raised.exception.code, "formalMemorySnapshotTooLarge")


if __name__ == "__main__":
    unittest.main()
