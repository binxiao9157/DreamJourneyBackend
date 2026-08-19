#!/usr/bin/env python3
"""Exercise the metadata-only message center in disposable PostgreSQL."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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

from app.async_effects.business_message_projection_repository import InboxAccountSnapshot
from app.async_effects.consumer_repository import AsyncEffectSyntheticConsumerCommand
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
)
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.in_app_message_center import (
    InAppMessageCenterNotFound,
    InAppMessageCenterService,
)
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
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def record_message(
    store: PostgresStore,
    *,
    owner_id: str,
    sequence: int,
    kind: InAppMessageKind = InAppMessageKind.CANDIDATE_READY,
) -> str:
    intent = AsyncEffectIntent(
        operation_type="asyncEffect.synthetic.inAppMessageCenter.smoke",
        target=AsyncEffectTarget(
            owner_subject_id=owner_id,
            vault_id=f"vault-{owner_id}",
            resource_type="candidate" if kind is InAppMessageKind.CANDIDATE_READY else "task",
            resource_id=f"message-center-resource-{sequence}",
            resource_version=sequence,
            purpose="inAppMessageCenterSmoke",
            authority_epoch=1,
        ),
        payload_hash=digest(f"in-app-message-center-{owner_id}-{sequence}-{kind.value}"),
    )
    with store.request_unit_of_work(
        correlation_id=f"message-center-accept:{intent.operation_id}",
        command_id=f"messageCenterAccept{sequence}",
    ):
        store.effect_kernel_repository().accept(intent)
    with store.request_unit_of_work(
        correlation_id=f"message-center-complete:{intent.operation_id}",
        command_id=f"messageCenterComplete{sequence}",
    ):
        receipt = store.async_effect_consumer_repository().consume(
            AsyncEffectSyntheticConsumerCommand(
                intent=intent,
                consumer_name="smoke.inAppMessageCenter",
                business_target_key=intent.business_target_key,
                outcome="completed",
                reason_code="smokeCompletion",
                result_ref_hash=digest(f"message-center-result-{sequence}"),
            )
        )
    source = BusinessCompletionMessageSource(
        intent=intent,
        completion=receipt,
        message_kind=kind,
    )
    with store.request_unit_of_work(
        correlation_id=f"message-center-project:{source.message_id}",
        command_id=f"messageCenterProject{sequence}",
    ):
        result = store.async_effect_business_message_projection_repository().record(
            source,
            InboxAccountSnapshot(
                inbox_subject_id=owner_id,
                inbox_vault_id=f"vault-{owner_id}",
                account_epoch=1,
            ),
        )
    return result.record.message.message_id


def service_call(store: PostgresStore, command_id: str, callback):
    with store.request_unit_of_work(
        correlation_id=f"message-center-command:{command_id}",
        command_id=command_id,
    ):
        return callback(InAppMessageCenterService(store.in_app_message_center_repository()))


def exercise(dsn: str) -> None:
    owner_id = "owner-message-center-smoke"
    other_id = "other-message-center-smoke"
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=4)
    store.open_pool(wait=True)
    try:
        owner_messages = [
            record_message(store, owner_id=owner_id, sequence=index)
            for index in range(1, 4)
        ]
        other_message = record_message(store, owner_id=other_id, sequence=4)
        record_message(
            store,
            owner_id=owner_id,
            sequence=5,
            kind=InAppMessageKind.TIME_LETTER,
        )

        first_page = service_call(
            store,
            str(uuid4()),
            lambda service: service.list_messages(owner_id, limit=2),
        )
        second_page = service_call(
            store,
            str(uuid4()),
            lambda service: service.list_messages(
                owner_id,
                limit=2,
                cursor=first_page.next_cursor,
            ),
        )
        require(first_page.unread_count == 3, "closed message kinds must not affect unread count")
        require(len(first_page.messages) == 2, "first page must respect limit")
        require(len(second_page.messages) == 1, "cursor must return the remaining message")

        try:
            service_call(
                store,
                str(uuid4()),
                lambda service: service.mark_read(
                    owner_id,
                    other_message,
                    command_id=str(uuid4()),
                    occurred_at=datetime.now(timezone.utc),
                ),
            )
            raise AssertionError("cross-account message read must fail closed")
        except InAppMessageCenterNotFound:
            pass

        command_id = str(uuid4())

        def mark_all() -> str:
            isolated = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=1)
            isolated.open_pool(wait=True)
            try:
                result = service_call(
                    isolated,
                    command_id,
                    lambda service: service.mark_all_read(
                        owner_id,
                        command_id=command_id,
                        occurred_at=datetime.now(timezone.utc),
                    ),
                )
                return result.outcome
            finally:
                isolated.close_pool()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = sorted(executor.map(lambda _: mark_all(), range(2)))
        require(outcomes == ["applied", "deduplicated"], "concurrent command must apply once")

        late_message = record_message(store, owner_id=owner_id, sequence=6)
        replay = service_call(
            store,
            command_id,
            lambda service: service.mark_all_read(
                owner_id,
                command_id=command_id,
                occurred_at=datetime.now(timezone.utc),
            ),
        )
        require(replay.outcome == "deduplicated", "command replay must stay deduplicated")
        require(replay.unread_count == 1, "message created after command must remain unread")

        deleted = service_call(
            store,
            str(uuid4()),
            lambda service: service.delete_read(
                owner_id,
                command_id=str(uuid4()),
                occurred_at=datetime.now(timezone.utc),
            ),
        )
        require(deleted.affected_count == 3, "delete-read must remove only the read snapshot")
        owner_page = service_call(
            store,
            str(uuid4()),
            lambda service: service.list_messages(owner_id),
        )
        other_page = service_call(
            store,
            str(uuid4()),
            lambda service: service.list_messages(other_id),
        )
        require(
            [message.message_id for message in owner_page.messages] == [late_message],
            "owner inbox must retain only its unread late message",
        )
        require(
            [message.message_id for message in other_page.messages] == [other_message],
            "delete-read must not affect another account",
        )
        require(set(owner_messages).isdisjoint({late_message}), "fixture identities must be unique")
    finally:
        store.close_pool()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_in_app_message_center_{uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    created = False
    try:
        create_database(admin_dsn, database_name)
        created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="in-app-message-center-v1",
            lock_timeout_ms=1_000,
            statement_timeout_ms=15_000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "temporary schema must verify")
        require(
            int(str(verified["expectedHead"])) >= 100,
            "message center requires migration 0100 or newer",
        )
        exercise(test_dsn)
        print("In-app message center PostgreSQL smoke passed.")
    finally:
        if created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
