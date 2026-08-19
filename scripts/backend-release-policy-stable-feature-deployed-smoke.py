#!/usr/bin/env python3
import json
import os
import urllib.parse
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", ""),
).rstrip("/")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path):
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        require(response.status == 200, f"GET {path} expected 200")
        require(response.headers.get("Cache-Control") == "no-store", f"{path} must be no-store")
        return json.loads(response.read().decode("utf-8"))


def policy_path(*, audience, feature):
    return "/v2/release-policy?" + urllib.parse.urlencode(
        {
            "audience": audience,
            "cohort": "authenticatedOwner",
            "clientBuild": 9001,
            "feature": feature,
        }
    )


def decision(*, audience, feature):
    payload = request_json(policy_path(audience=audience, feature=feature))
    features = payload.get("features") or []
    require(len(features) == 1, f"{feature} response must contain one decision")
    return features[0]


def assert_alias(*, audience, alias, canonical):
    legacy = decision(audience=audience, feature=alias)
    stable = decision(audience=audience, feature=canonical)
    require(legacy.get("feature") == alias, f"{alias} must preserve the legacy response name")
    require(stable.get("feature") == canonical, f"{canonical} must preserve the stable response name")
    comparable = (
        "enabled",
        "releaseVisible",
        "requiredGates",
        "reason",
        "requiredCapability",
        "capabilityReady",
    )
    for field in comparable:
        require(
            legacy.get(field) == stable.get(field),
            f"{alias} must resolve to {canonical} for {field}",
        )


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    runtime = request_json("/config/runtime")
    descriptor = runtime.get("releasePolicy") or {}
    require(
        descriptor.get("featureAliasSunsetAt") == "2026-11-30T00:00:00+00:00",
        "legacy feature alias sunset must be explicit",
    )
    require(
        descriptor.get("defaultClosedStageEffectsEnforced") is False,
        "stage labels must not control feature authority",
    )
    require(
        descriptor.get("defaultClosedFeatureEffectsEnforced") is True,
        "stable default-closed features must remain enforced",
    )
    aliases = descriptor.get("featureAliases") or {}
    require(
        aliases.get("visitorAccess")
        == ["publicationGrantManagement", "publicationVisitor"],
        "visitorAccess compatibility mapping must remain audience-scoped",
    )
    stable_features = set(descriptor.get("defaultClosedFeatures") or [])
    require(
        {"publication", "publicationGrantManagement", "publicationVisitor"}
        <= stable_features,
        "stable publication features must remain independently default closed",
    )

    assert_alias(
        audience="owner",
        alias="publicationManagementM2",
        canonical="publication",
    )
    assert_alias(
        audience="owner",
        alias="publicationGrantManagementM2",
        canonical="publicationGrantManagement",
    )
    assert_alias(
        audience="visitor",
        alias="publicationVisitorM2",
        canonical="publicationVisitor",
    )
    assert_alias(
        audience="owner",
        alias="visitorAccess",
        canonical="publicationGrantManagement",
    )
    assert_alias(
        audience="visitor",
        alias="visitorAccess",
        canonical="publicationVisitor",
    )

    unknown = decision(audience="owner", feature="futurePublicationFeature")
    require(unknown.get("enabled") is False, "unknown feature must be disabled")
    require(unknown.get("reason") == "unknownFeature", "unknown feature must fail closed")
    print(
        "Backend stable release-policy feature deployed smoke passed: "
        "new names, bounded aliases and stage-independent authority verified"
    )


if __name__ == "__main__":
    main()
