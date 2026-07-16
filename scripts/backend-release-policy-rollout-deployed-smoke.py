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
EXPECTED_CANARY = {
    item.strip()
    for item in os.environ.get("EXPECTED_RELEASE_POLICY_CANARY_FEATURES", "").split(",")
    if item.strip()
}
EXPECTED_KILL_SWITCH = {
    item.strip()
    for item in os.environ.get("EXPECTED_RELEASE_POLICY_KILL_SWITCH_FEATURES", "").split(",")
    if item.strip()
}
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()


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
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    if extra_headers:
        headers.update(extra_headers)
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
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


def assert_enforced(feature, path, payload):
    status, headers, body = request_json(
        path,
        method="POST",
        payload=payload,
        expected_statuses=(403,),
        extra_headers={
            "X-DreamJourney-Client-Build": "9001",
            "X-DreamJourney-Runtime-Contract-Version": "2",
        },
    )
    detail = body.get("detail") or {}
    require(status == 403, f"{feature} must be denied")
    require(detail.get("code") == "release_policy_denied", f"{feature} denial contract")
    require(detail.get("feature") == feature, f"{feature} classification")
    require(
        headers.get("x-dreamjourney-release-policy-mode") == "enforce",
        f"{feature} must be enforced",
    )


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(API_TOKEN, "BACKEND_API_TOKEN is required")

    _, runtime_headers, runtime = request_json(
        "/config/runtime",
        extra_headers={
            "X-DreamJourney-Client-Build": "9001",
            "X-DreamJourney-Runtime-Contract-Version": "2",
        },
    )
    descriptor = runtime.get("releasePolicy") or {}
    require(runtime_headers.get("cache-control") == "no-store", "runtime must be no-store")
    require(descriptor.get("rolloutContractVersion") == 1, "rollout contract version")
    require(descriptor.get("runtimeContractVersion") == 2, "runtime contract version")
    require(
        set(descriptor.get("canaryFeatures") or []) == EXPECTED_CANARY,
        "deployed canary features do not match expectation",
    )
    require(
        set(descriptor.get("killSwitchFeatures") or []) == EXPECTED_KILL_SWITCH,
        "deployed kill-switch features do not match expectation",
    )

    if "familyManagement" in EXPECTED_CANARY | EXPECTED_KILL_SWITCH:
        assert_enforced("familyManagement", "/family/invite", {})
    if "digitalHumanLivePanel" in EXPECTED_CANARY | EXPECTED_KILL_SWITCH:
        assert_enforced(
            "digitalHumanLivePanel",
            "/digital-human/sessions",
            {"userId": "rollout-smoke", "personaId": "rollout-smoke"},
        )

    _, observation_headers, observations = request_json(
        "/ops/release-policy/observations"
    )
    require(
        observation_headers.get("cache-control") == "no-store",
        "observation summary must be no-store",
    )
    require(
        observations.get("typedRuntimeContractHitCount", 0) >= 1,
        "typed runtime contract hit must be observed",
    )
    serialized = json.dumps(observations, ensure_ascii=False).lower()
    for forbidden in ("userid", "phone", "token", "authorization", "requestbody"):
        require(forbidden not in serialized, f"observation summary leaked {forbidden}")

    result = {
        "status": "passed",
        "policyVersion": descriptor.get("policyVersion"),
        "policyRevision": descriptor.get("policyRevision"),
        "emergencyRevision": descriptor.get("emergencyRevision"),
        "commandMode": descriptor.get("commandMode"),
        "canaryFeatures": sorted(EXPECTED_CANARY),
        "killSwitchFeatures": sorted(EXPECTED_KILL_SWITCH),
        "typedRuntimeContractHitCount": observations.get(
            "typedRuntimeContractHitCount",
            0,
        ),
        "legacyRuntimeAliasHitCount": observations.get(
            "legacyRuntimeAliasHitCount",
            0,
        ),
    }
    if OUTPUT_PATH:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print(
        "Backend release-policy rollout deployed smoke passed: "
        f"mode={result['commandMode']} canary={len(EXPECTED_CANARY)} "
        f"killSwitch={len(EXPECTED_KILL_SWITCH)}"
    )


if __name__ == "__main__":
    main()
