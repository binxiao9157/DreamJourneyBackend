#!/usr/bin/env python3
"""Exercise Live Source -> Worker -> review -> formal Memory in disposable PG.

All content is synthetic. The smoke uses the production Live extractor with a
controlled HTTP transport, the real PostgreSQL repositories and HTTP routes,
then destroys and recreates the Store before reading formal memories/sources.
"""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback
from threading import Thread
from typing import Any
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import httpx
import psycopg
import uvicorn
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

import app.main as main_module
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.owner_truth_candidate_extraction_worker import (
    ModelAssistedOwnerTruthLiveConversationExtractor,
    ModelAssistedOwnerTruthSourceExtractor,
    OwnerTruthCandidateExtractionWorkerRuntime,
)
from app.core.config import Settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.services.deepseek import DeepSeekLiveMemoryOrganizationProxy
from app.services.owner_truth_live_long_memory import StoreBackedLiveLongMemoryRepository
from app.services.owner_truth_live_memory_support import (
    build_live_memory_evidence_catalog,
)
from app.services.postgres_store import PostgresStore


def load_formal_helpers() -> Any:
    path = ROOT_DIR / "scripts/backend-owner-truth-interview-confirmation-formal-postgres-smoke.py"
    spec = importlib.util.spec_from_file_location("formal_confirmation_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("formal confirmation helpers are unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FORMAL = load_formal_helpers()


class DiagnosticWorkerRuntime(OwnerTruthCandidateExtractionWorkerRuntime):
    def _extract_with_lease_heartbeat(self, **kwargs):
        try:
            return super()._extract_with_lease_heartbeat(**kwargs)
        except Exception:
            traceback.print_exc()
            raise


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()


def json_after(prompt: str, marker: str):
    return json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1].lstrip())[0]


def facets() -> dict[str, object]:
    return {
        "people": [],
        "time": [],
        "places": [],
        "relationships": [],
        "emotions": [],
        "values": [],
        "personality": [],
        "habits": [],
        "goals": [],
        "identity": [],
        "reflections": [],
        "confidence": 0.9,
    }


def seed_live_source(
    dsn: str,
    *,
    owner_subject_id: str,
    vault_id: str,
    thread_id: str,
    session_id: str,
    turns: list[dict[str, object]],
    sequence: int,
) -> tuple[str, str, AsyncEffectIntent, str]:
    review_batch_id = str(uuid.uuid4())
    source_id = str(uuid.uuid4())
    source_text = "\n".join(str(turn["text"]) for turn in turns)
    content_payload = {"text": source_text}
    content_hash = digest(content_payload)
    intent = AsyncEffectIntent(
        operation_type="ownerTruth.source.created",
        target=AsyncEffectTarget(
            owner_subject_id=owner_subject_id,
            vault_id=vault_id,
            resource_type="source",
            resource_id=source_id,
            resource_version=1,
            purpose="candidateExtraction",
            authority_epoch=0,
        ),
        payload_hash=content_hash,
        max_attempts=3,
    )
    metadata = {
        "origin": "interviewReviewBatchCandidateProposal",
        "reviewBatchId": review_batch_id,
        "captureMode": "live",
        "sourcePolicy": "userEvidenceOnly",
        "conversationTurns": turns,
    }
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batches (
                    id, vault_id, owner_subject_id, session_id, thread_id,
                    trigger, state, captured_candidate_batch_turn_count,
                    owner_turn_start_count, owner_turn_end_count,
                    through_message_sequence, policy_version, acknowledged_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'sessionExit', 'acknowledged',
                    %s, 1, %s, %s, %s, NOW()
                )
                """,
                (
                    review_batch_id,
                    vault_id,
                    owner_subject_id,
                    session_id,
                    thread_id,
                    len([turn for turn in turns if turn["role"] == "user"]),
                    len([turn for turn in turns if turn["role"] == "user"]),
                    sequence,
                    OWNER_TRUTH_SCHEMA_VERSION,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.sources (
                    id, vault_id, owner_subject_id, source_kind, content_hash,
                    policy_version, metadata, content_payload
                ) VALUES (%s, %s, %s, 'conversation', %s, %s, %s, %s)
                """,
                (
                    source_id,
                    vault_id,
                    owner_subject_id,
                    content_hash,
                    OWNER_TRUTH_SCHEMA_VERSION,
                    Jsonb(metadata),
                    Jsonb(content_payload),
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batch_candidate_admissions (
                    id, vault_id, owner_subject_id, review_batch_id, source_id,
                    source_version, source_content_hash, effect_operation_id,
                    command_id_hash, payload_hash, actor_subject_id, policy_version,
                    owner_message_count, first_message_sequence, last_message_sequence
                ) VALUES (
                    %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s,
                    %s, 1, %s
                )
                """,
                (
                    str(uuid.uuid4()),
                    vault_id,
                    owner_subject_id,
                    review_batch_id,
                    source_id,
                    content_hash,
                    intent.operation_id,
                    digest({"command": review_batch_id}),
                    intent.payload_hash,
                    owner_subject_id,
                    OWNER_TRUTH_SCHEMA_VERSION,
                    len([turn for turn in turns if turn["role"] == "user"]),
                    sequence,
                ),
            )
        connection.commit()
    return review_batch_id, source_id, intent, source_text


def seed_owner_scope(
    dsn: str,
    *,
    owner_subject_id: str,
    vault_id: str,
) -> tuple[str, str]:
    thread_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO owner_truth.vaults (vault_id, owner_subject_id)
                VALUES (%s, %s)
                ON CONFLICT (vault_id) DO NOTHING
                """,
                (vault_id, owner_subject_id),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.conversation_threads (
                    id, vault_id, owner_subject_id, entry_mode, policy_version
                ) VALUES (%s, %s, %s, 'live', %s)
                """,
                (thread_id, vault_id, owner_subject_id, OWNER_TRUTH_SCHEMA_VERSION),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_sessions (
                    id, vault_id, owner_subject_id, current_thread_id, policy_version
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    vault_id,
                    owner_subject_id,
                    thread_id,
                    OWNER_TRUTH_SCHEMA_VERSION,
                ),
            )
        connection.commit()
    return thread_id, session_id


def seed_acknowledged_live_batch(
    dsn: str,
    *,
    owner_subject_id: str,
    vault_id: str,
    thread_id: str,
    session_id: str,
    turns: list[dict[str, object]],
) -> str:
    review_batch_id = str(uuid.uuid4())
    owner_turn_count = sum(1 for turn in turns if turn["role"] == "user")
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            existing = cursor.execute(
                """
                SELECT COALESCE(MAX(sequence_number), 0),
                       COUNT(*) FILTER (WHERE author = 'owner')
                FROM owner_truth.conversation_messages
                WHERE vault_id = %s AND session_id = %s
                """,
                (vault_id, session_id),
            ).fetchone()
            sequence_offset = int(existing[0])
            owner_turn_offset = int(existing[1])
            for sequence, turn in enumerate(turns, start=1):
                content_payload = {
                    "text": str(turn["text"]),
                    "captureMode": "live",
                }
                cursor.execute(
                    """
                    INSERT INTO owner_truth.conversation_messages (
                        id, vault_id, owner_subject_id, thread_id, session_id,
                        sequence_number, author, kind, content_schema_version,
                        content_hash, content_payload, authority_epoch
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, 'narrative',
                        %s, %s, %s, 0
                    )
                    """,
                    (
                        str(uuid.uuid4()),
                        vault_id,
                        owner_subject_id,
                        thread_id,
                        session_id,
                        sequence_offset + sequence,
                        "owner" if turn["role"] == "user" else "assistant",
                        "owner-truth-conversation-message-v1",
                        digest(content_payload),
                        Jsonb(content_payload),
                    ),
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_review_batches (
                    id, vault_id, owner_subject_id, session_id, thread_id,
                    trigger, state, captured_candidate_batch_turn_count,
                    owner_turn_start_count, owner_turn_end_count,
                    through_message_sequence, policy_version, acknowledged_at
                ) VALUES (
                    %s, %s, %s, %s, %s, 'sessionExit', 'acknowledged',
                    %s, %s, %s, %s, %s, NOW()
                )
                """,
                (
                    review_batch_id,
                    vault_id,
                    owner_subject_id,
                    session_id,
                    thread_id,
                    owner_turn_count,
                    owner_turn_offset + 1,
                    owner_turn_offset + owner_turn_count,
                    sequence_offset + len(turns),
                    OWNER_TRUTH_SCHEMA_VERSION,
                ),
            )
        connection.commit()
    return review_batch_id


