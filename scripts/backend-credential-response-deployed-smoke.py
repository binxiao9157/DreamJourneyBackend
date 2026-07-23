#!/usr/bin/env python3
import json
import os
import time
import urllib.error
import urllib.request


BASE_URL = os.environ.get(
    "BACKEND_BASE_URL",
    os.environ.get("DREAMJOURNEY_BACKEND_BASE_URL", "http://127.0.0.1:3100"),
).rstrip("/")
FORBIDDEN_PROVIDER_FIELDS = {
    "appkey",
    "accesstoken",
    "apptoken",
    "apikey",
    "secretkey",
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def normalized_key(value):
    return "".join(character for character in str(value).lower() if character.isalnum())


def assert_no_provider_credentials(value, path="response"):
    if isinstance(value, dict):
        for key, child in value.items():
            require(
                normalized_key(key) not in FORBIDDEN_PROVIDER_FIELDS,
                f"{path} exposes forbidden Provider credential field",
            )
            assert_no_provider_credentials(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_provider_credentials(child, f"{path}[{index}]")


def request_json(method, path, payload=None, expected=200, access_token=None):
    # The deployed backend retires legacy identity flows before endpoint logic
    # runs. Every smoke request therefore represents the typed runtime client,
    # not only the runtime-config request.
    headers = {
        "Accept": "application/json",
        "X-DreamJourney-Runtime-Contract-Version": "2",
        "X-DreamJourney-Client-Build": "9001",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {key.lower(): value for key, value in error.headers.items()}
        body = error.read().decode("utf-8", errors="replace")
    require(status == expected, f"{method} {path} expected {expected}, got {status}")
    require(response_headers.get("cache-control") == "no-store", f"{path} must be no-store")
    require(response_headers.get("pragma") == "no-cache", f"{path} must be no-cache")
    return json.loads(body) if body else {}


def main():
    suffix = str(int(time.time()))[-8:]
    login = request_json(
        "POST",
        "/auth/login",
        {
            "phone": f"137{suffix}",
            "nickname": "credential boundary smoke",
            "password": "credential-boundary-smoke-123",
        },
    )
    access_token = str((login.get("auth") or {}).get("accessToken") or "")
    user_id = str((login.get("user") or {}).get("id") or "")
    require(access_token.startswith("dja_"), "login must return an opaque user access token")
    require(user_id, "login must return user id")

    runtime = request_json("GET", "/config/runtime", access_token=access_token)
    assert_no_provider_credentials(runtime)
    require(runtime.get("capabilities", {}).get("realtimeToken") is False, "realtime token capability must be disabled")
    require(runtime.get("capabilities", {}).get("digitalHumanSession") is False, "digital-human session capability must be disabled")
    require(runtime.get("voice", {}).get("credentialMode") == "blockedStaticCredential", "voice credential mode must be blocked")
    require(runtime.get("voice", {}).get("accessPath") == "backendProxyOrText", "voice access path must remain backend proxy or text")
    require(runtime.get("voice", {}).get("mobileDirectAllowed") is False, "voice direct mobile path must remain denied")
    require((runtime.get("voice", {}).get("decisionReceipt") or {}).get("decision") == "keepDirectMobileClosed", "runtime must expose the direct-mobile denial receipt")
    runtime_digital_human = runtime.get("digitalHuman", {})
    require(runtime_digital_human.get("credentialMode") == "blockedStaticCredential", "digital-human credential mode must be blocked")
    require(runtime_digital_human.get("accessPath") == "textFallback", "digital-human access path must remain text fallback")
    require(runtime_digital_human.get("mobileDirectAllowed") is False, "digital-human direct mobile path must remain denied")
    require(runtime_digital_human.get("brokerStatus") == "providerContractNotVerified", "digital-human broker status must remain unverified")
    require((runtime_digital_human.get("decisionReceipt") or {}).get("decision") == "keepDirectMobileClosed", "runtime must expose the digital-human denial receipt")

    voice = request_json(
        "POST",
        "/voice/realtime-token",
        {"userId": user_id},
        access_token=access_token,
    )
    assert_no_provider_credentials(voice)
    require(voice.get("status") == "blocked", "realtime voice must return blocked")
    require(voice.get("providerReady") is False, "realtime voice Provider must not be ready")
    require(voice.get("accessPath") == "backendProxyOrText", "realtime voice must select backendProxyOrText")
    require(voice.get("mobileDirectAllowed") is False, "realtime voice must deny mobile direct access")
    require(voice.get("brokerStatus") == "providerContractNotVerified", "broker status must remain unverified")
    receipt = voice.get("decisionReceipt") or {}
    required = ["scope", "ttl", "audience", "revocation"]
    require(receipt.get("decision") == "keepDirectMobileClosed", "voice must expose a closed-path decision receipt")
    require(receipt.get("requiredProperties") == required, "voice receipt must list scoped credential requirements")
    require(receipt.get("verifiedProperties") == [], "voice receipt must not claim unverified properties")
    require(receipt.get("missingProperties") == required, "voice receipt must list every missing property")
    require("expiresAt" not in voice and "expiresInSeconds" not in voice, "blocked response must not invent TTL metadata")

    digital_human = request_json(
        "POST",
        "/digital-human/sessions",
        {
            "userId": user_id,
            "personaId": user_id,
            "scene": "echo",
            "deviceId": "credential-boundary-smoke",
            "lifecycleMode": "sunlight",
        },
        expected=503,
        access_token=access_token,
    )
    assert_no_provider_credentials(digital_human)
    detail = digital_human.get("detail") or {}
    require(detail.get("code") == "digital_human_credential_broker_unavailable", "digital human must fail with broker-unavailable")
    require(detail.get("accessPath") == "textFallback", "digital human must select text fallback")
    require(detail.get("mobileDirectAllowed") is False, "digital human must deny mobile direct access")
    require(detail.get("brokerStatus") == "providerContractNotVerified", "digital-human broker status must remain unverified")
    require(detail.get("releaseVisible") is False, "blocked digital human must not be release visible")
    require(detail.get("retryable") is False, "blocked digital human must not pretend a retry can mint credentials")
    digital_human_receipt = detail.get("decisionReceipt") or {}
    require(digital_human_receipt.get("decision") == "keepDirectMobileClosed", "digital human must expose a closed-path decision receipt")
    require(digital_human_receipt.get("requiredProperties") == required, "digital-human receipt must list scoped credential requirements")
    require(digital_human_receipt.get("verifiedProperties") == [], "digital-human receipt must not claim unverified properties")
    require(digital_human_receipt.get("missingProperties") == required, "digital-human receipt must list every missing property")
    require("expiresAt" not in detail and "expiresInSeconds" not in detail, "digital-human response must not invent TTL metadata")

    print("Backend credential response deployed smoke passed: no-store + value-free blocked contracts")


if __name__ == "__main__":
    main()
