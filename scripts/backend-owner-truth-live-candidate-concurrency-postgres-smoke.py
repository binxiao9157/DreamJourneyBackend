#!/usr/bin/env python3
"""Verify Live Candidate worker concurrency and rollback in disposable PostgreSQL."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from threading import Event, Thread
import uuid
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.consumer_repository import PostgresAsyncEffectConsumerRepository
from app.async_effects.owner_truth_candidate_extraction_worker import (
    ModelAssistedOwnerTruthLiveConversationExtractor,
    ModelAssistedOwnerTruthSourceExtractor,
    OwnerTruthCandidateExtractionWorkerRuntime,
)
from app.core.config import Settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.deepseek import DeepSeekLiveMemoryOrganizationProxy
from app.services.owner_truth_live_long_memory import (
    LiveLongMemoryAtomRecord,
    LiveLongMemoryBudgetPolicy,
    LiveLongMemoryManifestIncomplete,
    LiveLongMemoryRunIdentity,
    LiveLongMemoryUnitPlan,
    StoreBackedLiveLongMemoryRepository,
    build_publication_manifest,
)
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_helpers():
    path = ROOT_DIR / "scripts/backend-owner-truth-live-candidate-formal-postgres-smoke.py"
    spec = importlib.util.spec_from_file_location("live_formal_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Live formal smoke helpers are unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def dsn_for_database(base_dsn: str, database_name: str) -> str:
    values = conninfo_to_dict(base_dsn)
    values["dbname"] = database_name
    return make_conninfo(**values)


def create_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))


def drop_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (database_name,),
        )
        connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))


def open_store(dsn: str) -> PostgresStore:
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    store.open_pool(wait=True)
    return store


def accept(store: PostgresStore, intent) -> None:
    with store.request_unit_of_work(
        correlation_id=f"live-concurrency-accept-{intent.job_id}",
        command_id=f"liveConcurrencyAccept:{intent.operation_id}",
    ):
        store.effect_kernel_repository().accept(intent)


def counts(dsn: str, *, source_id: str, operation_id: str) -> dict[str, object]:
    with psycopg.connect(dsn) as connection:
        extraction_count = connection.execute(
            "SELECT COUNT(*) FROM owner_truth.extraction_results WHERE source_id = %s",
            (source_id,),
        ).fetchone()[0]
        candidate_count = connection.execute(
            "SELECT COUNT(*) FROM owner_truth.memory_candidates WHERE source_id = %s",
            (source_id,),
        ).fetchone()[0]
        inbox_count = connection.execute(
            "SELECT COUNT(*) FROM async_effects.consumer_inbox WHERE operation_id = %s",
            (operation_id,),
        ).fetchone()[0]
        receipt_rows = connection.execute(
            "SELECT receipt_type, COUNT(*) FROM async_effects.business_receipts "
            "WHERE operation_id = %s GROUP BY receipt_type ORDER BY receipt_type",
            (operation_id,),
        ).fetchall()
        job = connection.execute(
            "SELECT state, attempt FROM async_effects.jobs WHERE operation_id = %s",
            (operation_id,),
        ).fetchone()
    return {
        "extractions": int(extraction_count),
        "candidates": int(candidate_count),
        "inbox": int(inbox_count),
        "receipts": sum(int(row[1]) for row in receipt_rows),
        "receiptTypes": {str(row[0]): int(row[1]) for row in receipt_rows},
        "jobState": None if job is None else str(job[0]),
        "jobAttempt": None if job is None else int(job[1]),
    }


def active_transaction_count(dsn: str) -> int:
    with psycopg.connect(dsn) as connection:
        return int(connection.execute(
            "SELECT COUNT(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() "
            "AND state IN ('idle in transaction', 'idle in transaction (aborted)')"
        ).fetchone()[0])


def verify_long_run_provider_concurrency(
    dsn: str,
    *,
    store_a: PostgresStore,
    store_b: PostgresStore,
) -> dict[str, object]:
    owner_id = f"synthetic-live-unit-concurrency-{uuid.uuid4().hex[:10]}"
    thread_id, session_id = load_helpers().seed_owner_scope(
        dsn,
        owner_subject_id=owner_id,
        vault_id=owner_id,
    )
    identity = LiveLongMemoryRunIdentity(
        owner_subject_id=owner_id,
        vault_id=owner_id,
        product_session_id=f"product-{uuid.uuid4()}",
        capture_generation=1,
        authority_epoch=0,
    )
    policy = LiveLongMemoryBudgetPolicy()
    messages: list[tuple[str, int, str, str]] = []
    with psycopg.connect(dsn) as connection:
        for sequence in range(1, 26):
            message_id = str(uuid.uuid4())
            text = f"隔离数据库并发事实 {sequence}"
            content_payload = {"text": text, "captureMode": "live"}
            content_hash = load_helpers().digest(content_payload)
            connection.execute(
                """
                INSERT INTO owner_truth.conversation_messages (
                    id, vault_id, owner_subject_id, thread_id, session_id,
                    sequence_number, author, kind, content_schema_version,
                    content_hash, content_payload, authority_epoch
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'owner', 'narrative',
                    %s, %s, %s, 0
                )
                """,
                (
                    message_id,
                    owner_id,
                    owner_id,
                    thread_id,
                    session_id,
                    sequence,
                    "owner-truth-conversation-message-v1",
                    content_hash,
                    psycopg.types.json.Jsonb(content_payload),
                ),
            )
            messages.append((message_id, sequence, text, content_hash))
        connection.commit()

    with store_a.request_unit_of_work(
        correlation_id="live-long-concurrency-register",
        command_id=f"liveLongConcurrencyRegister:{identity.run_id}",
    ):
        repository = store_a.owner_truth_live_long_memory_repository()
        for message_id, sequence, text, content_hash in messages:
            repository.register_segment(
                identity=identity,
                message_id=message_id,
                sequence=sequence,
                role="user",
                text=text,
                text_hash=content_hash,
                policy=policy,
            )
        repository.finalize_open_unit(run_id=identity.run_id, policy=policy)

    def claim(store: PostgresStore, worker_id: str):
        with store.request_unit_of_work(
            correlation_id=f"live-long-concurrency-claim:{worker_id}",
            command_id=f"liveLongConcurrencyClaim:{identity.run_id}:{worker_id}",
        ):
            return store.owner_truth_live_long_memory_repository().claim_planned_unit(
                worker_id=worker_id,
                lease_seconds=120,
                maximum_concurrency=2,
            )

    first = claim(store_a, "live-unit-worker-a")
    second = claim(store_b, "live-unit-worker-b")
    third = claim(store_a, "live-unit-worker-c")
    require(first is not None, "first Live unit lease was not granted")
    require(second is not None, "second Live unit lease was not granted")
    require(third is None, "one Live run exceeded two active provider units")
    require(
        first.plan.run_id == identity.run_id and second.plan.run_id == identity.run_id,
        "Live unit concurrency leases crossed run identity",
    )
    return {
        "runId": identity.run_id,
        "plannedUnits": 4,
        "grantedWorkers": [first.lease_owner, second.lease_owner],
        "thirdLease": None,
    }


def verify_manifest_failure_is_transactional(
    dsn: str,
    *,
    store: PostgresStore,
) -> dict[str, object]:
    helpers = load_helpers()
    owner_id = f"synthetic-live-manifest-{uuid.uuid4().hex[:10]}"
    thread_id, session_id = helpers.seed_owner_scope(
        dsn,
        owner_subject_id=owner_id,
        vault_id=owner_id,
    )
    turns = [{"index": 1, "role": "user", "text": "三项独立事实", "captureMode": "live"}]
    _, source_id, _intent, _ = helpers.seed_live_source(
        dsn,
        owner_subject_id=owner_id,
        vault_id=owner_id,
        thread_id=thread_id,
        session_id=session_id,
        turns=turns,
        sequence=1,
    )
    with psycopg.connect(dsn) as connection:
        source_hash = str(connection.execute(
            "SELECT content_hash FROM owner_truth.sources WHERE id = %s",
            (source_id,),
        ).fetchone()[0])
    identity = LiveLongMemoryRunIdentity(owner_id, owner_id, str(uuid.uuid4()), 1, 0)
    repository = StoreBackedLiveLongMemoryRepository(store)
    policy = LiveLongMemoryBudgetPolicy()
    repository.begin_or_load(identity, policy)
    repository.bind_source(
        run_id=identity.run_id,
        authority_epoch=0,
        source_id=source_id,
        source_version=1,
        source_content_hash=source_hash,
        final_watermark=1,
    )
    plan = LiveLongMemoryUnitPlan(
        run_id=identity.run_id,
        ordinal=0,
        kind="atomExtraction",
        generation=1,
        ownership=({"index": 1, "role": "user", "textHash": helpers.digest("三项独立事实")},),
    )
    repository.record_unit(plan)
    memories = []
    atoms = []
    for index in range(3):
        memory = {
            "memoryKind": "knowledge",
            "claim": f"独立事实 {index + 1}",
            "sourceTurnIndices": [1],
            "facets": helpers.facets(),
            "_sourceEvidenceRanges": [{
                "turnIndex": 1,
                "start": 0,
                "end": 6,
                "textHash": helpers.digest("三项独立事实"),
            }],
            "_supportProofHash": helpers.digest({"supported": index + 1}),
        }
        atom = LiveLongMemoryAtomRecord.make(
            run_id=identity.run_id,
            unit_id=plan.unit_id,
            memory=memory,
        )
        atoms.append(atom)
        memories.append({**memory, "_atomIds": [atom.atom_id]})
    repository.record_unit_result(
        plan=plan,
        atoms=atoms,
        output_hash=helpers.digest({"atoms": 3}),
        coverage={"requiredUserTurnIndices": [1], "excludedUserTurnIndices": []},
    )
    blocked = False
    try:
        with store.request_unit_of_work(
            correlation_id=f"live-manifest-invalid:{identity.run_id}",
            command_id=f"liveManifestInvalid:{identity.run_id}",
        ):
            snapshot = store.owner_truth_live_long_memory_repository().snapshot(identity.run_id)
            manifest = build_publication_manifest(
                run_snapshot=snapshot,
                source_id=source_id,
                source_version=1,
                source_content_hash=source_hash,
                generation=1,
                memories=memories[:1],
                required_user_turn_indices=[1],
                excluded_user_turn_indices=[],
            )
            store.owner_truth_live_long_memory_repository().freeze_manifest(manifest)
    except LiveLongMemoryManifestIncomplete:
        blocked = True
    require(blocked, "incomplete atom disposition was not blocked in PostgreSQL")
    with psycopg.connect(dsn) as connection:
        candidate_count = int(connection.execute(
            "SELECT COUNT(*) FROM owner_truth.memory_candidates WHERE source_id = %s",
            (source_id,),
        ).fetchone()[0])
        manifest_count = int(connection.execute(
            "SELECT COUNT(*) FROM owner_truth.live_memory_publication_manifests WHERE run_id = %s",
            (identity.run_id,),
        ).fetchone()[0])
    require(candidate_count == 0 and manifest_count == 0, "invalid manifest leaked durable rows")
    return {"blocked": blocked, "candidateCount": candidate_count, "manifestCount": manifest_count}


def verify_preorganization_failure_terminalizes(
    dsn: str,
    *,
    store: PostgresStore,
) -> dict[str, object]:
    owner_id = f"synthetic-live-failure-{uuid.uuid4().hex[:10]}"
    thread_id, session_id = load_helpers().seed_owner_scope(
        dsn,
        owner_subject_id=owner_id,
        vault_id=owner_id,
    )
    message_id = str(uuid.uuid4())
    text = "连续失败仍须终态"
    content_payload = {"text": text, "captureMode": "live"}
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            INSERT INTO owner_truth.conversation_messages (
                id, vault_id, owner_subject_id, thread_id, session_id,
                sequence_number, author, kind, content_schema_version,
                content_hash, content_payload, authority_epoch
            ) VALUES (
                %s, %s, %s, %s, %s, 1, 'owner', 'narrative',
                %s, %s, %s, 0
            )
            """,
            (
                message_id,
                owner_id,
                owner_id,
                thread_id,
                session_id,
                "owner-truth-conversation-message-v1",
                load_helpers().digest(content_payload),
                psycopg.types.json.Jsonb(content_payload),
            ),
        )
        connection.commit()
    identity = LiveLongMemoryRunIdentity(
        owner_id,
        owner_id,
        str(uuid.uuid4()),
        1,
        0,
    )
    repository = StoreBackedLiveLongMemoryRepository(store)
    policy = LiveLongMemoryBudgetPolicy()
    repository.register_segment(
        identity=identity,
        message_id=message_id,
        sequence=1,
        role="user",
        text=text,
        text_hash=sha256(text.encode("utf-8")).hexdigest(),
        policy=policy,
    )
    repository.finalize_open_unit(run_id=identity.run_id, policy=policy)
    provider_calls: list[int] = []

    def fail_contract(request: httpx.Request) -> httpx.Response:
        provider_calls.append(1)
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"invalid": []})},
                }],
            },
        )

    settings = replace(
        Settings.from_env(),
        deepseek_api_key="synthetic-live-terminal-key",
        deepseek_base_url="https://controlled.invalid/chat/completions",
        async_effect_v1_enabled=True,
        async_effect_worker_enabled=True,
        owner_truth_candidate_extraction_worker_enabled=True,
        owner_truth_live_memory_organization_enabled=True,
        owner_truth_live_long_memory_pipeline_enabled=True,
    )
    runtime = OwnerTruthCandidateExtractionWorkerRuntime(
        settings=settings,
        store=store,
        worker_id="live-failure-terminal-worker",
    )
    runtime._live_preorganizer._organizer._transport = httpx.MockTransport(fail_contract)
    first = runtime.run_once()
    second = runtime.run_once()
    third = runtime.run_once()
    snapshot = repository.snapshot(identity.run_id)
    require(first.get("status") == "retryWait", f"first failure did not wait: {first}")
    require(second.get("status") == "failed", f"second failure did not terminalize: {second}")
    require(third.get("status") == "idle", f"failed unit was re-leased: {third}")
    require(snapshot.get("state") == "failed", "failed run remained organizing")
    require(snapshot["units"][0].get("state") == "failed", "failed unit remained executable")
    require(len(provider_calls) == 2, "terminal failure consumed an extra provider request")

    turns = [{
        "index": 1,
        "role": "user",
        "text": text,
        "captureMode": "live",
    }]
    _, source_id, intent, _ = load_helpers().seed_live_source(
        dsn,
        owner_subject_id=owner_id,
        vault_id=owner_id,
        thread_id=thread_id,
        session_id=session_id,
        turns=turns,
        sequence=1,
    )
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            UPDATE owner_truth.sources
            SET metadata = metadata || %s
            WHERE id = %s
            """,
            (
                psycopg.types.json.Jsonb({
                    "productSessionId": identity.product_session_id,
                    "productCaptureGeneration": identity.capture_generation,
                }),
                source_id,
            ),
        )
        connection.commit()
    accept(store, intent)

    parent_result = runtime.run_once()
    parent_counts = counts(
        dsn,
        source_id=source_id,
        operation_id=intent.operation_id,
    )
    with psycopg.connect(dsn) as connection:
        parent_extraction_status = str(connection.execute(
            "SELECT status FROM owner_truth.extraction_results WHERE source_id = %s",
            (source_id,),
        ).fetchone()[0])
    require(
        parent_result.get("status") == "failed"
        and parent_result.get("failureCode")
        == "candidateExtraction.live.pipelineInput.runTerminal",
        f"failed private run did not terminalize parent job: {parent_result}",
    )
    require(
        parent_counts["candidates"] == 0
        and parent_counts["extractions"] == 1
        and parent_extraction_status == "failed"
        and parent_counts["jobState"] == "failed",
        f"failed private run leaked parent business output: {parent_counts}",
    )
    require(
        len(provider_calls) == 2,
        "failed private run caused a third provider request during parent handoff",
    )

    restarted_runtime = OwnerTruthCandidateExtractionWorkerRuntime(
        settings=settings,
        store=store,
        worker_id="live-failure-terminal-restarted-worker",
    )
    restarted_runtime._live_preorganizer._organizer._transport = httpx.MockTransport(
        fail_contract
    )
    restarted = restarted_runtime.run_once()
    require(restarted.get("status") == "idle", f"failed parent was re-leased: {restarted}")
    require(len(provider_calls) == 2, "worker restart consumed another provider request")
    return {
        "first": first.get("status"),
        "second": second.get("status"),
        "third": third.get("status"),
        "providerCalls": len(provider_calls),
        "runState": snapshot.get("state"),
        "unitState": snapshot["units"][0].get("state"),
        "failureCode": snapshot["units"][0].get("failureCode"),
        "parentStatus": parent_result.get("status"),
        "parentFailureCode": parent_result.get("failureCode"),
        "parentCandidateCount": parent_counts["candidates"],
        "parentExtractionStatus": parent_extraction_status,
        "parentJobState": parent_counts["jobState"],
        "restartStatus": restarted.get("status"),
    }


def verify_default_runtime_preorganization_handoff(
    dsn: str,
    *,
    store: PostgresStore,
) -> dict[str, object]:
    helpers = load_helpers()
    owner_id = f"synthetic-live-default-{uuid.uuid4().hex[:10]}"
    thread_id, session_id = helpers.seed_owner_scope(
        dsn,
        owner_subject_id=owner_id,
        vault_id=owner_id,
    )
    facts = [
        "默认运行时交接测试代号是松柏九十三号。",
        "默认运行时交接测试地点是北岸会议室。",
    ]
    message_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    with psycopg.connect(dsn) as connection:
        for sequence, (message_id, fact) in enumerate(zip(message_ids, facts), start=1):
            content_payload = {"text": fact, "captureMode": "live"}
            connection.execute(
                """
                INSERT INTO owner_truth.conversation_messages (
                    id, vault_id, owner_subject_id, thread_id, session_id,
                    sequence_number, author, kind, content_schema_version,
                    content_hash, content_payload, authority_epoch
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'owner', 'narrative',
                    %s, %s, %s, 0
                )
                """,
                (
                    message_id,
                    owner_id,
                    owner_id,
                    thread_id,
                    session_id,
                    sequence,
                    "owner-truth-conversation-message-v1",
                    helpers.digest(content_payload),
                    psycopg.types.json.Jsonb(content_payload),
                ),
            )
        connection.commit()

    identity = LiveLongMemoryRunIdentity(
        owner_id,
        owner_id,
        str(uuid.uuid4()),
        1,
        0,
    )
    repository = StoreBackedLiveLongMemoryRepository(store)
    policy = LiveLongMemoryBudgetPolicy()
    for sequence, (message_id, fact) in enumerate(zip(message_ids, facts), start=1):
        repository.register_segment(
            identity=identity,
            message_id=message_id,
            sequence=sequence,
            role="user",
            text=fact,
            text_hash=sha256(fact.encode("utf-8")).hexdigest(),
            policy=policy,
        )
    repository.finalize_open_unit(run_id=identity.run_id, policy=policy)

    provider_stages: list[str] = []
    memories = [
        {
            "memoryKind": "knowledge",
            "claim": fact,
            "sourceTurnIndices": [index],
            "facets": helpers.facets(),
        }
        for index, fact in enumerate(facts, start=1)
    ]

    def controlled_contract(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        prompt = str(body["messages"][-1]["content"])
        if "memoryAssessments" in prompt:
            provider_stages.append("support")
            result = {
                "schemaVersion": "owner-truth-live-memory-support-v1",
                "turnAssessments": [
                    {"turnIndex": index, "speechAct": "assertion"}
                    for index in range(1, len(facts) + 1)
                ],
                "memoryAssessments": [
                    {
                        "memoryIndex": index,
                        "verdict": "supported",
                        "supportingTurnIndices": list(memory["sourceTurnIndices"]),
                    }
                    for index, memory in enumerate(memories)
                ],
                "omittedFactBearingTurnIndices": [],
            }
        elif "现有事实页：" in prompt and "新事实：" in prompt:
            provider_stages.append("relation")
            existing_match = re.search(r"现有事实页：(\[.*\])", prompt)
            require(
                existing_match is not None,
                "controlled single relation request did not expose its existing page",
            )
            existing_count = len(json.loads(existing_match.group(1)))
            result = {
                "decisions": [
                    {"existingIndex": index, "relation": "distinct"}
                    for index in range(existing_count)
                ]
            }
        elif "scannedExistingCount" in prompt:
            provider_stages.append("relation")
            incoming_match = re.search(r"新事实页：(\[.*?\])\n既有事实页：", prompt)
            existing_match = re.search(r'"scannedExistingCount":(\d+)', prompt)
            require(
                incoming_match is not None and existing_match is not None,
                "controlled relation request did not expose its page contract",
            )
            incoming_count = len(json.loads(incoming_match.group(1)))
            existing_count = int(existing_match.group(1))
            result = {
                "results": [
                    {
                        "incomingIndex": index,
                        "scannedExistingCount": existing_count,
                        "decisions": [],
                    }
                    for index in range(incoming_count)
                ]
            }
        else:
            provider_stages.append("organization")
            result = {"memories": memories}
        return httpx.Response(
            200,
            request=request,
            json={
                "usage": {"prompt_tokens": 32, "completion_tokens": 16},
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"content": json.dumps(result, ensure_ascii=False)},
                }],
            },
        )

    settings = replace(
        Settings.from_env(),
        database_url=dsn,
        deepseek_api_key="synthetic-live-default-key",
        deepseek_base_url="https://controlled.invalid/chat/completions",
        async_effect_v1_enabled=True,
        async_effect_worker_enabled=True,
        owner_truth_candidate_extraction_worker_enabled=True,
        owner_truth_live_memory_organization_enabled=True,
        owner_truth_live_long_memory_pipeline_enabled=True,
    )
    disabled_runtime = OwnerTruthCandidateExtractionWorkerRuntime(
        settings=replace(settings, owner_truth_live_long_memory_pipeline_enabled=False),
        store=store,
        worker_id="live-default-runtime-disabled-worker",
    )
    disabled_runtime._live_preorganizer._organizer._transport = httpx.MockTransport(
        controlled_contract
    )
    before_disabled = repository.snapshot(identity.run_id)
    disabled_result = disabled_runtime.run_once()
    after_disabled = repository.snapshot(identity.run_id)
    require(
        disabled_result.get("status") == "idle"
        and provider_stages == []
        and before_disabled["units"] == after_disabled["units"]
        and after_disabled["units"][0].get("state") == "planned",
        "disabled default Worker claimed a private unit or called Provider",
    )

    runtime = OwnerTruthCandidateExtractionWorkerRuntime(
        settings=settings,
        store=store,
        worker_id="live-default-runtime-worker",
    )
    runtime._live_preorganizer._organizer._transport = httpx.MockTransport(
        controlled_contract
    )

    preorganization = runtime.run_once()
    private_snapshot = repository.snapshot(identity.run_id)
    require(
        preorganization.get("status") == "completed"
        and provider_stages == ["organization", "support"],
        f"default runtime did not complete private organization: {preorganization}",
    )
    require(
        private_snapshot.get("sourceId") is None
        and private_snapshot.get("manifestHash") is None
        and private_snapshot["units"][0].get("state") == "completed",
        "private preorganization published before Source admission",
    )

    turns = [
        {"index": index, "role": "user", "text": fact, "captureMode": "live"}
        for index, fact in enumerate(facts, start=1)
    ]
    _, source_id, intent, _ = helpers.seed_live_source(
        dsn,
        owner_subject_id=owner_id,
        vault_id=owner_id,
        thread_id=thread_id,
        session_id=session_id,
        turns=turns,
        sequence=len(turns),
    )
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            UPDATE owner_truth.sources
            SET metadata = metadata || %s
            WHERE id = %s
            """,
            (
                psycopg.types.json.Jsonb({
                    "productSessionId": identity.product_session_id,
                    "productCaptureGeneration": identity.capture_generation,
                }),
                source_id,
            ),
        )
        connection.commit()
    accept(store, intent)

    published = runtime.run_once()
    final_snapshot = repository.snapshot(identity.run_id)
    published_counts = counts(
        dsn,
        source_id=source_id,
        operation_id=intent.operation_id,
    )
    require(
        published.get("status") == "completed"
        and published_counts["candidates"] == len(memories)
        and published_counts["jobState"] == "succeeded",
        f"default runtime did not publish frozen manifest: {published}; stages={provider_stages}",
    )
    require(final_snapshot.get("state") == "published", "Live run was not marked published")
    require(
        provider_stages == ["organization", "support", "relation"],
        f"parent handoff repeated organization or skipped default relation adapter: {provider_stages}",
    )

    restarted_runtime = OwnerTruthCandidateExtractionWorkerRuntime(
        settings=settings,
        store=store,
        worker_id="live-default-runtime-restarted-worker",
    )
    restarted_runtime._live_preorganizer._organizer._transport = httpx.MockTransport(
        controlled_contract
    )
    restarted = restarted_runtime.run_once()
    require(restarted.get("status") == "idle", f"published job was re-leased: {restarted}")
    require(
        provider_stages == ["organization", "support", "relation"],
        "worker restart repeated a provider request",
    )
    restart_snapshot = repository.snapshot(identity.run_id)
    require(
        restart_snapshot.get("providerRequestCount") == final_snapshot.get("providerRequestCount")
        and restart_snapshot.get("budgetPolicyHash") == final_snapshot.get("budgetPolicyHash"),
        "Worker restart reset the original Run budget",
    )
    return {
        "disabledStatus": disabled_result.get("status"),
        "disabledProviderCalls": 0,
        "disabledUnitState": after_disabled["units"][0].get("state"),
        "preorganizationStatus": preorganization.get("status"),
        "privateUnitState": private_snapshot["units"][0].get("state"),
        "privateSourceId": private_snapshot.get("sourceId"),
        "providerStages": provider_stages,
        "parentStatus": published.get("status"),
        "candidateCount": published_counts["candidates"],
        "jobState": published_counts["jobState"],
        "runState": final_snapshot.get("state"),
        "budgetPreservedAcrossRestart": True,
        "restartStatus": restarted.get("status"),
    }


