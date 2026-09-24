from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import httpx

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.owner_truth_candidate_extraction_worker import (
    ModelAssistedOwnerTruthLiveConversationExtractor,
)
from app.core.config import Settings
from app.domain.owner_truth.contracts import OwnerTruthContractError, SourceKind
from app.domain.owner_truth.source_commands import CreateTextSourceCommand
from app.services.deepseek import DeepSeekLiveMemoryOrganizationProxy
from app.services.owner_truth_candidate_extraction import (
    OwnerTruthCandidateExtractionInput,
)
from app.services.owner_truth_live_memory_contract_errors import (
    LiveMemoryContractFailure,
    contract_failure,
)
from app.services.owner_truth_live_memory_support import (
    build_live_memory_evidence_catalog,
)
from app.services.owner_truth_live_long_memory import (
    InMemoryLiveLongMemoryRepository,
    LiveLongMemoryAtomRecord,
    LiveLongMemoryBudgetPolicy,
    LiveLongMemoryConflict,
    LiveLongMemoryRunIdentity,
    LiveLongMemoryManifestIncomplete,
    build_publication_manifest,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_after(prompt: str, marker: str):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1].lstrip())[0]


def _facets() -> dict[str, object]:
    return {
        "people": [],
        "places": [],
        "organizations": [],
        "time": {"start": None, "end": None, "precision": "unknown"},
        "topics": ["synthetic-live-test"],
    }


def _typed_memory(text: str, indices: list[int]) -> dict[str, object]:
    facets = {
        key: []
        for key in (
            "people", "time", "places", "relationships", "emotions", "values",
            "personality", "habits", "goals", "identity", "reflections",
        )
    }
    facets["confidence"] = 0.9
    return {
        "memoryKind": "knowledge",
        "claim": text,
        "sourceTurnIndices": indices,
        "facets": facets,
        "factType": "knowledge",
        "dimensions": ["knowledgeSkills"],
        "predicate": "states",
        "object": None,
        "qualifiers": {
            "polarity": "unknown",
            "strengthExpression": None,
            "superlativeAsserted": False,
            "currentApplicability": "unknown",
            "validTime": {
                "start": None, "end": None, "precision": "unknown", "expression": None,
            },
            "place": None,
            "scenario": None,
        },
    }


def _bind_memory(memory: dict[str, object], *, text: str, atom_ids: list[str] | None = None) -> dict[str, object]:
    return {
        **memory,
        "_sourceEvidenceRanges": [{
            "turnIndex": int(list(memory["sourceTurnIndices"])[0]),
            "start": 0,
            "end": len(text),
            "textHash": _digest(text),
        }],
        "_supportProofHash": _digest(f"support:{text}"),
        **({"_atomIds": atom_ids} if atom_ids is not None else {}),
    }


class _CappedLegacyProvider:
    model = "controlled-live-provider"
    prompt_version = "controlled-live-v1"
    support_prompt_version = "controlled-support-v1"
    relation_prompt_version = "controlled-relation-v1"
    maximum_turn_count = 200
    maximum_turn_characters = 4_000
    maximum_total_characters = 30_000

    def __init__(self) -> None:
        self.organization_requests = 0
        self.support_requests = 0

    def request_organization(self, *, turns, **_kwargs):
        self.organization_requests += 1
        user_turns = [turn for turn in turns if turn["role"] == "user"]
        return {
            "memories": [
                {
                    "memoryKind": "knowledge",
                    "claim": str(turn["text"]),
                    "sourceTurnIndices": [int(turn["index"])],
                    "facets": _facets(),
                }
                for turn in user_turns[:8]
            ]
        }

    def request_support_review(self, *, turns, memories, **_kwargs):
        self.support_requests += 1
        user_turns = [turn for turn in turns if turn["role"] == "user"]
        covered = {
            int(index)
            for memory in memories
            for index in memory["sourceTurnIndices"]
        }
        return {
            "schemaVersion": "owner-truth-live-memory-support-v1",
            "turnAssessments": [
                {"turnIndex": int(turn["index"]), "speechAct": "assertion"}
                for turn in user_turns
            ],
            "memoryAssessments": [
                {
                    "memoryIndex": index,
                    "verdict": "supported",
                    "supportingTurnIndices": list(memory["sourceTurnIndices"]),
                }
                for index, memory in enumerate(memories)
            ],
            "omittedFactBearingTurnIndices": [
                int(turn["index"])
                for turn in user_turns
                if int(turn["index"]) not in covered
            ],
        }

    def request_relation_review(self, *, incoming, existing, **_kwargs):
        return {
            "decisions": [
                {"existingIndex": index, "relation": "distinct"}
                for index, _item in enumerate(existing)
            ]
        }


class _SemanticRelationProvider(_CappedLegacyProvider):
    def request_relation_review(self, *, incoming, existing, **_kwargs):
        claim = str(incoming.get("claim") or "")
        decisions = []
        for index, item in enumerate(existing):
            existing_claim = str(item.get("claim") or "")
            relation = "distinct"
            decision: dict[str, object] = {"existingIndex": index}
            if "更正" in claim and "代号" in existing_claim:
                relation = "correction"
            elif "撤回" in claim and "代号" in existing_claim:
                relation = "retraction"
            elif "补充" in claim and "计划" in existing_claim:
                relation = "supplement"
                decision["resolvedMemory"] = {
                    **incoming,
                    "claim": "用户的计划是周六上午在杭州跑步。",
                }
            decision["relation"] = relation
            decisions.append(decision)
        return {"decisions": decisions}

    def request_relation_batch_review(
        self,
        *,
        incoming,
        existing,
        intra_batch=False,
        **_kwargs,
    ):
        results = []
        for incoming_index, memory in enumerate(incoming):
            claim = str(memory.get("claim") or "")
            decisions = []
            for existing_index, item in enumerate(existing):
                if intra_batch and existing_index >= incoming_index:
                    continue
                existing_claim = str(item.get("claim") or "")
                decision = None
                if "更正" in claim and "代号" in existing_claim:
                    decision = {
                        "existingIndex": existing_index,
                        "relation": "correction",
                    }
                elif "撤回" in claim and "代号" in existing_claim:
                    decision = {
                        "existingIndex": existing_index,
                        "relation": "retraction",
                    }
                elif "补充" in claim and "计划" in existing_claim:
                    decision = {
                        "existingIndex": existing_index,
                        "relation": "supplement",
                        "resolvedMemory": {
                            **memory,
                            "claim": "用户的计划是周六上午在杭州跑步。",
                        },
                    }
                if decision is not None:
                    decisions.append(decision)
                    break
            results.append(
                {
                    "incomingIndex": incoming_index,
                    "scannedExistingCount": len(existing),
                    "decisions": decisions,
                }
            )
        return {"results": results}


class _ClauseProvider(_CappedLegacyProvider):
    def request_organization(self, *, turns, **_kwargs):
        self.organization_requests += 1
        memories = []
        for turn in turns:
            if turn["role"] != "user":
                continue
            facts = [item.strip() for item in str(turn["text"]).split("；") if item.strip()]
            for fact in facts[:8]:
                memories.append(
                    {
                        "memoryKind": "knowledge",
                        "claim": fact,
                        "sourceTurnIndices": [int(turn["index"])],
                        "facets": _facets(),
                    }
                )
        return {"memories": memories}


class _AmbiguousRelationProvider(_CappedLegacyProvider):
    def request_relation_review(self, *, existing, **_kwargs):
        return {
            "decisions": [
                {"existingIndex": index, "relation": "unresolved" if index == 0 else "distinct"}
                for index, _item in enumerate(existing)
            ]
        }


class _BatchDistinctProvider(_CappedLegacyProvider):
    def __init__(self) -> None:
        super().__init__()
        self.batch_relation_requests = 0
        self.maximum_incoming_page = 0
        self.maximum_existing_page = 0
        self.scanned_pairs: set[tuple[str, str]] = set()

    def request_relation_batch_review(
        self,
        *,
        incoming,
        existing,
        intra_batch=False,
        **_kwargs,
    ):
        self.batch_relation_requests += 1
        self.maximum_incoming_page = max(self.maximum_incoming_page, len(incoming))
        self.maximum_existing_page = max(self.maximum_existing_page, len(existing))
        for incoming_index, incoming_memory in enumerate(incoming):
            for existing_index, existing_memory in enumerate(existing):
                if intra_batch and existing_index >= incoming_index:
                    continue
                self.scanned_pairs.add((
                    str(incoming_memory.get("claim") or ""),
                    str(existing_memory.get("claim") or ""),
                ))
        return {
            "results": [
                {
                    "incomingIndex": index,
                    "scannedExistingCount": len(existing),
                    "decisions": [],
                }
                for index, _memory in enumerate(incoming)
            ]
        }


class _FactQuestionBatchProvider(_BatchDistinctProvider):
    """Controlled semantic boundary for predeclared fact/question fixtures."""

    def request_organization(self, *, turns, **_kwargs):
        self.organization_requests += 1
        memories = []
        for turn in turns:
            if turn["role"] != "user":
                continue
            for fragment in str(turn["text"]).split("；"):
                normalized = fragment.strip()
                if not normalized.startswith("事实："):
                    continue
                claim = normalized.removeprefix("事实：").strip()
                memories.append({
                    "memoryKind": "knowledge",
                    "claim": claim,
                    "sourceTurnIndices": [int(turn["index"])],
                    "facets": _facets(),
                })
        return {"memories": memories}

    def request_support_review(self, *, turns, memories, **_kwargs):
        self.support_requests += 1
        user_turns = [turn for turn in turns if turn["role"] == "user"]
        fact_turn_indices = {
            int(turn["index"])
            for turn in user_turns
            if "事实：" in str(turn["text"])
        }
        covered = {
            int(index)
            for memory in memories
            for index in memory["sourceTurnIndices"]
        }
        return {
            "schemaVersion": "owner-truth-live-memory-support-v1",
            "turnAssessments": [
                {
                    "turnIndex": int(turn["index"]),
                    "speechAct": (
                        "assertion"
                        if int(turn["index"]) in fact_turn_indices
                        else "query"
                    ),
                }
                for turn in user_turns
            ],
            "memoryAssessments": [
                {
                    "memoryIndex": index,
                    "verdict": "supported",
                    "supportingTurnIndices": list(memory["sourceTurnIndices"]),
                }
                for index, memory in enumerate(memories)
            ],
            "omittedFactBearingTurnIndices": sorted(fact_turn_indices - covered),
        }


