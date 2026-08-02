#!/usr/bin/env python3
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).strip().rstrip("/")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
DIRECT_ISSUE = os.environ.get(
    "BACKEND_AUTH_REFRESH_SMOKE_DIRECT_ISSUE",
    "",
).strip().lower() in {"1", "true", "yes"}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(method, path, *, payload=None, token=None, expected=200):
    headers = {"Accept": "application/json"}
    if path == "/config/runtime":
        headers["X-DreamJourney-Runtime-Contract-Version"] = "2"
        headers["X-DreamJourney-Client-Build"] = "9001"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {key.lower(): value for key, value in error.headers.items()}
        raw = error.read().decode("utf-8", errors="replace")
    require(status == expected, f"{method} {path} expected {expected}, got {status}: {raw}")
    require(response_headers.get("cache-control") == "no-store", f"{path} must be no-store")
    require(response_headers.get("pragma") == "no-cache", f"{path} must be no-cache")
    return json.loads(raw) if raw else {}


def assert_identity_admission_boundary(suffix):
    """Record why this smoke may issue only an in-container fixture session.

    Production intentionally has no synthetic SMS adapter.  Until a real
    identity provider is configured, a deployed refresh smoke must prove that
    both public login paths fail closed and then use its explicit internal
    fixture mode to exercise refresh rotation.  It must never silently fall
    back to legacy phone login.
    """

    runtime = request_json("GET", "/config/runtime")
    identity = (runtime.get("auth") or {}).get("identityChallenge") or {}
    challenge_enabled = identity.get("clientFlowEnabled") is True
    legacy_enabled = identity.get("legacyPhoneLoginEnabled") is True

    if not challenge_enabled:
        target = f"+1555555{secrets.randbelow(10**7):07d}"
        challenge = request_json(
            "POST",
            "/v2/auth/challenges",
            payload={
                "identityType": "phone",
                "target": target,
                "purpose": "login",
            },
            expected=503,
        )
        require(
            (challenge.get("detail") or {}).get("code")
            == "identity_challenge_unavailable",
            "disabled identity provider must fail closed",
        )
        require(target not in json.dumps(challenge, ensure_ascii=False), "challenge leaked target")

    if not legacy_enabled:
        legacy = request_json(
            "POST",
            "/auth/login",
            payload={
                "phone": f"196{secrets.randbelow(10**8):08d}",
                "password": f"retired-login-{suffix}",
            },
            expected=426,
        )
        require(
            (legacy.get("detail") or {}).get("reason") == "legacyIdentityFlowRetired",
            "retired legacy login must not be re-enabled by the refresh smoke",
        )

    if not DIRECT_ISSUE and (not challenge_enabled or not legacy_enabled):
        raise AssertionError(
            "BACKEND_AUTH_REFRESH_SMOKE_DIRECT_ISSUE=1 is required when the "
            "deployed identity provider is intentionally unavailable"
        )
    return {
        "identityChallengeEnabled": challenge_enabled,
        "legacyLoginEnabled": legacy_enabled,
        "fixtureSessionRequired": DIRECT_ISSUE,
    }


def issue_initial_session(suffix):
    if DIRECT_ISSUE:
        from app.main import _auth_session_service, store
        from app.services.store_factory import close_store, init_store

        user_id = f"auth-refresh-smoke-{suffix}"
        init_store(store)
        try:
            return user_id, _auth_session_service().issue(user_id)
        finally:
            close_store(store)

    phone_suffix = f"{secrets.randbelow(10**8):08d}"
    login = request_json(
        "POST",
        "/auth/login",
        payload={
            "phone": f"196{phone_suffix}",
            "nickname": "refresh CAS deployed smoke",
            "password": f"refresh-cas-{suffix}",
        },
    )
    return str(login["user"]["id"]), login["auth"]


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    suffix = secrets.token_hex(6)
    identity_boundary = assert_identity_admission_boundary(suffix)
    user_id, first = issue_initial_session(suffix)
    require(first["contractVersion"] == 2, "login must issue typed session v2")
    require(first["subjectId"] == user_id, "login subject must match canonical user")

    refreshed = request_json(
        "POST",
        "/auth/refresh",
        payload={"refreshToken": first["refreshToken"]},
    )
    second = refreshed["auth"]
    require(second["subjectId"] == first["userId"], "refresh subject must remain stable")
    require(second["userId"] == first["userId"], "refresh user must remain stable")
    require(second["tokenFamilyId"] == first["tokenFamilyId"], "refresh family must remain stable")
    require(second["parentSessionId"] == first["sessionId"], "refresh parent must be captured session")
    require(second["sessionId"] != first["sessionId"], "refresh must rotate session id")
    require(
        second["sessionVersion"] == first["sessionVersion"] + 1,
        "refresh must advance session version by exactly one",
    )

    request_json("GET", "/config/runtime", token=second["accessToken"])
    replay = request_json(
        "POST",
        "/auth/refresh",
        payload={"refreshToken": first["refreshToken"]},
        expected=401,
    )
    detail = replay.get("detail") or {}
    require(detail.get("code") == "refresh_token_reuse_detected", "replay must detect reuse")
    require(detail.get("reauthenticationRequired") is True, "reuse must require reauthentication")
    request_json(
        "GET",
        "/config/runtime",
        token=second["accessToken"],
        expected=401,
    )

    print(
        json.dumps(
            {
                "status": "passed",
                "contractVersion": second["contractVersion"],
                "identityChallengeEnabled": identity_boundary["identityChallengeEnabled"],
                "legacyLoginEnabled": identity_boundary["legacyLoginEnabled"],
                "fixtureSessionRequired": identity_boundary["fixtureSessionRequired"],
                "sessionVersion": second["sessionVersion"],
                "reuseCode": detail["code"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
