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
        self.answer_repository = InMemoryOwnerTruthAnswerCitationRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository

    def owner_truth_answer_citation_repository(self):
        return self.answer_repository


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