class OwnerTruthLiveLongMemoryPipelineRedTests(unittest.TestCase):
    def test_organization_output_capacity_splits_without_losing_owned_facts(self) -> None:
        facts = [f"合成容量事实 {index} 是灯塔 {index} 号。" for index in range(1, 13)]
        turns = [
            {"index": index, "role": "user",
             "text": facts[(index - 1) * 2] + facts[(index - 1) * 2 + 1],
             "captureMode": "live"}
            for index in range(1, 7)
        ]
        wire_organizations = []

        def handle(request: httpx.Request) -> httpx.Response:
            prompt = json.loads(request.content)["messages"][-1]["content"]
            if "memoryAssessments" in prompt:
                scoped = _json_after(prompt, "【待复核草案，memoryIndex 按数组下标】")
                result = {
                    "schemaVersion": "owner-truth-live-memory-support-v1",
                    "turnAssessments": [
                        {"turnIndex": turn["index"], "speechAct": "assertion"}
                        for turn in _json_after(prompt, "【整场对话】")
                        if turn["role"] == "user"
                    ],
                    "memoryAssessments": [
                        {"memoryIndex": index, "verdict": "supported",
                         "supportingTurnIndices": memory["sourceTurnIndices"]}
                        for index, memory in enumerate(scoped)
                    ],
                    "omittedFactBearingTurnIndices": [],
                }
            else:
                scoped_turns = _json_after(prompt, "【结构化对话】")
                wire_organizations.append(len(scoped_turns))
                memories = [
                    _typed_memory(fact, [turn["index"]])
                    for turn in scoped_turns if turn["role"] == "user"
                    for fact in facts if fact in turn["text"]
                ]
                result = {"memories": memories}
            return httpx.Response(200, request=request, json={
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps(result, ensure_ascii=False)
                }}],
            })

        settings = Settings(
            owner_truth_live_memory_organization_enabled=True,
            owner_truth_live_long_memory_pipeline_enabled=True,
            deepseek_api_key="synthetic-no-network",
            deepseek_base_url="https://controlled.invalid/chat/completions",
        )
        proxy = DeepSeekLiveMemoryOrganizationProxy(
            settings, transport=httpx.MockTransport(handle)
        )
        intent, source = self._input(source_id=str(uuid4()), turns=turns)
        command = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings, organizer=proxy, support_reviewer=proxy,
            relation_reviewer=_BatchDistinctProvider(),
            run_repository=InMemoryLiveLongMemoryRepository(),
        ).extract(intent=intent, source=source)
        self.assertEqual({item.content["claim"] for item in command.proposals}, set(facts))
        self.assertGreater(len(wire_organizations), 1)
        self.assertEqual(wire_organizations[0], 6)

    def test_prepared_organization_request_keeps_reserved_body_after_upstream_mutation(self) -> None:
        wire_bodies: list[dict[str, object]] = []
        fact = "本次合成测试代号是松柏九号。"

        def handle(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            wire_bodies.append(payload)
            prompt = payload["messages"][-1]["content"]
            if "memoryAssessments" in prompt:
                result = {
                    "schemaVersion": "owner-truth-live-memory-support-v1",
                    "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
                    "memoryAssessments": [{
                        "memoryIndex": 0,
                        "verdict": "supported",
                        "supportingTurnIndices": [1],
                    }],
                    "omittedFactBearingTurnIndices": [],
                }
            else:
                result = {"memories": [_typed_memory(fact, [1])]}
            return httpx.Response(200, request=request, json={
                "model": "controlled",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps(result, ensure_ascii=False),
                }}],
            })

        settings = Settings(
            owner_truth_live_memory_organization_enabled=True,
            owner_truth_live_long_memory_pipeline_enabled=True,
            deepseek_api_key="synthetic-no-network",
            deepseek_base_url="https://controlled.invalid/chat/completions",
        )
        proxy = DeepSeekLiveMemoryOrganizationProxy(
            settings, transport=httpx.MockTransport(handle)
        )

        class MutatingRepository(InMemoryLiveLongMemoryRepository):
            def reserve_provider_attempt(self, **kwargs):
                reservation = super().reserve_provider_attempt(**kwargs)
                if kwargs["stage"] == "atomExtraction":
                    proxy.model = "mutated-after-reservation"
                return reservation

        repository = MutatingRepository()
        intent, source = self._input(
            source_id=str(uuid4()),
            turns=[{"index": 1, "role": "user", "text": fact, "captureMode": "live"}],
        )
        command = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=proxy,
            support_reviewer=proxy,
            relation_reviewer=_BatchDistinctProvider(),
            run_repository=repository,
        ).extract(intent=intent, source=source)
        self.assertEqual(command.proposals[0].content["claim"], fact)
        organization_body = next(
            body for body in wire_bodies
            if "memoryAssessments" not in body["messages"][-1]["content"]
        )
        self.assertEqual(organization_body["model"], DeepSeekLiveMemoryOrganizationProxy.model)
        identity = LiveLongMemoryRunIdentity(
            owner_subject_id=intent.target.owner_subject_id,
            vault_id=intent.target.vault_id,
            product_session_id="product-session-long-test",
            capture_generation=1,
            authority_epoch=intent.target.authority_epoch,
        )
        attempt = next(
            item for item in repository.snapshot(identity.run_id)["attempts"]
            if item["stage"] == "atomExtraction"
        )
        self.assertEqual(
            attempt["requestHash"],
            hashlib.sha256(json.dumps(
                organization_body, ensure_ascii=False, sort_keys=True
            ).encode("utf-8")).hexdigest(),
        )

    def test_lm_expired_run_is_not_claimed_by_worker(self) -> None:
        from app.services.owner_truth_live_long_memory import LiveLongMemoryUnitPlan

        class FrozenDateTime(datetime):
            current = datetime(2026, 9, 23, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.current.astimezone(tz or timezone.utc)

        repository = InMemoryLiveLongMemoryRepository()
        identity = LiveLongMemoryRunIdentity("owner-claim", "vault-claim", "session-claim", 1, 1)
        policy = LiveLongMemoryBudgetPolicy(inactivity_deadline_seconds=60, absolute_deadline_seconds=120)
        with patch("app.services.owner_truth_live_long_memory.datetime", FrozenDateTime):
            repository.begin_or_load(identity, policy)
            repository.bind_source(
                run_id=identity.run_id, authority_epoch=1, source_id=str(uuid4()),
                source_version=1, source_content_hash=_digest("source"), final_watermark=1,
            )
            plan = LiveLongMemoryUnitPlan(
                run_id=identity.run_id, ordinal=0, kind="atomExtraction", generation=1,
                ownership=({"index": 1, "role": "user", "textHash": _digest("source")},),
            )
            repository.record_unit(plan)
            FrozenDateTime.current += timedelta(seconds=61)
            self.assertIsNone(repository.claim_planned_unit(worker_id="worker-late", lease_seconds=30))
            self.assertEqual(repository.snapshot(identity.run_id)["units"][0]["state"], "planned")

    def test_lm_deadline_blocks_late_provider_reservation_without_spending_budget(self) -> None:
        from app.services.owner_truth_live_long_memory import LiveLongMemoryBudgetExhausted, LiveLongMemoryUnitPlan

        class FrozenDateTime(datetime):
            current = datetime(2026, 9, 23, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.current.astimezone(tz or timezone.utc)

        repository = InMemoryLiveLongMemoryRepository()
        identity = LiveLongMemoryRunIdentity("owner-deadline", "vault-deadline", "session-deadline", 1, 1)
        policy = LiveLongMemoryBudgetPolicy(inactivity_deadline_seconds=60, absolute_deadline_seconds=120)
        with patch("app.services.owner_truth_live_long_memory.datetime", FrozenDateTime):
            repository.begin_or_load(identity, policy)
            repository.bind_source(
                run_id=identity.run_id, authority_epoch=1, source_id=str(uuid4()),
                source_version=1, source_content_hash=_digest("source"), final_watermark=1,
            )
            plan = LiveLongMemoryUnitPlan(
                run_id=identity.run_id, ordinal=0, kind="atomExtraction", generation=1,
                ownership=({"index": 1, "role": "user", "textHash": _digest("source")},),
            )
            repository.record_unit(plan)
            FrozenDateTime.current += timedelta(seconds=61)
            with self.assertRaises(LiveLongMemoryBudgetExhausted):
                repository.reserve_provider_attempt(
                    run_id=identity.run_id, unit_id=plan.unit_id, stage="organization",
                    request_hash=_digest("request"), reserved_input_tokens=10,
                    reserved_output_tokens=10, recovery=False,
                )
            self.assertEqual(repository.begin_or_load(identity, policy)["providerRequestCount"], 0)

    def test_lm_deadline_rejects_late_provider_result_without_completing_unit(self) -> None:
        from app.services.owner_truth_live_long_memory import LiveLongMemoryBudgetExhausted, LiveLongMemoryUnitPlan

        class FrozenDateTime(datetime):
            current = datetime(2026, 9, 23, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.current.astimezone(tz or timezone.utc)

        repository = InMemoryLiveLongMemoryRepository()
        identity = LiveLongMemoryRunIdentity("owner-slow", "vault-slow", "session-slow", 1, 1)
        policy = LiveLongMemoryBudgetPolicy(
            request_deadline_seconds=90, inactivity_deadline_seconds=300,
            absolute_deadline_seconds=600,
        )
        with patch("app.services.owner_truth_live_long_memory.datetime", FrozenDateTime):
            repository.begin_or_load(identity, policy)
            repository.bind_source(
                run_id=identity.run_id, authority_epoch=1, source_id=str(uuid4()),
                source_version=1, source_content_hash=_digest("source"), final_watermark=1,
            )
            plan = LiveLongMemoryUnitPlan(
                run_id=identity.run_id, ordinal=0, kind="atomExtraction", generation=1,
                ownership=({"index": 1, "role": "user", "textHash": _digest("source")},),
            )
            repository.record_unit(plan)
            repository.reserve_provider_attempt(
                run_id=identity.run_id, unit_id=plan.unit_id, stage="organization",
                request_hash=_digest("request"), reserved_input_tokens=10,
                reserved_output_tokens=10, recovery=False,
            )
            FrozenDateTime.current += timedelta(seconds=91)
            with self.assertRaises(LiveLongMemoryBudgetExhausted):
                repository.record_unit_result(
                    plan=plan, atoms=[], output_hash=_digest("late"), coverage={},
                )
            self.assertEqual(repository.begin_or_load(identity, policy)["providerRequestCount"], 1)
            self.assertNotEqual(repository.snapshot(identity.run_id)["units"][0]["state"], "completed")

    def test_lm_provider_429_is_typed_as_retryable_organization_http_failure(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429, request=request, headers={"Retry-After": "7"},
                json={"error": "synthetic"},
            )

        proxy = DeepSeekLiveMemoryOrganizationProxy(
            Settings(
                deepseek_api_key="synthetic-no-network",
                deepseek_base_url="https://controlled.invalid/chat/completions",
            ),
            transport=httpx.MockTransport(handle),
        )
        with self.assertRaises(LiveMemoryContractFailure) as raised:
            proxy.request_organization(turns=[{
                "index": 1,
                "role": "user",
                "text": "受控限流测试。",
                "captureMode": "live",
            }])
        failure = raised.exception
        self.assertEqual(failure.stage, "organizationRequest")
        self.assertEqual(failure.reason, "rateLimited")
        self.assertEqual(failure.category, "http")
        self.assertEqual(failure.provider_status, 429)
        self.assertTrue(failure.transport_retryable)
        self.assertFalse(failure.contract_retry_eligible)
        self.assertEqual(failure.retry_after_seconds, 7)

    def test_lm_retry_after_unbounded_or_invalid_header_is_safe(self) -> None:
        parse = DeepSeekLiveMemoryOrganizationProxy._retry_after_seconds
        self.assertEqual(parse(httpx.Headers({"Retry-After": "9" * 5000})), 86_400)
        self.assertIsNone(parse(httpx.Headers({"Retry-After": "not-a-date"})))

    def test_lm_provider_timeout_is_typed_as_retryable_transport_failure(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("synthetic timeout", request=request)

        proxy = DeepSeekLiveMemoryOrganizationProxy(
            Settings(
                deepseek_api_key="synthetic-no-network",
                deepseek_base_url="https://controlled.invalid/chat/completions",
            ),
            transport=httpx.MockTransport(handle),
        )
        with self.assertRaises(LiveMemoryContractFailure) as raised:
            proxy.request_organization(turns=[{
                "index": 1,
                "role": "user",
                "text": "受控超时测试。",
                "captureMode": "live",
            }])
        failure = raised.exception
        self.assertEqual(failure.stage, "organizationRequest")
        self.assertEqual(failure.reason, "readTimeout")
        self.assertEqual(failure.category, "transport")
        self.assertIsNone(failure.provider_status)
        self.assertTrue(failure.transport_retryable)
        self.assertFalse(failure.contract_retry_eligible)

    def test_lm_provider_transport_timeout_phase_is_preserved(self) -> None:
        cases = (
            (httpx.ConnectTimeout, "connectTimeout"),
            (httpx.ReadTimeout, "readTimeout"),
            (httpx.WriteTimeout, "writeTimeout"),
            (httpx.PoolTimeout, "poolTimeout"),
        )
        for error_type, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                def handle(request: httpx.Request) -> httpx.Response:
                    raise error_type("synthetic private failure", request=request)

                proxy = DeepSeekLiveMemoryOrganizationProxy(
                    Settings(
                        deepseek_api_key="synthetic-no-network",
                        deepseek_base_url="https://controlled.invalid/chat/completions",
                    ),
                    transport=httpx.MockTransport(handle),
                )
                with self.assertRaises(LiveMemoryContractFailure) as raised:
                    proxy.request_organization(turns=[{
                        "index": 1, "role": "user", "text": "受控超时分类测试。",
                        "captureMode": "live",
                    }])
                self.assertEqual(raised.exception.stage, "organizationRequest")
                self.assertEqual(raised.exception.reason, expected_reason)
                self.assertEqual(raised.exception.category, "transport")
                self.assertTrue(raised.exception.transport_retryable)

    def test_lm_provider_trickle_cannot_outlive_request_deadline(self) -> None:
        response_body = json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": '{"memories":[]}'}}]
        }).encode()
        clock = [0.0]

        class TrickleStream(httpx.SyncByteStream):
            def __iter__(self):
                midpoint = len(response_body) // 2
                yield response_body[:midpoint]
                clock[0] = 0.2
                yield response_body[midpoint:]

        proxy = DeepSeekLiveMemoryOrganizationProxy(
            Settings(
                deepseek_api_key="synthetic-no-network",
                deepseek_base_url="https://controlled.invalid/chat/completions",
            ),
            transport=httpx.MockTransport(lambda request: httpx.Response(
                200, request=request, stream=TrickleStream(),
            )),
        )
        proxy._request_deadline_seconds = 0.1
        proxy._clock = lambda: clock[0]
        with self.assertRaises(LiveMemoryContractFailure) as raised:
            proxy.request_organization(turns=[{
                "index": 1, "role": "user", "text": "受控慢流测试。", "captureMode": "live",
            }])
        self.assertEqual(raised.exception.stage, "organizationRequest")
        self.assertEqual(raised.exception.reason, "timeout")
        self.assertEqual(raised.exception.category, "transport")
        self.assertTrue(raised.exception.transport_retryable)

    def test_lm_provider_deadline_closes_blocked_transport_slot(self) -> None:
        class BlockingTransport(httpx.BaseTransport):
            def __init__(self):
                self.closed = Event()

            def handle_request(self, request):
                self.closed.wait(timeout=0.5)
                return httpx.Response(200, request=request, json={
                    "choices": [{"finish_reason": "stop", "message": {"content": '{"memories":[]}'}}]
                })

            def close(self):
                self.closed.set()

        transport = BlockingTransport()
        proxy = DeepSeekLiveMemoryOrganizationProxy(
            Settings(
                deepseek_api_key="synthetic-no-network",
                deepseek_base_url="https://controlled.invalid/chat/completions",
            ),
            transport=transport,
        )
        proxy._request_deadline_seconds = 0.05
        with self.assertRaises(LiveMemoryContractFailure) as raised:
            proxy.request_organization(turns=[{
                "index": 1, "role": "user", "text": "受控阻塞请求。", "captureMode": "live",
            }])
        self.assertTrue(transport.closed.is_set())
        self.assertEqual(raised.exception.reason, "timeout")

    @staticmethod
    def _input(*, source_id: str, turns: list[dict[str, object]]) -> tuple[AsyncEffectIntent, OwnerTruthCandidateExtractionInput]:
        text = "\n\n".join(str(turn["text"]) for turn in turns)
        intent = AsyncEffectIntent(
            operation_type="ownerTruth.source.created",
            target=AsyncEffectTarget(
                owner_subject_id="owner-live-long-test",
                vault_id="vault-live-long-test",
                resource_type="source",
                resource_id=source_id,
                resource_version=1,
                purpose="candidateExtraction",
                authority_epoch=7,
            ),
            payload_hash=_digest(f"long-live-{source_id}"),
            max_attempts=2,
        )
        source = OwnerTruthCandidateExtractionInput(
            source_content_hash=_digest(text),
            source_text=text,
            source_metadata={
                "captureMode": "live",
                "sourcePolicy": "userEvidenceOnly",
                "productSessionId": "product-session-long-test",
                "productCaptureGeneration": 1,
                "conversationTurns": turns,
            },
            source_id=source_id,
            source_version=1,
        )
        return intent, source

    def test_lm_01_twelve_independent_facts_are_not_capped_at_eight(self) -> None:
        turns: list[dict[str, object]] = []
        expected_claims: list[str] = []
        for ordinal in range(1, 40):
            if ordinal <= 12:
                claim = f"合成 F-R12 事实 {ordinal} 的代号是灯塔 {ordinal} 号。"
                expected_claims.append(claim)
                user_text = f"事实：{claim}"
            else:
                user_text = f"问题：第 {ordinal} 个测试问题是否需要回答？"
            turns.append({
                "index": len(turns) + 1,
                "role": "user",
                "text": user_text,
                "captureMode": "live",
            })
            if ordinal < 39:
                turns.append({
                    "index": len(turns) + 1,
                    "role": "assistant",
                    "text": "这是合成助手上下文，不是用户事实。",
                    "captureMode": "live",
                })
        self.assertEqual(len(turns), 77)
        self.assertEqual(sum(turn["role"] == "user" for turn in turns), 39)
        self.assertEqual(len(expected_claims), 12)
        source_id = str(uuid4())
        intent, source = self._input(source_id=source_id, turns=turns)
        provider = _FactQuestionBatchProvider()
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider,
            support_reviewer=provider,
            relation_reviewer=provider,
        )
        command = extractor.extract(intent=intent, source=source)
        actual_claims = [
            str(proposal.content.get("claim") or "")
            for proposal in command.proposals
        ]
        self.assertEqual(actual_claims, expected_claims)

    def test_lm_31_live_admission_has_a_server_only_large_source_contract(self) -> None:
        metadata = {
            "origin": "interviewReviewBatchCandidateProposal",
            "reviewBatchId": str(uuid4()),
            "captureMode": "live",
            "sourcePolicy": "userEvidenceOnly",
            "conversationTurns": [
                {
                    "index": 1,
                    "role": "user",
                    "text": "甲" * 20_000,
                    "captureMode": "live",
                },
                {
                    "index": 2,
                    "role": "user",
                    "text": "乙",
                    "captureMode": "live",
                },
            ],
        }
        text = "甲" * 20_000 + "\n\n乙"
        with self.assertRaises(OwnerTruthContractError):
            CreateTextSourceCommand(
                command_id="ordinary-conversation-over-limit",
                source_id=str(uuid4()),
                expected_version=0,
                text=text,
                metadata=metadata,
                source_kind=SourceKind.CONVERSATION,
            )

        command = CreateTextSourceCommand(
            command_id="trusted-live-conversation-over-limit",
            source_id=str(uuid4()),
            expected_version=0,
            text=text,
            metadata=metadata,
            source_kind=SourceKind.CONVERSATION,
            trusted_live_capacity=True,
        )
        self.assertEqual(command.text, text)

    def test_lm_32_live_capacity_cannot_be_enabled_by_metadata_alone(self) -> None:
        spoofed_metadata = {
            "captureMode": "live",
            "sourcePolicy": "userEvidenceOnly",
            "conversationTurns": [
                {"index": 1, "role": "user", "text": "甲", "captureMode": "live"}
            ],
        }
        with self.assertRaisesRegex(
            OwnerTruthContractError,
            "server-admitted Live metadata",
        ):
            CreateTextSourceCommand(
                command_id="spoofed-live-capacity",
                source_id=str(uuid4()),
                expected_version=0,
                text="甲" * 20_001,
                metadata=spoofed_metadata,
                source_kind=SourceKind.CONVERSATION,
                trusted_live_capacity=True,
            )

    def test_lm_14_completed_units_resume_without_provider_replay(self) -> None:
        turns = [
            {"index": index, "role": "user", "text": f"恢复事实 {index}", "captureMode": "live"}
            for index in range(1, 18)
        ]
        intent, source = self._input(
            source_id=str(uuid4()),
            turns=turns,
        )
        provider = _BatchDistinctProvider()
        repository = InMemoryLiveLongMemoryRepository()
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider,
            support_reviewer=provider,
            run_repository=repository,
        )

        first = extractor.extract(intent=intent, source=source)
        request_counts = (provider.organization_requests, provider.support_requests)
        second = extractor.extract(intent=intent, source=source)

        self.assertEqual(len(first.proposals), 17)
        self.assertEqual(first.proposals, second.proposals)
        self.assertEqual(
            (provider.organization_requests, provider.support_requests),
            request_counts,
            "restart recovery must reuse completed private units",
        )

    def test_lm_consecutive_private_preorganization_keeps_post_close_deadline_unstarted(self) -> None:
        repository = InMemoryLiveLongMemoryRepository()
        identity = LiveLongMemoryRunIdentity(
            owner_subject_id="synthetic-owner",
            vault_id="synthetic-vault",
            product_session_id=str(uuid4()),
            capture_generation=1,
            authority_epoch=1,
        )
        policy = LiveLongMemoryBudgetPolicy()
        for sequence in range(1, 18):
            text = f"Synthetic fact {sequence}."
            repository.register_segment(
                identity=identity,
                message_id=str(uuid4()),
                sequence=sequence,
                role="user",
                text=text,
                text_hash=_digest(text),
                policy=policy,
            )
        provider = _CappedLegacyProvider()
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider,
            support_reviewer=provider,
            relation_reviewer=provider,
            run_repository=repository,
        )

        self.assertEqual(extractor.preorganize_once(worker_id="synthetic-worker")["status"], "completed")
        midway = repository.snapshot(identity.run_id)
        self.assertIsNone(midway["sourceId"])
        self.assertIsNone(midway["organizationStartedAt"])
        self.assertIsNone(midway["lastProgressAt"])
        self.assertEqual(extractor.preorganize_once(worker_id="synthetic-worker")["status"], "completed")
        self.assertEqual(
            [unit["state"] for unit in repository.snapshot(identity.run_id)["units"]],
            ["completed", "completed"],
        )
        self.assertEqual(provider.organization_requests, 2)
        self.assertEqual(provider.support_requests, 2)

    def test_lm_16_live_batch_is_private_preorganized_and_reused_after_close(self) -> None:
        turns: list[dict[str, object]] = []
        for ordinal in range(1, 10):
            turns.append(
                {
                    "index": (ordinal * 2) - 1,
                    "role": "user",
                    "text": f"实时预整理事实 {ordinal}",
                    "captureMode": "live",
                }
            )
            if ordinal < 9:
                turns.append(
                    {
                        "index": ordinal * 2,
                        "role": "assistant",
                        "text": "已收到。",
                        "captureMode": "live",
                    }
                )
        intent, source = self._input(source_id=str(uuid4()), turns=turns)
        identity = LiveLongMemoryRunIdentity(
            owner_subject_id=intent.target.owner_subject_id,
            vault_id=intent.target.vault_id,
            product_session_id="product-session-long-test",
            capture_generation=1,
            authority_epoch=intent.target.authority_epoch,
        )
        repository = InMemoryLiveLongMemoryRepository()
        provider = _CappedLegacyProvider()
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider,
            support_reviewer=provider,
            relation_reviewer=provider,
            run_repository=repository,
        )
        policy = LiveLongMemoryBudgetPolicy()
        for turn in turns:
            text = str(turn["text"])
            repository.register_segment(
                identity=identity,
                message_id=str(uuid4()),
                sequence=int(turn["index"]),
                role=str(turn["role"]),
                text=text,
                text_hash=_digest(text),
                policy=policy,
            )

        result = extractor.preorganize_once(worker_id="controlled-live-worker")
        before_close = repository.snapshot(identity.run_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(provider.organization_requests, 1)
        self.assertEqual(provider.support_requests, 1)
        self.assertIsNone(before_close["sourceId"])
        self.assertIsNone(before_close["manifestHash"])
        self.assertEqual(
            [unit["state"] for unit in before_close["units"]],
            ["completed"],
            "preorganization is private and must not publish a Candidate",
        )

        repository.finalize_open_unit(run_id=identity.run_id, policy=policy)
        command = extractor.extract(intent=intent, source=source)

        self.assertEqual(len(command.proposals), 9)
        self.assertEqual(provider.organization_requests, 2)
        self.assertEqual(provider.support_requests, 2)
        self.assertEqual(
            len([unit for unit in repository.snapshot(identity.run_id)["units"] if unit["kind"] == "atomExtraction"]),
            2,
        )

    def test_lm_23_stale_unit_lease_cannot_commit_over_new_worker(self) -> None:
        repository = InMemoryLiveLongMemoryRepository()
        policy = LiveLongMemoryBudgetPolicy()
        identity = LiveLongMemoryRunIdentity(
            owner_subject_id="owner-lease",
            vault_id="vault-lease",
            product_session_id="product-lease",
            capture_generation=1,
            authority_epoch=1,
        )
        repository.register_segment(
            identity=identity,
            message_id=str(uuid4()),
            sequence=1,
            role="user",
            text="租约事实",
            text_hash=_digest("租约事实"),
            policy=policy,
        )
        repository.finalize_open_unit(run_id=identity.run_id, policy=policy)
        first = repository.claim_planned_unit(worker_id="worker-a", lease_seconds=-1)
        second = repository.claim_planned_unit(worker_id="worker-b", lease_seconds=30)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        atom = LiveLongMemoryAtomRecord.make(
            run_id=identity.run_id,
            unit_id=first.plan.unit_id,
            memory=_bind_memory({
                "memoryKind": "knowledge",
                "claim": "租约事实",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            }, text="租约事实"),
        )
        with self.assertRaisesRegex(LiveLongMemoryConflict, "lease is no longer current"):
            repository.record_unit_result(
                plan=first.plan,
                atoms=(atom,),
                output_hash=_digest("worker-a-output"),
                coverage={"requiredUserTurnIndices": [1], "excludedUserTurnIndices": []},
                lease_owner=first.lease_owner,
                lease_generation=first.lease_generation,
            )

    def test_lm_23_one_run_never_leases_more_than_two_provider_units(self) -> None:
        repository = InMemoryLiveLongMemoryRepository()
        policy = LiveLongMemoryBudgetPolicy()
        identity = LiveLongMemoryRunIdentity(
            owner_subject_id="owner-concurrency",
            vault_id="vault-concurrency",
            product_session_id="product-concurrency",
            capture_generation=1,
            authority_epoch=1,
        )
        for index in range(1, 26):
            text = f"并发事实 {index}"
            repository.register_segment(
                identity=identity,
                message_id=str(uuid4()),
                sequence=index,
                role="user",
                text=text,
                text_hash=_digest(text),
                policy=policy,
            )
        repository.finalize_open_unit(run_id=identity.run_id, policy=policy)

        first = repository.claim_planned_unit(
            worker_id="worker-one", lease_seconds=30, maximum_concurrency=2
        )
        second = repository.claim_planned_unit(
            worker_id="worker-two", lease_seconds=30, maximum_concurrency=2
        )
        third = repository.claim_planned_unit(
            worker_id="worker-three", lease_seconds=30, maximum_concurrency=2
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(third)

    def test_lm_08_through_lm_11_cross_batch_relations_are_resolved_before_publish(self) -> None:
        scenarios = (
            (
                ["用户的计划是周六跑步。", "补充：地点在杭州，时间是上午。"],
                ["用户的计划是周六上午在杭州跑步。"],
            ),
            (
                ["本场代号是旧港。", "更正：本场代号是新港。"],
                ["更正：本场代号是新港。"],
            ),
            (
                ["本场代号是旧港。", "撤回刚才的代号。"],
                [],
            ),
        )
        for ordinal, (claims, expected) in enumerate(scenarios):
            with self.subTest(ordinal=ordinal):
                turns = [
                    {"index": index, "role": "user", "text": text, "captureMode": "live"}
                    for index, text in enumerate(claims, 1)
                ]
                intent, source = self._input(source_id=str(uuid4()), turns=turns)
                provider = _SemanticRelationProvider()
                command = ModelAssistedOwnerTruthLiveConversationExtractor(
                    settings=Settings(
                        owner_truth_live_memory_organization_enabled=True,
                        owner_truth_live_long_memory_pipeline_enabled=True,
                    ),
                    organizer=provider,
                    support_reviewer=provider,
                    relation_reviewer=provider,
                ).extract(intent=intent, source=source)
                actual = [str(proposal.content.get("claim") or "") for proposal in command.proposals]
                self.assertEqual(actual, expected)

    def test_lm_short_session_preserves_twelve_independent_facts(self) -> None:
        turns: list[dict[str, Any]] = []
        expected: list[str] = []
        for ordinal in range(1, 7):
            claims = [
                f"短场第 {ordinal} 回合独立事实 {fact} 的代号是晨港 {ordinal}-{fact} 号。"
                for fact in (1, 2)
            ]
            expected.extend(claims)
            turns.append({
                "index": ordinal, "role": "user",
                "text": "；".join(f"事实：{claim}" for claim in claims),
                "captureMode": "live", "elapsedSeconds": ordinal * 10,
            })
        intent, source = self._input(source_id=str(uuid4()), turns=turns)
        provider = _FactQuestionBatchProvider()
        command = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider, support_reviewer=provider, relation_reviewer=provider,
        ).extract(intent=intent, source=source)
        actual = [str(proposal.content.get("claim") or "") for proposal in command.proposals]
        self.assertEqual(len(actual), 12)
        self.assertCountEqual(actual, expected)
        self.assertEqual(len(set(actual)), 12)

    def test_lm_relation_pages_cover_forty_by_one_thousand_global_pairs(self) -> None:
        repository = InMemoryLiveLongMemoryRepository()
        identity = LiveLongMemoryRunIdentity("owner-pairs", "vault-pairs", "session-pairs", 1, 1)
        policy = LiveLongMemoryBudgetPolicy()
        repository.begin_or_load(identity, policy)
        repository.bind_source(
            run_id=identity.run_id, authority_epoch=1, source_id=str(uuid4()),
            source_version=1, source_content_hash=_digest("事实证据"), final_watermark=1,
        )
        evidence = "事实证据"
        evidence_hash = hashlib.sha256(evidence.encode("utf-8")).hexdigest()

        def memory(label: str) -> dict[str, Any]:
            return {
                "memoryKind": "knowledge", "claim": label,
                "sourceTurnIndices": [1], "facets": _facets(),
                "_sourceEvidenceRanges": [{
                    "turnIndex": 1, "start": 0, "end": len(evidence),
                    "textHash": evidence_hash, "evidenceId": _digest(label),
                }],
            }

        existing = [memory(f"existing-{index}") for index in range(1000)]
        incoming = [memory(f"incoming-{index}") for index in range(40)]
        provider = _BatchDistinctProvider()
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider, support_reviewer=provider,
            relation_reviewer=provider, run_repository=repository,
        )
        for incoming_start in range(0, len(incoming), 8):
            for existing_start in range(0, len(existing), 32):
                page = existing[existing_start:existing_start + 32]
                results = extractor._relation_batch_page(
                    turns=[{"index": 1, "role": "user", "text": evidence, "captureMode": "live"}],
                    incoming=incoming[incoming_start:incoming_start + 8], existing=page,
                    run_identity=identity, retry_context=None,
                    ordinal=2_000_000 + incoming_start * 10_000 + existing_start,
                    intra_batch=False,
                )
                self.assertEqual(sum(result["scannedExistingCount"] for result in results),
                                 len(page) * len(incoming[incoming_start:incoming_start + 8]))
        expected_pairs = {
            (f"incoming-{incoming_index}", f"existing-{existing_index}")
            for incoming_index in range(40) for existing_index in range(1000)
        }
        self.assertEqual(provider.scanned_pairs, expected_pairs)
        self.assertEqual(repository.snapshot(identity.run_id)["providerRequestCount"],
                         provider.batch_relation_requests)

    def test_lm_04_dense_20_minutes_120_user_241_total_turns_publish_without_truncation(self) -> None:
        turns = [{
            "index": 1,
            "role": "assistant",
            "text": "长场测试开始。",
            "captureMode": "live",
            "elapsedSeconds": 0,
        }]
        expected_claims: list[str] = []
        for ordinal in range(1, 121):
            fact_count = 3 if ordinal <= 60 else 2
            facts = []
            for fact_ordinal in range(1, fact_count + 1):
                claim = (
                    f"密集长场第 {ordinal} 回合事实 {fact_ordinal} 的代号是"
                    f"航标 {ordinal}-{fact_ordinal} 号，"
                    + "并保留这一条合成原文证据。" * 5
                )
                expected_claims.append(claim)
                facts.append(f"事实：{claim}")
            turns.extend([
                {
                    "index": len(turns) + 1,
                    "role": "user",
                    "text": "；".join(facts),
                    "captureMode": "live",
                    "elapsedSeconds": ordinal * 10,
                },
                {
                    "index": len(turns) + 2,
                    "role": "assistant",
                    "text": f"已记录第 {ordinal} 条合成事实。",
                    "captureMode": "live",
                    "elapsedSeconds": ordinal * 10 + 1,
                },
            ])
        self.assertEqual(len(turns), 241)
        self.assertEqual(len(expected_claims), 300)
        self.assertGreater(len("".join(str(turn["text"]) for turn in turns)), 30_000)
        intent, source = self._input(source_id=str(uuid4()), turns=turns)
        provider = _FactQuestionBatchProvider()
        command = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider,
            support_reviewer=provider,
            relation_reviewer=provider,
        ).extract(intent=intent, source=source)

        actual_claims = [
            str(proposal.content.get("claim") or "")
            for proposal in command.proposals
        ]
        self.assertEqual(actual_claims, expected_claims)
        self.assertGreater(len(command.proposals), 32)
        self.assertGreaterEqual(provider.organization_requests, 15)
        self.assertEqual(provider.support_requests, provider.organization_requests)

    def test_lm_05_logical_65_minutes_150_user_301_total_turns_keeps_late_correction(self) -> None:
        turns = [{
            "index": 1,
            "role": "assistant",
            "text": "逻辑六十五分钟测试开始。",
            "captureMode": "live",
            "elapsedSeconds": 0,
        }]
        for ordinal in range(1, 151):
            core = (
                "更正：长场开场代号是终点一百五十号。"
                if ordinal == 150
                else (
                    "长场开场代号是起点一号。"
                    if ordinal == 1
                    else f"逻辑第 {ordinal} 段事实是航线 {ordinal}。"
                )
            )
            turns.extend([
                {
                    "index": len(turns) + 1,
                    "role": "user",
                    "text": core,
                    "captureMode": "live",
                    "elapsedSeconds": ordinal * 26,
                },
                {
                    "index": len(turns) + 2,
                    "role": "assistant",
                    "text": (
                        f"已收到第 {ordinal} 段。"
                        + "这是六十五分钟压力夹具中的合成助手上下文。" * 18
                    ),
                    "captureMode": "live",
                    "elapsedSeconds": ordinal * 26 + 1,
                },
            ])
        self.assertEqual(len(turns), 301)
        self.assertGreater(len("".join(str(turn["text"]) for turn in turns)), 50_000)
        intent, source = self._input(source_id=str(uuid4()), turns=turns)
        provider = _SemanticRelationProvider()
        command = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider,
            support_reviewer=provider,
            relation_reviewer=provider,
        ).extract(intent=intent, source=source)
        claims = [str(proposal.content.get("claim") or "") for proposal in command.proposals]

        self.assertEqual(len(claims), 149)
        self.assertFalse(any(claim.startswith("长场开场代号是起点一号。") for claim in claims))
        self.assertTrue(any(claim.startswith("更正：长场开场代号是终点一百五十号。") for claim in claims))

    def test_lm_06_one_long_turn_keeps_twenty_atomic_facts(self) -> None:
        facts = [f"单轮事实 {index} 的代号是星标 {index} 号" + "甲" * 210 for index in range(1, 21)]
        turns = [
            {
                "index": 1,
                "role": "user",
                "text": "；".join(facts) + "；",
                "captureMode": "live",
            }
        ]
        intent, source = self._input(source_id=str(uuid4()), turns=turns)
        provider = _ClauseProvider()
        command = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider,
            support_reviewer=provider,
            relation_reviewer=provider,
        ).extract(intent=intent, source=source)
        self.assertEqual(len(command.proposals), 20)
        self.assertGreater(provider.organization_requests, 1)

    def test_lm_12_ambiguous_relation_blocks_publication(self) -> None:
        turns = [
            {"index": 1, "role": "user", "text": "我大概会去杭州。", "captureMode": "live"},
            {"index": 2, "role": "user", "text": "也许不是刚才那个安排。", "captureMode": "live"},
        ]
        intent, source = self._input(source_id=str(uuid4()), turns=turns)
        provider = _AmbiguousRelationProvider()
        with self.assertRaisesRegex(LiveMemoryContractFailure, "unresolved"):
            ModelAssistedOwnerTruthLiveConversationExtractor(
                settings=Settings(
                    owner_truth_live_memory_organization_enabled=True,
                    owner_truth_live_long_memory_pipeline_enabled=True,
                ),
                organizer=provider,
                support_reviewer=provider,
                relation_reviewer=provider,
                ).extract(intent=intent, source=source)

    def test_lm_07_three_hundred_independent_facts_fit_bounded_relation_matrix(self) -> None:
        turns = [
            {
                "index": index,
                "role": "user",
                "text": f"独立压力事实 {index} 的代号是矩阵 {index} 号。",
                "captureMode": "live",
            }
            for index in range(1, 301)
        ]
        intent, source = self._input(source_id=str(uuid4()), turns=turns)
        repository = InMemoryLiveLongMemoryRepository()
        provider = _BatchDistinctProvider()
        command = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider,
            support_reviewer=provider,
            relation_reviewer=provider,
            run_repository=repository,
        ).extract(intent=intent, source=source)
        identity = LiveLongMemoryRunIdentity(
            owner_subject_id=intent.target.owner_subject_id,
            vault_id=intent.target.vault_id,
            product_session_id="product-session-long-test",
            capture_generation=1,
            authority_epoch=intent.target.authority_epoch,
        )
        snapshot = repository.snapshot(identity.run_id)

        self.assertEqual(len(command.proposals), 300)
        self.assertLessEqual(provider.maximum_incoming_page, 8)
        self.assertLessEqual(provider.maximum_existing_page, 32)
        self.assertEqual(provider.batch_relation_requests, 228)
        expected_claims = [str(turn["text"]) for turn in turns]
        expected_pairs = {
            (incoming, existing)
            for incoming_index, incoming in enumerate(expected_claims)
            for existing in expected_claims[:incoming_index]
        }
        self.assertEqual(provider.scanned_pairs, expected_pairs)
        self.assertEqual(snapshot["providerRequestCount"], 304)
        self.assertLessEqual(snapshot["plannedUnitCount"], 2_048)
        self.assertLessEqual(snapshot["reservedInputTokens"], 32_000_000)
        self.assertLessEqual(snapshot["reservedOutputTokens"], 8_000_000)

    def test_lm_11_relation_batch_contract_requires_complete_page_receipts(self) -> None:
        turns = [
            {"index": 1, "role": "user", "text": "事实一", "captureMode": "live"},
            {"index": 2, "role": "user", "text": "事实二", "captureMode": "live"},
        ]
        valid = DeepSeekLiveMemoryOrganizationProxy.parse_relation_batch_review(
            '{"results":['
            '{"incomingIndex":0,"scannedExistingCount":2,"decisions":[]},'
            '{"incomingIndex":1,"scannedExistingCount":2,"decisions":[]}'
            ']}',
            turns=turns,
            incoming_count=2,
            existing_count=2,
        )
        self.assertEqual(len(valid["results"]), 2)

        invalid_payloads = (
            '{"results":[{"incomingIndex":0,"scannedExistingCount":2,"decisions":[]}]}',
            '{"results":['
            '{"incomingIndex":0,"scannedExistingCount":1,"decisions":[]},'
            '{"incomingIndex":1,"scannedExistingCount":2,"decisions":[]}'
            ']}',
            '{"results":['
            '{"incomingIndex":0,"scannedExistingCount":2,"decisions":['
            '{"existingIndex":0,"relation":"duplicate"},'
            '{"existingIndex":1,"relation":"correction"}]},'
            '{"incomingIndex":1,"scannedExistingCount":2,"decisions":[]}'
            ']}',
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    DeepSeekLiveMemoryOrganizationProxy.parse_relation_batch_review(
                        payload,
                        turns=turns,
                        incoming_count=2,
                        existing_count=2,
                    )

    def test_lm_11_relation_batch_uses_real_http_contract_and_typed_decode(self) -> None:
        turns = [
            {"index": 1, "role": "user", "text": "原代号是晨港。", "captureMode": "live"},
            {"index": 2, "role": "user", "text": "更正为星港。", "captureMode": "live"},
        ]
        requests: list[dict[str, object]] = []

        def handle(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            requests.append(payload)
            self.assertEqual(request.headers["authorization"], "Bearer synthetic-live-key")
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            self.assertEqual(payload["max_tokens"], 4_096)
            prompt = payload["messages"][1]["content"]
            self.assertIn("incomingIndex", prompt)
            self.assertIn("scannedExistingCount", prompt)
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({
                                "results": [{
                                    "incomingIndex": 0,
                                    "scannedExistingCount": 1,
                                    "decisions": [{
                                        "existingIndex": 0,
                                        "relation": "correction",
                                    }],
                                }],
                            }, ensure_ascii=False),
                        },
                    }],
                },
            )

        proxy = DeepSeekLiveMemoryOrganizationProxy(
            Settings(
                deepseek_api_key="synthetic-live-key",
                deepseek_base_url="https://controlled.invalid/chat/completions",
            ),
            transport=httpx.MockTransport(handle),
        )
        result = proxy.request_relation_batch_review(
            turns=turns,
            incoming=[{
                "memoryKind": "knowledge",
                "claim": "更正为星港。",
                "sourceTurnIndices": [2],
                "facets": _facets(),
            }],
            existing=[{
                "memoryKind": "knowledge",
                "claim": "原代号是晨港。",
                "sourceTurnIndices": [1],
                "facets": _facets(),
            }],
        )

        self.assertEqual(len(requests), 1)
        self.assertEqual(result["results"][0]["decisions"][0]["relation"], "correction")

    def test_lm_r01_dense_single_turn_refines_until_all_twelve_facts_are_supported(self) -> None:
        facts = [
            "我的研究主题是古琴。", "我会游泳。", "我每天跑步。", "我喜欢杭州。",
            "我的专业是化学。", "我养了一只猫。", "我周末画画。", "我去年去了南京。",
            "我的生日在五月。", "我希望学习西班牙语。", "我喜欢绿色。", "我在图书馆工作。",
        ]
        last_memories: list[dict[str, object]] = []
        stages: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal last_memories
            payload = json.loads(request.content.decode("utf-8"))
            prompt = payload["messages"][-1]["content"]
            if "memoryAssessments" in prompt:
                stages.append("support")
                present = [fact for fact in facts if fact in prompt]
                result = {
                    "schemaVersion": "owner-truth-live-memory-support-v1",
                    "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
                    "memoryAssessments": [
                        {
                            "memoryIndex": index,
                            "verdict": "supported",
                            "supportingTurnIndices": [1],
                        }
                        for index in range(len(last_memories))
                    ],
                    "omittedFactBearingTurnIndices": (
                        [1] if len(last_memories) < len(present) else []
                    ),
                }
            else:
                stages.append("organization")
                present = [fact for fact in facts if fact in prompt]
                last_memories = [_typed_memory(fact, [1]) for fact in present[:8]]
                result = {"memories": last_memories}
            return httpx.Response(
                200,
                request=request,
                json={
                    "model": "controlled",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(result, ensure_ascii=False)},
                    }],
                },
            )

        settings = Settings(
            owner_truth_live_memory_organization_enabled=True,
            owner_truth_live_long_memory_pipeline_enabled=True,
            deepseek_api_key="synthetic-no-network",
            deepseek_base_url="https://controlled.invalid/chat/completions",
        )
        proxy = DeepSeekLiveMemoryOrganizationProxy(
            settings,
            transport=httpx.MockTransport(handle),
        )
        repository = InMemoryLiveLongMemoryRepository()
        intent, source = self._input(
            source_id=str(uuid4()),
            turns=[{
                "index": 1,
                "role": "user",
                "text": "".join(facts),
                "captureMode": "live",
            }],
        )
        command = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=proxy,
            support_reviewer=proxy,
            relation_reviewer=_BatchDistinctProvider(),
            run_repository=repository,
        ).extract(intent=intent, source=source)

        self.assertEqual(
            {proposal.content["claim"] for proposal in command.proposals},
            set(facts),
        )
        self.assertGreater(stages.count("organization"), 1)
        self.assertLessEqual(len(stages), 10)
        identity = LiveLongMemoryRunIdentity(
            owner_subject_id=intent.target.owner_subject_id,
            vault_id=intent.target.vault_id,
            product_session_id="product-session-long-test",
            capture_generation=1,
            authority_epoch=intent.target.authority_epoch,
        )
        attempts = repository.snapshot(identity.run_id)["attempts"]
        adapter_attempts = [
            attempt
            for attempt in attempts
            if str(attempt["stage"]).startswith("atom")
        ]
        self.assertTrue(all(attempt["finishReason"] == "stop" for attempt in adapter_attempts))
        self.assertTrue(
            all(attempt["usage"].get("prompt_tokens") == 100 for attempt in adapter_attempts)
        )

    def test_lm_r01_length_finish_is_recorded_and_refined_without_fact_loss(self) -> None:
        facts = ["我的开场代号是青竹一号。", "我的结束代号是海星二号。"]
        organization_calls = 0
        last_memories: list[dict[str, object]] = []

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal organization_calls, last_memories
            payload = json.loads(request.content.decode("utf-8"))
            prompt = payload["messages"][-1]["content"]
            if "memoryAssessments" in prompt:
                result = {
                    "schemaVersion": "owner-truth-live-memory-support-v1",
                    "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
                    "memoryAssessments": [
                        {
                            "memoryIndex": index,
                            "verdict": "supported",
                            "supportingTurnIndices": [1],
                        }
                        for index in range(len(last_memories))
                    ],
                    "omittedFactBearingTurnIndices": [],
                }
                finish_reason = "stop"
            else:
                organization_calls += 1
                if organization_calls == 1:
                    result = {"memories": []}
                    finish_reason = "length"
                else:
                    present = [fact for fact in facts if fact in prompt]
                    last_memories = [_typed_memory(fact, [1]) for fact in present]
                    result = {"memories": last_memories}
                    finish_reason = "stop"
            return httpx.Response(
                200,
                request=request,
                json={
                    "usage": {"prompt_tokens": 50, "completion_tokens": 20},
                    "choices": [{
                        "finish_reason": finish_reason,
                        "message": {"content": json.dumps(result, ensure_ascii=False)},
                    }],
                },
            )

        settings = Settings(
            owner_truth_live_memory_organization_enabled=True,
            owner_truth_live_long_memory_pipeline_enabled=True,
            deepseek_api_key="synthetic-no-network",
            deepseek_base_url="https://controlled.invalid/chat/completions",
        )
        proxy = DeepSeekLiveMemoryOrganizationProxy(
            settings,
            transport=httpx.MockTransport(handle),
        )
        repository = InMemoryLiveLongMemoryRepository()
        intent, source = self._input(
            source_id=str(uuid4()),
            turns=[{
                "index": 1,
                "role": "user",
                "text": "".join(facts),
                "captureMode": "live",
            }],
        )
        command = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=proxy,
            support_reviewer=proxy,
            relation_reviewer=_BatchDistinctProvider(),
            run_repository=repository,
        ).extract(intent=intent, source=source)

        self.assertEqual(
            {proposal.content["claim"] for proposal in command.proposals},
            set(facts),
        )
        identity = LiveLongMemoryRunIdentity(
            owner_subject_id=intent.target.owner_subject_id,
            vault_id=intent.target.vault_id,
            product_session_id="product-session-long-test",
            capture_generation=1,
            authority_epoch=intent.target.authority_epoch,
        )
        attempts = repository.snapshot(identity.run_id)["attempts"]
        self.assertEqual(attempts[0]["finishReason"], "length")
        self.assertEqual(attempts[0]["exposureState"], "rejected")
        self.assertEqual(organization_calls, 3)

    def test_lm_r01_unsplittable_minimum_fragment_fails_without_false_success(self) -> None:
        class TruncatingProvider(_CappedLegacyProvider):
            def request_organization(self, **_kwargs):
                self.organization_requests += 1
                raise contract_failure("organizationDecode", "outputTruncated", eligible=True)

        provider = TruncatingProvider()
        repository = InMemoryLiveLongMemoryRepository()
        intent, source = self._input(
            source_id=str(uuid4()),
            turns=[{
                "index": 1,
                "role": "user",
                "text": "短事实",
                "captureMode": "live",
            }],
        )
        with self.assertRaisesRegex(LiveMemoryContractFailure, "outputTruncated"):
            ModelAssistedOwnerTruthLiveConversationExtractor(
                settings=Settings(
                    owner_truth_live_memory_organization_enabled=True,
                    owner_truth_live_long_memory_pipeline_enabled=True,
                ),
                organizer=provider,
                support_reviewer=provider,
                relation_reviewer=provider,
                run_repository=repository,
            ).extract(intent=intent, source=source)
        self.assertEqual(provider.organization_requests, 1)

    def test_lm_r02_relation_text_is_revalidated_against_original_user_evidence(self) -> None:
        original = ["我周六去杭州看展。", "我还和女儿一起去了。"]
        support_calls = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal support_calls
            payload = json.loads(request.content.decode("utf-8"))
            prompt = payload["messages"][-1]["content"]
            if "memoryAssessments" in prompt:
                support_calls += 1
                prompt_memories = _json_after(
                    prompt, "【待复核草案，memoryIndex 按数组下标】"
                )
                if support_calls == 1:
                    assessments = [
                        {"memoryIndex": 0, "verdict": "supported", "supportingTurnIndices": [1]},
                        {"memoryIndex": 1, "verdict": "supported", "supportingTurnIndices": [2]},
                    ]
                else:
                    assessments = [{"memoryIndex": 0, "verdict": "unsupported", "supportingTurnIndices": []}]
                result = {
                    "schemaVersion": "owner-truth-live-memory-support-v1",
                    "turnAssessments": [
                        {"turnIndex": 1, "speechAct": "assertion"},
                        {"turnIndex": 2, "speechAct": "assertion"},
                    ],
                    "memoryAssessments": assessments,
                    "omittedFactBearingTurnIndices": [],
                }
                responsibility = list(
                    dict.fromkeys(
                        str(atom_id)
                        for memory in prompt_memories
                        for atom_id in memory.get("_atomIds", [])
                    )
                )
                if responsibility:
                    result["responsibilityAtomIds"] = responsibility
                    result["omittedOwnedAtomIds"] = []
            elif "existingIndex" in prompt:
                result = {
                    "decisions": [{
                        "existingIndex": 0,
                        "relation": "supplement",
                        "resolvedMemory": _typed_memory(
                            "我周六和女儿去杭州看展，并购买了一套海景房。",
                            [1, 2],
                        ),
                    }]
                }
            else:
                result = {
                    "memories": [
                        _typed_memory(original[0], [1]),
                        _typed_memory(original[1], [2]),
                    ]
                }
            return httpx.Response(
                200,
                request=request,
                json={
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10},
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(result, ensure_ascii=False)},
                    }],
                },
            )

        settings = Settings(
            owner_truth_live_memory_organization_enabled=True,
            owner_truth_live_long_memory_pipeline_enabled=True,
            deepseek_api_key="synthetic-no-network",
            deepseek_base_url="https://controlled.invalid/chat/completions",
        )
        proxy = DeepSeekLiveMemoryOrganizationProxy(settings, transport=httpx.MockTransport(handle))
        intent, source = self._input(
            source_id=str(uuid4()),
            turns=[
                {"index": 1, "role": "user", "text": original[0], "captureMode": "live"},
                {"index": 2, "role": "user", "text": original[1], "captureMode": "live"},
            ],
        )
        with self.assertRaisesRegex(LiveMemoryContractFailure, "factWithoutFinalDraft"):
            ModelAssistedOwnerTruthLiveConversationExtractor(
                settings=settings,
                organizer=proxy,
                support_reviewer=proxy,
                relation_reviewer=proxy,
            ).extract(intent=intent, source=source)
        self.assertEqual(support_calls, 2)

    def test_lm_r02_paraphrase_keeps_immutable_evidence_and_page_atom_scope(self) -> None:
        facts = [
            "我的研究主题是古琴。", "我会游泳。", "我每天跑步。", "我喜欢杭州。",
            "我的专业是化学。", "我养了一只猫。", "我周末画画。", "我去年去了南京。",
            "我的生日在五月。", "我希望学习西班牙语。", "我喜欢绿色。", "我在图书馆工作。",
        ]
        related = ["我在杭州工作。", "我在杭州的图书馆工作。", *facts[:10]]
        alias = {"我的研究主题是古琴。": "我的研究方向为古琴。"}

        def covered(fact: str, claims: str) -> bool:
            return (
                fact in claims
                or alias.get(fact, fact) in claims
                or (
                    fact == "我在杭州工作。"
                    and "我在杭州的图书馆工作。" in claims
                )
            )

        def handle(request: httpx.Request) -> httpx.Response:
            prompt = json.loads(request.content.decode("utf-8"))["messages"][-1]["content"]
            if "【待复核草案，memoryIndex 按数组下标】" in prompt:
                turns = _json_after(prompt, "【整场对话】")
                memories = _json_after(prompt, "【待复核草案，memoryIndex 按数组下标】")
                responsibility = list(
                    dict.fromkeys(
                        str(atom_id)
                        for memory in memories
                        for atom_id in memory.get("_atomIds", [])
                    )
                )
                claims = "".join(str(memory.get("claim") or "") for memory in memories)
                omitted = []
                if not responsibility:
                    for turn in turns:
                        if turn["role"] != "user":
                            continue
                        present = [fact for fact in related if fact in turn["text"]]
                        if any(not covered(fact, claims) for fact in present):
                            omitted.append(turn["index"])
                result = {
                    "schemaVersion": "owner-truth-live-memory-support-v1",
                    "turnAssessments": [
                        {"turnIndex": turn["index"], "speechAct": "assertion"}
                        for turn in turns if turn["role"] == "user"
                    ],
                    "memoryAssessments": [
                        {
                            "memoryIndex": index,
                            "verdict": "supported",
                            "supportingTurnIndices": list(memory["sourceTurnIndices"]),
                        }
                        for index, memory in enumerate(memories)
                    ],
                    "omittedFactBearingTurnIndices": sorted(set(omitted)),
                }
                if responsibility:
                    result["responsibilityAtomIds"] = responsibility
                    result["omittedOwnedAtomIds"] = []
            elif "新事实页：" in prompt:
                incoming = _json_after(prompt, "新事实页：")
                existing = _json_after(prompt, "既有事实页：")
                results = []
                for incoming_index, memory in enumerate(incoming):
                    decisions = []
                    for existing_index, prior in enumerate(existing):
                        if (
                            memory.get("claim") == "我在杭州的图书馆工作。"
                            and prior.get("claim") == "我在杭州工作。"
                        ):
                            decisions = [{
                                "existingIndex": existing_index,
                                "relation": "supplement",
                                "resolvedMemory": _typed_memory(
                                    "我在杭州的图书馆工作。",
                                    sorted(set(memory["sourceTurnIndices"] + prior["sourceTurnIndices"])),
                                ),
                            }]
                            break
                    results.append({
                        "incomingIndex": incoming_index,
                        "scannedExistingCount": len(existing),
                        "decisions": decisions,
                    })
                result = {"results": results}
            elif "新事实：" in prompt:
                incoming = _json_after(prompt, "新事实：")
                existing = _json_after(prompt, "现有事实页：")
                result = {
                    "decisions": [
                        {
                            "existingIndex": index,
                            "relation": (
                                "supplement"
                                if incoming.get("claim") == "我在杭州的图书馆工作。"
                                and prior.get("claim") == "我在杭州工作。"
                                else "distinct"
                            ),
                            **(
                                {
                                    "resolvedMemory": _typed_memory(
                                        "我在杭州的图书馆工作.",
                                        sorted(set(incoming["sourceTurnIndices"] + prior["sourceTurnIndices"])),
                                    )
                                }
                                if incoming.get("claim") == "我在杭州的图书馆工作。"
                                and prior.get("claim") == "我在杭州工作。"
                                else {}
                            ),
                        }
                        for index, prior in enumerate(existing)
                    ]
                }
            else:
                turns = _json_after(prompt, "【结构化对话】")
                catalog = _json_after(prompt, "【不可变证据片段】")
                memories = []
                for turn in turns:
                    if turn["role"] != "user":
                        continue
                    for fact in related:
                        if fact not in turn["text"]:
                            continue
                        evidence_id = next(
                            item["evidenceId"] for item in catalog if item["text"] == fact
                        )
                        memory = _typed_memory(alias.get(fact, fact), [turn["index"]])
                        memory["evidenceFragmentIds"] = [evidence_id]
                        memories.append(memory)
                result = {"memories": memories[:8]}
            return httpx.Response(
                200,
                request=request,
                json={
                    "usage": {"prompt_tokens": 100, "completion_tokens": 100},
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(result, ensure_ascii=False)},
                    }],
                },
            )

        settings = Settings(
            owner_truth_live_memory_organization_enabled=True,
            owner_truth_live_long_memory_pipeline_enabled=True,
            deepseek_api_key="synthetic-no-network",
            deepseek_base_url="https://controlled.invalid/chat/completions",
        )
        proxy = DeepSeekLiveMemoryOrganizationProxy(
            settings, transport=httpx.MockTransport(handle)
        )
        intent, source = self._input(
            source_id=str(uuid4()),
            turns=[{
                "index": 1,
                "role": "user",
                "text": "".join(related),
                "captureMode": "live",
            }],
        )
        command = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings,
            organizer=proxy,
            support_reviewer=proxy,
            relation_reviewer=proxy,
        ).extract(intent=intent, source=source)
        self.assertEqual(command.status.value, "succeeded")
        self.assertEqual(len(command.proposals), 11)
        self.assertIn(
            "我的研究方向为古琴。",
            {proposal.content.get("claim") for proposal in command.proposals},
        )

    def test_lm_r02_evidence_catalog_distinguishes_duplicate_positions(self) -> None:
        catalog = build_live_memory_evidence_catalog([
            {"index": 1, "role": "user", "text": "我喜欢古琴。我喜欢古琴。"},
        ])
        self.assertEqual([item["start"] for item in catalog], [0, 6])
        self.assertEqual(len({item["evidenceId"] for item in catalog}), 2)

    def test_lm_r02_nonliteral_expression_requires_valid_immutable_reference(self) -> None:
        turns = [{"index": 1, "role": "user", "text": "我的研究主题是古琴。"}]
        memory = _typed_memory("我的研究方向为古琴。", [1])
        review = {"schemaVersion": "owner-truth-live-memory-support-v1"}
        with self.assertRaisesRegex(LiveMemoryContractFailure, "evidenceBindingMissing"):
            ModelAssistedOwnerTruthLiveConversationExtractor._bind_support_proof(
                turns=turns,
                memories=[memory],
                review=review,
            )
        memory["_sourceEvidenceFragmentIds"] = ["not-a-real-reference"]
        with self.assertRaisesRegex(LiveMemoryContractFailure, "evidenceOutOfRange"):
            ModelAssistedOwnerTruthLiveConversationExtractor._bind_support_proof(
                turns=turns,
                memories=[memory],
                review=review,
            )

    def test_lm_r02_word_order_and_synonym_share_one_owned_fragment_safely(self) -> None:
        turns = [{
            "index": 1,
            "role": "user",
            "text": "我的研究主题是古琴，我每周练习三次。",
        }]
        evidence_id = build_live_memory_evidence_catalog(turns)[0]["evidenceId"]
        memories = [
            {
                **_typed_memory("古琴是我的研究主题。", [1]),
                "_sourceEvidenceFragmentIds": [evidence_id],
            },
            {
                **_typed_memory("我每周会练三回古琴。", [1]),
                "_sourceEvidenceFragmentIds": [evidence_id],
            },
        ]
        bound = ModelAssistedOwnerTruthLiveConversationExtractor._bind_support_proof(
            turns=turns,
            memories=memories,
            review={"schemaVersion": "owner-truth-live-memory-support-v1"},
        )
        self.assertEqual(len(bound), 2)
        self.assertEqual(
            bound[0]["_sourceEvidenceRanges"],
            bound[1]["_sourceEvidenceRanges"],
        )

    def test_lm_r03_manifest_requires_a_disposition_for_every_recognized_atom(self) -> None:
        repository = InMemoryLiveLongMemoryRepository()
        policy = LiveLongMemoryBudgetPolicy()
        identity = LiveLongMemoryRunIdentity("owner-ledger", "vault-ledger", "session-ledger", 1, 1)
        repository.begin_or_load(identity, policy)
        source_id = str(uuid4())
        source_text = "十二项事实"
        source_hash = _digest(source_text)
        repository.bind_source(
            run_id=identity.run_id,
            authority_epoch=1,
            source_id=source_id,
            source_version=1,
            source_content_hash=source_hash,
            final_watermark=1,
        )
        from app.services.owner_truth_live_long_memory import LiveLongMemoryUnitPlan
        plan = LiveLongMemoryUnitPlan(
            run_id=identity.run_id,
            ordinal=0,
            kind="atomExtraction",
            generation=1,
            ownership=({"index": 1, "role": "user", "textHash": source_hash},),
        )
        repository.record_unit(plan)
        atom_memories = [
            _bind_memory(_typed_memory(f"事实 {index}", [1]), text=source_text)
            for index in range(12)
        ]
        atoms = [
            LiveLongMemoryAtomRecord.make(
                run_id=identity.run_id,
                unit_id=plan.unit_id,
                memory=memory,
            )
            for memory in atom_memories
        ]
        repository.record_unit_result(
            plan=plan,
            atoms=atoms,
            output_hash=_digest("twelve-atoms"),
            coverage={"requiredUserTurnIndices": [1], "excludedUserTurnIndices": []},
        )
        snapshot = repository.snapshot(identity.run_id)
        incomplete = [{**atom_memories[0], "_atomIds": [atoms[0].atom_id]}]
        with self.assertRaisesRegex(
            LiveLongMemoryManifestIncomplete,
            "recognized atoms",
        ):
            build_publication_manifest(
                run_snapshot=snapshot,
                source_id=source_id,
                source_version=1,
                source_content_hash=source_hash,
                generation=1,
                memories=incomplete,
                required_user_turn_indices=[1],
                excluded_user_turn_indices=[],
            )

        complete = [
            {**memory, "_atomIds": [atom.atom_id]}
            for memory, atom in zip(atom_memories, atoms)
        ]
        manifest = build_publication_manifest(
            run_snapshot=snapshot,
            source_id=source_id,
            source_version=1,
            source_content_hash=source_hash,
            generation=1,
            memories=complete,
            required_user_turn_indices=[1],
            excluded_user_turn_indices=[],
        )
        self.assertEqual(len(manifest.items), 12)

        missing_replacement = json.loads(json.dumps(snapshot))
        missing_replacement["atoms"][0]["state"] = "superseded"
        missing_replacement["atoms"][0]["replacementAtomId"] = "missing-atom"
        with self.assertRaisesRegex(LiveLongMemoryManifestIncomplete, "replacement"):
            build_publication_manifest(
                run_snapshot=missing_replacement,
                source_id=source_id,
                source_version=1,
                source_content_hash=source_hash,
                generation=1,
                memories=complete,
                required_user_turn_indices=[1],
                excluded_user_turn_indices=[],
            )

        cycle = json.loads(json.dumps(snapshot))
        cycle["atoms"][0].update(
            state="superseded", replacementAtomId=cycle["atoms"][1]["atomId"]
        )
        cycle["atoms"][1].update(
            state="merged", replacementAtomId=cycle["atoms"][0]["atomId"]
        )
        with self.assertRaisesRegex(LiveLongMemoryManifestIncomplete, "cycle"):
            build_publication_manifest(
                run_snapshot=cycle,
                source_id=source_id,
                source_version=1,
                source_content_hash=source_hash,
                generation=1,
                memories=complete,
                required_user_turn_indices=[1],
                excluded_user_turn_indices=[],
            )

    def test_lm_08_cross_batch_exact_duplicate_revalidates_support_once(self) -> None:
        facts = [f"跨批事实 {index}" for index in range(1, 9)]
        turns = [
            {"index": index, "role": "user", "text": fact, "captureMode": "live"}
            for index, fact in enumerate([*facts, facts[0]], start=1)
        ]
        intent, source = self._input(source_id=str(uuid4()), turns=turns)
        repository = InMemoryLiveLongMemoryRepository()
        provider = _CappedLegacyProvider()
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider,
            support_reviewer=provider,
            relation_reviewer=provider,
            run_repository=repository,
        )

        command = extractor.extract(intent=intent, source=source)
        identity = LiveLongMemoryRunIdentity(
            intent.target.owner_subject_id,
            intent.target.vault_id,
            "product-session-long-test",
            1,
            intent.target.authority_epoch,
        )
        snapshot = repository.snapshot(identity.run_id)

        self.assertEqual(len(command.proposals), 8)
        self.assertEqual(len((snapshot or {}).get("atoms") or []), 9)
        self.assertEqual((snapshot or {}).get("state"), "readyToPublish")

    def test_lm_r04_preorganization_failure_retries_once_then_terminalizes(self) -> None:
        class FailingProvider(_CappedLegacyProvider):
            def request_organization(self, **_kwargs):
                self.organization_requests += 1
                raise contract_failure("organizationValidate", "schemaInvalid", eligible=True)

        repository = InMemoryLiveLongMemoryRepository()
        identity = LiveLongMemoryRunIdentity(
            "owner-live-long-test",
            "vault-live-long-test",
            "product-session-long-test",
            1,
            7,
        )
        policy = LiveLongMemoryBudgetPolicy()
        repository.register_segment(
            identity=identity,
            message_id=str(uuid4()),
            sequence=1,
            role="user",
            text="失败恢复事实",
            text_hash=_digest("失败恢复事实"),
            policy=policy,
        )
        repository.finalize_open_unit(run_id=identity.run_id, policy=policy)
        provider = FailingProvider()
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=provider,
            support_reviewer=provider,
            relation_reviewer=provider,
            run_repository=repository,
        )

        first = extractor.preorganize_once(worker_id="failure-worker")
        after_first = repository.snapshot(identity.run_id)
        second = extractor.preorganize_once(worker_id="failure-worker")
        after_second = repository.snapshot(identity.run_id)
        third = extractor.preorganize_once(worker_id="failure-worker")

        self.assertEqual(first["status"], "retryWait")
        self.assertEqual(after_first["units"][0]["state"], "planned")
        self.assertEqual(second["status"], "failed")
        self.assertEqual(after_second["state"], "failed")
        self.assertEqual(after_second["units"][0]["state"], "failed")
        self.assertEqual(third["status"], "idle")
        self.assertEqual(provider.organization_requests, 2)

        turns = [
            {
                "index": 1,
                "role": "user",
                "text": "失败恢复事实",
                "captureMode": "live",
            }
        ]
        intent, source = self._input(source_id=str(uuid4()), turns=turns)
        with self.assertRaisesRegex(LiveMemoryContractFailure, "runTerminal"):
            extractor.extract(intent=intent, source=source)
        self.assertEqual(
            provider.organization_requests,
            2,
            "the parent Source handoff must not reopen a terminal failed Run",
        )

    def test_lm_rate_limit_wait_does_not_reclaim_same_unit_early(self) -> None:
        class Clock(datetime):
            current = datetime(2026, 9, 23, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.current

        repository = InMemoryLiveLongMemoryRepository()
        identity = LiveLongMemoryRunIdentity(
            "owner-live-long-test", "vault-live-long-test",
            "product-session-rate-limit", 1, 7,
        )
        policy = LiveLongMemoryBudgetPolicy()
        with patch("app.services.owner_truth_live_long_memory.datetime", Clock):
            repository.register_segment(
                identity=identity, message_id=str(uuid4()), sequence=1,
                role="user", text="受控限流事实", text_hash=_digest("受控限流事实"),
                policy=policy,
            )
            repository.finalize_open_unit(run_id=identity.run_id, policy=policy)
            lease = repository.claim_planned_unit(worker_id="worker-a", lease_seconds=30)
            self.assertIsNotNone(lease)
            repository.record_unit_failure(
                plan=lease.plan, failure_code="candidateExtraction.live.organizationRequest.rateLimited",
                terminal=False, lease_owner=lease.lease_owner,
                lease_generation=lease.lease_generation, retry_seconds=7,
            )
            self.assertIsNone(repository.claim_planned_unit(worker_id="worker-b", lease_seconds=30))
            Clock.current += timedelta(seconds=7)
            resumed = repository.claim_planned_unit(worker_id="worker-b", lease_seconds=30)
            self.assertIsNotNone(resumed)
            self.assertEqual(resumed.plan.unit_id, lease.plan.unit_id)
            self.assertEqual(resumed.lease_generation, lease.lease_generation + 1)

    def test_lm_rate_limit_header_delays_real_adapter_preorganization(self) -> None:
        class Clock(datetime):
            current = datetime(2026, 9, 23, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.current

        calls = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(429, request=request, headers={"Retry-After": "7"})

        repository = InMemoryLiveLongMemoryRepository()
        identity = LiveLongMemoryRunIdentity(
            "owner-live-long-test", "vault-live-long-test",
            "product-session-rate-limit-adapter", 1, 7,
        )
        policy = LiveLongMemoryBudgetPolicy()
        proxy = DeepSeekLiveMemoryOrganizationProxy(
            Settings(
                deepseek_api_key="synthetic-no-network",
                deepseek_base_url="https://controlled.invalid/chat/completions",
            ),
            transport=httpx.MockTransport(handle),
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=proxy, support_reviewer=proxy, relation_reviewer=proxy,
            run_repository=repository,
        )
        with patch("app.services.owner_truth_live_long_memory.datetime", Clock):
            repository.register_segment(
                identity=identity, message_id=str(uuid4()), sequence=1,
                role="user", text="受控限流原文", text_hash=_digest("受控限流原文"),
                policy=policy,
            )
            repository.finalize_open_unit(run_id=identity.run_id, policy=policy)
            first = extractor.preorganize_once(worker_id="rate-limit-worker")
            second = extractor.preorganize_once(worker_id="rate-limit-worker")
            self.assertEqual(first["status"], "retryWait")
            self.assertEqual(first["reason"], "candidateExtraction.live.organizationRequest.rateLimited")
            self.assertEqual(second["status"], "idle")
            self.assertEqual(len(calls), 1)
            self.assertEqual(repository.snapshot(identity.run_id)["units"][0]["state"], "planned")
            Clock.current += timedelta(seconds=7)
            third = extractor.preorganize_once(worker_id="rate-limit-worker")
            self.assertEqual(third["status"], "failed")
            self.assertEqual(len(calls), 2)
            self.assertEqual(repository.snapshot(identity.run_id)["state"], "failed")

    def test_lm_rate_limit_cannot_schedule_beyond_frozen_run_deadline(self) -> None:
        class Clock(datetime):
            current = datetime(2026, 9, 23, tzinfo=timezone.utc)

            @classmethod
            def now(cls, tz=None):
                return cls.current

        calls = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(429, request=request, headers={"Retry-After": "7"})

        repository = InMemoryLiveLongMemoryRepository()
        identity = LiveLongMemoryRunIdentity(
            "owner-live-long-test", "vault-live-long-test",
            "product-session-rate-limit-expiry", 1, 7,
        )
        policy = LiveLongMemoryBudgetPolicy(
            inactivity_deadline_seconds=10, absolute_deadline_seconds=10,
        )
        proxy = DeepSeekLiveMemoryOrganizationProxy(
            Settings(
                deepseek_api_key="synthetic-no-network",
                deepseek_base_url="https://controlled.invalid/chat/completions",
            ),
            transport=httpx.MockTransport(handle),
        )
        extractor = ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=Settings(
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            ),
            organizer=proxy, support_reviewer=proxy, relation_reviewer=proxy,
            run_repository=repository,
        )
        with patch("app.services.owner_truth_live_long_memory.datetime", Clock), patch(
            "app.async_effects.owner_truth_candidate_extraction_worker.datetime", Clock
        ):
            repository.register_segment(
                identity=identity, message_id=str(uuid4()), sequence=1,
                role="user", text="受控期限事实", text_hash=_digest("受控期限事实"),
                policy=policy,
            )
            repository.finalize_open_unit(run_id=identity.run_id, policy=policy)
            repository.bind_source(
                run_id=identity.run_id, authority_epoch=7, source_id=str(uuid4()),
                source_version=1, source_content_hash=_digest("source"), final_watermark=1,
            )
            Clock.current += timedelta(seconds=8)
            result = extractor.preorganize_once(worker_id="deadline-worker")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "candidateExtraction.live.organizationRequest.rateLimited")
            self.assertEqual(len(calls), 1)
            self.assertEqual(repository.snapshot(identity.run_id)["state"], "failed")
            self.assertEqual(extractor.preorganize_once(worker_id="deadline-worker")["status"], "idle")

if __name__ == "__main__":
    unittest.main()
