#!/usr/bin/env python3
"""Exercise internal business-message shadow persistence in disposable Postgres.

The smoke uses only synthetic, completed async-effect receipts. It neither
writes mailbox_letters nor enables a worker, notification dispatcher, or
Provider. Cross-account inbox coordinates are supplied explicitly as an
account snapshot; this is not a delegated-access grant test.
"""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.business_message_projection_repository import (
    BusinessMessageProjectionConflict,
    InboxAccountSnapshot,
)
from app.async_effects.consumer_repository import AsyncEffectSyntheticConsumerCommand
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
)
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


def intent() -> AsyncEffectIntent:
    return AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.businessMessageProjection.smoke",
        target=AsyncEffectTarget(
            owner_subject_id="owner-message-projection-smoke",
            vault_id="owner-vault-projection-smoke",
            resource_type="timeLetter",
            resource_id="letter-message-projection-smoke",
            resource_version=2,
            purpose="timeLetterDelivery",
            authority_epoch=4,
        ),
        payload_hash=digest("business-message-projection-smoke"),
    )


def completed_source(store: PostgresStore) -> BusinessCompletionMessageSource:
    value = intent()
    with store.request_unit_of_work(
        correlation_id=f"business-message-projection-accept:{value.operation_id}",
        command_id="businessMessageProjectionAccept",
    ):
        store.effect_kernel_repository().accept(value)
    with store.request_unit_of_work(
        correlation_id=f"business-message-projection-complete:{value.operation_id}",
        command_id="businessMessageProjectionComplete",
    ):
        receipt = store.async_effect_consumer_repository().consume(
            AsyncEffectSyntheticConsumerCommand(
                intent=value,
                consumer_name="smoke.businessMessageProjection",
                business_target_key=value.business_target_key,
                outcome="completed",
                reason_code="smokeCompletion",
                result_ref_hash=digest("business-message-projection-result"),
            )
        )
    return BusinessCompletionMessageSource(
        intent=value,
        completion=receipt,
        message_kind=InAppMessageKind.TIME_LETTER,
    )


def record(store: PostgresStore, source: BusinessCompletionMessageSource, snapshot: InboxAccountSnapshot):
    with store.request_unit_of_work(
        correlation_id=f"business-message-projection-record:{source.message_id}",
        command_id="businessMessageProjectionRecord",
    ):
        return store.async_effect_business_message_projection_repository().record(source, snapshot)


def table_count(dsn: str, table: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(*table.split("."))))
            return int(cursor.fetchone()[0])


def assert_append_only(dsn: str, message_id: str) -> None:
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE async_effects.business_message_projections SET state = 'read' "
                    "WHERE message_id = %s",
                    (message_id,),
                )
        raise AssertionError("append-only projection accepted a state mutation")
    except psycopg.Error:
        pass


def assert_business_receipt_append_only(dsn: str, business_receipt_id: str) -> None:
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE async_effects.business_receipts SET resource_version = 3 "
                    "WHERE receipt_id = %s",
                    (business_receipt_id,),
                )
        raise AssertionError("business receipt accepted an immutable coordinate mutation")
    except psycopg.Error:
        pass


def assert_direct_receipt_coordinate_mismatch_rejected(
    dsn: str,
    *,
    record,
) -> None:
    try:
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO async_effects.business_message_projections (
                        message_id, business_receipt_id, operation_id,
                        resource_owner_subject_id, resource_vault_id,
                        resource_type, resource_id, resource_version,
                        resource_authority_epoch, purpose, business_target_key,
                        inbox_subject_id, inbox_vault_id, inbox_account_epoch,
                        message_kind, state, projection_hash, schema_version
                    )
                    SELECT %s, business_receipt_id, operation_id,
                           resource_owner_subject_id, resource_vault_id,
                           resource_type, resource_id, resource_version + 1,
                           resource_authority_epoch, purpose, business_target_key,
                           'direct-mismatch-inbox', 'direct-mismatch-vault', 0,
                           message_kind, state, projection_hash, schema_version
                    FROM async_effects.business_message_projections
                    WHERE message_id = %s
                    """,
                    (str(uuid4()), record.message.message_id),
                )
        raise AssertionError("direct receipt-coordinate mismatch must fail closed")
    except psycopg.Error:
        pass


def exercise(dsn: str) -> None:
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    store.open_pool(wait=True)
    try:
        source = completed_source(store)
        owner_snapshot = InboxAccountSnapshot(
            inbox_subject_id="owner-message-projection-smoke",
            inbox_vault_id="owner-vault-projection-smoke",
            account_epoch=4,
        )
        owner = record(store, source, owner_snapshot)
        duplicate = record(store, source, owner_snapshot)
        require(owner.outcome == "recorded", "first self inbox projection must record")
        require(duplicate.outcome == "deduplicated", "same inbox projection must deduplicate")

        family_source = BusinessCompletionMessageSource(
            intent=source.intent,
            completion=source.completion,
            message_kind=InAppMessageKind.TIME_LETTER,
            inbox_subject_id="family-message-projection-smoke",
            inbox_vault_id="family-vault-projection-smoke",
        )
        family = record(
            store,
            family_source,
            InboxAccountSnapshot(
                inbox_subject_id="family-message-projection-smoke",
                inbox_vault_id="family-vault-projection-smoke",
                account_epoch=9,
            ),
        )
        require(family.outcome == "recorded", "explicit family inbox shadow must record")
        require(
            owner.record.message.message_id != family.record.message.message_id,
            "each inbox must have a separate stable message identity",
        )
        require(
            table_count(dsn, "async_effects.business_message_projections") == 2,
            "two inboxes must create two shadow rows",
        )
        require(table_count(dsn, "mailbox_letters") == 0, "shadow must not change public mailbox")

        try:
            record(
                store,
                family_source,
                InboxAccountSnapshot(
                    inbox_subject_id="family-message-projection-smoke",
                    inbox_vault_id="family-vault-projection-smoke",
                    account_epoch=10,
                ),
            )
            raise AssertionError("changed inbox epoch must not overwrite immutable shadow")
        except BusinessMessageProjectionConflict:
            pass

        assert_business_receipt_append_only(dsn, source.completion.business_receipt_id)
        assert_append_only(dsn, owner.record.message.message_id)
        assert_direct_receipt_coordinate_mismatch_rejected(dsn, record=owner.record)
    finally:
        store.close_pool()

    reopened_store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    reopened_store.open_pool(wait=True)
    try:
        with reopened_store.request_unit_of_work(
            correlation_id="business-message-projection-reopen",
            command_id="businessMessageProjectionReopen",
        ):
            restored = reopened_store.async_effect_business_message_projection_repository().load(
                owner.record.message.message_id
            )
        require(restored == owner.record, "shadow record must survive connection reopen")
    finally:
        reopened_store.close_pool()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_business_message_projection_{uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    created = False
    try:
        create_database(admin_dsn, database_name)
        created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="business-message-projection-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        require(migrator.verify()["status"] == "ready", "temporary schema must verify")
        exercise(test_dsn)
        print(
            "Business-message projection Postgres smoke passed "
            "(internal shadow only; mailbox, worker, notification, and Provider remain unchanged)."
        )
    finally:
        if created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
