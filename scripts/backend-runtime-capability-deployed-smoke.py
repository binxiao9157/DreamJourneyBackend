#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", "http://127.0.0.1:3100"),
).rstrip("/")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_runtime():
    request = urllib.request.Request(
        f"{BASE_URL}/config/runtime",
        headers={
            "Accept": "application/json",
            "X-DreamJourney-Runtime-Contract-Version": "2",
            "X-DreamJourney-Client-Build": "9001",
        },
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
    require(status == 200, f"GET /config/runtime expected 200, got {status}")
    require(headers.get("cache-control") == "no-store", "/config/runtime must be no-store")
    return json.loads(body)


def main():
    runtime = request_runtime()
    require(runtime.get("capabilitySnapshotSchemaVersion") == 1, "snapshot schema must be v1")
    snapshots = runtime.get("capabilitySnapshots") or {}
    required = (
        "archiveImageAnalysis",
        "archiveAudioUpload",
        "archiveVideoUpload",
        "timeLetters",
        "familyManagement",
        "familySpace",
        "voiceCloneShell",
        "digitalHumanLivePanel",
    )
    axes = (
        "implemented",
        "enabled",
        "providerReady",
        "releaseVisible",
        "externalVerified",
    )
    for capability in required:
        snapshot = snapshots.get(capability) or {}
        require(snapshot.get("schemaVersion") == 1, f"{capability} schema is incomplete")
        require(snapshot.get("capability") == capability, f"{capability} identity mismatch")
        for axis in axes:
            require(type(snapshot.get(axis)) is bool, f"{capability}.{axis} must be bool")
        for field in ("provider", "fallbackMode", "reason", "evidenceTimestamp"):
            require(field in snapshot, f"{capability}.{field} is missing")

    image = snapshots["archiveImageAnalysis"]
    if image["provider"] == "deepseek/text-only":
        require(image["providerReady"] is False, "text-only provider must not claim vision readiness")
        require(image["reason"] in {"providerVisionUnsupported", "runtimeDisabled"}, "image reason changed")

    for capability in ("archiveAudioUpload", "archiveVideoUpload"):
        media = snapshots[capability]
        require(media["provider"] == "mockObjectStorage", f"{capability} provider changed unexpectedly")
        require(media["providerReady"] is False, f"{capability} mock provider must not be ready")
        require(media["releaseVisible"] is False, f"{capability} must remain release hidden")

    for capability in ("voiceCloneShell", "digitalHumanLivePanel"):
        snapshot = snapshots[capability]
        require(snapshot["releaseVisible"] is False, f"{capability} must remain release hidden")
        require(snapshot["externalVerified"] is False, f"{capability} cannot self-sign G3/G4")

    serialized = json.dumps(runtime, ensure_ascii=False).lower()
    for forbidden in ("secretkey", "accesskey", "accesstoken", "x-api-key"):
        require(forbidden not in serialized, f"runtime response exposed forbidden field: {forbidden}")

    print("Backend runtime capability deployed smoke passed: five axes remain independent")


if __name__ == "__main__":
    main()
