#!/usr/bin/env python3
"""Exercise P2-S4A local publication lifecycle execution in Postgres.

The smoke creates a disposable database derived from ``DATABASE_URL`` and
never writes to the configured application database. It proves the immediate
local safety boundary only: Owner withdrawal/objection deny future Visitor
reads, revoke active grants/sessions, persist a redacted receipt, and replay
idempotently. It also proves existing Owner Truth authority invalidation
triggers now revoke active access and leave an authority-trigger receipt.
"""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.async_effects.publication_external_cleanup_materializer_worker import (
    PublicationExternalCleanupMaterializerWorkerRuntime,
)
from app.core.config import Settings
from app.services.postgres_store import PostgresStore
from app.services.publication_lifecycle_execution import (
    PublicationLifecycleExecutionCommand,
    PublicationLifecycleExecutionService,
)
from app.services.publication_external_cleanup import PublicationExternalCleanupCoordinator
from app.services.publication_visitor_access import PublicationVisitorAccessUnavailable


_authority_smoke = runpy.run_path(
    str(ROOT_DIR / "scripts" / "backend-publication-authority-postgres-smoke.py")
)
_visitor_smoke = runpy.run_path(
    str(ROOT_DIR / "scripts" / "backend-publication-visitor-access-postgres-smoke.py")
)
create_database = _authority_smoke["create_database"]
drop_database = _authority_smoke["drop_database"]
dsn_for_database = _authority_smoke["dsn_for_database"]
seed_publishable_memory = _authority_smoke["seed_publishable_memory"]
create_draft = _authority_smoke["create_draft"]
confirm_draft = _authority_smoke["confirm_draft"]
issue = _visitor_smoke["issue"]
admit = _visitor_smoke["admit"]
owner_publications = _visitor_smoke["owner_publications"]
read_projection = _visitor_smoke["read_projection"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_unavailable(operation, message: str) -> None:
    try:
        operation()
    except PublicationVisitorAccessUnavailable:
        return
    raise AssertionError(message)


def lifecycle_execute(
    store: PostgresStore,
    *,
    seed,
    publication_id: str,
    action: str,
    command_id: str,
):
    command = PublicationLifecycleExecutionCommand(
        command_id=command_id,
        publication_id=publication_id,
        expected_authority_epoch=0,
        action=action,
    )
    with store.request_unit_of_work(
        correlation_id=f"publication-lifecycle-smoke:{action}:{publication_id}",
        command_id=command.command_id,
    ):
        return PublicationLifecycleExecutionService(
            store.publication_lifecycle_execution_repository(),
            enabled=True,
        ).execute(context=seed.context, command=command)


def materialize_external_cleanup(store: PostgresStore, *, result):
    """Mirror the post-commit API materializer without touching a provider."""

    with store.request_unit_of_work(
        correlation_id=f"publication-lifecycle-cleanup-smoke:{result.receipt_id}",
        command_id=f"publicationLifecycleCleanupSmoke:{result.receipt_id}",
    ):
        cleanup_repository = store.publication_external_cleanup_repository()
        statuses = PublicationExternalCleanupCoordinator(
            effect_repository=store.effect_kernel_repository(),
            provider_effect_repository=store.provider_effect_repository(),
            cleanup_repository=cleanup_repository,
        ).materialize(cleanup_repository.materialization_target(result.receipt_id))
    return statuses


def materialize_pending_external_cleanup(store: PostgresStore, *, limit: int = 20):
    """Prove authority-triggered receipts are recoverable by the real worker shell."""

    return PublicationExternalCleanupMaterializerWorkerRuntime(
        settings=Settings(
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            publication_external_cleanup_materializer_enabled=True,
        ),
        store=store,
        worker_id="publication-lifecycle-cleanup-smoke-worker",
    ).run_once(limit=limit)


def issue_and_admit(
    store: PostgresStore,
    *,
    seed,
    publication,
    visitor_subject_id: str,
    suffix: str,
):
    issued = issue(
        store,
        seed=seed,
        publication_id=publication.publication_id,
        publication_version_id=publication.publication_version_id,
        visitor_subject_id=visitor_subject_id,
        use_limit=1,
    )
    require(bool(issued.grant_credential), "lifecycle smoke requires a one-time grant credential")
    assert issued.grant_credential is not None
    admitted = admit(
        store,
        visitor_subject_id=visitor_subject_id,
        grant_id=issued.grant_id,
        grant_credential=issued.grant_credential,
        suffix=suffix,
    )
    return issued, admitted, f"visitor-session-{suffix}-" + "s" * 32


def execute(dsn: str) -> None:
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=6)
    store.open_pool(wait=True)
    try:
        seed = seed_publishable_memory(dsn, label="lifecycle-withdraw")
        withdrawal = confirm_draft(store, seed, create_draft(store, seed), command_id=str(uuid.uuid4()))
        owner_summaries = owner_publications(store, seed=seed)
        require(
            len(owner_summaries) == 1
            and owner_summaries[0].publication_id == withdrawal.publication_id
            and owner_summaries[0].lifecycle_authority_epoch == 0,
            "owner management read must expose the publication lifecycle authority epoch",
        )
        visitor = "publication-lifecycle-visitor"
        issued, admitted, session_credential = issue_and_admit(
            store,
            seed=seed,
            publication=withdrawal,
            visitor_subject_id=visitor,
            suffix="withdrawal",
        )
        command_id = str(uuid.uuid4())
        result = lifecycle_execute(
            store,
            seed=seed,
            publication_id=withdrawal.publication_id,
            action="withdraw",
            command_id=command_id,
        )
        require(
            result.outcome == "withdrawn"
            and result.publication_state == "withdrawn"
            and result.projection_state == "withdrawn"
            and not result.conflict_hold,
            "withdrawal must mark the local publication and projection unavailable",
        )
        require(
            result.revoked_grant_count == 1 and result.revoked_visitor_session_count == 1,
            "withdrawal must revoke its active ShareGrant and Visitor session",
        )
        cleanup_statuses = materialize_external_cleanup(store, result=result)
        require(
            len(cleanup_statuses) == 5
            and {item.domain.value for item in cleanup_statuses}
            == {"publicIndex", "cache", "digitalHumanSession", "providerVoice", "objectStorage"}
            and all(item.state.value == "pending" for item in cleanup_statuses)
            and all(not item.provider_receipt_present for item in cleanup_statuses),
            "external cleanup must enqueue five redacted pending effects without claiming provider completion",
        )
        replay = lifecycle_execute(
            store,
            seed=seed,
            publication_id=withdrawal.publication_id,
            action="withdraw",
            command_id=command_id,
        )
        require(
            replay.outcome == "deduplicated" and replay.receipt_id == result.receipt_id,
            "lifecycle command replay must return its original redacted receipt",
        )
        replay_cleanup = materialize_external_cleanup(store, result=replay)
        require(
            replay_cleanup == cleanup_statuses,
            "external cleanup materialization must be idempotent across lifecycle replay",
        )
        expect_unavailable(
            lambda: read_projection(
                store,
                visitor_subject_id=visitor,
                session_id=admitted.session_id,
                session_credential=session_credential,
            ),
            "withdrawn publication must deny an existing Visitor session",
        )

        suspension = confirm_draft(store, seed, create_draft(store, seed), command_id=str(uuid.uuid4()))
        suspended_result = lifecycle_execute(
            store,
            seed=seed,
            publication_id=suspension.publication_id,
            action="suspend",
            command_id=str(uuid.uuid4()),
        )
        require(
            suspended_result.outcome == "suspended"
            and suspended_result.conflict_hold
            and suspended_result.reason_code == "thirdPartyObjection",
            "third-party objection must create an irreversible local conflict hold",
        )
        suspended_cleanup = materialize_external_cleanup(store, result=suspended_result)
        require(
            len(suspended_cleanup) == 5
            and all(item.state.value == "pending" for item in suspended_cleanup),
            "third-party objection must queue cleanup without claiming completion",
        )

        trigger_seed = seed_publishable_memory(dsn, label="lifecycle-trigger")
        trigger_publication = confirm_draft(
            store,
            trigger_seed,
            create_draft(store, trigger_seed),
            command_id=str(uuid.uuid4()),
        )
        trigger_visitor = "publication-lifecycle-trigger-visitor"
        trigger_issued, trigger_admitted, trigger_credential = issue_and_admit(
            store,
            seed=trigger_seed,
            publication=trigger_publication,
            visitor_subject_id=trigger_visitor,
            suffix="trigger",
        )
        del trigger_issued
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE owner_truth.sources SET state = 'redacted' WHERE id = %s",
                    (trigger_seed.source_id,),
                )
            connection.commit()
        expect_unavailable(
            lambda: read_projection(
                store,
                visitor_subject_id=trigger_visitor,
                session_id=trigger_admitted.session_id,
                session_credential=trigger_credential,
            ),
            "authority-triggered block must deny an existing Visitor session",
        )
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, action, publication_state, projection_state, conflict_hold,
                        revoked_grant_count, revoked_visitor_session_count,
                        access_deny_state, public_index_cleanup_state, runtime_cleanup_state
                    FROM publication.publication_lifecycle_receipts
                    WHERE publication_version_id = %s
                      AND reason_code = 'sourceAuthorityChanged'
                      AND origin = 'authorityTrigger'
                    """,
                    (trigger_publication.publication_version_id,),
                )
                receipt = cursor.fetchone()
                require(receipt is not None, "authority trigger must append a lifecycle receipt")
                require(
                    receipt
                    == (
                        receipt[0],
                        "systemSuspend",
                        "suspended",
                        "blocked",
                        False,
                        1,
                        1,
                        "completed",
                        "pending",
                        "notApplicable",
                    ),
                    "authority trigger receipt must report local denial without external cleanup claims",
                )
                cursor.execute(
                    """
                    SELECT state FROM publication.share_grants
                    WHERE publication_id = %s
                    """,
                    (trigger_publication.publication_id,),
                )
                require(cursor.fetchone() == ("revoked",), "authority trigger must revoke the ShareGrant")
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM publication.lifecycle_external_cleanup_effects
                    WHERE lifecycle_receipt_id = %s
                    """,
                    (result.receipt_id,),
                )
                require(
                    cursor.fetchone() == (5,),
                    "withdrawal must persist five cleanup effect links",
                )
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM async_effects.operations
                    WHERE operation_type = 'publication.lifecycle.externalCleanup'
                    """
                )
                require(
                    cursor.fetchone() == (10,),
                    "withdrawal and objection must create one generic operation per cleanup domain",
                )
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM async_effects.provider_effects
                    WHERE state = 'unknown'
                    """
                )
                require(
                    cursor.fetchone() == (10,),
                    "initial external cleanup provider evidence must remain unknown",
                )

        triggered_materialization = materialize_pending_external_cleanup(store)
        require(
            triggered_materialization["status"] == "materialized"
            and triggered_materialization["materializedReceiptCount"] == 1
            and triggered_materialization["materializedEffectCount"] == 5
            and triggered_materialization["domainStates"]
            == {
                "cache:pending": 1,
                "digitalHumanSession:pending": 1,
                "objectStorage:pending": 1,
                "providerVoice:pending": 1,
                "publicIndex:pending": 1,
            },
            "authority-triggered lifecycle receipt must remain materializable without reopening access",
        )
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM publication.lifecycle_external_cleanup_effects
                    WHERE lifecycle_receipt_id = %s
                    """,
                    (receipt[0],),
                )
                require(
                    cursor.fetchone() == (5,),
                    "worker materialization must persist five effect links for authority-triggered denial",
                )
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM async_effects.operations
                    WHERE operation_type = 'publication.lifecycle.externalCleanup'
                    """
                )
                require(
                    cursor.fetchone() == (15,),
                    "worker materialization must add five generic operations for authority-triggered denial",
                )
        print(
            "Publication lifecycle execution Postgres smoke passed "
            "(withdrawal, objection hold, idempotency, local access denial, "
            "trigger propagation and external cleanup receipts verified)."
        )
    finally:
        store.close_pool()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", "").strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_publication_lifecycle_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="publication-lifecycle-execution-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0083" in applied["appliedVersions"], "external cleanup migration must apply")
        execute(test_dsn)
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":  # pragma: no cover
    main()
