#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", "http://127.0.0.1:3100"),
).rstrip("/")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path, *, expected=200):
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            headers = {key.lower(): value for key, value in response.headers.items()}
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        headers = {key.lower(): value for key, value in error.headers.items()}
        body = error.read().decode("utf-8", errors="replace")
    require(status == expected, f"GET {path} expected {expected}, got {status}")
    require(headers.get("cache-control") == "no-store", f"{path} must be no-store")
    return json.loads(body) if body else {}


def policy_path(**params):
    return "/v2/release-policy?" + urllib.parse.urlencode(params)


def main():
    snapshot = request_json(
        policy_path(
            audience="owner",
            cohort="closedPilotAdultSelf",
            clientBuild=1,
        )
    )
    require(snapshot.get("schemaVersion") == 1, "release policy schema must be v1")
    require(snapshot.get("policyVersion") == "release-policy-v1", "unexpected release policy version")
    require(snapshot.get("policyRevision") == 1, "unexpected release policy revision")
    require(snapshot.get("source") == "server", "release policy must be server sourced")
    require(snapshot.get("shadowMode") is True, "WI-S0-06-01 must remain shadow-only")
    require(snapshot.get("minClient") == 1, "minimum client contract is missing")
    require(snapshot.get("emergencyRevision") == 0, "unexpected emergency revision")

    features = {item.get("feature"): item for item in snapshot.get("features", [])}
    require(features.get("echoTextInput", {}).get("releaseVisible") is True, "owner text core must be explicit")
    for feature in (
        "familyManagement",
        "timeLetters",
        "voiceCloneShell",
        "digitalHumanLivePanel",
        "careDashboard",
    ):
        require(features.get(feature, {}).get("releaseVisible") is False, f"{feature} must remain hidden")

    unknown = request_json(
        policy_path(
            audience="owner",
            cohort="closedPilotAdultSelf",
            clientBuild=1,
            feature="deployedUnknownFeature",
        )
    )
    require(len(unknown.get("features", [])) == 1, "unknown feature response must be scoped")
    decision = unknown["features"][0]
    require(decision.get("releaseVisible") is False, "unknown feature must fail closed")
    require(decision.get("reason") == "unknownFeature", "unknown feature reason must be stable")

    downgrade = request_json(
        policy_path(
            audience="owner",
            cohort="closedPilotAdultSelf",
            clientBuild=1,
            knownPolicyRevision=999,
        ),
        expected=409,
    )
    require(
        (downgrade.get("detail") or {}).get("code") == "release_policy_version_downgrade",
        "version downgrade must return a stable code",
    )
    serialized = json.dumps(snapshot, ensure_ascii=False).lower()
    require("credential" not in serialized and "accesstoken" not in serialized, "policy must be value-free")

    print("Backend release-policy deployed smoke passed: typed shadow + fail-closed decisions")


if __name__ == "__main__":
    main()
