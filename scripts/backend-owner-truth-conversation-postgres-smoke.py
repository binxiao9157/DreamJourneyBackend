#!/usr/bin/env python3
"""Exercise the M0-A private conversation lane in a disposable Postgres DB.

The smoke creates an isolated database, applies all migrations, then proves
owner/vault isolation, command replay, optimistic version checks, append-only
message records, restart reads, and the absence of Source/Candidate/Memory
promotion. It never writes to the configured application database.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Callable
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    ConversationMessageAuthor,
    ConversationMessageKind,
    InterviewBoundary,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationVersionConflict,
    SetInterviewBoundaryCommand,
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService
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


def invoke(
    store: PostgresStore,
    *,
    command_id: str,
    operation: Callable[[OwnerTruthConversationService], object],
) -> object:
    with store.request_unit_of_work(
        correlation_id=f"owner-truth-conversation-smoke-{command_id}",
        command_id=command_id,
    ):
        return operation(OwnerTruthConversationService(store.owner_truth_conversation_repository()))


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_owner_truth_conversation_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    store = None
    restarted_store = None
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-conversation-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0029" in applied["appliedVersions"], "conversation migration must apply")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        store.open_pool(wait=True)
        context = OwnerTruthCommandContext(
            vault_id="conversation-vault-a",
            owner_subject_id="conversation-owner-a",
            actor_subject_id="conversation-owner-a",
            policy_version="owner-truth-v1",
        )
        thread_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        start = StartInterviewSessionCommand(
            command_id="start-conversation-smoke",
            thread_id=thread_id,
            session_id=session_id,
            expected_thread_version=0,
            entry_mode="naturalInput",
        )
        started = invoke(
            store,
            command_id="start-conversation-smoke",
            operation=lambda service: service.start_session(command=start, context=context),
        )
        require(started.outcome == "created", "start must create one session")
        replayed_start = invoke(
            store,
            command_id="start-conversation-smoke-replay",
            operation=lambda service: service.start_session(command=start, context=context),
        )
        require(replayed_start.outcome == "deduplicated", "start replay must deduplicate")

        append = AppendInterviewMessageCommand(
            command_id="append-conversation-smoke",
            thread_id=thread_id,
            session_id=session_id,
            message_id=message_id,
            expected_thread_version=1,
            expected_session_version=1,
            author=ConversationMessageAuthor.OWNER,
            kind=ConversationMessageKind.NARRATIVE,
            text="这是一条仅用于隔离数据库验证的私人访谈消息。",
        )
        appended = invoke(
            store,
            command_id="append-conversation-smoke",
            operation=lambda service: service.append_message(command=append, context=context),
        )
        require(appended.outcome == "created", "append must create one private message")
        require(appended.message_sequence == 1, "first message sequence must be one")
        replayed_append = invoke(
            store,
            command_id="append-conversation-smoke-replay",
            operation=lambda service: service.append_message(command=append, context=context),
        )
        require(replayed_append.outcome == "deduplicated", "append replay must deduplicate")

        stale_rejected = False
        try:
            invoke(
                store,
                command_id="append-conversation-smoke-stale",
                operation=lambda service: service.append_message(
                    command=AppendInterviewMessageCommand(
                        command_id="append-conversation-smoke-stale",
                        thread_id=thread_id,
                        session_id=session_id,
                        message_id=str(uuid.uuid4()),
                        expected_thread_version=1,
                        expected_session_version=1,
                        author=ConversationMessageAuthor.OWNER,
                        kind=ConversationMessageKind.NARRATIVE,
                        text="这个陈旧版本必须被拒绝。",
                    ),
                    context=context,
                ),
            )
        except OwnerTruthConversationVersionConflict:
            stale_rejected = True
        require(stale_rejected, "stale expectedVersion must be rejected")

        other_context = OwnerTruthCommandContext(
            vault_id=context.vault_id,
            owner_subject_id="conversation-owner-b",
            actor_subject_id="conversation-owner-b",
            policy_version="owner-truth-v1",
        )
        cross_owner_rejected = False
        try:
            invoke(
                store,
                command_id="cross-owner-read",
                operation=lambda service: service.read_session(
                    session_id=session_id,
                    context=other_context,
                ),
            )
        except OwnerTruthConversationAccessDenied:
            cross_owner_rejected = True
        require(cross_owner_rejected, "cross-owner read must be denied")

        boundary = SetInterviewBoundaryCommand(
            command_id="boundary-conversation-smoke",
            thread_id=thread_id,
            session_id=session_id,
            expected_session_version=2,
            boundary=InterviewBoundary.DO_NOT_ASK,
        )
        paused = invoke(
            store,
            command_id="boundary-conversation-smoke",
            operation=lambda service: service.set_boundary(command=boundary, context=context),
        )
        require(paused.state.value == "paused", "doNotAsk must pause the interview session")

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM owner_truth.conversation_messages WHERE vault_id = %s",
                    (context.vault_id,),
                )
                require(cursor.fetchone()[0] == 1, "exactly one private message must persist")
                for relation in (
                    "sources",
                    "memory_candidates",
                    "memories",
                    "memory_versions",
                ):
                    cursor.execute(f"SELECT COUNT(*) FROM owner_truth.{relation}")
                    require(cursor.fetchone()[0] == 0, f"conversation must not write owner_truth.{relation}")
                immutable_message_rejected = False
                try:
                    cursor.execute(
                        "UPDATE owner_truth.conversation_messages SET content_hash = %s WHERE id = %s",
                        ("tampered", message_id),
                    )
                except Exception:
                    immutable_message_rejected = True
                    connection.rollback()
                require(immutable_message_rejected, "conversation message must be append-only")

        store.close_pool()
        store = None
        restarted_store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        restarted_store.open_pool(wait=True)
        restored = invoke(
            restarted_store,
            command_id="read-after-restart",
            operation=lambda service: service.read_session(session_id=session_id, context=context),
        )
        require(restored.row_version == 3, "session state must survive a store restart")
        require(restored.boundary is InterviewBoundary.DO_NOT_ASK, "boundary must survive restart")
        print("owner_truth_conversation_postgres_smoke=passed")
    finally:
        if restarted_store is not None:
            restarted_store.close_pool()
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
