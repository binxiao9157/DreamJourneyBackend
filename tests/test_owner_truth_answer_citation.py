from __future__ import annotations

from contextlib import contextmanager
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
from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionAccessDenied
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_answer_citation import (
    InMemoryOwnerTruthAnswerCitationRepository,
    OwnerTruthAnswerCitationCommand,
    OwnerTruthAnswerCitationConflict,
    OwnerTruthAnswerCitationService,
    answer_citation_summary,
)
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)
from app.services.owner_truth_memory_projection import (
    InMemoryOwnerTruthMemoryProjectionRepository,
    OwnerTruthMemoryProjectionService,
)
from app.services.owner_truth_memory_search_projection import (
    InMemoryOwnerTruthMemorySearchDocumentProjectionRepository,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Store:
    def __init__(self) -> None:
        self.review_repository = InMemoryOwnerTruthCandidateReviewRepository()
        self.projection_repository = InMemoryOwnerTruthMemoryProjectionRepository(
            self.review_repository
        )
        self.search_projection_repository = (
            InMemoryOwnerTruthMemorySearchDocumentProjectionRepository(
                self.projection_repository
            )
        )
        self.answer_repository = InMemoryOwnerTruthAnswerCitationRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository

    def owner_truth_memory_search_document_projection_repository(self):
        return self.search_projection_repository

    def owner_truth_answer_citation_repository(self):
        return self.answer_repository


class _InvalidateProjectionBeforeSecondRead:
    """Test double that invalidates a source between Context build and write."""

    def __init__(self, delegate, *, invalidate) -> None:
        self._delegate = delegate
        self._invalidate = invalidate
        self._read_count = 0

    def read(self, *, context):
        self._read_count += 1
        if self._read_count == 2:
            self._invalidate()
        return self._delegate.read(context=context)

    def __getattr__(self, name):
        return getattr(self._delegate, name)


class OwnerTruthAnswerCitationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-answer-citation"
        self.owner_id = "subject-answer-citation"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _Store()
        self.review_service = OwnerTruthCandidateReviewService(self.store)
        self.projection_service = OwnerTruthMemoryProjectionService(self.store)
        self.service = OwnerTruthAnswerCitationService(self.store, enabled=True)

    def _candidate(self, *, kind: MemoryKind, content: dict[str, str]) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=kind,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [
                    {
                        "sourceId": source_id,
                        "sourceVersion": 1,
                        "span": {"start": 0, "end": 10},
                    }
                ],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    def _activate(self, candidate: OwnerTruthCandidateSnapshot, *, command_id: str) -> None:
        self.store.review_repository.seed(candidate)
        self.review_service.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=command_id,
                candidate_id=candidate.candidate_id,
                expected_candidate_version=candidate.row_version,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )

    @staticmethod
    def _command(*, command_id: str, answer_text: str) -> OwnerTruthAnswerCitationCommand:
        return OwnerTruthAnswerCitationCommand(
            command_id=command_id,
            answer_text=answer_text,
        )

    def test_records_only_typed_citations_and_replays_idempotently(self) -> None:
        experience = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "只有已确认的经历才能出现在回答引用中"},
        )
        knowledge = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "只有已确认的知识才能出现在回答引用中"},
        )
        self._activate(experience, command_id="answer-citation-experience")
        self._activate(knowledge, command_id="answer-citation-knowledge")
        self.projection_service.rebuild(context=self.context)

        raw_query = "请总结我的私密记忆"
        raw_answer = "我只会根据你确认过的内容回答。"
        command = self._command(
            command_id="answer-citation-record-001",
            answer_text=raw_answer,
        )
        created = self.service.record(
            context=self.context,
            command=command,
            context_payload={"intent": "echo_chat", "query": raw_query},
        )
        replayed = self.service.record(
            context=self.context,
            command=command,
            context_payload={"intent": "echo_chat", "query": raw_query},
        )
        summary = answer_citation_summary(created)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.answer_id, replayed.answer_id)
        self.assertEqual(created.context_version, "echo-context-v4-shadow")
        self.assertEqual(created.citation_count, 2)
        self.assertEqual(created.fallbacks, ())
        self.assertTrue(created.context_hash)
        self.assertTrue(created.answer_hash)
        self.assertEqual(
            {item["citation"]["sourceId"] for item in created.citations},
            {experience.source_id, knowledge.source_id},
        )
        self.assertTrue(all(item["resolved"] is True for item in created.citations))
        self.assertNotIn(raw_query, str(summary))
        self.assertNotIn(raw_answer, str(summary))
        self.assertNotIn(experience.content["summary"], str(summary))
        self.assertNotIn(knowledge.content["claim"], str(summary))

    def test_projection_unavailable_records_explicit_no_personal_memory_fallback(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "未重建投影时不得进入答案引用"},
        )
        self._activate(candidate, command_id="answer-citation-stale")

        result = self.service.record(
            context=self.context,
            command=self._command(
                command_id="answer-citation-fallback-001",
                answer_text="我暂时没有足够的已确认个人记忆可以引用。",
            ),
            context_payload={"query": "投影未就绪时不读取旧档案"},
        )

        self.assertEqual(result.citation_count, 0)
        self.assertEqual(
            result.fallbacks,
            ("owner_truth_context_unavailable_no_personal_memory",),
        )

    def test_query_ranked_answer_citation_uses_only_matching_confirmed_memory(self) -> None:
        matched = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "父亲修好自行车后带我去公园"},
        )
        unmatched = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "夏天的海边总有温暖的风"},
        )
        self._activate(matched, command_id="answer-citation-query-matched")
        self._activate(unmatched, command_id="answer-citation-query-unmatched")
        self.projection_service.rebuild(context=self.context)
        self.store.search_projection_repository.rebuild(context=self.context)

        result = self.service.record(
            context=self.context,
            command=self._command(
                command_id="answer-citation-query-ranked-001",
                answer_text="我只引用与你的问题有关的已确认记忆。",
            ),
            context_payload={
                "intent": "echo_chat",
                "query": "自行车",
                "selectionMode": "deterministicTextFallback",
            },
        )

        self.assertEqual(result.citation_count, 1)
        self.assertEqual(result.citations[0]["citation"]["sourceId"], matched.source_id)
        self.assertEqual(result.fallbacks, ())

    def test_rejects_context_that_becomes_stale_before_answer_evidence_write(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "来源撤回后不得保留为新的答案引用"},
        )
        self._activate(candidate, command_id="answer-citation-currentness")
        self.projection_service.rebuild(context=self.context)
        original_repository = self.store.projection_repository
        self.store.projection_repository = _InvalidateProjectionBeforeSecondRead(
            original_repository,
            invalidate=lambda: self.store.review_repository._source_states.__setitem__(
                (self.vault_id, candidate.source_id),
                "deleted",
            ),
        )

        with self.assertRaises(OwnerTruthAnswerCitationConflict):
            self.service.record(
                context=self.context,
                command=self._command(
                    command_id="answer-citation-stale-before-write-001",
                    answer_text="这个证据不能在来源撤回后写入。",
                ),
                context_payload={"query": "不应写入已经撤回的来源"},
            )

        self.assertEqual(self.store.answer_repository.snapshot()["records"], [])

    def test_rejects_non_owner_and_conflicting_command_reuse(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "跨 Vault 或跨 Owner 不得读取答案引用"},
        )
        self._activate(candidate, command_id="answer-citation-access")
        self.projection_service.rebuild(context=self.context)

        non_owner = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id="different-subject",
        )
        with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
            self.service.record(
                context=non_owner,
                command=self._command(
                    command_id="answer-citation-denied-001",
                    answer_text="不应创建。",
                ),
                context_payload={"query": "不应读取"},
            )

        command = self._command(
            command_id="answer-citation-conflict-001",
            answer_text="第一个答案摘要。",
        )
        self.service.record(
            context=self.context,
            command=command,
            context_payload={"query": "同一个命令"},
        )
        with self.assertRaises(OwnerTruthAnswerCitationConflict):
            self.service.record(
                context=self.context,
                command=self._command(
                    command_id="answer-citation-conflict-001",
                    answer_text="不同的答案摘要。",
                ),
                context_payload={"query": "同一个命令"},
            )


if __name__ == "__main__":
    unittest.main()
