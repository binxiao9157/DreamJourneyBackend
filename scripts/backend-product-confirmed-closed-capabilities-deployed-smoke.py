#!/usr/bin/env python3
"""Prove product-closed capabilities have zero new deployed side effects."""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request

from app.core.config import settings
from app.services.auth_sessions import AuthSessionService
from app.services.postgres_store import PostgresStore
from scripts.dispatch_due_time_letters import product_closed_summary


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).strip().rstrip("/")
API_TOKEN = os.environ.get(
    "BACKEND_API_TOKEN",
    os.environ.get("DREAMJOURNEY_BACKEND_API_TOKEN", ""),
).strip()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def request_json(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    token: str | None = None,
    expected_status: int = 200,
) -> tuple[dict, dict[str, str]]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {
            key.lower(): value for key, value in error.headers.items()
        }
        body = error.read().decode("utf-8", errors="replace")
    require(status == expected_status, f"{method} {path} returned {status}: {body}")
    return (json.loads(body) if body else {}), response_headers


def issue_smoke_user() -> tuple[PostgresStore, str, str]:
    database_url = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(database_url, "DATABASE_URL is required")
    store = PostgresStore(
        dsn=database_url,
        pool_min_size=1,
        pool_max_size=2,
        pool_timeout_seconds=2.0,
    )
    store.open_pool(wait=True)
    user = store.upsert_user(
        phone=f"196{secrets.randbelow(10**8):08d}",
        nickname="closed capability smoke",
    )
    user_id = str(user.get("id") or "").strip()
    require(user_id, "smoke user id missing")
    session = AuthSessionService(
        store,
        access_ttl_seconds=300,
        refresh_ttl_seconds=900,
    ).issue(user_id)
    token = str(session.get("accessToken") or "").strip()
    require(token.startswith("dja_"), "smoke access token missing")
    return store, user_id, token


def closed_resource_counts(store: PostgresStore, user_id: str) -> dict[str, int]:
    queries = {
        "digitalHumanSession": (
            "SELECT COUNT(*) FROM digital_human_sessions WHERE user_id = %s",
            (user_id,),
        ),
        "timeLetter": (
            "SELECT COUNT(*) FROM archive_items "
            "WHERE user_id = %s AND payload->>'kind' = 'timeLetter'",
            (user_id,),
        ),
        "delayedReply": (
            "SELECT COUNT(*) FROM echo_delayed_replies WHERE user_id = %s",
            (user_id,),
        ),
        "mailbox": (
            "SELECT COUNT(*) FROM mailbox_letters WHERE user_id = %s",
            (user_id,),
        ),
    }
    counts: dict[str, int] = {}
    with store.request_unit_of_work(
        correlation_id="closed-capability-counts",
        command_id=f"closedCapabilityCounts:{secrets.token_hex(8)}",
    ) as unit_of_work:
        with unit_of_work.connection.cursor() as cursor:
            for name, (query, params) in queries.items():
                cursor.execute(query, params)
                row = cursor.fetchone()
                counts[name] = int(row[0] if row else 0)
    return counts


def cleanup_smoke_user(store: PostgresStore | None, user_id: str) -> None:
    if store is None:
        return
    try:
        if user_id:
            with store.request_unit_of_work(
                correlation_id="closed-capability-smoke-cleanup",
                command_id="cleanupClosedCapabilitySmoke",
            ) as unit_of_work:
                with unit_of_work.connection.cursor() as cursor:
                    cursor.execute("DELETE FROM session_events WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM token_families WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
    finally:
        store.close_pool()


def assert_product_closed(body: dict, headers: dict[str, str], feature: str) -> None:
    detail = body.get("detail") or {}
    require(detail.get("code") == "release_policy_denied", f"{feature} code drift")
    require(detail.get("feature") == feature, f"{feature} classification drift")
    require(detail.get("reason") == "productClosed", f"{feature} reason drift")
    require(detail.get("retryable") is False, f"{feature} must not be retryable")
    require(
        headers.get("x-dreamjourney-release-policy-decision") == "deny",
        f"{feature} must be denied before side effects",
    )


def main() -> None:
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(API_TOKEN, "BACKEND_API_TOKEN is required")
    runtime, _ = request_json("GET", "/config/runtime")
    snapshots = runtime.get("capabilitySnapshots") or {}
    for capability in ("digitalHumanLivePanel", "timeLetters", "echoDelayedReplies"):
        snapshot = snapshots.get(capability) or {}
        require(snapshot.get("enabled") is False, f"{capability} must be disabled")
        require(snapshot.get("releaseVisible") is False, f"{capability} must be hidden")
        require(snapshot.get("reason") == "productClosed", f"{capability} reason drift")

    store: PostgresStore | None = None
    user_id = ""
    try:
        store, user_id, user_token = issue_smoke_user()
        before = closed_resource_counts(store, user_id)
        require(not any(before.values()), "temporary smoke user must start empty")

        attempts = (
            (
                "POST",
                "/digital-human/sessions",
                {
                    "userId": user_id,
                    "personaId": user_id,
                    "scene": "echo",
                    "deviceId": "pc-e2-smoke",
                    "lifecycleMode": "sunlight",
                },
                user_token,
                "digitalHumanLivePanel",
            ),
            (
                "POST",
                "/digital-human/sessions/pc-e2-missing/heartbeat",
                {"userId": user_id, "deviceId": "pc-e2-smoke"},
                user_token,
                "digitalHumanLivePanel",
            ),
            (
                "POST",
                "/archive/items",
                {"userId": user_id, "id": "pc-e2-letter", "kind": "timeLetter"},
                user_token,
                "timeLetters",
            ),
            (
                "POST",
                "/archive/time-letters/dispatch-due",
                {"limit": 10},
                API_TOKEN,
                "timeLetters",
            ),
            (
                "POST",
                "/echo/delayed-replies",
                {
                    "userId": user_id,
                    "delayedReplyId": "pc-e2-reply",
                    "deliverAt": "2026-08-20T00:00:00Z",
                    "roundCount": 10,
                    "trigger": "tenRoundBaseline",
                },
                user_token,
                "echoDelayedReplies",
            ),
            (
                "POST",
                "/echo/delayed-replies/dispatch-due",
                {"limit": 10},
                API_TOKEN,
                "echoDelayedReplies",
            ),
        )
        for method, path, payload, token, feature in attempts:
            body, headers = request_json(
                method,
                path,
                payload=payload,
                token=token,
                expected_status=403,
            )
            assert_product_closed(body, headers, feature)

        worker = product_closed_summary("2026-08-20T00:00:00Z")
        require(worker.get("itemCount") == 0, "closed worker must deliver zero items")
        require(worker.get("reminderCount") == 0, "closed worker must emit zero reminders")
        require(
            worker.get("providerDeliveryAttempted") is False,
            "closed worker must not call a provider",
        )
        after = closed_resource_counts(store, user_id)
        require(after == before, "closed requests must not mutate product storage")
    finally:
        cleanup_smoke_user(store, user_id)

    print(
        json.dumps(
            {
                "status": "passed",
                "schemaVersion": 1,
                "blockedAttemptCount": 6,
                "digitalHumanSessionCreated": 0,
                "digitalHumanHeartbeatAccepted": 0,
                "timeLetterCreated": 0,
                "delayedReplyCreated": 0,
                "scheduledDeliveryCount": 0,
                "providerDeliveryAttempted": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
