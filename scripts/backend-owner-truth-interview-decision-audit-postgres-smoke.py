#!/usr/bin/env python3
"""Exercise value-free guided-interview decision audits in disposable Postgres.

The smoke never uses production business tables.  It proves migration 0041,
Owner/Vault/message binding, idempotent replay, stale fencing, and the absence
of message content from the decision-audit table.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sys
from typing import Iterator
from uuid import uuid4

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
    StartInterviewSessionCommand,
)
from app.domain.owner_truth.interview_orchestration import InterviewAction
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_interview_decision_audit import (
    OwnerTruthInterviewDecisionAuditCommand,
    OwnerTruthInterviewDecisionAuditService,
    OwnerTruthInterviewDecisionAuditStale,
)
from app.services.owner_truth_interview_session_orchestration import (
    InterviewSessionOrchestrationSignals,
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


@contextmanager
def unit_of_work(
    store: PostgresStore,
    *,
    correlation_id: str,
    command_id: str,
) -> Iterator[None]:
    with store.request_unit_of_work(correlation_id=correlation_id, command_id=command_id):
        yield


def append_owner_narrative(
    *,
    store: PostgresStore,
    context: OwnerTruthCommandContext,
    thread_id: str,
    session_id: str,
    command_id: str,
    expected_thread_version: int,
    expected_session_version: int,
    text: str,
):
    with unit_of_work(store, correlation_id=command_id, command_id=command_id):
        return OwnerTruthConversationService(store.owner_truth_conversation_repository()).append_message(
            command=AppendInterviewMessageCommand(
                command_id=command_id,
                thread_id=thread_id,
                session_id=session_id,
                message_id=str(uuid4()),
                expected_thread_version=expected_thread_version,
                expected_session_version=expected_session_version,
                author=ConversationMessageAuthor.OWNER,
                kind=ConversationMessageKind.NARRATIVE,
                text=text,
            ),
            context=context,
        )


def decision_count_and_row_text(dsn: str) -> tuple[int, str]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*), COALESCE(string_agg(row_to_json(d)::text, ''), '') FROM owner_truth.interview_decisions AS d")
            row = cursor.fetchone()
    require(row is not None, "decision audit query is unavailable")
    return int(row[0]), str(row[1])


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_interview_decision_audit_smoke_{uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-interview-decision-audit-g0",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(verified["appliedHead"] == "0041", "decision audit migration must be applied")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        context = OwnerTruthCommandContext(
            vault_id="vault-interview-decision-audit-smoke",
            owner_subject_id="owner-interview-decision-audit-smoke",
            actor_subject_id="owner-interview-decision-audit-smoke",
            policy_version="owner-truth-v1",
        )
        thread_id = str(uuid4())
        session_id = str(uuid4())
        with unit_of_work(store, correlation_id="decision-audit-start", command_id="decision-audit-start"):
            started = OwnerTruthConversationService(
                store.owner_truth_conversation_repository()
            ).start_session(
                command=StartInterviewSessionCommand(
                    command_id="decision-audit-start",
                    thread_id=thread_id,
                    session_id=session_id,
                    expected_thread_version=0,
                    entry_mode="naturalInput",
                ),
                context=context,
            )
        first = append_owner_narrative(
            store=store,
            context=context,
            thread_id=thread_id,
            session_id=session_id,
            command_id="decision-audit-first",
            expected_thread_version=started.thread_version,
            expected_session_version=started.session_version,
            text="这段真实私有叙述不得复制到访谈动作审计表",
        )
        require(first.message_id is not None, "owner narrative must persist a message id")
        command = OwnerTruthInterviewDecisionAuditCommand(
            command_id="decision-audit-record",
            thread_id=thread_id,
            session_id=session_id,
            message_id=first.message_id,
            expected_session_version=first.session_version,
        )
        audit_service = OwnerTruthInterviewDecisionAuditService(store, enabled=True)
        created = audit_service.decide_and_record(
            command=command,
            context=context,
            signals=InterviewSessionOrchestrationSignals(
                topic_id="topic-private-story",
                topic_incomplete=True,
            ),
        )
        require(created.outcome == "created", "first decision audit must be created")
        require(created.action is InterviewAction.DEEPEN, "expected deterministic deepen decision")

        second = append_owner_narrative(
            store=store,
            context=context,
            thread_id=thread_id,
            session_id=session_id,
            command_id="decision-audit-second",
            expected_thread_version=first.thread_version,
            expected_session_version=first.session_version,
            text="后续叙述用于证明状态推进不会破坏审计重放",
        )
        replayed = audit_service.decide_and_record(
            command=command,
            context=context,
            signals=InterviewSessionOrchestrationSignals(topic_id="topic-private-story"),
        )
        require(replayed.outcome == "deduplicated", "same command must replay after session advances")
        require(replayed.decision_id == created.decision_id, "replay must preserve decision id")

        try:
            audit_service.decide_and_record(
                command=OwnerTruthInterviewDecisionAuditCommand(
                    command_id="decision-audit-stale",
                    thread_id=thread_id,
                    session_id=session_id,
                    message_id=first.message_id,
                    expected_session_version=first.session_version,
                ),
                context=context,
                signals=InterviewSessionOrchestrationSignals(topic_id="topic-private-story"),
            )
        except OwnerTruthInterviewDecisionAuditStale:
            pass
        else:
            raise AssertionError("stale message/session binding must be rejected")

        count, rendered = decision_count_and_row_text(test_dsn)
        require(count == 1, "one owner narrative must produce one audit record")
        require("这段真实私有叙述" not in rendered, "audit record must not store message text")
        require("content_payload" not in rendered, "audit record must not expose content payload")
        require(second.session_version > first.session_version, "second append must advance session")
        print(
            "owner truth interview decision audit postgres smoke passed "
            "schemaHead=0041 valueFree=true deduplicated=true staleFenced=true"
        )
    finally:
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as error:  # pragma: no cover - cleanup must not hide primary failure
            print(f"warning: failed to drop disposable database {database_name}: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
