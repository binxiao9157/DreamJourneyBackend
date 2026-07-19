#!/usr/bin/env python3
"""Exercise the hidden V4 delayed Echo Answer/Inbox transaction in Postgres.

This smoke always creates and drops a disposable database. It does not call a
model Provider, enable a worker, expose a new route, or write into production
application data.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
from pathlib import Path
import sys
from typing import Any
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.echo_delayed_reply_effects import (
    ECHO_DELAYED_REPLY_SCHEMA_VERSION,
    EchoDelayedReplyGeneratedAnswer,
    build_echo_delayed_reply_plan,
)
from app.services.echo_delayed_reply_service import (
    EchoDelayedReplyAtomicCompletionPersistenceError,
    EchoDelayedReplyAtomicCompletionService,
)
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


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


def v4_reply(*, reply_id: str, owner_subject_id: str, context_seed: str) -> dict[str, Any]:
    return {
        "id": reply_id,
        "delayedReplyId": reply_id,
        "userId": owner_subject_id,
        "ownerSubjectId": owner_subject_id,
        "vaultId": owner_subject_id,
        "conversationId": f"conversation-{reply_id}",
        "requestId": f"request-{reply_id}",
        "replyGeneration": 1,
        "contextHash": sha256(context_seed.encode("utf-8")).hexdigest(),
        "contextVersion": "echo-context-v4",
        "policyVersion": "echo-policy-v4",
        "authorityEpoch": 0,
        "rowVersion": 1,
        "deliverAt": "2026-07-20T09:00:00Z",
        "contextExpiresAt": "2026-07-20T10:00:00Z",
        "authorityState": "active",
        "deliveryState": "scheduled",
        "deliveryProtocolVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
    }


def generated_answer(*, seed: str) -> EchoDelayedReplyGeneratedAnswer:
    return EchoDelayedReplyGeneratedAnswer(
        answer_text=f"PRIVATE_ECHO_DELAYED_REPLY_BODY_{seed}",
        citation_receipt_hash=sha256(f"citation:{seed}".encode("utf-8")).hexdigest(),
        provider_result_hash=sha256(f"provider:{seed}".encode("utf-8")).hexdigest(),
    )


class FailingMailboxStore(PostgresStore):
    def add_mailbox_letter(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise EchoDelayedReplyAtomicCompletionPersistenceError("injected mailbox persistence failure")


def count_rows(dsn: str, table: str, user_id: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT COUNT(*) FROM {} WHERE user_id = %s").format(sql.Identifier(table)), (user_id,))
            return int(cursor.fetchone()[0])


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_echo_delayed_reply_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None
    failing_store: FailingMailboxStore | None = None
    owner = "owner-echo-delayed-smoke"
    now_iso = "2026-07-20T09:00:01Z"

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="echo-delayed-reply-g0",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedVersions"][-1] == "0024", "unexpected migration head")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
        store.open_pool(wait=True)
        reply = v4_reply(reply_id="reply-atomic", owner_subject_id=owner, context_seed="atomic")
        store.add_echo_delayed_reply(owner, reply)
        plan = build_echo_delayed_reply_plan(reply, now_iso=now_iso)
        service = EchoDelayedReplyAtomicCompletionService(store)
        answer = generated_answer(seed="atomic")
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _: service.complete(plan, generated_answer=answer, now_iso=now_iso),
                    range(2),
                )
            )
        require(
            sorted(result.outcome for result in results) == ["already_terminal", "completed"],
            "double completion must yield one completed result and one terminal replay",
        )
        require(count_rows(test_dsn, "echo_delayed_reply_answers", owner) == 1, "one private Answer is required")
        require(count_rows(test_dsn, "mailbox_letters", owner) == 1, "one owner Inbox item is required")
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT payload::text FROM mailbox_letters WHERE user_id = %s", (owner,))
                mailbox_text = str(cursor.fetchone()[0])
                require("PRIVATE_ECHO_DELAYED_REPLY_BODY" not in mailbox_text, "Inbox must not retain answer body")
                cursor.execute(
                    "SELECT payload::text FROM echo_delayed_reply_answers WHERE user_id = %s",
                    (owner,),
                )
                answer_text = str(cursor.fetchone()[0])
                require("PRIVATE_ECHO_DELAYED_REPLY_BODY_atomic" in answer_text, "private Answer must retain body")
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM async_effects.consumer_inbox
                    WHERE consumer_name = 'echo.delayedReply.answer' AND state = 'completed'
                    """
                )
                require(int(cursor.fetchone()[0]) == 1, "one business completion receipt is required")

        legacy = {
            "id": "legacy-due",
            "delayedReplyId": "legacy-due",
            "deliverAt": "2026-07-20T08:00:00Z",
            "minutes": 7,
            "trigger": "tenRoundBaseline",
            "deliveryState": "scheduled",
        }
        v4_scheduled = v4_reply(reply_id="reply-v4-legacy-guard", owner_subject_id=owner, context_seed="guard")
        v4_scheduled["deliverAt"] = "2026-07-20T08:00:00Z"
        store.add_echo_delayed_reply(owner, legacy)
        store.add_echo_delayed_reply(owner, v4_scheduled)
        dispatched = store.mark_due_echo_delayed_replies_for_dispatch(now_iso, now_iso)
        require([item["id"] for item in dispatched] == ["legacy-due"], "legacy dispatcher must skip V4 replies")

        failing_reply = v4_reply(reply_id="reply-rollback", owner_subject_id=owner, context_seed="rollback")
        store.add_echo_delayed_reply(owner, failing_reply)
        failing_plan = build_echo_delayed_reply_plan(failing_reply, now_iso=now_iso)
        failing_store = FailingMailboxStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        failing_store.open_pool(wait=True)
        try:
            with raises(EchoDelayedReplyAtomicCompletionPersistenceError):
                EchoDelayedReplyAtomicCompletionService(failing_store).complete(
                    failing_plan,
                    generated_answer=generated_answer(seed="rollback"),
                    now_iso=now_iso,
                )
        finally:
            failing_store.close_pool()
            failing_store = None
        require(count_rows(test_dsn, "echo_delayed_reply_answers", owner) == 1, "failed transaction must roll back Answer")
        require(count_rows(test_dsn, "mailbox_letters", owner) == 1, "failed transaction must roll back Inbox")

        print(
            "echo delayed reply atomic completion postgres smoke passed "
            "answer=unique inbox=value-free concurrent=deduplicated "
            "legacyDispatcher=isolated rollback=clean"
        )
    finally:
        if failing_store is not None:
            failing_store.close_pool()
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


class raises:
    def __init__(self, expected: type[BaseException]):
        self._expected = expected

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _tb):
        if exc_type is None:
            raise AssertionError(f"expected {self._expected.__name__}")
        return issubclass(exc_type, self._expected)


if __name__ == "__main__":
    main()
