#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.request


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
):
    headers = {"Accept": "application/json"}
    body = None
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
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

    status, headers, _ = request_json(
        "/profile",
        method="POST",
        payload={},
        expected_statuses=(400,),
        extra_headers={
            "X-DreamJourney-Policy-Audience": "qa",
            "X-DreamJourney-Feature": "profileSettings",
            "X-DreamJourney-Feature-Allowed": "true",
        },
    )
    require(status == 400, "profile fixture should reach validation without persistence")
    require(
        headers.get("x-dreamjourney-release-policy-feature") == "profileSettings",
        "profile command must be classified as profileSettings",
    )
    require(
        headers.get("x-dreamjourney-release-policy-decision") == "allow",
        "production must normalize a forged QA audience to the owner core policy",
    )
    require(
        headers.get("x-dreamjourney-release-policy-decision-id", "").startswith("server:"),
        "system command must expose a value-free server decision identifier",
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
        payload={},
        expected_statuses=expected_family_statuses,
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

    print(
        "Backend release-policy command deployed smoke passed: "
        f"mode={EXPECTED_MODE} forgedQA=owner core=allow hidden={expected_decision}"
    )


if __name__ == "__main__":
    main()