def controlled_extractor(
    settings: Settings,
    *,
    turns: list[dict[str, object]],
    memories: list[dict[str, object]],
    store: PostgresStore,
    expose_handler: bool = False,
) -> tuple[ModelAssistedOwnerTruthSourceExtractor | Any, list[dict[str, object]]]:
    request_bodies: list[dict[str, object]] = []
    last_memories: list[dict[str, object]] = []
    expected_turns = {int(turn["index"]): dict(turn) for turn in turns}

    def semantic_fact_value(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: semantic_fact_value(child)
                for key, child in value.items()
                if not key.startswith("_")
                and key not in {"evidenceFragmentIds", "sourceTurnIndices"}
            }
        if isinstance(value, list):
            return [semantic_fact_value(child) for child in value]
        return value

    def memory_fact_key(memory: dict[str, object]) -> str:
        value = semantic_fact_value(memory)
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    trusted_fact_evidence: dict[str, set[int]] = {}
    evidence_turn_by_id = {
        str(item["evidenceId"]): int(item["turnIndex"])
        for item in build_live_memory_evidence_catalog(turns)
    }

    def memory_evidence_indices(memory: dict[str, object]) -> set[int]:
        indices = {
            int(value) for value in memory.get("sourceTurnIndices") or []
        }
        indices.update(
            int(raw_range.get("turnIndex") or 0)
            for raw_range in memory.get("_sourceEvidenceRanges") or []
        )
        return {index for index in indices if index > 0}

    def register_trusted_fact(
        memory: dict[str, object],
        *,
        inherited_indices: set[int] | None = None,
    ) -> str:
        key = memory_fact_key(memory)
        trusted_fact_evidence.setdefault(key, set()).update(
            memory_evidence_indices(memory) | (inherited_indices or set())
        )
        return key

    for memory in memories:
        register_trusted_fact(dict(memory))

    def validate_prompt_turns(
        actual_turns: list[dict[str, object]],
        *,
        stage: str,
        scoped_memories: list[dict[str, object]] | None = None,
    ) -> None:
        scoped_ranges: dict[int, set[tuple[int, int, str]]] = {}
        for memory in scoped_memories or []:
            for raw_range in memory.get("_sourceEvidenceRanges") or []:
                turn_index = int(raw_range.get("turnIndex") or 0)
                start = int(raw_range.get("start") or 0)
                end = int(raw_range.get("end") or 0)
                expected = expected_turns.get(turn_index)
                require(
                    expected is not None and 0 <= start < end <= len(str(expected["text"])),
                    f"{stage} supplied an invalid owned evidence range",
                )
                fragment = str(expected["text"])[start:end]
                require(
                    sha256(fragment.encode("utf-8")).hexdigest()
                    == str(raw_range.get("textHash") or ""),
                    f"{stage} changed an owned evidence fragment",
                )
                scoped_ranges.setdefault(turn_index, set()).add((start, end, fragment))
        seen: set[int] = set()
        for actual in actual_turns:
            index = int(actual.get("index") or 0)
            require(index not in seen, f"{stage} duplicated turn identity")
            expected = expected_turns.get(index)
            require(expected is not None, f"{stage} introduced an unknown turn identity")
            require(
                str(actual.get("role") or "") == str(expected.get("role") or ""),
                f"{stage} changed turn ownership",
            )
            expected_text = str(expected.get("text") or "")
            allowed_texts = {expected_text}
            if index in scoped_ranges:
                allowed_texts.add(
                    " ".join(
                        fragment
                        for _start, _end, fragment in sorted(scoped_ranges[index])
                    )
                )
            require(
                str(actual.get("text") or "") in allowed_texts,
                f"{stage} changed or truncated request text",
            )
            seen.add(index)

    def validate_memory_evidence(
        values: list[dict[str, object]],
        *,
        stage: str,
        prompt_turns: list[dict[str, object]] | None = None,
    ) -> None:
        prompt_indices = {
            int(turn.get("index") or 0) for turn in (prompt_turns or [])
        }
        for memory in values:
            fact_key = memory_fact_key(memory)
            allowed_indices = trusted_fact_evidence.get(fact_key)
            require(
                allowed_indices is not None,
                f"{stage} introduced an unsupported fact",
            )
            indices = [int(value) for value in memory.get("sourceTurnIndices") or []]
            require(indices, f"{stage} memory lost source turn identity")
            require(
                all(
                    index in expected_turns
                    and str(expected_turns[index].get("role") or "") == "user"
                    for index in indices
                ),
                f"{stage} memory references non-user or foreign evidence",
            )
            require(
                set(indices).issubset(allowed_indices),
                (
                    f"{stage} fact is bound to unrelated user evidence "
                    f"fact={sha256(fact_key.encode('utf-8')).hexdigest()[:12]} "
                    f"claimed={sorted(set(indices))} allowed={sorted(allowed_indices)}"
                ),
            )
            evidence_ranges = list(memory.get("_sourceEvidenceRanges") or [])
            range_indices = {
                int(raw_range.get("turnIndex") or 0)
                for raw_range in evidence_ranges
            }
            require(
                range_indices.issubset(allowed_indices),
                f"{stage} fact is bound to an unrelated evidence range",
            )
            fragment_ids = {
                str(value) for value in memory.get("evidenceFragmentIds") or []
            }
            require(
                all(
                    fragment_id in evidence_turn_by_id
                    and evidence_turn_by_id[fragment_id] in allowed_indices
                    for fragment_id in fragment_ids
                ),
                f"{stage} fact is bound to an unrelated evidence fragment",
            )
            if prompt_turns is not None:
                require(
                    all(index in prompt_indices for index in indices),
                    f"{stage} omitted evidence required by its fact page",
                )

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal last_memories
        request_bodies.append(json.loads(request.content.decode("utf-8")))
        prompt = str(request_bodies[-1]["messages"][-1]["content"])
        if "【待复核草案，memoryIndex 按数组下标】" in prompt:
            prompt_turns = json_after(prompt, "【整场对话】")
            prompt_memories = json_after(
                prompt, "【待复核草案，memoryIndex 按数组下标】"
            )
            validate_prompt_turns(
                prompt_turns,
                stage="controlled support",
                scoped_memories=prompt_memories,
            )
            validate_memory_evidence(
                prompt_memories,
                stage="controlled support",
                prompt_turns=prompt_turns,
            )
            responsibility = list(
                dict.fromkeys(
                    str(atom_id)
                    for memory in prompt_memories
                    for atom_id in memory.get("_atomIds", [])
                )
            )
            result = {
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [
                    {
                        "turnIndex": int(turn["index"]),
                        "speechAct": (
                            "correction"
                            if str(turn.get("text") or "").startswith(("更正", "我撤回"))
                            else "query"
                            if "？" in str(turn.get("text") or "")
                            else "timeSupplement"
                            if str(turn.get("text") or "").startswith("补充：我常在周六")
                            else "assertion"
                        ),
                    }
                    for turn in prompt_turns
                    if turn["role"] == "user"
                ],
                "memoryAssessments": [
                    {
                        "memoryIndex": index,
                        "verdict": "supported",
                        "supportingTurnIndices": list(memory["sourceTurnIndices"]),
                    }
                    for index, memory in enumerate(prompt_memories)
                ],
                "omittedFactBearingTurnIndices": [],
            }
            if responsibility:
                result["responsibilityAtomIds"] = responsibility
                result["omittedOwnedAtomIds"] = []
        elif "新事实页：" in prompt:
            incoming = json_after(prompt, "新事实页：")
            existing = json_after(prompt, "既有事实页：")
            prompt_turns = json_after(prompt, "用户证据：")
            validate_prompt_turns(
                prompt_turns,
                stage="controlled relation evidence",
                scoped_memories=incoming + existing,
            )
            validate_memory_evidence(
                incoming,
                stage="controlled relation incoming",
                prompt_turns=prompt_turns,
            )
            validate_memory_evidence(
                existing,
                stage="controlled relation existing",
                prompt_turns=prompt_turns,
            )
            intra_batch = "这是批内检查" in prompt
            results = []
            for incoming_index, memory in enumerate(incoming):
                decisions = []
                for existing_index, prior in enumerate(existing):
                    if intra_batch and existing_index >= incoming_index:
                        continue
                    incoming_claim = str(memory.get("claim") or "")
                    prior_claim = str(prior.get("claim") or "")
                    relation = None
                    resolved_memory = None
                    if "更正为自驾" in incoming_claim and "坐火车" in prior_claim:
                        relation = "correction"
                    elif "撤回周六上午" in incoming_claim and "周六上午" in prior_claim:
                        relation = "retraction"
                    elif incoming_claim == prior_claim and (
                        "稳定兴趣" in incoming_claim
                        or "逻辑二十分钟测试偏好" in incoming_claim
                    ):
                        relation = "duplicate"
                    elif "周末上午阅读历史故事" in incoming_claim and "阅读历史故事" in prior_claim:
                        relation = "supplement"
                        resolved_memory = dict(prior)
                        resolved_memory["claim"] = (
                            "我的逻辑二十分钟测试偏好是阅读历史故事，通常在周末上午阅读。"
                        )
                    elif "周日下午阅读历史故事" in incoming_claim and "周末上午" in prior_claim:
                        relation = "correction"
                    elif "撤回社区图书馆" in incoming_claim and "社区图书馆" in prior_claim:
                        relation = "retraction"
                    elif (
                        "长舟终点号" in incoming_claim
                        and "第 1 段事实" in prior_claim
                    ):
                        relation = "correction"
                    if relation is not None:
                        decision = {
                            "existingIndex": existing_index,
                            "relation": relation,
                        }
                        if resolved_memory is not None:
                            decision["resolvedMemory"] = resolved_memory
                            register_trusted_fact(
                                resolved_memory,
                                inherited_indices=(
                                    trusted_fact_evidence.get(memory_fact_key(memory), set())
                                    | trusted_fact_evidence.get(memory_fact_key(prior), set())
                                ),
                            )
                        else:
                            register_trusted_fact(
                                memory,
                                inherited_indices=(
                                    trusted_fact_evidence.get(memory_fact_key(memory), set())
                                    | trusted_fact_evidence.get(memory_fact_key(prior), set())
                                ),
                            )
                        decisions = [decision]
                        break
                results.append({
                    "incomingIndex": incoming_index,
                    "scannedExistingCount": len(existing),
                    "decisions": decisions,
                })
            result = {"results": results}
        elif "新事实：" in prompt:
            incoming = json_after(prompt, "新事实：")
            existing = json_after(prompt, "现有事实页：")
            prompt_turns = json_after(prompt, "用户证据：")
            validate_prompt_turns(
                prompt_turns,
                stage="controlled relation evidence",
                scoped_memories=[incoming] + existing,
            )
            validate_memory_evidence(
                [incoming],
                stage="controlled relation incoming",
                prompt_turns=prompt_turns,
            )
            validate_memory_evidence(
                existing,
                stage="controlled relation existing",
                prompt_turns=prompt_turns,
            )
            result = {
                "decisions": [
                    dict({
                        "existingIndex": index,
                        "relation": (
                            "correction"
                            if (
                                "更正为自驾" in str(incoming.get("claim") or "")
                                and "坐火车" in str(prior.get("claim") or "")
                            )
                            or (
                                "长舟终点号" in str(incoming.get("claim") or "")
                                and "第 1 段事实" in str(prior.get("claim") or "")
                            )
                            else "retraction"
                            if (
                                "撤回周六上午" in str(incoming.get("claim") or "")
                                and "周六上午" in str(prior.get("claim") or "")
                            )
                            or (
                                "撤回社区图书馆" in str(incoming.get("claim") or "")
                                and "社区图书馆" in str(prior.get("claim") or "")
                            )
                            else "duplicate"
                            if (
                                str(incoming.get("claim") or "")
                                == str(prior.get("claim") or "")
                                and "稳定兴趣" in str(incoming.get("claim") or "")
                            )
                            else "supplement"
                            if (
                                "周末上午阅读历史故事" in str(incoming.get("claim") or "")
                                and "阅读历史故事" in str(prior.get("claim") or "")
                            )
                            else "correction"
                            if (
                                "周日下午阅读历史故事" in str(incoming.get("claim") or "")
                                and "周末上午" in str(prior.get("claim") or "")
                            )
                            else "distinct"
                        ),
                    }, **({
                        "resolvedMemory": {
                            **prior,
                            "claim": "我的逻辑二十分钟测试偏好是阅读历史故事，通常在周末上午阅读。",
                        }
                    } if (
                        "周末上午阅读历史故事" in str(incoming.get("claim") or "")
                        and "阅读历史故事" in str(prior.get("claim") or "")
                    ) else {}))
                    for index, prior in enumerate(existing)
                ]
            }
            for decision in result["decisions"]:
                if decision.get("resolvedMemory") is not None:
                    resolved_memory = dict(decision["resolvedMemory"])
                    register_trusted_fact(
                        resolved_memory,
                        inherited_indices=(
                            trusted_fact_evidence.get(memory_fact_key(incoming), set())
                            | {
                                index
                                for prior in existing
                                for index in trusted_fact_evidence.get(
                                    memory_fact_key(prior), set()
                                )
                            }
                        ),
                    )
                elif str(decision.get("relation") or "") != "distinct":
                    prior = existing[int(decision["existingIndex"])]
                    register_trusted_fact(
                        incoming,
                        inherited_indices=(
                            trusted_fact_evidence.get(memory_fact_key(incoming), set())
                            | trusted_fact_evidence.get(memory_fact_key(prior), set())
                        ),
                    )
        else:
            prompt_turns = json_after(prompt, "【结构化对话】")
            evidence_catalog = json_after(prompt, "【不可变证据片段】")
            validate_prompt_turns(prompt_turns, stage="controlled organization")
            expected_evidence = {
                str(item["evidenceId"]): item
                for item in build_live_memory_evidence_catalog(prompt_turns)
            }
            for item in evidence_catalog:
                expected = expected_evidence.get(str(item.get("evidenceId") or ""))
                require(expected is not None, "controlled organization evidence is foreign")
                require(
                    int(item.get("turnIndex") or 0) == int(expected["turnIndex"])
                    and str(item.get("text") or "") == str(expected["text"]),
                    "controlled organization evidence binding changed",
                )
            require(
                len(evidence_catalog) == len(expected_evidence),
                "controlled organization evidence catalog was truncated",
            )
            last_memories = [
                json.loads(json.dumps(memory, ensure_ascii=False))
                for memory in memories
                if any(
                    int(index) == int(turn["index"])
                    for index in memory["sourceTurnIndices"]
                    for turn in prompt_turns
                    if turn["role"] == "user"
                )
            ]
            for memory in last_memories:
                source_indices = {int(value) for value in memory["sourceTurnIndices"]}
                matching = [
                    item
                    for item in evidence_catalog
                    if int(item["turnIndex"]) in source_indices
                ]
                require(matching, "controlled organization lost immutable evidence binding")
                require(
                    {int(item["turnIndex"]) for item in matching} == source_indices,
                    "controlled organization evidence does not cover every declared source turn",
                )
                memory["evidenceFragmentIds"] = [str(item["evidenceId"]) for item in matching]
            result = {"memories": last_memories}
        return httpx.Response(
            200,
            request=request,
            json={
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(result, ensure_ascii=False)
                        },
                    }
                ]
            },
        )

    if expose_handler:
        return handle, request_bodies
    provider = DeepSeekLiveMemoryOrganizationProxy(
        settings,
        transport=httpx.MockTransport(handle),
    )
    live = ModelAssistedOwnerTruthLiveConversationExtractor(
        settings=settings,
        organizer=provider,
        support_reviewer=provider,
        relation_reviewer=provider,
        run_repository=StoreBackedLiveLongMemoryRepository(store),
    )
    return (
        ModelAssistedOwnerTruthSourceExtractor(
            settings=settings,
            live_extractor=live,
        ),
        request_bodies,
    )


