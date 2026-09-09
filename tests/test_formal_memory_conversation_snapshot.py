import json
import unittest
from unittest.mock import patch

import app.services.formal_memory_conversation_snapshot as snapshot_module
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
        "rightsState": "active",
        "authorityEpoch": 7,
        "memoryRevision": 12,
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
        self.assertEqual(
            {item["statement"] for item in first["coreFacts"]},
            {"本科毕业于 A 大学计算机专业。", "小时候住在河边的小村庄。"},
        )

    def test_snapshot_hash_excludes_generation_time_but_keeps_authority_binding(self):
        snapshot = FormalMemoryConversationSnapshotService(
            _ProjectionStore(_ready_projection())
        ).build(context=self.context, persona_scope="personal")

        self.assertEqual(snapshot["schemaVersion"], "formal-memory-conversation-v2")
        self.assertEqual(snapshot["subjectId"], "subject-1")
        self.assertEqual(snapshot["authorityEpoch"], 7)
        self.assertTrue(snapshot["contextHash"].startswith("sha256:"))
        self.assertIn("generatedAt", snapshot)
        self.assertEqual(snapshot["coverage"]["eligibleFactCount"], 2)
        self.assertIn("qualifiers", snapshot["coreFacts"][0])
        self.assertIn("provenanceMode", snapshot["coreFacts"][0])

    def test_snapshot_contains_only_current_formal_fact_fields(self):
        snapshot = FormalMemoryConversationSnapshotService(
            _ProjectionStore(_ready_projection())
        ).build(context=self.context)

        serialized = str(snapshot)
        self.assertNotIn("sourceId", serialized)
        self.assertNotIn("candidate", serialized.lower())
        self.assertNotIn("review", serialized.lower())
        self.assertEqual(
            {tuple(item["sourceMemoryVersionIds"]) for item in snapshot["coreFacts"]},
            {("memory-version-school",), ("memory-version-home",)},
        )

    def test_unavailable_projection_fails_closed(self):
        projection = _ready_projection()
        projection["state"] = "rebuilding"

        with self.assertRaises(FormalMemoryConversationSnapshotError) as raised:
            FormalMemoryConversationSnapshotService(
                _ProjectionStore(projection)
            ).build(context=self.context)

        self.assertEqual(raised.exception.code, "formalMemorySnapshotUnavailable")

    def test_revoked_ready_projection_cannot_be_injected_into_live_context(self):
        projection = _ready_projection()
        projection["rightsState"] = "revoked"

        with self.assertRaises(FormalMemoryConversationSnapshotError) as raised:
            FormalMemoryConversationSnapshotService(
                _ProjectionStore(projection)
            ).build(context=self.context)

        self.assertEqual(raised.exception.code, "formalMemorySnapshotUnavailable")

    def test_oversized_projection_is_budgeted_with_explicit_coverage(self):
        projection = _ready_projection()
        projection["entries"].extend(
            {
                "memoryVersionId": f"memory-version-{index}",
                "memoryKind": "knowledge",
                "content": {"claim": f"第 {index} 条已确认事实"},
            }
            for index in range(64)
        )

        snapshot = FormalMemoryConversationSnapshotService(
            _ProjectionStore(projection),
            max_chars=1_024,
        ).build(context=self.context)

        self.assertLessEqual(len(json.dumps(snapshot, ensure_ascii=False)), 1_024)
        self.assertEqual(snapshot["coverage"]["eligibleFactCount"], 66)
        self.assertTrue(snapshot["coverage"]["truncated"])
        self.assertGreater(snapshot["coverage"]["omittedFactCount"], 0)

    def test_oversized_projection_uses_bounded_serialization_work(self):
        projection = _ready_projection()
        projection["entries"].extend(
            {
                "memoryVersionId": f"memory-version-large-{index}",
                "memoryKind": "knowledge",
                "content": {"claim": (f"第 {index} 条已确认事实 " + "内容" * 120)},
            }
            for index in range(512)
        )
        service = FormalMemoryConversationSnapshotService(
            _ProjectionStore(projection),
            max_chars=4_096,
        )

        with patch(
            "app.services.formal_memory_conversation_snapshot._transport_json",
            wraps=snapshot_module._transport_json,
        ) as transport_json:
            snapshot = service.build(context=self.context)

        self.assertTrue(snapshot["coverage"]["truncated"])
        self.assertLessEqual(transport_json.call_count, 12)


if __name__ == "__main__":
    unittest.main()
