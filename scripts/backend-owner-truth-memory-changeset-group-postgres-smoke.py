#!/usr/bin/env python3
"""Exercise atomic related-Candidate review in a disposable PostgreSQL DB.

The smoke creates a uniquely named database, applies every migration, and
uses the production PostgresStore/UoW/repositories.  It proves that failures
after the second decision receipt, formal-memory activation, or projection
Outbox write leave no partial group state.  It also races two independent
connections against one preview and verifies command replay is idempotent.

DATABASE_URL must point at an isolated PostgreSQL server whose role may create
and drop databases.  The configured application database is never mutated.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from threading import Barrier, Lock
from typing import Any
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewConflict,
)
from app.domain.owner_truth.contracts import MemoryKind
from app.domain.owner_truth.memory_changeset_group import (
    OwnerTruthMemoryChangeSetGroupCommand,
    OwnerTruthMemoryChangeSetGroupDependency,
    OwnerTruthMemoryChangeSetGroupSelection,
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    enrich_memory_payload_v5,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_memory_changeset_group_review import (
    OwnerTruthMemoryChangeSetGroupReviewService,
)
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def dsn_for_database(base_dsn: str, database_name: str) -> str:
    parameters = conninfo_to_dict(base_dsn)
    parameters["dbname"] = database_name
    return make_conninfo(**parameters)


def create_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))


def drop_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def _education_content(*, school: str, year: int, degree: str) -> dict[str, Any]:
    statement = f"我于{year}年从{school}{degree}毕业"
    return enrich_memory_payload_v5(
        kind=MemoryKind.KNOWLEDGE,
        payload={
            "statement": statement,
            "knowledgeType": "personal_profile",
            "domains": ["教育"],
            "factType": "knowledge",
            "predicate": "graduated_from",
            "object": {"label": school, "category": "school"},
            "qualifiers": {
                "polarity": "positive",
                "degree": degree,
                "validTime": {
                    "start": str(year),
                    "end": str(year),
                    "precision": "year",
                    "expression": f"{year}年",
                },
            },
        },
        provenance={"mode": "selfReport"},
        memory_subject_id="person-owner",
        claim_subject_id="person-owner",
    )


def seed_group(
    dsn: str,
    *,
    label: str,
) -> tuple[OwnerTruthCommandContext, tuple[str, str]]:
    owner_subject_id = f"terra-a-owner-{label}-{uuid.uuid4().hex[:8]}"
    vault_id = f"terra-a-vault-{label}-{uuid.uuid4().hex[:8]}"
    context = OwnerTruthCommandContext(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        actor_subject_id=owner_subject_id,
    )
    candidates = (
        _education_content(school="江南理工大学", year=2016, degree="本科"),
        _education_content(school="海州科技大学", year=2019, degree="硕士"),
    )
    candidate_ids: list[str] = []
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (vault_id, owner_subject_id),
            )
            for content in candidates:
                source_id = str(uuid.uuid4())
                candidate_id = str(uuid.uuid4())
                candidate_ids.append(candidate_id)
                statement = str(content["statement"])
                payload = {
                    "schemaVersion": "owner-truth-candidate-proposal-v1",
                    "candidateKind": "knowledge",
                    "perspectiveType": "firstPerson",
                    "epistemicStatus": "recalled",
                    "sensitivity": "standard",
                    "content": content,
                    "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
                    "evidenceRefs": [
                        {
                            "sourceId": source_id,
                            "sourceVersion": 1,
                            "span": {"start": 0, "end": len(statement)},
                        }
                    ],
                    "reviewMode": "batch",
                }
                cursor.execute(
                    """
                    INSERT INTO owner_truth.sources (
                        id, vault_id, owner_subject_id, source_kind, content_hash,
                        policy_version, authority_epoch
                    ) VALUES (%s, %s, %s, 'text', %s, %s, 0)
                    """,
                    (
                        source_id,
                        vault_id,
                        owner_subject_id,
                        canonical_hash({"statement": statement}),
                        context.policy_version,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_candidates (
                        id, vault_id, owner_subject_id, source_id, candidate_kind,
                        perspective_type, epistemic_status, sensitivity,
                        policy_version, authority_epoch, content_hash,
                        payload_schema_version, payload
                    ) VALUES (
                        %s, %s, %s, %s, 'knowledge', 'firstPerson', 'recalled',
                        'standard', %s, 0, %s, %s, %s
                    )
                    """,
                    (
                        candidate_id,
                        vault_id,
                        owner_subject_id,
                        source_id,
                        context.policy_version,
                        canonical_hash(content),
                        OWNER_TRUTH_SCHEMA_VERSION_V5,
                        Jsonb(payload),
                    ),
                )
        connection.commit()
    return context, (candidate_ids[0], candidate_ids[1])


