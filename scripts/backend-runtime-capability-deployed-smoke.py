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
        "kbliteUserSurface",
        "accountDataExport",
        "ownerTruthMediaStorage",
        "ownerTruthMediaProcessing",
        "identityChallenge",
        "timeLetters",
        "echoDelayedReplies",
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
        for field in (
            "provider",
            "fallbackMode",
            "reason",
            "evidenceTimestamp",
            "providerKind",
            "operation",
            "dataClass",
            "region",
            "retentionPolicyVersion",
            "configurationStatus",
            "evidenceStatus",
            "controlState",
            "readinessEpoch",
            "readinessObservedAt",
            "readinessExpiresAt",
        ):
            require(field in snapshot, f"{capability}.{field} is missing")

    image = snapshots["archiveImageAnalysis"]
    if image["provider"] == "deepseek/text-only":
        require(image["providerReady"] is False, "text-only provider must not claim vision readiness")
        require(image["reason"] in {"providerVisionUnsupported", "runtimeDisabled"}, "image reason changed")

    for capability in ("archiveAudioUpload", "archiveVideoUpload"):
        media = snapshots[capability]
        require(media["provider"] == "internalOnly", f"{capability} must not advertise a mock provider")
        require(media["enabled"] is False, f"{capability} must remain disabled")
        require(media["providerReady"] is False, f"{capability} must not be provider ready")
        require(media["releaseVisible"] is False, f"{capability} must remain release hidden")
        require(media["reason"] == "productClosed", f"{capability} reason changed")
        require(media["fallbackMode"] == "disabled", f"{capability} fallback must remain disabled")
        require(
            media["configurationStatus"] == "productClosed",
            f"{capability} configuration status changed",
        )

    for capability in ("kbliteUserSurface", "accountDataExport"):
        closed_surface = snapshots[capability]
        require(closed_surface["enabled"] is False, f"{capability} must remain disabled")
        require(closed_surface["providerReady"] is False, f"{capability} must not be provider ready")
        require(closed_surface["releaseVisible"] is False, f"{capability} must remain hidden")
        require(closed_surface["reason"] == "productClosed", f"{capability} reason changed")
        require(
            closed_surface["configurationStatus"] == "productClosed",
            f"{capability} configuration status changed",
        )

    for capability in ("timeLetters", "echoDelayedReplies"):
        closed = snapshots[capability]
        require(closed["enabled"] is False, f"{capability} must remain disabled")
        require(closed["providerReady"] is False, f"{capability} must not dispatch")
        require(closed["releaseVisible"] is False, f"{capability} must remain hidden")
        require(closed["reason"] == "productClosed", f"{capability} reason changed")

    capabilities = runtime.get("capabilities") or {}
    require(capabilities.get("timeLetters") is False, "timeLetters alias must be closed")
    require(
        capabilities.get("echoDelayedReplies") is False,
        "echoDelayedReplies alias must be closed",
    )
    for capability in (
        "archiveAudioUpload",
        "archiveVideoUpload",
        "kbliteUserSurface",
        "accountDataExport",
    ):
        require(capabilities.get(capability) is False, f"{capability} alias must be closed")
    require(capabilities.get("kbSync") is True, "internal KBLite read/write compatibility must remain enabled")

    inventory = runtime.get("providerInventory") or {}
    require(inventory.get("contractVersion") == 1, "provider inventory contract must be v1")
    require(inventory.get("validatedAtStartup") is True, "provider inventory must be startup-validated")
    inventory_capabilities = inventory.get("capabilities") or {}
    for capability in (
        "ownerTruthMediaStorage",
        "ownerTruthMediaProcessing",
        "identityChallenge",
        "voiceCloneShell",
        "digitalHumanLivePanel",
    ):
        descriptor = inventory_capabilities.get(capability) or {}
        snapshot = snapshots[capability]
        for field in (
            "enabled",
            "provider",
            "providerKind",
            "operation",
            "dataClass",
            "region",
            "retentionPolicyVersion",
            "fallbackMode",
            "configurationStatus",
            "evidenceStatus",
        ):
            require(
                descriptor.get(field) == snapshot.get(field),
                f"{capability}.{field} inventory/snapshot mismatch",
            )
        require(
            not snapshot["providerReady"] or descriptor["providerReady"],
            f"{capability} runtime control cannot make an unready provider ready",
        )

    runtime_control = runtime.get("runtimeCapabilityControl") or {}
    require(runtime_control.get("contractVersion") == 1, "runtime control contract must be v1")
    controlled = runtime_control.get("capabilities") or {}
    for capability in ("ownerTruthMediaStorage", "ownerTruthMediaProcessing"):
        decision = controlled.get(capability) or {}
        snapshot = snapshots[capability]
        require(
            decision.get("controlState") in {"ready", "blocked", "stale"},
            f"{capability} control state is missing",
        )
        require(
            snapshot.get("controlState") == decision.get("controlState"),
            f"{capability} snapshot/control state mismatch",
        )
        require(
            snapshot.get("readinessEpoch") == decision.get("readinessEpoch"),
            f"{capability} snapshot/control epoch mismatch",
        )
        require(decision.get("observedAt"), f"{capability} observedAt is missing")
        require(decision.get("expiresAt"), f"{capability} expiresAt is missing")
        if decision["controlState"] == "ready":
            require(decision.get("readinessEpoch"), f"{capability} ready epoch is missing")
        else:
            require(decision.get("readinessEpoch") is None, f"{capability} blocked epoch must clear")
            require(snapshot["providerReady"] is False, f"{capability} blocked snapshot must fail closed")

    owner_truth_media = runtime.get("ownerTruthMedia") or {}
    require(
        owner_truth_media.get("captureCapability") == "ownerTruthMediaStorage",
        "owner truth media capture must bind to the storage capability",
    )
    require(
        owner_truth_media.get("processingCapability") == "ownerTruthMediaProcessing",
        "owner truth media processing must bind to the processing capability",
    )
    require(
        owner_truth_media.get("contractVersion") == 1,
        "owner truth media contract must be v1",
    )
    require(
        owner_truth_media.get("supportedMediaKinds") == ["document", "image"],
        "ordinary Owner media must expose only document and image",
    )
    storage = snapshots["ownerTruthMediaStorage"]
    processing = snapshots["ownerTruthMediaProcessing"]
    require(
        capabilities.get("ownerTruthMediaCapture") is storage["providerReady"],
        "capture alias must follow the startup provider decision",
    )
    require(
        capabilities.get("ownerTruthMediaProcessing") is processing["providerReady"],
        "processing alias must follow the startup provider decision",
    )
    require(
        capabilities.get("identityChallenge") is snapshots["identityChallenge"]["enabled"],
        "identity alias must follow the startup provider decision",
    )

    voice_clone = snapshots["voiceCloneShell"]
    require(voice_clone["externalVerified"] is False, "voiceCloneShell cannot self-sign G3/G4")
    if voice_clone["releaseVisible"]:
        require(voice_clone["enabled"] is True, "visible voice clone must be enabled")
        require(voice_clone["providerReady"] is True, "visible voice clone must have a ready provider")

    digital_human = snapshots["digitalHumanLivePanel"]
    require(digital_human["enabled"] is False, "digitalHumanLivePanel must remain disabled")
    require(digital_human["providerReady"] is False, "digitalHumanLivePanel provider must remain blocked")
    require(digital_human["releaseVisible"] is False, "digitalHumanLivePanel must remain release hidden")
    require(digital_human["reason"] == "productClosed", "digitalHumanLivePanel reason changed")
    require(digital_human["externalVerified"] is False, "digitalHumanLivePanel cannot self-sign G3/G4")

    serialized = json.dumps(runtime, ensure_ascii=False).lower()
    for forbidden in ("secretkey", "accesskey", "accesstoken", "x-api-key"):
        require(forbidden not in serialized, f"runtime response exposed forbidden field: {forbidden}")

    inventory_serialized = json.dumps(inventory, ensure_ascii=False).lower()
    for forbidden in (
        "secret_access_key",
        "access_key_id",
        "api_key",
        "bucket",
        "endpoint_url",
        "appkey",
        "accesstoken",
    ):
        require(
            forbidden not in inventory_serialized,
            f"provider inventory exposed sensitive configuration name: {forbidden}",
        )

    print(
        "Backend runtime capability deployed smoke passed: confirmed first-release "
        "scope and readiness epochs are value-free"
    )


if __name__ == "__main__":
    main()
