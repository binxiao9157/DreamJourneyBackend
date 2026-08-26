from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.core.config import Settings
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
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)
from app.services.owner_truth_context_shadow import (
    OwnerTruthContextShadowReadService,
    context_shadow_summary,
)
from app.services.owner_truth_context_shadow_build import (
    OwnerTruthContextShadowBuildService,
    context_shadow_build_summary,
)
from app.services.owner_truth_context_shadow_compare import (
    OwnerTruthContextShadowCompareError,
    OwnerTruthContextShadowCompareService,
)
from app.services.owner_truth_context_materialization import (
    OwnerTruthContextMaterializationService,
    context_materialization_summary,
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
        self.legacy_kblite_read_count = 0
        self.projection_repository = InMemoryOwnerTruthMemoryProjectionRepository(
            self.review_repository
        )
        self.search_projection_repository = (
            InMemoryOwnerTruthMemorySearchDocumentProjectionRepository(
                self.projection_repository
            )
        )

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository

    def owner_truth_memory_search_document_projection_repository(self):
        return self.search_projection_repository

    def get_kb_snapshot(self, _user_id: str):
        self.legacy_kblite_read_count += 1
        return {"people": [], "places": [], "events": [], "facts": []}

    def list_archive_items(self, _user_id: str):
        return []

    def get_latest_care_snapshot(self, _user_id: str, viewer_family_member_id=None):
        return None

    def list_voice_profiles(self, _user_id: str):
        return []

    def get_family_relationship_by_member(self, _user_id: str, _member_id: str):
        return None


class OwnerTruthContextShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-context-shadow"
        self.owner_id = "subject-context-shadow"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _Store()
        self.review_service = OwnerTruthCandidateReviewService(self.store)
        self.projection_service = OwnerTruthMemoryProjectionService(self.store)

    def _candidate(
        self,
        *,
        kind: MemoryKind,
        content: dict[str, str],
        sensitivity: SensitivityLevel = SensitivityLevel.STANDARD,
        perspective_type: PerspectiveType = PerspectiveType.FIRST_PERSON,
        epistemic_status: EpistemicStatus = EpistemicStatus.RECALLED,
    ) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=kind,
            perspective_type=perspective_type,
            epistemic_status=epistemic_status,
            sensitivity=sensitivity,
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

    def test_disabled_shadow_reads_no_projection_or_legacy_kblite(self) -> None:
        result = OwnerTruthContextShadowReadService(self.store).read(context=self.context)

        self.assertEqual(result["state"], "disabled")
        self.assertEqual(result["selectedContext"], [])
        self.assertIsNone(result["contextHash"])
        self.assertEqual(self.store.legacy_kblite_read_count, 0)

    def test_ready_shadow_selects_standard_confirmed_memories_with_typed_citations(self) -> None:
        experience = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "只允许出现在投影输入中的体验内容"},
        )
        knowledge = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "只允许出现在投影输入中的知识内容"},
        )
        restricted = self._candidate(
            kind=MemoryKind.EMOTION,
            content={"label": "仅用于敏感过滤验证"},
            sensitivity=SensitivityLevel.RESTRICTED,
        )
        self._activate(experience, command_id="context-shadow-experience")
        self._activate(knowledge, command_id="context-shadow-knowledge")
        self._activate(restricted, command_id="context-shadow-restricted")
        self.projection_service.rebuild(context=self.context)

        result = OwnerTruthContextShadowReadService(self.store, enabled=True).read(
            context=self.context
        )
        summary = context_shadow_summary(result)

        self.assertEqual(result["state"], "ready")
        self.assertTrue(result["shadowOnly"])
        self.assertTrue(result["legacyContextUnchanged"])
        self.assertEqual(len(result["selectedContext"]), 2)
        self.assertEqual(len(result["filteredContext"]), 1)
        self.assertEqual(
            result["filteredContext"][0]["reason"],
            "sensitivity_not_context_eligible",
        )
        self.assertEqual(
            result["selectedContextSourceCounts"],
            {"owner-truth-memory-projection": 2},
        )
        self.assertTrue(result["contextHash"])
        self.assertTrue(
            all(
                item["sourceRef"]
                == {
                    "vaultId": self.vault_id,
                    "sourceId": item["citation"]["sourceId"],
                    "sourceVersion": item["citation"]["sourceVersion"],
                }
                and item["rank"]["strategy"] == "projectionCitationOrder"
                for item in result["selectedContext"]
            )
        )
        self.assertEqual(
            {
                item["citation"]["sourceId"]
                for item in result["selectedContext"]
            },
            {experience.source_id, knowledge.source_id},
        )
        self.assertNotIn(experience.content["summary"], str(summary))
        self.assertNotIn(knowledge.content["claim"], str(summary))
        self.assertNotIn(restricted.content["label"], str(summary))
        self.assertTrue(
            all("content" not in item for item in summary["selectedContext"])
        )
        self.assertTrue(
            all("content" not in item for item in summary["filteredContext"])
        )

    def test_missing_or_stale_projection_fails_closed(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "投影未重建时不得进入 Context"},
        )
        self._activate(candidate, command_id="context-shadow-stale")

        result = OwnerTruthContextShadowReadService(self.store, enabled=True).read(
            context=self.context
        )

        self.assertEqual(result["state"], "rebuilding")
        self.assertEqual(result["selectedContext"], [])
        self.assertEqual(result["filteredContext"], [])
        self.assertIsNone(result["contextHash"])

    def test_shadow_and_materialization_exclude_ai_only_projection_entries(self) -> None:
        confirmed = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "已确认的回忆可以进入访谈上下文"},
        )
        inferred_perspective = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "AI 推断视角绝不能进入访谈上下文"},
            perspective_type=PerspectiveType.INFERRED,
        )
        inferred_epistemic = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "AI 推断认识状态绝不能进入访谈上下文"},
            epistemic_status=EpistemicStatus.INFERRED,
        )
        self._activate(confirmed, command_id="context-shadow-ai-only-confirmed")
        self._activate(
            inferred_perspective,
            command_id="context-shadow-ai-only-perspective",
        )
        self._activate(
            inferred_epistemic,
            command_id="context-shadow-ai-only-epistemic",
        )
        self.projection_service.rebuild(context=self.context)

        shadow = OwnerTruthContextShadowReadService(self.store, enabled=True).read(
            context=self.context
        )
        materialization = OwnerTruthContextMaterializationService(
            self.store,
            enabled=True,
        ).build(
            context=self.context,
            payload={"intent": "echo_chat"},
        )
        summary = context_materialization_summary(materialization)

        self.assertEqual(
            {item["citation"]["sourceId"] for item in shadow["selectedContext"]},
            {confirmed.source_id},
        )
        self.assertEqual(
            {
                item["citation"]["sourceId"]: item["reason"]
                for item in shadow["filteredContext"]
            },
            {
                inferred_perspective.source_id: "ai_only_perspective_not_context_eligible",
                inferred_epistemic.source_id: "ai_only_epistemic_status_not_context_eligible",
            },
        )
        self.assertEqual(materialization["generationContext"]["sourceCount"], 1)
        self.assertIn(confirmed.content["summary"], materialization["generationContext"]["text"])
        self.assertNotIn(
            inferred_perspective.content["summary"],
            materialization["generationContext"]["text"],
        )
        self.assertNotIn(
            inferred_epistemic.content["claim"],
            materialization["generationContext"]["text"],
        )
        rendered_summary = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        for forbidden in (
            confirmed.content["summary"],
            inferred_perspective.content["summary"],
            inferred_epistemic.content["claim"],
        ):
            self.assertNotIn(forbidden, rendered_summary)

    def test_shadow_build_uses_only_confirmed_citations_and_never_keeps_raw_query(self) -> None:
        experience = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "只允许投影作为个人上下文来源"},
        )
        restricted = self._candidate(
            kind=MemoryKind.EMOTION,
            content={"label": "不应进入上下文"},
            sensitivity=SensitivityLevel.RESTRICTED,
        )
        self._activate(experience, command_id="context-shadow-build-experience")
        self._activate(restricted, command_id="context-shadow-build-restricted")
        self.projection_service.rebuild(context=self.context)

        raw_query = "请用这段私密问题构建回响上下文"
        result = OwnerTruthContextShadowBuildService(self.store, enabled=True).build(
            context=self.context,
            payload={"intent": "echo_chat", "query": raw_query},
        )
        summary = context_shadow_build_summary(result)

        self.assertEqual(result["contextVersion"], "echo-context-v4-shadow")
        self.assertTrue(result["shadowOnly"])
        self.assertTrue(result["legacyContextUnchanged"])
        self.assertFalse(result["legacyContextRead"])
        self.assertTrue(result["contextHash"])
        self.assertEqual(result["authority"]["state"], "ready")
        self.assertEqual(result["fallbacks"], [])
        self.assertEqual(result["trace"]["citationProofCount"], 1)
        self.assertEqual(result["citationProof"][0]["resolution"], "current_confirmed_projection_entry")
        self.assertEqual(
            result["citationProof"][0]["citation"]["sourceId"],
            experience.source_id,
        )
        self.assertEqual(result["filteredContext"][0]["reason"], "sensitivity_not_context_eligible")
        self.assertTrue(result["request"]["queryHash"])
        self.assertNotIn(raw_query, str(summary))
        self.assertNotIn(experience.content["summary"], str(summary))
        self.assertNotIn(restricted.content["label"], str(summary))

    def test_shadow_build_can_query_rank_current_search_projection_without_raw_values(self) -> None:
        matched = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "父亲修好自行车后带我去公园"},
        )
        unmatched = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "夏天的海边总有温暖的风"},
        )
        restricted = self._candidate(
            kind=MemoryKind.EMOTION,
            content={"label": "自行车相关的敏感情绪"},
            sensitivity=SensitivityLevel.RESTRICTED,
        )
        self._activate(matched, command_id="context-shadow-query-matched")
        self._activate(unmatched, command_id="context-shadow-query-unmatched")
        self._activate(restricted, command_id="context-shadow-query-restricted")
        self.projection_service.rebuild(context=self.context)
        self.store.search_projection_repository.rebuild(context=self.context)

        query = "自行车"
        result = OwnerTruthContextShadowBuildService(self.store, enabled=True).build(
            context=self.context,
            payload={
                "intent": "echo_chat",
                "query": query,
                "selectionMode": "deterministicTextFallback",
            },
        )
        summary = context_shadow_build_summary(result)

        self.assertEqual(result["request"]["selectionMode"], "deterministicTextFallback")
        self.assertEqual(len(result["selectedContext"]), 1)
        self.assertEqual(
            result["selectedContext"][0]["citation"]["sourceId"],
            matched.source_id,
        )
        self.assertEqual(
            result["selectedContext"][0]["reason"],
            "confirmed_current_memory_version_query_match",
        )
        self.assertEqual(
            result["selectedContext"][0]["rank"],
            {"position": 1, "strategy": "deterministicTextFallback"},
        )
        self.assertEqual(
            {item["reason"] for item in result["filteredContext"]},
            {"query_not_matched", "sensitivity_not_context_eligible"},
        )
        self.assertEqual(result["fallbacks"], [])
        self.assertNotIn(query, str(summary))
        self.assertNotIn(matched.content["summary"], str(summary))
        self.assertNotIn(unmatched.content["claim"], str(summary))
        self.assertNotIn(restricted.content["label"], str(summary))

    def test_shadow_build_query_selection_fails_closed_without_search_projection(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "没有当前检索索引时不得回退全部个人记忆"},
        )
        self._activate(candidate, command_id="context-shadow-query-search-unavailable")
        self.projection_service.rebuild(context=self.context)

        result = OwnerTruthContextShadowBuildService(self.store, enabled=True).build(
            context=self.context,
            payload={
                "query": "检索索引",
                "selectionMode": "deterministicTextFallback",
            },
        )

        self.assertEqual(result["authority"]["state"], "ready")
        self.assertEqual(result["selectedContext"], [])
        self.assertEqual(
            result["fallbacks"],
            ["owner_truth_context_search_unavailable_no_personal_memory"],
        )
        self.assertEqual(
            result["filteredContext"][0]["reason"],
            "query_retrieval_unavailable",
        )

    def test_shadow_build_context_hash_binds_query_summary(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "每轮 QA 上下文必须绑定提交的问题摘要"},
        )
        self._activate(candidate, command_id="context-shadow-query-hash")
        self.projection_service.rebuild(context=self.context)

        first = OwnerTruthContextShadowBuildService(self.store, enabled=True).build(
            context=self.context,
            payload={"query": "第一个问题"},
        )
        second = OwnerTruthContextShadowBuildService(self.store, enabled=True).build(
            context=self.context,
            payload={"query": "第二个问题"},
        )

        self.assertNotEqual(first["contextHash"], second["contextHash"])

    def test_shadow_build_uses_explicit_no_personal_memory_fallback_when_projection_unavailable(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "未重建投影时不得回退旧档案"},
        )
        self._activate(candidate, command_id="context-shadow-build-unavailable")

        result = OwnerTruthContextShadowBuildService(self.store, enabled=True).build(
            context=self.context,
            payload={"query": "投影未就绪时不得回退旧档案"},
        )

        self.assertEqual(result["authority"]["state"], "rebuilding")
        self.assertEqual(result["selectedContext"], [])
        self.assertEqual(result["citationProof"], [])
        self.assertTrue(result["contextHash"])
        self.assertEqual(
            result["fallbacks"],
            ["owner_truth_context_unavailable_no_personal_memory"],
        )

    def test_materialization_uses_only_selected_confirmed_projection_content(self) -> None:
        matched = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "父亲修好自行车后带我去公园"},
        )
        unmatched = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "夏天的海边总有温暖的风"},
        )
        restricted = self._candidate(
            kind=MemoryKind.EMOTION,
            content={"label": "自行车相关的敏感情绪"},
            sensitivity=SensitivityLevel.RESTRICTED,
        )
        self._activate(matched, command_id="context-materialization-matched")
        self._activate(unmatched, command_id="context-materialization-unmatched")
        self._activate(restricted, command_id="context-materialization-restricted")
        self.projection_service.rebuild(context=self.context)
        self.store.search_projection_repository.rebuild(context=self.context)

        raw_query = "自行车"
        result = OwnerTruthContextMaterializationService(self.store, enabled=True).build(
            context=self.context,
            payload={
                "intent": "echo_chat",
                "query": raw_query,
                "selectionMode": "deterministicTextFallback",
            },
        )
        summary = context_materialization_summary(result)

        self.assertEqual(result["state"], "ready")
        self.assertTrue(result["shadowOnly"])
        self.assertTrue(result["legacyContextUnchanged"])
        self.assertFalse(result["legacyContextRead"])
        self.assertEqual(result["generationContext"]["sourceCount"], 1)
        self.assertIn(matched.content["summary"], result["generationContext"]["text"])
        self.assertNotIn(unmatched.content["claim"], result["generationContext"]["text"])
        self.assertNotIn(restricted.content["label"], result["generationContext"]["text"])
        self.assertEqual(
            result["typedCitations"][0]["memoryVersionId"],
            result["selectedContext"][0]["citation"]["memoryVersionId"],
        )
        self.assertNotIn(raw_query, str(summary))
        self.assertNotIn(matched.content["summary"], str(summary))
        self.assertNotIn(unmatched.content["claim"], str(summary))
        self.assertNotIn(restricted.content["label"], str(summary))
        self.assertNotIn("'text':", str(summary))

    def test_materialization_renders_one_semantic_memory_with_all_atomic_citations(self) -> None:
        first = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "我在北京大学完成了本科阶段学习"},
        )
        second = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "我在北京大学完成了本科阶段学习"},
        )
        self._activate(first, command_id="context-materialization-duplicate-first")
        self._activate(second, command_id="context-materialization-duplicate-second")
        self.projection_service.rebuild(context=self.context)
        self.store.search_projection_repository.rebuild(context=self.context)

        result = OwnerTruthContextMaterializationService(self.store, enabled=True).build(
            context=self.context,
            payload={
                "intent": "echo_chat",
                "query": "北京大学",
                "selectionMode": "deterministicTextFallback",
            },
        )

        self.assertEqual(result["state"], "ready")
        self.assertEqual(len(result["selectedContext"]), 1)
        self.assertEqual(result["generationContext"]["sourceCount"], 1)
        self.assertIn("confirmed-memory-group:", result["generationContext"]["text"])
        self.assertIn("北京大学", result["generationContext"]["text"])
        self.assertEqual(len(result["typedCitations"]), 2)
        self.assertEqual(
            {item["sourceId"] for item in result["typedCitations"]},
            {first.source_id, second.source_id},
        )

    def test_materialization_fails_closed_without_current_projection(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "投影未就绪时不得物化个人上下文"},
        )
        self._activate(candidate, command_id="context-materialization-unavailable")

        result = OwnerTruthContextMaterializationService(self.store, enabled=True).build(
            context=self.context,
            payload={"query": "投影尚未重建"},
        )

        self.assertEqual(result["state"], "rebuilding")
        self.assertEqual(result["generationContext"]["text"], "")
        self.assertEqual(result["typedCitations"], [])
        self.assertEqual(
            result["fallbacks"],
            ["owner_truth_context_unavailable_no_personal_memory"],
        )

    def test_non_owner_is_denied(self) -> None:
        non_owner_context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id="different-subject",
        )

        with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
            OwnerTruthContextShadowReadService(self.store, enabled=True).read(
                context=non_owner_context
            )

    def test_unknown_vault_is_normalized_to_projection_access_denied(self) -> None:
        unknown_vault_context = OwnerTruthCommandContext(
            vault_id="vault-not-owned-by-context-subject",
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )

        with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
            OwnerTruthContextShadowBuildService(self.store, enabled=True).build(
                context=unknown_vault_context,
                payload={"query": "跨 Vault 访问不应泄露底层异常"},
            )

    def test_compare_binds_legacy_and_v4_to_one_value_free_request(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "上下文对照只保留请求哈希和引用完整性"},
        )
        self._activate(candidate, command_id="context-shadow-compare")
        self.projection_service.rebuild(context=self.context)

        raw_query = "不要把这段私密问题写入对照结果"
        result = OwnerTruthContextShadowCompareService(
            self.store,
            Settings(store_backend="memory"),
            enabled=True,
        ).compare(
            context=self.context,
            payload={"intent": "echo_chat", "query": raw_query},
        )

        self.assertTrue(result["shadowOnly"])
        self.assertTrue(result["legacyContextUnchanged"])
        self.assertTrue(result["legacyContextRead"])
        self.assertTrue(result["requestCorrelationMatches"])
        self.assertEqual(result["disposition"], "observed")
        self.assertEqual(result["v4"]["selectedContextCount"], 1)
        self.assertTrue(result["v4"]["allSelectedItemsHaveTypedCitation"])
        self.assertTrue(result["v4"]["authorityEpochPresent"])
        self.assertTrue(result["v4"]["projectionCheckpointPresent"])
        self.assertNotIn(raw_query, str(result))
        self.assertNotIn(candidate.content["summary"], str(result))

    def test_compare_marks_unready_projection_as_no_personal_memory(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "未完成重建时不得把旧上下文标为 V4 个人记忆"},
        )
        self._activate(candidate, command_id="context-shadow-compare-unready")

        result = OwnerTruthContextShadowCompareService(
            self.store,
            Settings(store_backend="memory"),
            enabled=True,
        ).compare(
            context=self.context,
            payload={"query": "投影尚未就绪"},
        )

        self.assertTrue(result["requestCorrelationMatches"])
        self.assertEqual(result["v4"]["state"], "rebuilding")
        self.assertEqual(result["v4"]["selectedContextCount"], 0)
        self.assertEqual(result["disposition"], "v4_no_personal_memory")

    def test_compare_rejects_unapproved_intent_without_retaining_it(self) -> None:
        compare = OwnerTruthContextShadowCompareService(
            self.store,
            Settings(store_backend="memory"),
            enabled=True,
        )

        with self.assertRaises(OwnerTruthContextShadowCompareError):
            compare.compare(
                context=self.context,
                payload={"intent": "untrusted free form intent", "query": "ignored"},
            )


if __name__ == "__main__":
    unittest.main()