def start_controlled_model_http(handler: Any) -> tuple[ThreadingHTTPServer, Thread]:
    class LocalModelRequestHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                request = httpx.Request(
                    "POST", f"http://127.0.0.1:{self.server.server_port}{self.path}",
                    content=body,
                )
                response = handler(request)
                self.send_response(response.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)
            except Exception:
                traceback.print_exc()
                self.send_error(500, "controlled model validation failed")

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalModelRequestHandler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def start_local_api_http() -> tuple[uvicorn.Server, Thread, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        main_module.app, host="127.0.0.1", port=port,
        lifespan="off", log_level="error", access_log=False,
    ))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        raise RuntimeError("isolated loopback API did not start")
    return server, thread, port


def main() -> int:
    require(
        os.environ.get("DREAMJOURNEY_OWNER_TRUTH_LIVE_FORMAL_SMOKE") == "1",
        "DREAMJOURNEY_OWNER_TRUTH_LIVE_FORMAL_SMOKE=1 is required",
    )
    base = Settings.from_env()
    base_dsn = os.environ.get(
        "OWNER_TRUTH_LIVE_FORMAL_SMOKE_ADMIN_DATABASE_URL", ""
    ).strip()
    require(base_dsn, "OWNER_TRUTH_LIVE_FORMAL_SMOKE_ADMIN_DATABASE_URL is required")
    require(bool(conninfo_to_dict(base_dsn).get("user")), "database user is required")
    admin_dsn = FORMAL.dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_live_formal_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = FORMAL.dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None
    client: TestClient | httpx.Client | None = None
    api_server: uvicorn.Server | None = None
    api_thread: Thread | None = None

    previous = {
        "store": main_module.store,
        "BACKEND_API_TOKEN": main_module.BACKEND_API_TOKEN,
        "AUTH_LEGACY_PHONE_LOGIN_ENABLED": main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED,
        "AUTH_ROUTE_MODE": main_module.AUTH_ROUTE_MODE,
        "AUTH_OWNERSHIP_MODE": main_module.AUTH_OWNERSHIP_MODE,
        "RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS": main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS,
    }
    policy_service = main_module.RELEASE_POLICY_SERVICE
    previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)

    try:
        FORMAL.create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="live-candidate-formal-postgres-smoke",
            lock_timeout_ms=1_000,
            statement_timeout_ms=30_000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {
            "ownerTruthCandidateReview"
        }
        if os.environ.get("DJ_LIVE_FORMAL_API_HTTP") == "1":
            api_server, api_thread, api_port = start_local_api_http()
            client = httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=30)
        else:
            client = TestClient(main_module.app)
        owner_id, owner_headers, user_session_id = FORMAL.login(
            client,
            phone="13900000972",
        )
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
            {*previous["RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS"], owner_id}
        )
        vault_id = "vault-live-candidate-formal-postgres-smoke"
        short_turns = [
            {"index": 1, "role": "user", "text": "我小学参加过校园合唱演出。", "captureMode": "live"},
            {"index": 2, "role": "assistant", "text": "你还记得什么细节？", "captureMode": "live"},
            {"index": 3, "role": "user", "text": "补充：那次演出的测试代号是松塔七号。", "captureMode": "live"},
        ]
        short_memories = [
            {
                "memoryKind": "experience",
                "summary": "我小学参加过校园合唱演出，测试代号是松塔七号。",
                "sourceTurnIndices": [1, 3],
                "facets": facets(),
            }
        ]
        short_b_turns = [
            {"index": 1, "role": "user", "text": "我学过一段合成的古琴练习。", "captureMode": "live"},
            {"index": 2, "role": "assistant", "text": "练习还有什么细节？", "captureMode": "live"},
            {"index": 3, "role": "user", "text": "补充：这段练习的测试代号是云桥八号。", "captureMode": "live"},
        ]
        short_b_memories = [{
            "memoryKind": "experience",
            "summary": "我学过一段古琴练习，测试代号是云桥八号。",
            "sourceTurnIndices": [1, 3],
            "facets": facets(),
        }]
        long_user_turns = [
            f"长场分批事实 {index:02d} 的测试代号是远帆{index:02d}号。"
            for index in range(1, 41)
        ]
        long_turns: list[dict[str, object]] = []
        for offset, text in enumerate(long_user_turns):
            user_index = offset * 2 + 1
            long_turns.append(
                {"index": user_index, "role": "user", "text": text, "captureMode": "live"}
            )
            if offset < len(long_user_turns) - 1:
                long_turns.append(
                    {
                        "index": user_index + 1,
                        "role": "assistant",
                        "text": "继续。",
                        "captureMode": "live",
                    }
                )
        long_memories = [
            {
                "memoryKind": "knowledge",
                "claim": text,
                "sourceTurnIndices": [offset * 2 + 1],
                "facets": facets(),
            }
            for offset, text in enumerate(long_user_turns)
        ]

        dense_turns: list[dict[str, object]] = []
        dense_memories: list[dict[str, object]] = []
        for ordinal in range(1, 151):
            user_text = (
                f"逻辑六十五分钟第 {ordinal} 段事实的测试代号是长舟{ordinal}号。"
                + "这是一段用于验证真实 Live Source 完整封存的合成证据。" * 14
            )
            claim = f"逻辑六十五分钟第 {ordinal} 段事实的测试代号是长舟{ordinal}号。"
            if ordinal == 150:
                user_text = (
                    "更正：第一段测试代号在六十一分钟后改为长舟终点号。"
                    + "这是一段用于验证真实 Live Source 完整封存的合成证据。" * 14
                )
                claim = "第一段测试代号在六十一分钟后改为长舟终点号。"
            user_index = len(dense_turns) + 1
            dense_turns.extend([
                {
                    "index": user_index,
                    "role": "user",
                    "text": user_text,
                    "captureMode": "live",
                    "elapsedSeconds": ordinal * 26,
                },
                {
                    "index": user_index + 1,
                    "role": "assistant",
                    "text": "已收到本段合成测试信息。",
                    "captureMode": "live",
                    "elapsedSeconds": ordinal * 26,
                },
            ])
            dense_memories.append({
                "memoryKind": "knowledge",
                "claim": claim,
                "sourceTurnIndices": [user_index],
                "facets": facets(),
            })
        dense_turns.append({
            "index": len(dense_turns) + 1,
            "role": "assistant",
            "text": "逻辑六十五分钟大正文 admission 测试结束。",
            "captureMode": "live",
            "elapsedSeconds": 3900,
        })
        dense_expected_memories = [*dense_memories[1:149], dense_memories[149]]

        scenarios = [
            ("short", short_turns, short_memories, short_memories),
            ("long", long_turns, long_memories, long_memories),
        ]
        if os.environ.get("DJ_LIVE_FORMAL_FOUR_SCENES") == "1":
            scenarios.append(("shortB", short_b_turns, short_b_memories, short_b_memories))
        scenarios.append(("f65", dense_turns, dense_memories, dense_expected_memories))
        expected_summaries = {
            str(memory.get("summary") or memory.get("claim"))
            for _name, _turns, _provider_memories, expected_memories in scenarios
            for memory in expected_memories
        }
        review_batches: list[dict[str, object]] = []
        provider_request_count = 0
        for sequence, (name, turns, provider_memories, expected_memories) in enumerate(scenarios, start=1):
            with psycopg.connect(test_dsn) as connection:
                connection.execute(
                    """
                    UPDATE owner_truth.interview_sessions
                    SET state = 'paused', updated_at = NOW()
                    WHERE vault_id = %s AND state = 'active'
                    """,
                    (vault_id,),
                )
                connection.commit()
            thread_id, interview_session_id = seed_owner_scope(
                test_dsn,
                owner_subject_id=owner_id,
                vault_id=vault_id,
            )
            review_batch_id = seed_acknowledged_live_batch(
                test_dsn,
                owner_subject_id=owner_id,
                vault_id=vault_id,
                thread_id=thread_id,
                session_id=interview_session_id,
                turns=turns,
            )
            admission = client.post(
                f"/v2/vaults/{vault_id}/interview-review-batches/"
                f"{review_batch_id}/candidate-proposal/admit",
                headers=FORMAL.formal_headers(
                    owner_headers,
                    session_id=user_session_id,
                    decision_id=f"live-formal-{name}-admission",
                ),
                json={
                    "commandId": f"live-formal-{name}-admission-command",
                    "expectedReviewBatchVersion": 1,
                },
            )
            require(admission.status_code == 201, f"{name} admission failed: {admission.text}")
            with psycopg.connect(test_dsn) as connection:
                row = connection.execute(
                    """
                    SELECT admission.source_id, source.content_payload, source.metadata
                    FROM owner_truth.interview_review_batch_candidate_admissions admission
                    JOIN owner_truth.sources source ON source.id = admission.source_id
                    WHERE admission.review_batch_id = %s
                    """,
                    (review_batch_id,),
                ).fetchone()
            require(row is not None, f"{name} admission did not persist its Source")
            source_id = str(row[0])
            source_text = str(dict(row[1]).get("text") or "")
            source_metadata = dict(row[2])
            if name == "f65":
                require(len(turns) == 301, "F-65 fixture must contain 301 turns")
                require(
                    sum(turn["role"] == "user" for turn in turns) == 150,
                    "F-65 fixture must contain 150 user turns",
                )
                require(len(source_text) > 50_000, "F-65 admitted Source is not over 50k")
                require(
                    len(source_metadata.get("conversationTurns") or []) == 301,
                    "F-65 admitted conversationTurns were truncated",
                )
                require(
                    source_metadata.get("captureMode") == "live"
                    and source_metadata.get("sourcePolicy") == "userEvidenceOnly",
                    "F-65 admission lost its server-owned Live binding",
                )
            runtime = replace(
                base,
                database_url=test_dsn,
                deepseek_api_key="synthetic-live-formal-key",
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                owner_truth_candidate_extraction_worker_enabled=True,
                owner_truth_live_memory_organization_enabled=True,
                owner_truth_live_long_memory_pipeline_enabled=True,
            )
            default_http = os.environ.get("DJ_LIVE_FORMAL_DEFAULT_HTTP") == "1"
            extractor_or_handler, provider_requests = controlled_extractor(
                runtime,
                turns=turns,
                memories=provider_memories,
                store=store,
                expose_handler=default_http,
            )
            model_server = None
            model_thread = None
            if default_http:
                model_server, model_thread = start_controlled_model_http(
                    extractor_or_handler
                )
                runtime = replace(
                    runtime,
                    deepseek_base_url=(
                        f"http://127.0.0.1:{model_server.server_port}/chat/completions"
                    ),
                )
            try:
                worker = DiagnosticWorkerRuntime(
                    settings=runtime,
                    store=store,
                    worker_id=f"live-formal-{name}-worker",
                    extractor=None if default_http else extractor_or_handler,
                )
                if default_http:
                    require(
                        worker._live_preorganizer is not None,
                        "default Worker did not construct the production Live extractor",
                    )
                worker_result = worker.run_once()
            finally:
                if model_server is not None:
                    model_server.shutdown()
                    model_server.server_close()
                    model_thread.join(timeout=5)
            require(
                worker_result.get("status") == "completed",
                f"{name} Worker failed: {worker_result}",
            )
            require(
                int(worker_result.get("candidateCount") or 0) == len(expected_memories),
                f"{name} candidate count mismatch",
            )
            require(provider_requests, f"{name} must traverse controlled real adapter")
            provider_request_count += len(provider_requests)

            formal_read_headers = FORMAL.formal_headers(
                owner_headers,
                session_id=user_session_id,
                decision_id=f"live-formal-{name}-read",
            )
            status = client.get(
                f"/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/candidate-proposal/status",
                headers=formal_read_headers,
            )
            require(status.status_code == 200, f"{name} status read failed: {status.text}")
            require(
                status.json()["candidateReview"]["status"] == "reviewReady",
                f"{name} status must be reviewReady",
            )
            confirmation = client.get(
                f"/v2/vaults/{vault_id}/interview-review-batches/{review_batch_id}/confirmation",
                headers=formal_read_headers,
            )
            require(
                confirmation.status_code == 200,
                f"{name} confirmation read failed: {confirmation.text}",
            )
            body = confirmation.json()
            candidates = list(body["singleCandidates"])
            require(len(candidates) == len(expected_memories), f"{name} confirmation count mismatch")
            require(not body["batchCandidates"], f"{name} Live candidates must use single review")
            require(
                {candidate["sourceId"] for candidate in candidates} == {source_id},
                f"{name} candidate Source binding mismatch",
            )
            require(
                all(
                    all(ref.get("sourceId") == source_id for ref in candidate["sourceRefs"])
                    for candidate in candidates
                ),
                f"{name} evidence Source binding mismatch",
            )
            review_batches.append(
                {
                    "name": name,
                    "reviewBatchId": review_batch_id,
                    "sourceId": source_id,
                    "sourceText": source_text,
                    "candidates": candidates,
                }
            )

        inbox = client.get(
            f"/v2/vaults/{vault_id}/candidates",
            headers=FORMAL.formal_headers(
                owner_headers,
                session_id=user_session_id,
                decision_id="live-formal-candidate-inbox",
            ),
        )
        require(inbox.status_code == 200, f"candidate inbox failed: {inbox.text}")
        require(
            len(inbox.json().get("candidates") or []) == len(expected_summaries),
            "candidate inbox must expose every generated Candidate exactly once",
        )

        replay_requests: list[tuple[str, dict[str, object], str]] = []
        for batch in review_batches:
            for index, candidate in enumerate(batch["candidates"], start=1):
                candidate_id = str(candidate["candidateId"])
                refreshed_confirmation = client.get(
                    f"/v2/vaults/{vault_id}/interview-review-batches/"
                    f"{batch['reviewBatchId']}/confirmation",
                    headers=FORMAL.formal_headers(
                        owner_headers,
                        session_id=user_session_id,
                        decision_id=(
                            f"live-formal-{batch['name']}-refresh-{index}"
                        ),
                    ),
                )
                require(
                    refreshed_confirmation.status_code == 200,
                    f"confirmation refresh failed: {refreshed_confirmation.text}",
                )
                refreshed_candidates = list(
                    refreshed_confirmation.json().get("singleCandidates") or []
                )
                refreshed_candidate = next(
                    (
                        item
                        for item in refreshed_candidates
                        if str(item.get("candidateId") or "") == candidate_id
                    ),
                    None,
                )
                require(
                    isinstance(refreshed_candidate, dict),
                    "pending Candidate disappeared before explicit owner review",
                )
                decision_path = (
                    f"/v2/vaults/{vault_id}/interview-review-batches/"
                    f"{batch['reviewBatchId']}/confirmation/candidates/{candidate_id}/decision"
                )
                decision_payload = {
                    "commandId": f"live-formal-{batch['name']}-confirm-{index}",
                    "expectedCandidateVersion": int(
                        refreshed_candidate["candidateVersion"]
                    ),
                    "action": "accept",
                }
                proposal = refreshed_candidate.get("proposedChangeSet")
                require(
                    isinstance(proposal, dict),
                    "V5 Live Candidate must expose an immutable proposed ChangeSet",
                )
                decision_payload.update(
                    {
                        "expectedMemoryRevision": int(proposal["baseMemoryRevision"]),
                        "expectedChangeSetId": str(proposal["changeSetId"]),
                        "expectedProposalHash": str(proposal["proposalHash"]),
                    }
                )
                decision = client.post(
                    decision_path,
                    headers=FORMAL.formal_headers(
                        owner_headers,
                        session_id=user_session_id,
                        decision_id=f"live-formal-{batch['name']}-confirm-authority-{index}",
                    ),
                    json=decision_payload,
                )
                require(decision.status_code == 201, f"confirmation failed: {decision.text}")
                activation_path = (
                    f"/v2/vaults/{vault_id}/interview-review-batches/"
                    f"{batch['reviewBatchId']}/confirmation/candidates/{candidate_id}/memory-activation"
                )
                activation_payload = {
                    "commandId": f"live-formal-{batch['name']}-activate-{index}"
                }
                activation = client.post(
                    activation_path,
                    headers=FORMAL.formal_headers(
                        owner_headers,
                        session_id=user_session_id,
                        decision_id=f"live-formal-{batch['name']}-activation-authority-{index}",
                    ),
                    json=activation_payload,
                )
                require(activation.status_code == 201, f"activation failed: {activation.text}")
                replay_requests.append((decision_path, decision_payload, "decision"))
                replay_requests.append((activation_path, activation_payload, "activation"))

        expected_count = len(expected_summaries)
        require(
            FORMAL.memory_counts(test_dsn, vault_id=vault_id)
            == (expected_count, expected_count),
            "confirmation must create exactly one formal version per Candidate",
        )

        client.close()
        client = None
        store.close_pool()
        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        main_module.store = store
        client = TestClient(main_module.app)

        read_headers = FORMAL.formal_headers(
            owner_headers,
            session_id=user_session_id,
            decision_id="live-formal-after-restart-read",
        )
        memories: list[dict[str, object]] = []
        cursor: str | None = None
        while True:
            formal_list = client.get(
                f"/v2/vaults/{vault_id}/memories",
                headers=read_headers,
                params={
                    "limit": 100,
                    **({"cursor": cursor} if cursor is not None else {}),
                },
            )
            require(formal_list.status_code == 200, f"formal list failed: {formal_list.text}")
            page = formal_list.json()
            memories.extend(page.get("memories") or [])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        require(len(memories) == expected_count, "formal list count changed after restart")
        observed_values: set[str] = set()
        for item in memories:
            detail = client.get(
                f"/v2/vaults/{vault_id}/memories/{item['memoryId']}",
                headers=read_headers,
            )
            require(detail.status_code == 200, f"formal detail failed: {detail.text}")
            memory = detail.json()["memory"]
            version = memory["currentVersion"]
            require(version["versionNumber"] == 1, "formal version must remain one")
            require(version["sourceCount"] == 1, "formal Source count must remain one")
            content = version["content"]
            observed_values.add(str(content.get("summary") or content.get("claim") or ""))
        require(observed_values == expected_summaries, "formal content changed after restart")
        require(
            "远帆40号" in json.dumps(memories, ensure_ascii=False),
            "tail long-session value was lost after restart",
        )
        require(
            any("长舟终点号" in value for value in observed_values)
            and not any("第 1 段事实的测试代号是长舟1号" in value for value in observed_values),
            "F-65 tail correction did not supersede the first fact",
        )

        for batch in review_batches:
            source = client.get(
                f"/v2/vaults/{vault_id}/source-records/{batch['sourceId']}",
                headers=read_headers,
            )
            require(source.status_code == 200, f"Source read failed: {source.text}")
            record = source.json()["record"]
            require(record["text"] == batch["sourceText"], "Source text changed after restart")
            require(record["confirmedCount"] == len(batch["candidates"]), "Source confirmation count mismatch")

        for index, (path, payload, kind) in enumerate(replay_requests, start=1):
            replay = client.post(
                path,
                headers=FORMAL.formal_headers(
                    owner_headers,
                    session_id=user_session_id,
                    decision_id=f"live-formal-replay-{kind}-{index}",
                ),
                json=payload,
            )
            require(
                replay.status_code == 200 and replay.json().get("status") == "deduplicated",
                f"{kind} replay must deduplicate after restart: {replay.text}",
            )
        require(
            FORMAL.memory_counts(test_dsn, vault_id=vault_id)
            == (expected_count, expected_count),
            "replay must not duplicate formal memories",
        )

        print(
            json.dumps(
                {
                    "status": "passed",
                    "defaultWorkerModelHTTP": os.environ.get("DJ_LIVE_FORMAL_DEFAULT_HTTP") == "1",
                    "apiMode": (
                        "loopbackHTTP" if os.environ.get("DJ_LIVE_FORMAL_API_HTTP") == "1"
                        else "TestClient"
                    ),
                    "schemaHead": verified["expectedHead"],
                    "providerRequestCount": provider_request_count,
                    "shortCandidateCount": len(review_batches[0]["candidates"]),
                    "longCandidateCount": len(review_batches[1]["candidates"]),
                    "shortBCandidateCount": next((
                        len(batch["candidates"]) for batch in review_batches
                        if batch["name"] == "shortB"
                    ), None),
                    "f65CandidateCount": next(
                        len(batch["candidates"]) for batch in review_batches
                        if batch["name"] == "f65"
                    ),
                    "scenarioOrder": [batch["name"] for batch in review_batches],
                    "longPipelineEnabled": True,
                    "admissionUsedHTTPRoute": True,
                    "formalMemoryCount": expected_count,
                    "storeRecreated": True,
                    "sourceDetailsReRead": True,
                    "commandsDeduplicatedAfterRestart": True,
                    "f65Admission": {
                        "httpStatus": 201,
                        "totalTurnCount": len(dense_turns),
                        "userTurnCount": sum(
                            turn.get("role") == "user" for turn in dense_turns
                        ),
                        "sourceCharacters": len(str(next(
                            batch["sourceText"] for batch in review_batches
                            if batch["name"] == "f65"
                        ))),
                        "captureMode": "live",
                        "tailCorrection": "长舟终点号",
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    finally:
        if client is not None:
            client.close()
        if api_server is not None:
            api_server.should_exit = True
            if api_thread is not None:
                api_thread.join(timeout=10)
        if store is not None:
            store.close_pool()
        for name, value in previous.items():
            setattr(main_module, name, value)
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible
        try:
            FORMAL.drop_database(admin_dsn, database_name)
        except Exception as error:
            print(
                f"warning: temporary database cleanup failed: {type(error).__name__}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    raise SystemExit(main())
