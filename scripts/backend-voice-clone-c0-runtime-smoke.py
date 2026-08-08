#!/usr/bin/env python3
"""Deployed fail-closed smoke for the C0 voice-clone admission contract."""

import json
import os
import urllib.error
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", "http://127.0.0.1:3100"),
).rstrip("/")
EXPECTED_IDENTITY_READY = os.environ.get(
    "VOICE_IDENTITY_ELIGIBILITY_EXPECTED_READY", "0"
).strip().lower() in {"1", "true", "yes"}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    request = urllib.request.Request(
        f"{BASE_URL}/config/runtime",
        headers={
            "Accept": "application/json",
            "X-DreamJourney-Runtime-Contract-Version": "3",
            "X-DreamJourney-Client-Build": "9001",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read().decode("utf-8", errors="replace")
    require(status == 200, f"GET /config/runtime expected 200, got {status}")
    runtime = json.loads(body)
    voice = runtime.get("voiceClone") or {}
    for field in (
        "identityEligibilityProviderReady",
        "identityEligibilityProvider",
        "trainingAdmissionEnabled",
        "trainingAdmissionReason",
        "trainingAdmissionContractVersion",
        "operationMatrix",
    ):
        require(field in voice, f"voiceClone.{field} is missing")
    require(
        type(voice["identityEligibilityProviderReady"]) is bool,
        "identityEligibilityProviderReady must be bool",
    )
    require(
        type(voice["trainingAdmissionEnabled"]) is bool,
        "trainingAdmissionEnabled must be bool",
    )
    require(
        voice["trainingAdmissionContractVersion"] == 1,
        "voice clone admission contract version changed",
    )
    if not EXPECTED_IDENTITY_READY:
        require(
            voice["identityEligibilityProviderReady"] is False,
            "unconfigured identity verifier must stay unavailable",
        )
        require(
            voice["trainingAdmissionEnabled"] is False,
            "voice clone training must fail closed without identity verifier",
        )
        require(
            voice["trainingAdmissionReason"] == "identityLivenessProviderUnavailable",
            "missing verifier must expose a safe, explicit reason",
        )
    operation_matrix = voice["operationMatrix"]
    require(operation_matrix.get("schemaVersion") == 1, "voice operation matrix version changed")
    operations = operation_matrix.get("operations") or {}
    require(
        set(operations) == {"train", "query", "preview", "accept", "synthesize", "pause", "delete"},
        "voice operation matrix is incomplete",
    )
    require(operations["pause"].get("available") is True, "local pause must remain available")
    require(operations["delete"].get("available") is True, "local delete must remain available")
    require(
        operations["delete"].get("providerCapability") in {"ready", "unsupported", "unavailable"},
        "voice deletion provider capability is invalid",
    )
    if operations["delete"].get("providerCapability") != "ready":
        require(
            operations["delete"].get("providerCompletionAvailable") is False,
            "unsupported deletion must not claim Provider completion",
        )
    serialized = json.dumps(voice, ensure_ascii=False).lower()
    for forbidden in ("api_key", "accesskey", "secret", "receiptid", "livenessdocument"):
        require(forbidden not in serialized, f"voice runtime exposed sensitive field {forbidden}")
    print(
        "Voice clone C0 runtime smoke passed: "
        f"identityReady={voice['identityEligibilityProviderReady']} "
        f"trainingAdmissionEnabled={voice['trainingAdmissionEnabled']} "
        f"deleteProvider={operations['delete']['providerCapability']}"
    )


if __name__ == "__main__":
    main()
