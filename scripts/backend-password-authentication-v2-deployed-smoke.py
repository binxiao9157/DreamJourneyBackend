#!/usr/bin/env python3
import json
import os
import secrets
import urllib.error
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).strip().rstrip("/")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(method, path, *, payload=None, expected=200):
    headers = {
        "Accept": "application/json",
        "X-DreamJourney-Runtime-Contract-Version": "2",
        "X-DreamJourney-Client-Build": "9001",
    }
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
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {
            key.lower(): value for key, value in error.headers.items()
        }
        raw = error.read().decode("utf-8", errors="replace")
    require(
        status == expected,
        f"{method} {path} expected {expected}, got {status}: {raw}",
    )
    require(
        response_headers.get("cache-control") == "no-store",
        f"{path} must be no-store",
    )
    return json.loads(raw) if raw else {}


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    ready = request_json("GET", "/ready")
    require(ready.get("status") == "ready", "backend readiness is not ready")

    runtime = request_json("GET", "/config/runtime")
    auth = runtime.get("auth") or {}
    identity = auth.get("identityChallenge") or {}
    password = auth.get("passwordAuthentication") or {}
    require(password.get("contractVersion") == 2, "password contract must be v2")
    require(password.get("implemented") is True, "password contract is missing")
    require(
        password.get("loginEndpoint") == "/v2/auth/password/login",
        "login endpoint drift",
    )
    require(
        password.get("setupEndpoint") == "/v2/auth/password/setup",
        "setup endpoint drift",
    )
    require(
        password.get("changeEndpoint") == "/v2/auth/password/change",
        "change endpoint drift",
    )
    require(
        password.get("resetEndpoint") == "/v2/auth/password/reset",
        "reset endpoint drift",
    )
    for field in (
        "enabled",
        "ready",
        "loginReady",
        "changeReady",
        "setupReady",
        "resetReady",
        "reauthReady",
        "testRecoveryReady",
    ):
        require(type(password.get(field)) is bool, f"{field} must be boolean")
    require(password.get("sessionContractVersion") == 2, "session contract drift")
    if identity.get("productionReady") is True:
        require(password.get("setupReady") is True, "production setup must be ready")
        require(password.get("resetReady") is True, "production reset must be ready")
        require(password.get("reauthReady") is True, "production reauth must be ready")
        require(
            password.get("recoveryMode") == "production",
            "production recovery mode drift",
        )
    else:
        require(
            password.get("setupReady") is False,
            "test OTP must not expose public password setup",
        )
        require(
            password.get("resetReady") is False,
            "test OTP must not expose public password reset",
        )
        require(
            password.get("reauthReady") is False,
            "test OTP must not expose public sensitive reauth",
        )
        if identity.get("testAccountFlowEnabled") is True:
            require(
                password.get("testRecoveryReady") is True,
                "test allowlist recovery must remain diagnosable",
            )
            require(
                password.get("recoveryMode") == "testAllowlist",
                "test allowlist recovery mode drift",
            )

    target = f"+1555{secrets.randbelow(10**7):07d}"
    candidate_password = f"deployed-negative-{secrets.token_hex(10)}"
    failed = request_json(
        "POST",
        "/v2/auth/password/login",
        payload={
            "identityType": "phone",
            "target": target,
            "password": candidate_password,
        },
        expected=401,
    )
    detail = failed.get("detail") or {}
    require(
        detail.get("code") == "password_authentication_failed",
        "password login must use the generic failure code",
    )
    serialized = json.dumps(failed, ensure_ascii=False)
    require(target not in serialized, "password response exposed the identity target")
    require(
        candidate_password not in serialized,
        "password response exposed the credential",
    )

    print(
        json.dumps(
            {
                "status": "passed",
                "contractVersion": password["contractVersion"],
                "loginReady": password["loginReady"],
                "resetReady": password["resetReady"],
                "testRecoveryReady": password["testRecoveryReady"],
                "recoveryMode": password.get("recoveryMode"),
                "recoveryReason": password.get("recoveryReason"),
                "negativeLoginCode": detail["code"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