def group_commands(
    *,
    command_prefix: str,
    candidate_ids: tuple[str, str],
) -> tuple[
    OwnerTruthMemoryChangeSetGroupCommand,
    tuple[OwnerTruthMemoryChangeSetGroupSelection, ...],
    tuple[OwnerTruthMemoryChangeSetGroupDependency, ...],
]:
    selections = tuple(
        OwnerTruthMemoryChangeSetGroupSelection(
            candidate_id=candidate_id,
            expected_candidate_version=1,
            action=CandidateReviewAction.ACCEPT,
            corrected_value=None,
            corrected_value_schema_version=None,
            reason_code="ownerReviewedRelatedGroup",
        )
        for candidate_id in candidate_ids
    )
    dependencies = (
        OwnerTruthMemoryChangeSetGroupDependency(
            before_candidate_id=candidate_ids[0],
            after_candidate_id=candidate_ids[1],
        ),
    )
    return (
        OwnerTruthMemoryChangeSetGroupCommand(
            command_id=f"{command_prefix}-preview",
            selections=selections,
            dependencies=dependencies,
        ),
        selections,
        dependencies,
    )


def prepare_confirmation(
    store: Any,
    *,
    context: OwnerTruthCommandContext,
    candidate_ids: tuple[str, str],
    command_prefix: str,
    confirmation_command_id: str | None = None,
) -> OwnerTruthMemoryChangeSetGroupCommand:
    preview_command, selections, dependencies = group_commands(
        command_prefix=command_prefix,
        candidate_ids=candidate_ids,
    )
    proposal = OwnerTruthMemoryChangeSetGroupReviewService(store).preview(
        command=preview_command,
        context=context,
    )
    return OwnerTruthMemoryChangeSetGroupCommand(
        command_id=confirmation_command_id or f"{command_prefix}-confirm",
        selections=selections,
        dependencies=dependencies,
        expected_memory_revision=proposal.base_memory_revision,
        expected_group_proposal_id=proposal.proposal_id,
        expected_group_proposal_hash=proposal.proposal_hash,
    )


class _RepositoryFaultProxy:
    def __init__(self, delegate: Any, owner: "_FaultInjectingStore") -> None:
        self._delegate = delegate
        self._owner = owner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def decide(self, **kwargs: Any) -> Any:
        result = self._delegate.decide(**kwargs)
        if self._owner.bump("decision") == 2 and self._owner.failure_point == "decision":
            raise RuntimeError("injected second decision receipt failure")
        return result

    def activate_memory_version(self, **kwargs: Any) -> Any:
        result = self._delegate.activate_memory_version(**kwargs)
        if self._owner.bump("activation") == 2 and self._owner.failure_point == "activation":
            raise RuntimeError("injected second formal-memory activation failure")
        return result


class _EffectFaultProxy:
    def __init__(self, delegate: Any, owner: "_FaultInjectingStore") -> None:
        self._delegate = delegate
        self._owner = owner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def accept(self, intent: Any) -> Any:
        result = self._delegate.accept(intent)
        if self._owner.bump("effect") == 2 and self._owner.failure_point == "effect":
            raise RuntimeError("injected second projection Outbox failure")
        return result


