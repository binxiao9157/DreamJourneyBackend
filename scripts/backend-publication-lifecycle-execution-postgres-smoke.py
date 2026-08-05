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
from app.services.postgres_store import PostgresStore
from app.services.publication_lifecycle_execution import (
    PublicationLifecycleExecutionCommand,
    PublicationLifecycleExecutionService,
)
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
                    SELECT publication_state, projection_state, conflict_hold,
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
                    == ("suspended", "blocked", False, 1, 1, "completed", "pending", "notApplicable"),
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
        print(
            "Publication lifecycle execution Postgres smoke passed "
            "(withdrawal, objection hold, idempotency, local access denial and trigger propagation verified)."
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
        require("0082" in applied["appliedVersions"], "lifecycle execution migration must apply")
        execute(test_dsn)
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":  # pragma: no cover
    main()
