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
    require(EXPECTED_MODE in {"observe", "enforce"}, "invalid expected command mode")

    query = urllib.parse.urlencode({"audience": "owner", "cohort": "closedPilotAdultSelf", "clientBuild": 1})
    _, _, policy = request_json(f"/v2/release-policy?{query}")
    decisions = policy.get("features") or []
    public_features = {
        item.get("feature")
        for item in decisions
        if item.get("enabled") is True and item.get("releaseVisible") is True
    }
    hidden_features = {item.get("feature") for item in decisions if item.get("enabled") is not True}
    require(public_features == OWNER_CORE, "deployed owner core differs from Closed Pilot baseline")
    require(hidden_features.isdisjoint(OWNER_CORE), "owner core must not be hidden")

    _, profile_headers, _ = request_json(
        "/profile",
        method="POST",
        payload={},
        extra_headers={"X-DreamJourney-Policy-Audience": "qa"},
        expected=(400,),
    )
    require(profile_headers.get("x-dreamjourney-release-policy-decision") == "allow", "forged QA must normalize to owner core")

    expected_family_status = (400,) if EXPECTED_MODE == "observe" else (403,)
    _, family_headers, _ = request_json(
        "/family/invite",
        method="POST",
        payload={},
        extra_headers={
            "X-DreamJourney-Policy-Audience": "qa",
            "X-DreamJourney-Feature-Allowed": "true",
        },
        expected=expected_family_status,
    )
    hidden_decision = "observeDeny" if EXPECTED_MODE == "observe" else "deny"
    require(family_headers.get("x-dreamjourney-release-policy-decision") == hidden_decision, "hidden command must remain denied")

    _, _, unknown = request_json("/v2/release-policy?" + urllib.parse.urlencode({"feature": "futureUnknownFeature"}))
    unknown_decision = (unknown.get("features") or [{}])[0]
    require(unknown_decision.get("enabled") is False, "unknown feature must deny")
    require(unknown_decision.get("reason") == "unknownFeature", "unknown feature reason changed")

    result = {
        "schemaVersion": 1,
        "policyVersion": policy.get("policyVersion"),
        "policyRevision": policy.get("policyRevision"),
        "mode": EXPECTED_MODE,
        "features": {
            "publicOwnerCore": sorted(public_features),
            "hiddenCount": len(hidden_features),
            "unknownDenied": True,
        },
        "routes": {
            "classifiedDecisionCount": len(decisions),
            "hiddenRouteBypassCount": 0,
        },
        "commands": [
            {"feature": "profileSettings", "forgedAudience": "qa", "effectiveAudience": "owner", "decision": "allow"},
            {"feature": "familyManagement", "forgedAudience": "qa", "decision": hidden_decision},
        ],
    }
    if OUTPUT_PATH:
        path = pathlib.Path(OUTPUT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print("Backend Public Release Scope deployed smoke passed")


if __name__ == "__main__":
    main()
