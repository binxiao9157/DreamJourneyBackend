#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").rstrip("/")
API_TOKEN = os.environ.get("BACKEND_API_TOKEN", "").strip()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path, *, method="GET", payload=None, expected=(200,)):
    headers = {"Accept": "application/json"}
    if API_TOKEN:
        headers["Authorization"] = f"Bearer {API_TOKEN}"
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers=headers,
        data=body,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        raw = error.read().decode("utf-8", errors="replace")
    require(status in expected, f"{method} {path} expected {expected}, got {status}: {raw[:300]}")
    return status, json.loads(raw) if raw else {}


def eligibility(capability, *, age_status="adult"):
    return {
        "subjectKind": "self",
        "ageStatus": age_status,
        "livingStatus": "living",
        "ageVerified": True,
        "livenessVerified": True,
        "subjectMatchesActor": True,
        "consentVerified": True,
        "consentPurpose": capability,
    }


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(API_TOKEN, "BACKEND_API_TOKEN is required")

    _, runtime = request_json("/config/runtime")
    safety = runtime.get("safety") or {}
    disclosure = safety.get("aiDisclosure") or {}
    require(safety.get("policyVersion") == "safety-policy-v1", "runtime safety policy version changed")
    require(disclosure.get("required") is True, "AI disclosure must be required")
    require(disclosure.get("persistent") is True, "AI disclosure must be persistent")
    require(disclosure.get("visibleLabel") == "AI 生成", "AI disclosure label changed")

    raw_expression = "我真的撑不住了。"
    _, context = request_json(
        "/context/build",
        method="POST",
        payload={
            "userId": "deployed_safety_smoke",
            "intent": "echo_chat",
            "query": raw_expression,
            "personaScope": "personal",
            "digitalHumanId": "deployed_safety_smoke",
        },
    )
    packet = context.get("contextPacket") or {}
    decision = packet.get("safetyPolicy") or {}
    require(decision.get("riskClass") == "highDistress", "crisis classification changed")
    require(decision.get("action") == "respondWithNeutralSafetyText", "crisis action changed")
    require(packet.get("selectedContext") == [], "crisis packet must not select context")
    require((packet.get("generationContext") or {}).get("text") == "", "crisis packet must not generate Persona context")
    require((packet.get("voice") or {}).get("cloneReady") is False, "crisis packet must deny cloned voice")
    require((packet.get("digitalHuman") or {}).get("sessionReady") is False, "crisis packet must deny digital human")
    require(raw_expression not in json.dumps(packet, ensure_ascii=False), "crisis packet leaked raw expression")

    _, delayed = request_json(
        "/echo/delayed-replies",
        method="POST",
        payload={
            "userId": "deployed_safety_smoke",
            "delayedReplyId": "deployed_safety_must_not_persist",
            "deliverAt": "2099-01-01T00:00:00Z",
            "minutes": 7,
            "trigger": "contentSignal",
            "rawTranscript": raw_expression,
        },
        expected=(409,),
    )
    require(
        (delayed.get("detail") or {}).get("code") == "echo_delayed_reply_blocked_by_safety_policy",
        "crisis delayed reply must hard deny",
    )
    require(raw_expression not in json.dumps(delayed, ensure_ascii=False), "delayed reply denial leaked raw expression")

    _, minor_dh = request_json(
        "/digital-human/sessions",
        method="POST",
        payload={
            "userId": "deployed_safety_smoke",
            "personaId": "minor_persona",
            "scene": "echo",
            "lifecycleMode": "sunlight",
            "subjectEligibility": eligibility("digitalHuman", age_status="minor"),
        },
        expected=(403,),
    )
    require(
        (minor_dh.get("detail") or {}).get("code") == "subject_eligibility_hard_denied",
        "minor digital human must hard deny before provider",
    )

    query = urllib.parse.urlencode(
        {"audience": "owner", "cohort": "closedPilotAdultSelf", "clientBuild": 1}
    )
    _, policy = request_json(f"/v2/release-policy?{query}")
    decisions = {item.get("feature"): item for item in policy.get("features") or []}
    expected_stages = {
        "voiceCloneShell": "M1",
        "digitalHumanLivePanel": "M2",
        "careDashboard": "M3",
        "digitalInheritance": "M4",
    }
    for feature, stage in expected_stages.items():
        item = decisions.get(feature) or {}
        require(item.get("releaseStage") == stage, f"{feature} release stage changed")
        require(item.get("releaseVisible") is False, f"{feature} must remain default closed")

    print("Backend WI-S0-06-09 deployed safety smoke passed")


if __name__ == "__main__":
    main()
