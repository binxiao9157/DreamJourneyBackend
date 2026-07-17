#!/usr/bin/env python3
"""Deployed old-client fence smoke using only read and invalid mutation probes."""

import json
import os
import urllib.error
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).rstrip("/")
EXPECTED_MODE = os.environ.get("COMPAT_EXPECTED_MODE", "").strip().lower()
MINIMUM_CLIENT_BUILD = int(os.environ.get("COMPAT_MIN_CLIENT_BUILD", "1"))
USER_ACCESS_TOKEN = os.environ.get("COMPAT_USER_ACCESS_TOKEN", "").strip()
USER_ID = os.environ.get("COMPAT_USER_ID", "").strip()
BACKEND_API_TOKEN = os.environ.get("BACKEND_API_TOKEN", "").strip()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path, *, method="GET", payload=None, headers=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    request_headers.update(headers or {})
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            response_headers = {
                key.lower(): value for key, value in response.headers.items()
            }
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {
            key.lower(): value for key, value in error.headers.items()
        }
        text = error.read().decode("utf-8", errors="replace")
    return status, response_headers, json.loads(text) if text else {}


def require_upgrade_contract(status, body, expected_reason):
    require(status == 426, f"expected 426, got {status}")
    detail = body.get("detail") or {}
    require(detail.get("code") == "upgrade_required", "upgrade code drift")
    require(detail.get("reason") == expected_reason, "upgrade reason drift")
    require(detail.get("retryable") is False, "upgrade retryability drift")
    require(
        detail.get("reauthenticationRequired") is False,
        "upgrade reauthentication contract drift",
    )
    require(
        detail.get("minimumClientBuild") == MINIMUM_CLIENT_BUILD,
        "minimum client build drift",
    )
    require(detail.get("accessMode") == "readOnly", "upgrade access mode drift")


def user_headers(client_build=None):
    headers = {"Authorization": f"Bearer {USER_ACCESS_TOKEN}"}
    if client_build is not None:
        headers["X-DreamJourney-Client-Build"] = str(client_build)
    return headers


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(EXPECTED_MODE in {"observe", "enforce"}, "COMPAT_EXPECTED_MODE is invalid")
    require(MINIMUM_CLIENT_BUILD >= 1, "COMPAT_MIN_CLIENT_BUILD must be positive")
    require(USER_ACCESS_TOKEN, "COMPAT_USER_ACCESS_TOKEN is required")
    require(USER_ID, "COMPAT_USER_ID is required")
    require(BACKEND_API_TOKEN, "BACKEND_API_TOKEN is required")

    ready_status, _, ready = request_json("/ready")
    require(ready_status == 200, f"GET /ready expected 200, got {ready_status}")
    require(ready.get("status") == "ready", "backend readiness is not ready")

    # Missing password fields guarantee that an observe probe cannot mutate state.
    invalid_password_payload = {"userId": USER_ID}
    old_status, old_headers, old_body = request_json(
        "/auth/password",
        method="POST",
        payload=invalid_password_payload,
        headers=user_headers(),
    )
    require(
        old_headers.get("x-dreamjourney-client-compatibility-mode") == EXPECTED_MODE,
        "compatibility mode header drift",
    )
    if EXPECTED_MODE == "enforce":
        require_upgrade_contract(old_status, old_body, "missingClientBuild")
        require(
            old_headers.get("x-dreamjourney-client-compatibility-decision") == "deny",
            "enforce compatibility decision drift",
        )
    else:
        require(old_status != 426, "observe mode unexpectedly enforced an upgrade")
        require(old_status >= 400, "invalid mutation probe unexpectedly succeeded")
        require(
            old_headers.get("x-dreamjourney-client-compatibility-decision")
            == "observeDeny",
            "observe compatibility decision drift",
        )

    supported_status, supported_headers, _ = request_json(
        "/auth/password",
        method="POST",
        payload=invalid_password_payload,
        headers=user_headers(MINIMUM_CLIENT_BUILD),
    )
    require(supported_status != 426, "minimum supported build was fenced")
    require(supported_status >= 400, "invalid supported-build probe unexpectedly succeeded")
    require(
        supported_headers.get("x-dreamjourney-client-compatibility-decision") == "allow",
        "supported build compatibility decision drift",
    )

    read_status, read_headers, _ = request_json(
        f"/profile/{USER_ID}",
        headers=user_headers(),
    )
    require(read_status != 426, "read-only route was fenced")
    require(
        read_headers.get("x-dreamjourney-client-compatibility-reason")
        == "readOnlyMethod",
        "read-only compatibility reason drift",
    )
    head_status, head_headers, _ = request_json(
        f"/profile/{USER_ID}",
        method="HEAD",
        headers=user_headers(),
    )
    require(head_status != 426, "HEAD read-only route was fenced")
    require(
        head_headers.get("x-dreamjourney-client-compatibility-reason")
        == "readOnlyMethod",
        "HEAD compatibility reason drift",
    )

    for machine_headers in (
        {"Authorization": f"Bearer {BACKEND_API_TOKEN}"},
        {"X-DreamJourney-Api-Token": BACKEND_API_TOKEN},
    ):
        machine_status, machine_response_headers, _ = request_json(
            "/auth/password",
            method="POST",
            payload=invalid_password_payload,
            headers=machine_headers,
        )
        require(machine_status == 403, "machine principal crossed a user route")
        require(
            machine_response_headers.get("x-dreamjourney-route-auth-reason")
            == "userPrincipalRequired",
            "machine terminal-deny reason drift",
        )

    for path in ("/auth/login", "/auth/restore"):
        legacy_status, _, legacy_body = request_json(
            path,
            method="POST",
            payload={},
        )
        require_upgrade_contract(
            legacy_status,
            legacy_body,
            "legacyIdentityFlowRetired",
        )

    observations_status, _, observations = request_json(
        "/ops/release-policy/observations",
        headers={"Authorization": f"Bearer {BACKEND_API_TOKEN}"},
    )
    require(observations_status == 200, "machine observations endpoint is unavailable")
    compatibility = observations.get("clientCompatibility") or {}
    require(compatibility.get("valueFree") is True, "compatibility metrics are not value-free")
    require(
        compatibility.get("minimumClientBuild") == MINIMUM_CLIENT_BUILD,
        "observed minimum client build drift",
    )
    require(
        int(compatibility.get("upgradeRequired426Count") or 0) >= 2,
        "426 compatibility count did not include legacy probes",
    )

    print(
        "Backend client compatibility deployed smoke passed: "
        f"mode={EXPECTED_MODE}, mutation probes were non-persistent"
    )


if __name__ == "__main__":
    main()
