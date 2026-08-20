#!/usr/bin/env python3
import json
import os
import secrets
import urllib.error
import urllib.request

from app.core.config import settings
from app.services.auth_sessions import AuthSessionService
from app.services.postgres_store import PostgresStore


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).rstrip("/")
API_TOKEN = os.environ.get(
    "BACKEND_API_TOKEN",
    os.environ.get("DREAMJOURNEY_BACKEND_API_TOKEN", ""),
).strip()
EXPECTED_MODE = os.environ.get("EXPECTED_RELEASE_POLICY_COMMAND_MODE", "observe").strip()
EXPECTED_CANARY = {
    item.strip()
    for item in os.environ.get("EXPECTED_RELEASE_POLICY_CANARY_FEATURES", "").split(",")
    if item.strip()
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(
    path,
    *,
    method="GET",
    payload=None,
    expected_statuses=(200,),
    extra_headers=None,
    access_token=None,
):
    headers = {"Accept": "application/json"}
    body = None
    request_token = access_token or API_TOKEN
    if request_token:
        headers["Authorization"] = f"Bearer {request_token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers=headers,
        data=body,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {
            key.lower(): value for key, value in error.headers.items()
        }
        response_body = error.read().decode("utf-8", errors="replace")
    require(
        status in expected_statuses,
        f"{method} {path} expected {expected_statuses}, got {status}",
    )
    parsed = json.loads(response_body) if response_body else {}
    return status, response_headers, parsed


def issue_smoke_user_session():
    database_url = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(database_url, "DATABASE_URL is required for user-route smoke")
    store = PostgresStore(
        dsn=database_url,
        pool_min_size=1,
        pool_max_size=2,
        pool_timeout_seconds=2.0,
    )
    store.open_pool(wait=True)
    user = store.upsert_user(
        phone=f"196{secrets.randbelow(10**8):08d}",
        nickname="release policy command smoke",
    )
    user_id = str(user.get("id") or "").strip()
    require(user_id, "release-policy smoke user id missing")
    auth = AuthSessionService(
        store,
        access_ttl_seconds=300,
        refresh_ttl_seconds=900,
    ).issue(user_id)
    access_token = str(auth.get("accessToken") or "").strip()
    require(access_token.startswith("dja_"), "release-policy smoke access token missing")
    return store, user_id, access_token


def cleanup_smoke_user(store, user_id):
    if store is None:
        return
    try:
        if user_id:
            with store.request_unit_of_work(
                correlation_id="release-policy-command-smoke-cleanup",
                command_id="cleanupReleasePolicyCommandSmoke",
            ) as unit_of_work:
                with unit_of_work.connection.cursor() as cursor:
                    cursor.execute("DELETE FROM session_events WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM token_families WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
    finally:
        store.close_pool()


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(API_TOKEN, "BACKEND_API_TOKEN is required")
    require(EXPECTED_MODE in {"observe", "mixed", "enforce"}, "unexpected command mode")

    _, _, runtime = request_json(
        "/config/runtime",
        extra_headers={
            "X-DreamJourney-Runtime-Contract-Version": "2",
            "X-DreamJourney-Client-Build": "9001",
        },
    )
    descriptor = runtime.get("releasePolicy") or {}
    require(
        descriptor.get("commandMode") == EXPECTED_MODE,
        "runtime release-policy command mode does not match deployment expectation",
    )
    require(
        descriptor.get("shadowMode") is (EXPECTED_MODE != "enforce"),
        "runtime shadowMode must match command mode",
    )
    if EXPECTED_MODE == "mixed":
        require(
            set(descriptor.get("canaryFeatures") or []) == EXPECTED_CANARY,
            "runtime canary feature set does not match deployment expectation",
        )

    store = None
    user_id = ""
    try:
        store, user_id, user_access_token = issue_smoke_user_session()
        status, headers, _ = request_json(
            "/profile",
            method="POST",
            payload={"userId": user_id},
            expected_statuses=(400,),
            extra_headers={
                "X-DreamJourney-Policy-Audience": "qa",
                "X-DreamJourney-Feature": "profileSettings",
                "X-DreamJourney-Feature-Allowed": "true",
            },
            access_token=user_access_token,
        )
        require(status == 400, "profile fixture should reach validation without persistence")
        require(
            headers.get("x-dreamjourney-release-policy-feature") == "profileSettings",
            "profile command must be classified as profileSettings",
        )
        require(
            headers.get("x-dreamjourney-release-policy-decision") == "observeDeny",
            "forged QA metadata must not grant profile command authority",
        )
        require(
            headers.get("x-dreamjourney-release-policy-reason") == "missingCapturedPolicy",
            "profile command must preserve the missing server capture reason",
        )
        require(
            headers.get("x-dreamjourney-release-policy-mode") == "observe",
            "non-canary profile command must remain observe-only",
        )
        require(
            headers.get("x-dreamjourney-release-policy-decision-id") == "none",
            "denied client metadata must not be promoted to a server decision id",
        )

        family_mode = (
            "enforce"
            if EXPECTED_MODE == "enforce" or "familyManagement" in EXPECTED_CANARY
            else "observe"
        )
        expected_family_statuses = (400,) if family_mode == "observe" else (403,)
        status, headers, payload = request_json(
            "/family/invite",
            method="POST",
            payload={"userId": user_id, "personaScope": "invalid"},
            expected_statuses=expected_family_statuses,
            access_token=user_access_token,
        )
        expected_decision = "observeDeny" if family_mode == "observe" else "deny"
        require(
            headers.get("x-dreamjourney-release-policy-feature") == "familyManagement",
            "family command must be classified as familyManagement",
        )
        require(
            headers.get("x-dreamjourney-release-policy-decision") == expected_decision,
            "hidden command decision must match observe/enforce mode",
        )
        require(
            headers.get("x-dreamjourney-release-policy-reason") == "notApprovedForClosedPilot",
            "hidden command must preserve the server denial reason",
        )
        require(
            headers.get("x-dreamjourney-release-policy-mode") == family_mode,
            "hidden command mode must match feature rollout",
        )
        if family_mode == "enforce":
            detail = payload.get("detail") or {}
            require(detail.get("code") == "release_policy_denied", "enforce must return stable denial")
    finally:
        cleanup_smoke_user(store, user_id)

    print(
        "Backend release-policy command deployed smoke passed: "
        f"mode={EXPECTED_MODE} forgedQA=owner core=allow hidden={expected_decision}"
    )


if __name__ == "__main__":
    main()
