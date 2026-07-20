#!/usr/bin/env python3
"""Exercise inert restore-fenced replay-request persistence in disposable Postgres.

The smoke creates one synthetic terminal job and records only opaque IDs and
hashes.  It proves authorization evidence persistence without re-queueing the
job, starting a worker, or invoking a Provider.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectJobState, AsyncEffectTarget
from app.async_effects.dead_letter_effects import (
    DeadLetterCause,
    DeadLetterReplayCommand,
    admit_dead_letter,
)
from app.async_effects.dead_letter_repository import DeadLetterPersistenceSummary
from app.async_effects.dead_letter_replay_repository import (
    DeadLetterReplayRequestConflict,
    DeadLetterReplayRequestError,
    DeadLetterReplayRequestPersistenceSummary,
)
from app.async_effects.recovery_evidence import DeadLetterRestoreReplayContext
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


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
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))


def build_intent(*, resource_id: str = "dead-letter-replay-request-smoke") -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.deadLetterReplayRequest",
        target=AsyncEffectTarget(
            owner_subject_id="owner-dead-letter-replay-smoke",
            vault_id="vault-dead-letter-replay-smoke",
            resource_type="timeLetter",
            resource_id=resource_id,
            resource_version=1,
            purpose="timeLetterDelivery",
            authority_epoch=0,
        ),
        payload_hash=digest(f"dead-letter-replay-request:{resource_id}"),
    )


def terminal_failed_admission(
    store: PostgresStore,
    dsn: str,
    intent: AsyncEffectIntent,
):
    """Seed a value-free historical failure without enabling a real worker."""

    with store.request_unit_of_work(
        correlation_id=f"dead-letter-replay-request-seed:{intent.job_id}",
        command_id="deadLetterReplayRequestSeed",
    ):
        store.effect_kernel_repository().accept(intent)
    with store.request_unit_of_work(
        correlation_id=f"dead-letter-replay-request-claim:{intent.job_id}",
        command_id="deadLetterReplayRequestClaim",
    ):
        lease = store.async_effect_lease_repository().claim_next(
            worker_id="dead-letter-replay-request-smoke-worker",
            lease_seconds=30,
            supported_job_types=[intent.job_type],
        )
        require(lease is not None, "synthetic job must be claimable")

    # The current worker contract has no executable failed completion path.
    # This fixture simulates a pre-existing terminal failure so the replay
    # request repository can prove it never changes that historical record.
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE async_effects.jobs
                SET state = 'failed', terminal_at = NOW(), lease_owner = NULL,
                    lease_until = NULL, heartbeat_at = NULL, updated_at = NOW()
                WHERE job_id = %s AND state = 'leased' AND attempt = 1
                RETURNING job_id
                """,
                (intent.job_id,),
            )
            require(cursor.fetchone() is not None, "fixture job must become terminal failed")
            cursor.execute(
                """
                UPDATE async_effects.job_attempts
                SET state = 'terminalFailed', error_code = 'syntheticMaxAttempts',
                    finished_at = NOW(), updated_at = NOW()
                WHERE job_id = %s AND attempt = 1 AND state = 'started'
                RETURNING attempt_id
                """,
                (intent.job_id,),
            )
            require(cursor.fetchone() is not None, "fixture attempt must become terminal failed")
            cursor.execute(
                """
                UPDATE async_effects.operations
                SET state = 'failed', attempt = 1, terminal_at = NOW(), updated_at = NOW()
                WHERE operation_id = %s AND state = 'accepted'
                RETURNING operation_id
                """,
                (intent.operation_id,),
            )
            require(cursor.fetchone() is not None, "fixture operation must become terminal failed")
            cursor.execute(
                """
                UPDATE async_effects.outbox_events
                SET state = 'dispatched', attempt = 1, dispatched_at = NOW(), updated_at = NOW()
                WHERE operation_id = %s AND state IN ('pending', 'claimed')
                RETURNING event_id
                """,
                (intent.operation_id,),
            )
            require(cursor.fetchone() is not None, "fixture outbox must become terminal dispatched")

    return admit_dead_letter(
        intent=intent,
        job_state=AsyncEffectJobState.FAILED,
        attempt=1,
        max_attempts=1,
        cause=DeadLetterCause.MAX_ATTEMPTS_EXCEEDED,
        failure_hash=digest(f"dead-letter-replay-request-failure:{intent.job_id}"),
        last_receipt_hash=digest(f"dead-letter-replay-request-receipt:{intent.job_id}"),
    )


def persist_dead_letter(
    store: PostgresStore,
    admission,
    *,
    command_id: str,
) -> DeadLetterPersistenceSummary:
    with store.request_unit_of_work(
        correlation_id=f"dead-letter-replay-request-admission:{command_id}",
        command_id=command_id,
    ):
        return store.async_effect_dead_letter_repository().record(admission)


def command(admission, *, authorization: str = "operator-replay-authorization") -> DeadLetterReplayCommand:
    target = admission.intent.target
    return DeadLetterReplayCommand(
        dead_letter_id=admission.dead_letter_id,
        actor_subject_id=target.owner_subject_id,
        owner_subject_id=target.owner_subject_id,
        vault_id=target.vault_id,
        authority_epoch=target.authority_epoch,
        authorization_receipt_hash=digest(authorization),
        reason_code="operatorApproved",
    )


def restore_context(*, authorization: str = "recovery-replay-authorization") -> DeadLetterRestoreReplayContext:
    return DeadLetterRestoreReplayContext(
        restore_id="restore-dead-letter-replay-smoke",
        owner_subject_id="owner-dead-letter-replay-smoke",
        vault_id="vault-dead-letter-replay-smoke",
        authority_epoch=0,
        restore_checkpoint_hash=digest("isolated-dead-letter-replay-restore"),
        recovery_authorization_receipt_hash=digest(authorization),
    )