class _FaultInjectingStore:
    def __init__(self, delegate: PostgresStore, *, failure_point: str) -> None:
        self._delegate = delegate
        self.failure_point = failure_point
        self._counts: dict[str, int] = {}
        self._lock = Lock()

    def bump(self, name: str) -> int:
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + 1
            return self._counts[name]

    def request_unit_of_work(self, **kwargs: Any) -> Any:
        return self._delegate.request_unit_of_work(**kwargs)

    def owner_truth_candidate_review_repository(self) -> _RepositoryFaultProxy:
        return _RepositoryFaultProxy(
            self._delegate.owner_truth_candidate_review_repository(),
            self,
        )

    def effect_kernel_repository(self) -> _EffectFaultProxy:
        return _EffectFaultProxy(self._delegate.effect_kernel_repository(), self)


def vault_counts(dsn: str, *, context: OwnerTruthCommandContext) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            result: dict[str, Any] = {}
            cursor.execute(
                """
                SELECT decision_status, COUNT(*)
                FROM owner_truth.memory_candidates
                WHERE vault_id = %s
                GROUP BY decision_status
                """,
                (context.vault_id,),
            )
            result["candidateStates"] = {
                str(state): int(count) for state, count in cursor.fetchall()
            }
            for key, table in (
                ("decisionReceipts", "owner_truth.decision_receipts"),
                ("memories", "owner_truth.memories"),
                ("memoryVersions", "owner_truth.memory_versions"),
                ("memoryChangeSets", "owner_truth.memory_changesets"),
                ("groupReceipts", "owner_truth.memory_changeset_group_receipts"),
                ("effectOperations", "async_effects.operations"),
                ("outboxEvents", "async_effects.outbox_events"),
            ):
                cursor.execute(
                    sql.SQL("SELECT COUNT(*) FROM {} WHERE vault_id = %s").format(
                        sql.SQL(table)
                    ),
                    (context.vault_id,),
                )
                result[key] = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT revision FROM owner_truth.memory_revisions WHERE vault_id = %s",
                (context.vault_id,),
            )
            row = cursor.fetchone()
            result["memoryRevision"] = int(row[0]) if row else 0
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM owner_truth.memory_versions
                WHERE vault_id = %s AND is_current
                """,
                (context.vault_id,),
            )
            result["currentMemoryVersions"] = int(cursor.fetchone()[0])
    return result


def assert_rolled_back(counts: dict[str, Any], *, label: str) -> None:
    require(counts["candidateStates"] == {"pending": 2}, f"{label}: candidates changed")
    require(counts["memoryRevision"] == 0, f"{label}: memory revision advanced")
    require(
        counts["currentMemoryVersions"] == 0,
        f"{label}: current memory version remained",
    )
    for key in (
        "decisionReceipts",
        "memories",
        "memoryVersions",
        "memoryChangeSets",
        "groupReceipts",
        "effectOperations",
        "outboxEvents",
    ):
        require(counts[key] == 0, f"{label}: partial state remained in {key}")


def run_fault_case(dsn: str, *, failure_point: str) -> dict[str, Any]:
    context, candidate_ids = seed_group(dsn, label=f"fault-{failure_point}")
    base_store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    base_store.open_pool(wait=True)
    try:
        store = _FaultInjectingStore(base_store, failure_point=failure_point)
        command = prepare_confirmation(
            store,
            context=context,
            candidate_ids=candidate_ids,
            command_prefix=f"terra-a-{failure_point}",
        )
        try:
            OwnerTruthMemoryChangeSetGroupReviewService(store).confirm(
                command=command,
                context=context,
            )
        except RuntimeError as error:
            require("injected" in str(error), f"{failure_point}: unexpected failure")
        else:
            raise AssertionError(f"{failure_point}: injected failure did not run")
        counts = vault_counts(dsn, context=context)
        assert_rolled_back(counts, label=failure_point)
        return counts
    finally:
        base_store.close_pool()


def run_replay_case(dsn: str) -> dict[str, Any]:
    context, candidate_ids = seed_group(dsn, label="replay")
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    store.open_pool(wait=True)
    try:
        command = prepare_confirmation(
            store,
            context=context,
            candidate_ids=candidate_ids,
            command_prefix="terra-a-replay",
        )
        service = OwnerTruthMemoryChangeSetGroupReviewService(store)
        created = service.confirm(command=command, context=context)
        first_counts = vault_counts(dsn, context=context)
        replayed = service.confirm(command=command, context=context)
        second_counts = vault_counts(dsn, context=context)
        require(created.outcome == "created", "replay baseline must create the group")
        require(replayed.outcome == "deduplicated", "replay must return persisted receipt")
        require(first_counts == second_counts, "replay changed persisted state")
        require(second_counts["candidateStates"] == {"accepted": 2}, "group not accepted")
        require(second_counts["decisionReceipts"] == 2, "decision receipt count mismatch")
        require(second_counts["memoryVersions"] == 2, "memory version count mismatch")
        require(second_counts["memoryChangeSets"] == 2, "ChangeSet count mismatch")
        require(second_counts["groupReceipts"] == 1, "group receipt must be unique")
        require(second_counts["effectOperations"] == 2, "effect count mismatch")
        require(second_counts["outboxEvents"] == 2, "Outbox count mismatch")
        require(second_counts["memoryRevision"] == 2, "memory revision must advance twice")
        require(second_counts["currentMemoryVersions"] >= 1, "current version missing")
        return second_counts
    finally:
        store.close_pool()


def run_concurrent_case(dsn: str) -> dict[str, Any]:
    context, candidate_ids = seed_group(dsn, label="concurrent")
    preview_store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    preview_store.open_pool(wait=True)
    try:
        base_command = prepare_confirmation(
            preview_store,
            context=context,
            candidate_ids=candidate_ids,
            command_prefix="terra-a-concurrent",
            confirmation_command_id="terra-a-concurrent-confirm-a",
        )
    finally:
        preview_store.close_pool()

    commands = (
        base_command,
        OwnerTruthMemoryChangeSetGroupCommand(
            command_id="terra-a-concurrent-confirm-b",
            selections=base_command.selections,
            dependencies=base_command.dependencies,
            expected_memory_revision=base_command.expected_memory_revision,
            expected_group_proposal_id=base_command.expected_group_proposal_id,
            expected_group_proposal_hash=base_command.expected_group_proposal_hash,
        ),
    )
    stores = (
        PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=1),
        PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=1),
    )
    for store in stores:
        store.open_pool(wait=True)
    barrier = Barrier(2)

    def submit(index: int) -> str:
        barrier.wait(timeout=10)
        try:
            result = OwnerTruthMemoryChangeSetGroupReviewService(stores[index]).confirm(
                command=commands[index],
                context=context,
            )
            return result.outcome
        except OwnerTruthCandidateReviewConflict:
            return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(submit, (0, 1)))
    finally:
        for store in stores:
            store.close_pool()
    require(outcomes.count("created") == 1, "exactly one concurrent group must commit")
    require(outcomes.count("conflict") == 1, "losing concurrent group must conflict")
    counts = vault_counts(dsn, context=context)
    require(counts["candidateStates"] == {"accepted": 2}, "concurrent winner incomplete")
    require(counts["decisionReceipts"] == 2, "concurrent receipts duplicated")
    require(counts["memoryVersions"] == 2, "concurrent versions duplicated")
    require(counts["groupReceipts"] == 1, "concurrent group receipt duplicated")
    require(counts["effectOperations"] == 2, "concurrent effects duplicated")
    require(counts["memoryRevision"] == 2, "concurrent revision is inconsistent")
    return {"outcomes": sorted(outcomes), **counts}


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_terra_a_group_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    result: dict[str, Any] = {
        "schemaVersion": "dreamjourney-terra-a-group-postgres-smoke-v1",
        "databaseIsolation": "disposable",
    }
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="terra-a-group-postgres-smoke-v1",
            lock_timeout_ms=2_000,
            statement_timeout_ms=30_000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                vector_row = cursor.fetchone()
                require(vector_row is not None, "pgvector extension must be installed")
                result["pgvectorVersion"] = str(vector_row[0])
        result["migrationHead"] = verified.get("schemaHead") or verified.get("head")
        result["appliedMigrationCount"] = len(applied.get("appliedVersions", ()))
        result["faults"] = {
            point: run_fault_case(test_dsn, failure_point=point)
            for point in ("decision", "activation", "effect")
        }
        result["replay"] = run_replay_case(test_dsn)
        result["concurrency"] = run_concurrent_case(test_dsn)
        result["status"] = "PASS"
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
