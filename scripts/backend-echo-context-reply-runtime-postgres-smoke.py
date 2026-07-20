#!/usr/bin/env python3
"""Prove Context Packet to delayed Echo Answer runtime boundaries in Postgres.

This smoke creates a disposable database, applies the current migrations, and
uses the real FastAPI routes plus the default-off V4 completion service.  It
does not call a model Provider, enable a worker, or write production data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import app.main as main_module
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.echo_delayed_reply_effects import (
    ECHO_DELAYED_REPLY_SCHEMA_VERSION,
    EchoDelayedReplyGeneratedAnswer,
    build_echo_delayed_reply_plan,
)
from app.services.echo_delayed_reply_service import EchoDelayedReplyAtomicCompletionService
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


def login(client: TestClient, *, phone: str, nickname: str) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/auth/login",
        json={
            "phone": phone,
            "nickname": nickname,
            "password": "echo-context-runtime-smoke",
        },
    )
    require(response.status_code == 200, f"temporary user login failed: {response.text}")
    body = response.json()
    user_id = str((body.get("user") or {}).get("id") or "")
    access_token = str((body.get("auth") or {}).get("accessToken") or "")
    require(user_id, "login must return a user id")
    require(access_token.startswith("dja_"), "login must return an access token")
    return user_id, {"Authorization": f"Bearer {access_token}"}


def utc_iso(offset_seconds: int = 0) -> str:
    value = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=offset_seconds)
    return value.isoformat().replace("+00:00", "Z")


def v4_reply(
    *,
    reply_id: str,
    owner_subject_id: str,
    context_hash: str,
    context_version: str,
    policy_version: str,
) -> dict[str, Any]:
    return {
        "id": reply_id,
        "delayedReplyId": reply_id,
        "userId": owner_subject_id,
        "ownerSubjectId": owner_subject_id,
        "vaultId": owner_subject_id,
        "conversationId": f"conversation-{reply_id}",
        "requestId": f"request-{reply_id}",
        "replyGeneration": 1,
        "contextHash": context_hash,
        "contextVersion": context_version,
        "policyVersion": policy_version,
        "authorityEpoch": 0,
        "rowVersion": 1,
        "deliverAt": utc_iso(-1),
        "contextExpiresAt": utc_iso(300),
        "authorityState": "active",
        "deliveryState": "scheduled",
        "deliveryProtocolVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
    }


def generated_answer(seed: str) -> EchoDelayedReplyGeneratedAnswer:
    return EchoDelayedReplyGeneratedAnswer(
        answer_text=f"PRIVATE_ECHO_CONTEXT_REPLY_BODY_{seed}",
        citation_receipt_hash=sha256(f"citation:{seed}".encode("utf-8")).hexdigest(),
        provider_result_hash=sha256(f"provider:{seed}".encode("utf-8")).hexdigest(),
    )


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_echo_context_reply_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    store: PostgresStore | None = None
    previous_store = main_module.store
    previous_backend_token = main_module.BACKEND_API_TOKEN
    previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
    previous_route_mode = main_module.AUTH_ROUTE_MODE
    previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="echo-context-reply-runtime-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
        store.open_pool(wait=True)
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"

        client = TestClient(main_module.app)
        owner_id, owner_headers = login(
            client,
            phone="13900000201",
            nickname="Echo context runtime owner",
        )
        other_id, other_headers = login(
            client,
            phone="13900000202",
            nickname="Echo context runtime other",
        )

        fact_id = "echo_context_runtime_fact"
        fact_statement = "院子里的桂花树见证了家人的夏天。"
        synced = client.post(
            "/kb/sync",
            headers=owner_headers,
            json={
                "userId": owner_id,
                "graph": {
                    "people": [],
                    "places": [],
                    "events": [],
                    "facts": [
                        {
                            "id": fact_id,
                            "statement": fact_statement,
                            "confidence": "high",
                            "privacyMetadata": {"scope": "generationAllowed"},
                        }
                    ],
                },
            },
        )
        require(synced.status_code == 200, f"knowledge seed failed: {synced.text}")

        context = client.post(
            "/context/build",
            headers=owner_headers,
            json={
                "userId": owner_id,
                "intent": "echo_chat",
                "query": "请回忆院子里的桂花树。",
                "personaScope": "personal",
                "digitalHumanId": owner_id,
                "lifecycleMode": "sunlight",
            },
        )
        require(context.status_code == 200, f"context build failed: {context.text}")
        packet = context.json().get("contextPacket") or {}
        persona = packet.get("persona") or {}
        generation = packet.get("generationContext") or {}
        content_hash = str(generation.get("contentHash") or "")
        context_hash = content_hash.removeprefix("sha256:")
        require(packet.get("userId") == owner_id, "context packet owner drifted")
        require(persona.get("personaScope") == "personal", "context packet scope drifted")
        require(persona.get("digitalHumanId") == owner_id, "context packet digital human drifted")
        require(
            fact_statement in str(generation.get("text") or ""),
            "owner fact must enter the generation context",
        )
        require(len(context_hash) == 64, "context generation hash must be sha256")
        require(
            any(item.get("refId") == fact_id for item in packet.get("selectedContext") or []),
            "selected context must retain the knowledge fact reference",
        )

        mismatched_context = client.post(
            "/context/build",
            headers=owner_headers,
            json={
                "userId": other_id,
                "intent": "echo_chat",
                "query": "cross owner probe",
                "personaScope": "personal",
                "digitalHumanId": other_id,
            },
        )
        require(mismatched_context.status_code == 403, "cross-owner context request must be denied")

        public_v4_attempt = client.post(
            "/echo/delayed-replies",
            headers=owner_headers,
            json={
                **v4_reply(
                    reply_id="reply-public-v4-disabled",
                    owner_subject_id=owner_id,
                    context_hash=context_hash,
                    context_version=str(packet.get("contextVersion") or "echo-context-v2"),
                    policy_version=str((packet.get("safetyPolicy") or {}).get("policyVersion") or "safety-policy-v1"),
                ),
                "minutes": 7,
                "trigger": "tenRoundBaseline",
                "rawTranscript": "请在安全边界内安排一封回信。",
            },
        )
        require(public_v4_attempt.status_code == 409, "public V4 completion route must remain disabled")
        require(
            ((public_v4_attempt.json().get("detail") or {}).get("code")) == "echo_delayed_reply_v4_disabled",
            "public V4 completion denial code drifted",
        )

        reply_id = "reply-context-bound"
        reply = v4_reply(
            reply_id=reply_id,
            owner_subject_id=owner_id,
            context_hash=context_hash,
            context_version=str(packet.get("contextVersion") or "echo-context-v2"),
            policy_version=str((packet.get("safetyPolicy") or {}).get("policyVersion") or "safety-policy-v1"),
        )
        store.add_echo_delayed_reply(owner_id, reply)
        plan = build_echo_delayed_reply_plan(reply, now_iso=utc_iso())
        completed = EchoDelayedReplyAtomicCompletionService(store).complete(
            plan,
            generated_answer=generated_answer("context-bound"),
            now_iso=utc_iso(),
        )
        require(completed.outcome == "completed", "context-bound delayed reply must complete once")
        require(completed.delivery_state == "completed", "completed reply must be terminal")

        answer = client.get(
            f"/echo/delayed-replies/{owner_id}/{reply_id}/answer",
            headers=owner_headers,
        )
        require(answer.status_code == 200, f"owner answer read failed: {answer.text}")
        answer_payload = answer.json()
        answer_context = (answer_payload.get("answer") or {}).get("contextReceipt") or {}
        require(answer_payload.get("userId") == owner_id, "answer route owner drifted")
        require(
            answer_context.get("contextHash") == context_hash,
            "Answer must bind the exact Context Packet hash",
        )
        require(
            answer_context.get("contextVersion") == packet.get("contextVersion"),
            "Answer must bind the Context Packet version",
        )
        require(
            (answer_payload.get("receipt") or {}).get("mailboxProjectionBodyRedacted") is True,
            "Answer receipt must declare the redacted mailbox projection",
        )

        private_body = "PRIVATE_ECHO_CONTEXT_REPLY_BODY_context-bound"
        mailbox = client.get(f"/mailbox/letters/{owner_id}", headers=owner_headers)
        require(mailbox.status_code == 200, f"owner mailbox read failed: {mailbox.text}")
        require(private_body not in json.dumps(mailbox.json(), ensure_ascii=False), "mailbox leaked Answer body")

        other_answer = client.get(
            f"/echo/delayed-replies/{owner_id}/{reply_id}/answer",
            headers=other_headers,
        )
        require(other_answer.status_code == 403, "cross-owner Answer read must be denied")
        other_mailbox = client.get(f"/mailbox/letters/{owner_id}", headers=other_headers)
        require(other_mailbox.status_code == 403, "cross-owner mailbox read must be denied")

        print(
            json.dumps(
                {
                    "answerBoundToContext": True,
                    "contextIdentityMatched": True,
                    "crossOwnerAnswerDenied": True,
                    "crossOwnerContextDenied": True,
                    "crossOwnerMailboxDenied": True,
                    "mailboxBodyRedacted": True,
                    "migrationHead": applied["appliedVersions"][-1],
                    "publicV4RouteDisabled": True,
                    "status": "passed",
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    finally:
        main_module.store = previous_store
        main_module.BACKEND_API_TOKEN = previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = previous_ownership_mode
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
