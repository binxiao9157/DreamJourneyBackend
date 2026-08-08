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
    policy_revision = snapshot.get("policyRevision")
    require(isinstance(policy_revision, int) and policy_revision >= 1, "invalid release policy revision")
    require(snapshot.get("source") == "server", "release policy must be server sourced")
    require(snapshot.get("shadowMode") is True, "WI-S0-06-01 must remain shadow-only")
    require(isinstance(snapshot.get("minClient"), int), "minimum client contract is missing")
    require(
        isinstance(snapshot.get("emergencyRevision"), int),
        "emergency revision contract is missing",
    )
    require(snapshot.get("cohort") == "unassigned", "client query must not self-enroll a cohort")

    features = {item.get("feature"): item for item in snapshot.get("features", [])}
    require(
        features.get("echoTextInput", {}).get("releaseVisible") is False,
        "unassigned caller must remain outside owner text core",
    )
    for feature in (
        "familyManagement",
        "timeLetters",
        "voiceCloneShell",
        "digitalHumanLivePanel",
        "careDashboard",
    ):
        require(features.get(feature, {}).get("releaseVisible") is False, f"{feature} must remain hidden")

    capture = features.get("ownerMediaCaptureV1", {})
    processing = features.get("ownerMediaProcessingV1", {})
    data_export = features.get("accountDataExport", {})
    require(capture.get("requiredCapability") == "ownerTruthMediaStorage", "capture capability binding missing")
    require(
        processing.get("requiredCapability") == "ownerTruthMediaProcessing",
        "processing capability binding missing",
    )
    require(isinstance(capture.get("capabilityReady"), bool), "capture capability state missing")
    require(isinstance(processing.get("capabilityReady"), bool), "processing capability state missing")
    require(data_export.get("releaseStage") == "M0", "data export must have an independent M0 decision")
    require(data_export.get("requiredCapability") is None, "data export must not inherit media capability")

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
            knownPolicyRevision=policy_revision + 1,
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
