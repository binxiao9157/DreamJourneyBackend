from __future__ import annotations

import unittest
from uuid import NAMESPACE_URL, uuid5

from app.domain.owner_truth.formal_fact_eligibility import (
    evaluate_formal_fact_eligibility,
)
from app.domain.owner_truth.memory_projection import (
    OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.search_documents import (
    build_owner_truth_search_document_projection,
)
from app.services.formal_memory_conversation_snapshot import (
    FormalMemoryConversationSnapshotService,
)


def _id(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"owner-truth-eligibility:{label}"))


V_OLD = _id("version-old")
V_CURRENT = _id("version-current")
V_DISPUTED = _id("version-disputed")
V_UNGROUPED = _id("version-ungrouped")


def _entry(version_id: str, statement: str) -> dict[str, object]:
    return {
        "memoryVersionId": version_id,
        "memoryKind": "knowledge",
        "content": {"claim": statement},
        "citation": {
            "memoryId": _id(f"memory-{version_id}"),
            "memoryVersionId": version_id,
            "sourceId": _id(f"source-{version_id}"),
            "sourceVersion": 1,
            "contentHash": f"hash-{version_id}",
        },
        "memoryVersion": 1,
        "visibility": "owner",
        "perspectiveType": "firstPerson",
        "sensitivity": "standard",
        "contentSchemaVersion": "owner-truth-v1",
    }


def _projection() -> dict[str, object]:
    return {
        "schemaVersion": OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION,
        "state": "ready",
        "vaultId": "vault-eligibility",
        "ownerSubjectId": "owner-eligibility",
        "authorityEpoch": 2,
        "memoryRevision": 4,
        "rightsState": "active",
        "checkpoint": "checkpoint-2",
        "entries": [
            _entry(V_OLD, "我曾在 A 大学学习。"),
            _entry(V_CURRENT, "我本科毕业于 A 大学计算机专业。"),
            _entry(V_DISPUTED, "我毕业于 B 大学。"),
            _entry(V_UNGROUPED, "我现在住在上海。"),
        ],
        "personMemoryModel": {
            "semanticConsolidation": {
                "groups": [
                    {
                        "groupId": "education-current",
                        "status": "merged",
                        "memoryKind": "knowledge",
                        "representativeMemoryVersionId": V_CURRENT,
                        "supportingMemoryVersionIds": [V_OLD, V_CURRENT],
                        "narrative": "我本科毕业于 A 大学计算机专业。",
                    },
                    {
                        "groupId": "education-disputed",
                        "status": "disputed",
                        "memoryKind": "knowledge",
                        "representativeMemoryVersionId": V_DISPUTED,
                        "supportingMemoryVersionIds": [V_DISPUTED],
                        "narrative": "我毕业于 B 大学。",
                    },
                ]
            }
        },
    }


class _ProjectionStore:
    def __init__(self, projection: dict[str, object]) -> None:
        self.projection = projection

    def owner_truth_memory_projection_repository(self):
        return self

    def read(self, *, context):
        del context
        return self.projection


class FormalFactEligibilityTests(unittest.TestCase):
    def test_one_shared_rule_keeps_representative_and_ungrouped_facts_only(self) -> None:
        result = evaluate_formal_fact_eligibility(_projection())

        self.assertEqual(
            result.eligible_memory_version_ids,
            frozenset({V_CURRENT, V_UNGROUPED}),
        )
        self.assertEqual(
            result.public_summary()["suppressionReasons"],
            {
                "semanticGroupNonRepresentative": 1,
                "semanticGroupNotReady": 1,
            },
        )

    def test_live_snapshot_and_search_use_the_same_eligible_versions(self) -> None:
        projection = _projection()
        context = OwnerTruthCommandContext(
            vault_id="vault-eligibility",
            owner_subject_id="owner-eligibility",
            actor_subject_id="owner-eligibility",
        )

        snapshot = FormalMemoryConversationSnapshotService(
            _ProjectionStore(projection)
        ).build(context=context)
        search_projection = build_owner_truth_search_document_projection(
            memory_projection=projection
        )
        assert search_projection is not None

        self.assertEqual(
            {item["sourceMemoryVersionIds"][0] for item in snapshot["coreFacts"]},
            {V_CURRENT, V_UNGROUPED},
        )
        self.assertEqual(
            {item.memory_version_id for item in search_projection.documents},
            {V_CURRENT, V_UNGROUPED},
        )
        # The two unusable semantic-group facts are surfaced through the
        # eligibility summary, not misreported as a transport-budget omission.
        self.assertEqual(snapshot["coverage"]["omittedFactCount"], 0)
        self.assertEqual(snapshot["factEligibility"]["suppressedFactCount"], 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
