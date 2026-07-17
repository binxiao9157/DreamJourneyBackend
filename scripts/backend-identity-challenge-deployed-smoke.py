#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", "http://127.0.0.1:3100"),
).rstrip("/")
TEST_TARGET = "+15555550123"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path, *, method="GET", payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-DreamJourney-Runtime-Contract-Version": "2",
            "X-DreamJourney-Client-Build": "9001",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            headers = {key.lower(): value for key, value in response.headers.items()}
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        headers = {key.lower(): value for key, value in error.headers.items()}
        text = error.read().decode("utf-8", errors="replace")
    return status, headers, json.loads(text)


def main():
    ready_status, _, ready = request_json("/ready")
    require(ready_status == 200, f"GET /ready expected 200, got {ready_status}")
    require(ready.get("status") == "ready", "backend readiness is not ready")
    components = {
        item.get("component"): item.get("status")
        for item in ready.get("components") or []
    }
    require(components.get("database") == "ready", "database readiness failed")
    require(components.get("schema") == "ready", "schema head is not ready")

    runtime_status, runtime_headers, runtime = request_json("/config/runtime")
    require(runtime_status == 200, f"GET /config/runtime expected 200, got {runtime_status}")
    require(runtime_headers.get("cache-control") == "no-store", "runtime must be no-store")
    identity = ((runtime.get("auth") or {}).get("identityChallenge") or {})
    require(identity.get("contractVersion") == 1, "identity challenge contract must be v1")
    require(identity.get("challengeEndpoint") == "/v2/auth/challenges", "challenge endpoint drift")
    require(
        identity.get("verifyEndpointTemplate")
        == "/v2/auth/challenges/{challengeId}/verify",
        "verify endpoint drift",
    )
    require(identity.get("providerMode") == "unavailable", "production provider must remain unavailable")
    require(identity.get("productionReady") is False, "production readiness must remain false")
    require(identity.get("clientFlowEnabled") is False, "unverified production client flow must remain disabled")
    require(identity.get("internalVerificationEnabled") is False, "synthetic verification leaked into production")

    challenge_status, challenge_headers, challenge = request_json(
        "/v2/auth/challenges",
        method="POST",
        payload={
            "identityType": "phone",
            "target": TEST_TARGET,
            "purpose": "login",
        },
    )
    require(challenge_status == 503, f"production challenge expected 503, got {challenge_status}")
    require(challenge_headers.get("cache-control") == "no-store", "challenge response must be no-store")
    detail = challenge.get("detail") or {}
    require(detail.get("code") == "identity_challenge_unavailable", "challenge failure code drift")
    serialized = json.dumps(challenge, ensure_ascii=False)
    require(TEST_TARGET not in serialized, "challenge response exposed the identity target")
    require("synthetic" not in serialized.lower(), "challenge response exposed synthetic internals")

    verify_status, _, verify = request_json(
        "/v2/auth/challenges/ach_deployed_smoke_missing/verify",
        method="POST",
        payload={"code": "000000"},
    )
    require(verify_status == 503, f"disabled-provider verify expected 503, got {verify_status}")
    require(
        (verify.get("detail") or {}).get("code") == "identity_challenge_unavailable",
        "disabled-provider verify failure code drift",
    )

    legacy_status, _, legacy = request_json(
        "/auth/login",
        method="POST",
        payload={"phone": TEST_TARGET, "password": "not-a-real-password"},
    )
    require(legacy_status == 426, f"legacy login expected 426, got {legacy_status}")
    legacy_detail = legacy.get("detail") or {}
    require(legacy_detail.get("code") == "upgrade_required", "legacy upgrade code drift")
    require(
        legacy_detail.get("reason") == "legacyIdentityFlowRetired",
        "legacy login retirement reason drift",
    )
    require(legacy_detail.get("retryable") is False, "legacy retryability drift")
    require(
        legacy_detail.get("reauthenticationRequired") is False,
        "legacy reauthentication contract drift",
    )
    require(legacy_detail.get("accessMode") == "readOnly", "legacy access mode drift")
    require(TEST_TARGET not in json.dumps(legacy), "legacy response exposed identity target")

    print("Backend identity challenge deployed smoke passed: production remains fail-closed")


if __name__ == "__main__":
    main()
