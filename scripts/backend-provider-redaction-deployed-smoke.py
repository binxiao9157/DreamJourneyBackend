#!/usr/bin/env python3
"""Verify deployed provider dry-run responses remain value-free.

The smoke never invokes an external provider. It sends distinct canaries to
the deployed dry-run routes and asserts that only the allowlisted diagnostics
contract returns. Its output deliberately contains counts and state only.
"""

from __future__ import annotations

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
).strip().rstrip("/")
POLICY_VERSION = "providerDryRun-v2"
CANARIES = (
    "KB_TRANSCRIPT_CANARY",
    "KB_SUMMARY_CANARY",
    "IMAGE_BASE64_CANARY",
    "TTS_TEXT_CANARY",
    "MAP_QUERY_CANARY",
    "qa-provider-redaction-user",
    "qa-provider-redaction-archive",
    "QA_VOICE_TYPE",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def request_json(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    access_token: str | None = None,
) -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
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
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        raw_body = error.read().decode("utf-8")
    try:
        body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as error:
        raise AssertionError(f"{method} {path} returned non-JSON") from error
    return status, body


def issue_smoke_access_token() -> tuple[PostgresStore, str, str]:
    """Use a typed user principal for user-owned provider routes.

    A machine service token must not be able to call `/kb/extract`,
    `/archive/image-analysis`, or `/tts` in production enforcement mode.
    The temporary smoke account is intentionally not printed or exported, and
    is removed after the smoke completes. Issuing an opaque session directly
    keeps this operational smoke independent from the separately configured
    phone-identity provider.
    """

    database_url = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(database_url, "DATABASE_URL is required for user-route smoke")
    store = PostgresStore(
        dsn=database_url,
        pool_min_size=1,
        pool_max_size=2,
        pool_timeout_seconds=2.0,
    )
    store.open_pool(wait=True)
    phone_suffix = f"{secrets.randbelow(10**8):08d}"
    user = store.upsert_user(
        phone=f"196{phone_suffix}",
        nickname="provider redaction smoke",
    )
    user_id = str(user.get("id") or "").strip()
    require(user_id, "smoke user id missing")
    auth = AuthSessionService(
        store,
        access_ttl_seconds=300,
        refresh_ttl_seconds=900,
    ).issue(user_id)
    access_token = str(auth.get("accessToken") or "").strip()
    require(access_token.startswith("dja_"), "smoke user access token missing")
    return store, user_id, access_token


def cleanup_smoke_user(store: PostgresStore | None, user_id: str) -> None:
    if store is None:
        return
    try:
        if user_id:
            with store.request_unit_of_work(
                correlation_id="provider-redaction-smoke-cleanup",
                command_id="cleanupProviderRedactionSmoke",
            ) as unit_of_work:
                with unit_of_work.connection.cursor() as cursor:
                    cursor.execute("DELETE FROM session_events WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM token_families WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
    finally:
        store.close_pool()


def main() -> None:
    require(BASE_URL, "BACKEND_BASE_URL is required")

    ready_status, ready = request_json("GET", "/ready")
    require(ready_status == 200 and ready.get("status") == "ready", "deployed service is not ready")
    store: PostgresStore | None = None
    user_id = ""
    try:
        store, user_id, access_token = issue_smoke_access_token()
        surfaces = (
            (
                "POST",
                "/kb/extract?dryRun=true",
                {
                    "userId": user_id,
                    "transcript": "KB_TRANSCRIPT_CANARY",
                    "existingSummary": "KB_SUMMARY_CANARY",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            ),
            (
                "POST",
                "/archive/image-analysis?dryRun=true",
                {
                    "userId": user_id,
                    "archiveItemId": "qa-provider-redaction-archive",
                    "imageBase64": "IMAGE_BASE64_CANARY",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            ),
            (
                "POST",
                "/tts?dryRun=true",
                {
                    "userId": user_id,
                    "text": "TTS_TEXT_CANARY",
                    "voiceType": "QA_VOICE_TYPE",
                },
            ),
            ("GET", "/maps/district?dryRun=true&keyword=MAP_QUERY_CANARY", None),
        )

        outcomes = []
        for method, path, payload in surfaces:
            status, body = request_json(
                method,
                path,
                payload=payload,
                access_token=access_token,
            )
            outcomes.append(
                assert_value_free_response(status, body, surface=f"{method} {path}")
            )
        print(
            json.dumps(
                {
                    "policyVersion": POLICY_VERSION,
                    "providerDryRunReports": outcomes.count("report"),
                    "safePolicyDenials": outcomes.count("policyDenied"),
                    "safeProviderErrors": outcomes.count("safeError"),
                    "status": "passed",
                    "surfaces": len(surfaces),
                    "userRouteAuthentication": "opaqueAccessToken",
                },
                sort_keys=True,
            )
        )
    finally:
        cleanup_smoke_user(store, user_id)


def assert_value_free_response(status: int, body: dict, *, surface: str) -> str:
    serialized = json.dumps(body, ensure_ascii=False, sort_keys=True)
    require(not any(canary in serialized for canary in CANARIES), "private dry-run value leaked")
    if status == 200:
        report = body.get("dryRun") or {}
        require("request" not in body, "dry-run response must not expose upstream request")
        require("context" not in body, "dry-run response must not expose request context")
        require(
            report.get("redactionPolicyVersion") == POLICY_VERSION,
            "deployed dry-run policy version missing",
        )
        require(report.get("schemaVersion") == 1, "deployed dry-run schema version missing")
        require((report.get("transport") or {}).get("payloadIncluded") is False, "payloadIncluded must be false")
        return "report"

    if status == 403:
        detail = body.get("detail") or {}
        require(isinstance(detail, dict), "policy denial detail must be structured")
        require(
            detail.get("code") == "release_policy_command_denied",
            f"{surface} returned an unexpected denial contract",
        )
        return "policyDenied"

    require(
        status in {400, 502, 503},
        f"{surface} returned unexpected dry-run status {status}",
    )
    detail = body.get("detail") or {}
    require(isinstance(detail, dict), "provider error detail must be structured")
    require(
        detail.get("redactionPolicyVersion") == POLICY_VERSION,
        "provider error redaction policy version missing",
    )
    return "safeError"


if __name__ == "__main__":
    main()