def barrier_extractor(settings: Settings, *, fact: str, entered: Event, release: Event):
    memories = [{
        "memoryKind": "knowledge",
        "claim": fact,
        "sourceTurnIndices": [1],
        "facets": load_helpers().facets(),
    }]
    support = {
        "schemaVersion": "owner-truth-live-memory-support-v1",
        "turnAssessments": [{"turnIndex": 1, "speechAct": "assertion"}],
        "memoryAssessments": [{
            "memoryIndex": 0,
            "verdict": "supported",
            "supportingTurnIndices": [1],
        }],
        "omittedFactBearingTurnIndices": [],
    }
    responses = iter([{"memories": memories}, support])
    request_count: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        request_count.append(1)
        if len(request_count) == 1:
            entered.set()
            require(release.wait(timeout=5), "controlled provider barrier timed out")
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(next(responses), ensure_ascii=False),
                    },
                }],
            },
        )

    provider = DeepSeekLiveMemoryOrganizationProxy(
        settings,
        transport=httpx.MockTransport(handle),
    )
    return (
        ModelAssistedOwnerTruthSourceExtractor(
            settings=settings,
            live_extractor=ModelAssistedOwnerTruthLiveConversationExtractor(
                settings=settings,
                organizer=provider,
            ),
        ),
        request_count,
    )