def persist_request(
    store: PostgresStore,
    admission,
    replay_command: DeadLetterReplayCommand,
    context: DeadLetterRestoreReplayContext,
    *,
    command_id: str,
) -> DeadLetterReplayRequestPersistenceSummary:
    with store.request_unit_of_work(
        correlation_id=f"dead-letter-replay-request-record:{command_id}",
        command_id=command_id,
    ):
        return store.async_effect_dead_letter_replay_request_repository().record(
            admission,
            replay_command,
            context,
        )


def request_count(dsn: str, dead_letter_id: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM async_effects.dead_letter_replay_requests WHERE dead_letter_id = %s",
                (dead_letter_id,),
            )
            return int(cursor.fetchone()[0])


def terminal_evidence(dsn: str, job_id: str) -> tuple[str, str, str, int, int]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT state, attempt FROM async_effects.jobs WHERE job_id = %s",
                (job_id,),
            )
            job = cursor.fetchone()
            cursor.execute(
                "SELECT state FROM async_effects.operations WHERE operation_id = "
                "(SELECT operation_id FROM async_effects.jobs WHERE job_id = %s)",
                (job_id,),
            )
            operation = cursor.fetchone()
            cursor.execute(
                "SELECT state FROM async_effects.outbox_events WHERE operation_id = "
                "(SELECT operation_id FROM async_effects.jobs WHERE job_id = %s)",
                (job_id,),
            )
            outbox = cursor.fetchone()
            cursor.execute(
                "SELECT COUNT(*) FROM async_effects.job_attempts WHERE job_id = %s",
                (job_id,),
            )
            attempt_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM async_effects.dead_letters WHERE job_id = %s",
                (job_id,),
            )
            dead_letter_count = int(cursor.fetchone()[0])
    require(job is not None and operation is not None and outbox is not None, "terminal fixture must persist")
    return str(job[0]), str(operation[0]), str(outbox[0]), int(job[1]), attempt_count + dead_letter_count


def expect_rejected(dsn: str, operation, message: str) -> None:
    rejected = False
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                operation(cursor)
    except Exception:
        rejected = True
    require(rejected, message)


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_dead_letter_replay_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    store: PostgresStore | None = None
    database_created = False
    try:
        create_database(admin_dsn, database_name)
        database_created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="dead-letter-replay-request-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedVersions"][-1] == "0027", "replay-request migration must apply")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
        store.open_pool(wait=True)
        admission = terminal_failed_admission(store, test_dsn, build_intent())
        admitted = persist_dead_letter(
            store,
            admission,
            command_id="deadLetterReplayRequestAdmission",
        )
        require(admitted.outcome == "admitted", "terminal admission must persist first")

        approved_command = command(admission)
        approved_context = restore_context()

        def persist_once(index: int) -> str:
            return persist_request(
                store,
                admission,
                approved_command,
                approved_context,
                command_id=f"deadLetterReplayRequest{index}",
            ).outcome

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = set(executor.map(persist_once, (1, 2)))
        require(outcomes == {"authorized", "deduplicated"}, "same replay authority must persist once")
        summary = persist_request(
            store,
            admission,
            approved_command,
            approved_context,
            command_id="deadLetterReplayRequestLoad",
        )
        require(request_count(test_dsn, admission.dead_letter_id) == 1, "one replay request must persist")
        with store.request_unit_of_work(
            correlation_id="dead-letter-replay-request-load",
            command_id="deadLetterReplayRequestLoad",
        ):
            loaded = store.async_effect_dead_letter_replay_request_repository().load(
                summary.request.replay_id
            )
        require(loaded == summary.request, "durable request must reconstruct immutable evidence")

        changed_authorization = False
        try:
            persist_request(
                store,
                admission,
                command(admission, authorization="later-operator-replay-authorization"),
                approved_context,
                command_id="deadLetterReplayRequestChangedAuthorization",
            )
        except DeadLetterReplayRequestConflict:
            changed_authorization = True
        require(changed_authorization, "different replay authority must not reuse the dead letter")

        stale_recovery_authority = False
        try:
            persist_request(
                store,
                admission,
                approved_command,
                restore_context(authorization="operator-replay-authorization"),
                command_id="deadLetterReplayRequestStaleRecoveryAuthority",
            )
        except DeadLetterReplayRequestError:
            stale_recovery_authority = True
        require(stale_recovery_authority, "recovery authority must be distinct from replay authorization")
        require(request_count(test_dsn, admission.dead_letter_id) == 1, "rejected authority cannot append")

        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "UPDATE async_effects.dead_letter_replay_requests SET state = 'authorized' "
                "WHERE replay_id = %s",
                (summary.request.replay_id,),
            ),
            "replay request must be append-only",
        )
        expect_rejected(
            test_dsn,
            lambda cursor: cursor.execute(
                "DELETE FROM async_effects.dead_letter_replay_requests WHERE replay_id = %s",
                (summary.request.replay_id,),
            ),
            "replay request must be non-deletable",
        )

        job_state, operation_state, outbox_state, attempt, evidence_count = terminal_evidence(
            test_dsn,
            admission.intent.job_id,
        )
        require(
            (job_state, operation_state, outbox_state, attempt) == ("failed", "failed", "dispatched", 1),
            "replay authority must not change the terminal job, operation, or outbox",
        )
        require(evidence_count == 2, "replay authority must not create a worker attempt or second dead letter")

        print(
            "Async-effect dead-letter replay-request Postgres smoke passed "
            "(request is inert; worker replay and Provider calls remain disabled)."
        )
    finally:
        if store is not None:
            store.close_pool()
        if database_created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
