from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION_V2
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_formal_memory import (
    OwnerTruthFormalMemoryConflict,
    OwnerTruthFormalMemoryCorrectionCommand,
    OwnerTruthFormalMemoryCursor,
    OwnerTruthFormalMemoryError,
    OwnerTruthFormalMemoryFacetFilter,
    OwnerTruthFormalMemoryQuery,
    OwnerTruthFormalMemoryService,
)


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _content(summary: str, *, place: str) -> dict[str, object]:
    return {
        "summary": summary,
        "facets": {
            "people": [],
            "time": [],
            "places": [{"value": place, "evidenceMode": "ownerStated", "confidence": 1.0}],
            "relationships": [],
            "emotions": [],
            "values": [],
            "personality": [],
            "confidence": 1.0,
        },
    }


class OwnerTruthFormalMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.context = OwnerTruthCommandContext(
            vault_id="vault-formal-memory",
            owner_subject_id="owner-formal-memory",
            actor_subject_id="owner-formal-memory",
        )
        self.review = OwnerTruthCandidateReviewService(self.store)
        self.service = OwnerTruthFormalMemoryService(self.store)

    def _activate(self, summary: str, *, place: str, created_at: str) -> tuple[str, str]:
        source_id = str(uuid4())
        candidate_id = str(uuid4())
        content = _content(summary, place=place)
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=candidate_id,
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=self.context.policy_version,
            authority_epoch=0,
            row_version=1,
            content_hash=_digest(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
            payload={
                "schemaVersion": "owner-truth-candidate-v1",
                "candidateKind": "experience",
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V2,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
            },
        )
        self.store.owner_truth_candidate_review_repository().seed(candidate, created_at=created_at)
        result = self.review.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=f"activate-{candidate_id}",
                candidate_id=candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )
        return result.memory_activation.memory_id or "", result.memory_activation.memory_version_id or ""

    def test_list_supports_query_kind_facet_and_cursor(self) -> None:
        first_id, _ = self._activate(
            "在老院子里听外祖父讲故事",
            place="老院子",
            created_at="2026-08-18T10:00:00+00:00",
        )
        second_id, _ = self._activate(
            "第一次去海边看日出",
            place="海边",
            created_at="2026-08-19T10:00:00+00:00",
        )

        first_page = self.service.list(
            context=self.context,
            query=OwnerTruthFormalMemoryQuery(limit=1),
        )
        self.assertEqual([item.memory_id for item in first_page.items], [second_id])
        self.assertIsNotNone(first_page.next_cursor)
        second_page = self.service.list(
            context=self.context,
            query=OwnerTruthFormalMemoryQuery(
                limit=1,
                cursor=OwnerTruthFormalMemoryCursor.decode(first_page.next_cursor),
            ),
        )
        self.assertEqual([item.memory_id for item in second_page.items], [first_id])

        filtered = self.service.list(
            context=self.context,
            query=OwnerTruthFormalMemoryQuery(
                kind="experience",
                query="外祖父",
                facets=(OwnerTruthFormalMemoryFacetFilter(name="places", value="老院子"),),
            ),
        )
        self.assertEqual([item.memory_id for item in filtered.items], [first_id])

    def test_confirmed_correction_is_idempotent_and_exposes_current_plus_three_history(self) -> None:
        memory_id, _ = self._activate(
            "第一版记忆",
            place="老院子",
            created_at="2026-08-18T10:00:00+00:00",
        )
        first_command: OwnerTruthFormalMemoryCorrectionCommand | None = None
        first_result = None
        for revision in range(2, 6):
            detail = self.service.detail(context=self.context, memory_id=memory_id)
            command = OwnerTruthFormalMemoryCorrectionCommand(
                command_id=f"formal-memory-edit-{revision}",
                expected_version=detail.current_version.version_number,
                expected_content_hash=detail.current_version.content_hash,
                expected_content_schema_version=detail.current_version.content_schema_version,
                content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                corrected_content=_content(f"第{revision}版记忆", place="老院子"),
                second_confirmation=True,
            )
            result = self.service.correct(
                context=self.context,
                memory_id=memory_id,
                command=command,
            )
            self.assertEqual(result.replacement_version, revision)
            if revision == 2:
                first_command = command
                first_result = result

        history = self.service.detail(context=self.context, memory_id=memory_id)
        self.assertEqual(history.current_version.version_number, 5)
        self.assertEqual([item.version_number for item in history.versions], [5, 4, 3, 2])
        self.assertTrue(history.history_truncated)
        self.assertEqual(history.versions[0].content["summary"], "第5版记忆")

        replay = self.service.correct(
            context=self.context,
            memory_id=memory_id,
            command=first_command,
        )
        self.assertEqual(replay.outcome, "deduplicated")
        self.assertEqual(replay.replacement_version_id, first_result.replacement_version_id)

    def test_stale_or_unconfirmed_correction_never_writes(self) -> None:
        memory_id, _ = self._activate(
            "仍然有效的版本",
            place="家中",
            created_at="2026-08-18T10:00:00+00:00",
        )
        detail = self.service.detail(context=self.context, memory_id=memory_id)
        with self.assertRaises(OwnerTruthFormalMemoryError):
            OwnerTruthFormalMemoryCorrectionCommand(
                command_id="unconfirmed-edit",
                expected_version=detail.current_version.version_number,
                expected_content_hash=detail.current_version.content_hash,
                expected_content_schema_version=detail.current_version.content_schema_version,
                content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                corrected_content=_content("不应写入", place="家中"),
                second_confirmation=False,
            )
        with self.assertRaises(OwnerTruthFormalMemoryConflict):
            self.service.correct(
                context=self.context,
                memory_id=memory_id,
                command=OwnerTruthFormalMemoryCorrectionCommand(
                    command_id="stale-edit",
                    expected_version=detail.current_version.version_number + 1,
                    expected_content_hash=detail.current_version.content_hash,
                    expected_content_schema_version=detail.current_version.content_schema_version,
                    content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                    corrected_content=_content("不应写入", place="家中"),
                    second_confirmation=True,
                ),
            )
        after = self.service.detail(context=self.context, memory_id=memory_id)
        self.assertEqual(after.current_version.version_number, 1)


if __name__ == "__main__":
    unittest.main()