def seed_job(helpers, dsn: str, *, fact: str):
    owner_id = f"synthetic-live-concurrency-{uuid.uuid4().hex[:10]}"
    thread_id, session_id = helpers.seed_owner_scope(
        dsn,
        owner_subject_id=owner_id,
        vault_id=owner_id,
    )
    turns = [{"index": 1, "role": "user", "text": fact, "captureMode": "live"}]
    _, source_id, intent, _ = helpers.seed_live_source(
        dsn,
        owner_subject_id=owner_id,
        vault_id=owner_id,
        thread_id=thread_id,
        session_id=session_id,
        turns=turns,
        sequence=1,
    )
    return owner_id, source_id, intent, turns


def worker(settings: Settings, store: PostgresStore, *, worker_id: str, extractor):
    return OwnerTruthCandidateExtractionWorkerRuntime(
        settings=settings,
        store=store,
        worker_id=worker_id,
        lease_seconds=120,
        retry_seconds=0,
        heartbeat_interval_seconds=30,
        extractor=extractor,
    )


def main() -> int:
    base_dsn = os.environ.get("DATABASE_URL", Settings.from_env().database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_live_concurrency_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    stores: list[PostgresStore] = []
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="live-candidate-concurrency-postgres-smoke",
            lock_timeout_ms=1_000,
            statement_timeout_ms=30_000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        helpers = load_helpers()
        settings = replace(
            Settings.from_env(),
            database_url=test_dsn,
            deepseek_api_key="synthetic-live-concurrency-key",
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            owner_truth_candidate_extraction_worker_enabled=True,
            owner_truth_live_memory_organization_enabled=True,
        )

        owner_id, source_id, intent, _ = seed_job(
            helpers,
            test_dsn,
            fact="本次并发门禁测试代号是松风八十一号。",
        )
        store_a = open_store(test_dsn)
        store_b = open_store(test_dsn)
        stores.extend([store_a, store_b])
        long_run_provider_concurrency = verify_long_run_provider_concurrency(
            test_dsn,
            store_a=store_a,
            store_b=store_b,
        )
        manifest_transaction = verify_manifest_failure_is_transactional(
            test_dsn,
            store=store_a,
        )
        preorganization_terminal = verify_preorganization_failure_terminalizes(
            test_dsn,
            store=store_a,
        )
        default_runtime_handoff = verify_default_runtime_preorganization_handoff(
            test_dsn,
            store=store_a,
        )
        accept(store_a, intent)
        entered = Event()
        release = Event()
        extractor, provider_requests = barrier_extractor(
            settings,
            fact="本次并发门禁测试代号是松风八十一号。",
            entered=entered,
            release=release,
        )
        result_a: dict[str, object] = {}

        def run_a() -> None:
            result_a.update(worker(
                settings,
                store_a,
                worker_id="candidate-concurrency-a",
                extractor=extractor,
            ).run_once())

        thread = Thread(target=run_a, daemon=True)
        thread.start()
        require(entered.wait(timeout=5), "first worker did not reach provider barrier")
        require(active_transaction_count(test_dsn) == 0, "provider wait held a database transaction")
        result_b = worker(
            settings,
            store_b,
            worker_id="candidate-concurrency-b",
            extractor=extractor,
        ).run_once()
        require(result_b.get("status") == "idle", "second worker acquired the active lease")
        release.set()
        thread.join(timeout=8)
        require(not thread.is_alive(), "first worker did not finish")
        require(result_a.get("status") == "completed", f"first worker failed: {result_a}")
        concurrent_counts = counts(test_dsn, source_id=source_id, operation_id=intent.operation_id)
        require(concurrent_counts == {
            "extractions": 1,
            "candidates": 1,
            "inbox": 1,
            "receipts": 2,
            "receiptTypes": {
                "consumer.ownerTruth.source.extraction.completion": 1,
                "operationAccepted": 1,
            },
            "jobState": "succeeded",
            "jobAttempt": 1,
        }, f"concurrent commit was not singular: {concurrent_counts}")
        require(len(provider_requests) == 2, "concurrent worker duplicated provider requests")

        _, rollback_source_id, rollback_intent, rollback_turns = seed_job(
            helpers,
            test_dsn,
            fact="本次回滚门禁测试代号是松风八十二号。",
        )
        accept(store_a, rollback_intent)
        rollback_memories = [{
            "memoryKind": "knowledge",
            "claim": "本次回滚门禁测试代号是松风八十二号。",
            "sourceTurnIndices": [1],
            "facets": helpers.facets(),
        }]
        rollback_extractor, _ = helpers.controlled_extractor(
            settings,
            turns=rollback_turns,
            memories=rollback_memories,
            store=store_a,
        )
        original_consume = PostgresAsyncEffectConsumerRepository.consume

        def fail_after_consume(repository, command):
            original_consume(repository, command)
            raise Exception("synthetic transient candidate transaction failure")

        with patch.object(PostgresAsyncEffectConsumerRepository, "consume", fail_after_consume):
            rollback_result = worker(
                settings,
                store_a,
                worker_id="candidate-rollback-a",
                extractor=rollback_extractor,
            ).run_once()
        require(rollback_result.get("status") != "completed", "faulted transaction reported success")
        rolled_back = counts(
            test_dsn,
            source_id=rollback_source_id,
            operation_id=rollback_intent.operation_id,
        )
        require(
            rolled_back["extractions"] == 0
            and rolled_back["candidates"] == 0
            and rolled_back["inbox"] == 0
            and rolled_back["receipts"] == 1
            and rolled_back["receiptTypes"] == {"operationAccepted": 1}
            and rolled_back["jobState"] == "retryWait",
            f"faulted transaction left partial business rows: {rolled_back}",
        )
        with psycopg.connect(test_dsn) as connection:
            connection.execute(
                "UPDATE async_effects.jobs SET available_at = NOW() WHERE operation_id = %s",
                (rollback_intent.operation_id,),
            )
            connection.commit()
        retry_extractor, _ = helpers.controlled_extractor(
            settings,
            turns=rollback_turns,
            memories=rollback_memories,
            store=store_b,
        )
        retry_result = worker(
            settings,
            store_b,
            worker_id="candidate-rollback-b",
            extractor=retry_extractor,
        ).run_once()
        require(retry_result.get("status") == "completed", f"same job did not recover: {retry_result}")
        recovered = counts(
            test_dsn,
            source_id=rollback_source_id,
            operation_id=rollback_intent.operation_id,
        )
        require(
            recovered["extractions"] == 1
            and recovered["candidates"] == 1
            and recovered["inbox"] == 1
            and recovered["receipts"] == 2
            and recovered["receiptTypes"] == {
                "consumer.ownerTruth.source.extraction.completion": 1,
                "operationAccepted": 1,
            }
            and recovered["jobState"] == "succeeded"
            and recovered["jobAttempt"] == 2,
            f"same job recovery violated idempotency or budget: {recovered}",
        )

        epoch_owner, epoch_source_id, epoch_intent, _ = seed_job(
            helpers,
            test_dsn,
            fact="本次权限门禁测试代号是松风八十三号。",
        )
        accept(store_a, epoch_intent)
        epoch_entered = Event()
        epoch_release = Event()
        epoch_extractor, _ = barrier_extractor(
            settings,
            fact="本次权限门禁测试代号是松风八十三号。",
            entered=epoch_entered,
            release=epoch_release,
        )
        epoch_result: dict[str, object] = {}

        def run_epoch() -> None:
            epoch_result.update(worker(
                settings,
                store_a,
                worker_id="candidate-epoch-a",
                extractor=epoch_extractor,
            ).run_once())

        epoch_thread = Thread(target=run_epoch, daemon=True)
        epoch_thread.start()
        require(epoch_entered.wait(timeout=5), "epoch worker did not reach provider barrier")
        require(active_transaction_count(test_dsn) == 0, "epoch provider wait held a transaction")
        with psycopg.connect(test_dsn) as connection:
            connection.execute(
                "UPDATE owner_truth.vaults SET authority_epoch = authority_epoch + 1, updated_at = NOW() "
                "WHERE vault_id = %s",
                (epoch_owner,),
            )
            connection.commit()
        epoch_release.set()
        epoch_thread.join(timeout=8)
        require(not epoch_thread.is_alive(), "epoch worker did not finish")
        require(epoch_result.get("status") == "blocked", f"stale authority committed: {epoch_result}")
        epoch_counts = counts(
            test_dsn,
            source_id=epoch_source_id,
            operation_id=epoch_intent.operation_id,
        )
        require(
            epoch_counts["extractions"] == 0 and epoch_counts["candidates"] == 0,
            f"stale authority persisted a Candidate: {epoch_counts}",
        )

        print(json.dumps({
            "schemaVersion": "owner-truth-live-candidate-concurrency-postgres-smoke-v1",
            "status": "passed",
            "schemaHead": verified["expectedHead"],
            "longRunProviderConcurrency": long_run_provider_concurrency,
            "manifestFailureTransaction": manifest_transaction,
            "preorganizationFailureTerminal": preorganization_terminal,
            "defaultRuntimePreorganizationHandoff": default_runtime_handoff,
            "independentWorkerCompetition": concurrent_counts,
            "midCommitRollback": rolled_back,
            "sameJobRecovery": recovered,
            "authorityEpochChangedDuringProviderWait": epoch_counts,
            "providerWaitHeldTransaction": False,
        }, sort_keys=True))
        return 0
    finally:
        for store in stores:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as error:
            print(f"warning: temporary database cleanup failed: {type(error).__name__}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
