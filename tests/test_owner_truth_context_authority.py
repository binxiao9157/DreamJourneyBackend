from __future__ import annotations

from hashlib import sha256
import json
import os
import unittest
from unittest.mock import patch
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
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION,
    OWNER_TRUTH_SCHEMA_VERSION_V2,
    empty_memory_facets,
)
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.services.context_packet import ContextPacketBuilder
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_context_authority import OwnerTruthContextAuthorityService
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService
from app.services.owner_truth_source import OwnerTruthSourceCommandService


def _content_hash(content: dict[str, object]) -> str:
    return sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


class _NoLegacyMemoryReadStore(InMemoryStore):
    def list_archive_items(self, _user_id: str):  # type: ignore[override]
        raise AssertionError("Owner Truth Context Authority must not read legacy archive items")

    def get_kb_snapshot(self, _user_id: str):  # type: ignore[override]
        raise AssertionError("Owner Truth Context Authority must not read legacy KBLite")

    def get_latest_care_snapshot(self, _user_id: str, viewer_family_member_id=None):  # type: ignore[override]
        raise AssertionError("Owner Truth Context Authority must not read legacy care snapshots")


class OwnerTruthContextAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner_id = "owner-context-authority"
        self.context = OwnerTruthCommandContext(
            vault_id=self.owner_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _NoLegacyMemoryReadStore()
        self.authority = OwnerTruthContextAuthorityService(
            self.store,
            settings=Settings(store_backend="memory"),
            enabled=True,
        )

    def test_context_authority_switch_defaults_off_and_requires_explicit_environment_enablement(self) -> None:
        self.assertFalse(Settings().owner_truth_context_authority_enabled)
        self.assertFalse(Settings().owner_truth_context_authority_closed_pilot_enabled)
        with patch.dict(
            os.environ,
            {"OWNER_TRUTH_CONTEXT_AUTHORITY_ENABLED": "true"},
            clear=False,
        ):
            self.assertTrue(Settings.from_env().owner_truth_context_authority_enabled)

    def test_context_authority_switch_accepts_legacy_environment_alias(self) -> None:
        with patch.dict(
            os.environ,
            {"OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED": "true"},
            clear=False,
        ):
            self.assertTrue(Settings.from_env().owner_truth_context_authority_enabled)

    def _activate_memory(
        self,
        *,
        kind: MemoryKind,
        content: dict[str, str],
        perspective_type: PerspectiveType = PerspectiveType.FIRST_PERSON,
        epistemic_status: EpistemicStatus = EpistemicStatus.RECALLED,
        content_schema_version: str = OWNER_TRUTH_SCHEMA_VERSION,
    ) -> OwnerTruthCandidateSnapshot:
        operation_context = OwnerTruthCommandContext(
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            actor_subject_id=self.context.actor_subject_id,
            policy_version=content_schema_version,
        )
        source_id = str(uuid4())
        OwnerTruthSourceCommandService(self.store).create_text_source(
            command=CreateTextSourceCommand(
                command_id=f"context-authority-source-{source_id}",
                source_id=source_id,
                expected_version=0,
                text="由 Owner 明确提交的个人回忆原文。",
                metadata={"origin": "context-authority-test"},
            ),
            context=operation_context,
        )
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.owner_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=kind,
            perspective_type=perspective_type,
            epistemic_status=epistemic_status,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=content_schema_version,
            authority_epoch=0,
            row_version=1,
            content_hash=_content_hash(content),
            content_schema_version=content_schema_version,
            payload={
                "content": content,
                "contentSchemaVersion": content_schema_version,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )
        self.store.owner_truth_candidate_review_repository().seed(candidate)
        OwnerTruthCandidateReviewService(self.store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=f"context-authority-accept-{candidate.candidate_id}",
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=content_schema_version,
                reason_code="ownerReviewed",
            ),
            context=operation_context,
        )
        return candidate

    def _rebuild_projection(self) -> None:
        OwnerTruthMemoryProjectionService(self.store).rebuild(context=self.context)
        self.store.owner_truth_memory_search_document_projection_repository().rebuild(
            context=self.context
        )

    def test_confirmed_projection_builds_runtime_packet_without_legacy_memory_reads(self) -> None:
        confirmed = self._activate_memory(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "小时候在院子里听雨的回忆已经由本人确认。"},
        )
        ai_only = self._activate_memory(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "AI 推断的内容绝不能进入回响上下文。"},
            epistemic_status=EpistemicStatus.INFERRED,
        )
        self._rebuild_projection()

        packet = self.authority.build_packet(
            context=self.context,
            payload={
                "userId": self.owner_id,
                "intent": "echo_chat",
                "query": "请陪我回忆小时候在院子里听雨的时光",
                "personaScope": "personal",
                "digitalHumanId": self.owner_id,
            },
        )

        self.assertEqual(packet["contextVersion"], "echo-context-v4-owner")
        self.assertFalse(packet["contextAuthority"]["legacyContextRead"])
        self.assertEqual(packet["contextAuthority"]["mode"], "ownerTruthConfirmedProjection")
        self.assertEqual(packet["contextAuthority"]["cohort"], "authenticatedOwner")
        self.assertEqual(packet["contextAuthority"]["fallbackPolicy"], "failClosedNoLegacy")
        self.assertFalse(packet["contextAuthority"]["mixedAuthorityAllowed"])
        self.assertEqual(len(packet["contextAuthority"]["authorityGeneration"]), 64)
        self.assertEqual(packet["memory"]["archiveItems"], [])
        self.assertEqual(packet["memory"]["kbFacts"], [])
        self.assertEqual(len(packet["selectedContext"]), 1)
        self.assertEqual(
            packet["selectedContext"][0]["citation"]["sourceId"],
            confirmed.source_id,
        )
        self.assertIn(confirmed.content["summary"], packet["generationContext"]["text"])
        self.assertNotIn(ai_only.content["claim"], packet["generationContext"]["text"])
        self.assertEqual(
            packet["filteredContext"][0]["reason"],
            "ai_only_epistemic_status_not_context_eligible",
        )
        self.assertEqual(
            packet["contextAuthority"]["retrievalMode"],
            "deterministicTextFallback",
        )
        self.assertEqual(packet["selectedContext"][0]["rank"]["position"], 1)

    def test_context_authority_ranks_natural_language_query_and_omits_unrelated_memory(self) -> None:
        matched = self._activate_memory(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "父亲修好自行车后带我去公园散步。"},
        )
        unmatched = self._activate_memory(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "夏天的海边总有温暖的风。"},
        )
        self._rebuild_projection()

        packet = self.authority.build_packet(
            context=self.context,
            payload={
                "userId": self.owner_id,
                "intent": "echo_chat",
                "query": "请陪我回忆父亲修自行车的那段时光",
                "personaScope": "personal",
                "digitalHumanId": self.owner_id,
            },
        )

        self.assertEqual(len(packet["selectedContext"]), 1)
        self.assertEqual(packet["selectedContext"][0]["citation"]["sourceId"], matched.source_id)
        self.assertNotIn(unmatched.content["claim"], packet["generationContext"]["text"])
        self.assertEqual(packet["contextAuthority"]["retrievalOutcome"], "grounded")
        self.assertEqual(packet["contextAuthority"]["candidateLimit"], 20)
        self.assertEqual(packet["contextAuthority"]["selectedLimit"], 8)

    def test_context_authority_caps_candidates_at_twenty_and_final_context_at_eight(self) -> None:
        for index in range(25):
            self._activate_memory(
                kind=MemoryKind.EXPERIENCE,
                content={"summary": f"第 {index} 次公园散步是本人确认的回忆。"},
            )
        self._rebuild_projection()

        packet = self.authority.build_packet(
            context=self.context,
            payload={
                "userId": self.owner_id,
                "intent": "echo_chat",
                "query": "公园散步",
                "personaScope": "personal",
                "digitalHumanId": self.owner_id,
            },
        )

        self.assertEqual(len(packet["selectedContext"]), 8)
        self.assertEqual(packet["generationContext"]["sourceCounts"]["ownerTruthMemoryProjection"], 8)
        self.assertLessEqual(len(packet["generationContext"]["text"]), 4096)
        self.assertEqual(packet["trace"]["retrievalCandidateCount"], 20)
        self.assertEqual(packet["trace"]["retrievalSelectedCount"], 8)
        self.assertEqual(
            sum(
                item["reason"] == "query_context_limit_exceeded"
                for item in packet["filteredContext"]
            ),
            12,
        )

    def test_context_authority_no_match_fails_closed_without_legacy_reads(self) -> None:
        self._activate_memory(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "父亲修好自行车后带我去公园。"},
        )
        self._rebuild_projection()

        packet = self.authority.build_packet(
            context=self.context,
            payload={
                "userId": self.owner_id,
                "intent": "echo_chat",
                "query": "海边日落",
                "personaScope": "personal",
                "digitalHumanId": self.owner_id,
            },
        )

        self.assertEqual(packet["selectedContext"], [])
        self.assertEqual(packet["generationContext"]["text"], "")
        self.assertEqual(packet["contextAuthority"]["retrievalOutcome"], "gap")
        self.assertIn("owner_truth_context_no_query_match_no_personal_memory", packet["fallbacks"])

    def test_context_authority_search_projection_failure_is_explicit_and_fail_closed(self) -> None:
        self._activate_memory(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "检索组件不可用时不得回退全部正式记忆。"},
        )
        OwnerTruthMemoryProjectionService(self.store).rebuild(context=self.context)

        packet = self.authority.build_packet(
            context=self.context,
            payload={
                "userId": self.owner_id,
                "intent": "echo_chat",
                "query": "检索组件",
                "personaScope": "personal",
                "digitalHumanId": self.owner_id,
            },
        )

        self.assertEqual(packet["selectedContext"], [])
        self.assertEqual(packet["contextAuthority"]["retrievalOutcome"], "fallback")
        self.assertIn(
            "owner_truth_context_search_unavailable_no_personal_memory",
            packet["fallbacks"],
        )

    def test_context_authority_supports_current_v2_memory_and_facets(self) -> None:
        facets = empty_memory_facets(confidence=1.0)
        facets["people"] = [
            {"value": "父亲", "evidenceMode": "ownerStated", "confidence": 1.0}
        ]
        facets["places"] = [
            {"value": "苏州", "evidenceMode": "ownerStated", "confidence": 1.0}
        ]
        confirmed = self._activate_memory(
            kind=MemoryKind.EXPERIENCE,
            content={
                "summary": "父亲在苏州教我修自行车。",
                "facets": facets,
            },
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
        )
        self._rebuild_projection()

        packet = self.authority.build_packet(
            context=self.context,
            payload={
                "userId": self.owner_id,
                "intent": "echo_chat",
                "query": "父亲在苏州做过什么",
                "personaScope": "personal",
                "digitalHumanId": self.owner_id,
            },
        )

        self.assertEqual(len(packet["selectedContext"]), 1)
        self.assertEqual(packet["selectedContext"][0]["citation"]["sourceId"], confirmed.source_id)
        self.assertIn("父亲在苏州教我修自行车", packet["generationContext"]["text"])

    def test_missing_personal_vault_returns_empty_v4_context_instead_of_legacy_fallback(self) -> None:
        packet = self.authority.build_packet(
            context=self.context,
            payload={
                "userId": self.owner_id,
                "intent": "echo_chat",
                "query": "没有已确认回忆时仍应安全响应",
                "personaScope": "personal",
                "digitalHumanId": self.owner_id,
            },
        )

        self.assertEqual(packet["contextVersion"], "echo-context-v4-owner")
        self.assertEqual(packet["selectedContext"], [])
        self.assertEqual(packet["generationContext"]["text"], "")
        self.assertIn("owner_truth_context_unavailable_no_personal_memory", packet["fallbacks"])
        self.assertFalse(packet["contextAuthority"]["legacyContextRead"])
        self.assertEqual(packet["contextAuthority"]["fallbackPolicy"], "failClosedNoLegacy")
        self.assertEqual(len(packet["contextAuthority"]["authorityGeneration"]), 64)

    def test_builder_rejects_non_self_persona_for_owner_truth_authority(self) -> None:
        materialization = self.authority.materialize(context=self.context, payload={"intent": "echo_chat"})
        builder = ContextPacketBuilder(self.store, Settings(store_backend="memory"))

        with self.assertRaisesRegex(ValueError, "personal self persona"):
            builder.build_from_owner_truth_materialization(
                {
                    "userId": self.owner_id,
                    "intent": "echo_chat",
                    "personaScope": "family",
                    "digitalHumanId": "family-member-1",
                },
                materialization=materialization,
            )
