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

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository

    def get_kb_snapshot(self, _user_id: str):
        raise AssertionError("Owner Truth Context shadow must not read legacy KBLite")


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
    ) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=kind,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
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


if __name__ == "__main__":
    unittest.main()
