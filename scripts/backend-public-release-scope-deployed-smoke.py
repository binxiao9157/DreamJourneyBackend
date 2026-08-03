#!/usr/bin/env python3
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").rstrip("/")
API_TOKEN = os.environ.get("BACKEND_API_TOKEN", "").strip()
EXPECTED_MODE = os.environ.get("EXPECTED_RELEASE_POLICY_COMMAND_MODE", "observe").strip()
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
OWNER_CORE = {"echoTextInput", "profileSettings", "legalCenter", "accountDeletion"}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path, *, method="GET", payload=None, extra_headers=None, expected=(200,)):
    headers = {"Accept": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    if extra_headers:
        headers.update(extra_headers)
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(f"{BASE_URL}{path}", headers=headers, data=body, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {key.lower(): value for key, value in error.headers.items()}
        raw = error.read().decode("utf-8", errors="replace")
    require(status in expected, f"{method} {path} expected {expected}, got {status}")
    return status, response_headers, json.loads(raw) if raw else {}


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(API_TOKEN, "BACKEND_API_TOKEN is required")
    require(EXPECTED_MODE in {"observe", "mixed", "enforce"}, "invalid expected command mode")

    query = urllib.parse.urlencode({"audience": "owner", "cohort": "closedPilotAdultSelf", "clientBuild": 1})
    _, _, policy = request_json(f"/v2/release-policy?{query}")
    decisions = policy.get("features") or []
    public_features = {
        item.get("feature")
        for item in decisions
        if item.get("enabled") is True and item.get("releaseVisible") is True
    }
    hidden_features = {item.get("feature") for item in decisions if item.get("enabled") is not True}
    # This deployed smoke uses the backend machine credential. A machine can
    # inspect the policy contract, but must never self-enrol into an Owner
    # closed pilot merely by sending a cohort query parameter.
    require(policy.get("cohort") == "unassigned", "machine caller must not self-enrol into Closed Pilot")
    require(not public_features, "unassigned machine caller must not receive Owner-visible features")
    require(OWNER_CORE.issubset(hidden_features), "Owner core must remain hidden from an unassigned machine caller")
    require(
        bool(policy.get("shadowMode")) is (EXPECTED_MODE != "enforce"),
        "deployed policy shadow mode differs from the expected command mode",
    )

    _, profile_headers, _ = request_json(
        "/profile",
        method="POST",
        payload={},
        extra_headers={"X-DreamJourney-Policy-Audience": "qa"},
        expected=(403,),
    )
    require(
        profile_headers.get("x-dreamjourney-route-auth-decision") == "deny",
        "machine credential must not bypass the user-only profile route",
    )
    require(
        profile_headers.get("x-dreamjourney-route-auth-reason") == "userPrincipalRequired",
        "profile route must reject a machine credential as a user principal",
    )

    _, family_headers, _ = request_json(
        "/family/invite",
        method="POST",
        payload={},
        extra_headers={
            "X-DreamJourney-Policy-Audience": "qa",
            "X-DreamJourney-Feature-Allowed": "true",
        },
        expected=(403,),
    )
    require(
        family_headers.get("x-dreamjourney-route-auth-decision") == "deny",
        "machine credential must not bypass the user-only family route",
    )
    require(
        family_headers.get("x-dreamjourney-route-auth-reason") == "userPrincipalRequired",
        "family route must reject a machine credential as a user principal",
    )

    _, _, unknown = request_json("/v2/release-policy?" + urllib.parse.urlencode({"feature": "futureUnknownFeature"}))
    unknown_decision = (unknown.get("features") or [{}])[0]
    require(unknown_decision.get("enabled") is False, "unknown feature must deny")
    require(unknown_decision.get("reason") == "unknownFeature", "unknown feature reason changed")

    result = {
        "schemaVersion": 1,
        "policyVersion": policy.get("policyVersion"),
        "policyRevision": policy.get("policyRevision"),
        "mode": EXPECTED_MODE,
        "canaryFeatures": sorted(EXPECTED_CANARY),
        "killSwitchFeatures": sorted(EXPECTED_KILL_SWITCH),
        "features": {
            "visibleForUnassignedMachine": sorted(public_features),
            "closedPilotOwnerCore": sorted(OWNER_CORE),
            "hiddenCount": len(hidden_features),
            "unknownDenied": True,
        },
        "routes": {
            "classifiedDecisionCount": len(decisions),
            "hiddenRouteBypassCount": 0,
        },
        "commands": [
            {"feature": "profileSettings", "forgedAudience": "qa", "routeAuthentication": "userPrincipalRequired"},
            {"feature": "familyManagement", "forgedAudience": "qa", "routeAuthentication": "userPrincipalRequired"},
        ],
    }
    if OUTPUT_PATH:
        path = pathlib.Path(OUTPUT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print("Backend Public Release Scope deployed smoke passed")


if __name__ == "__main__":
    main()
