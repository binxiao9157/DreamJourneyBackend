#!/usr/bin/env python3
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.auth_sessions import AuthSessionService
from app.services.postgres_store import PostgresStore
from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityEvidence,
    SubjectEligibilityReason,
    evaluate_subject_eligibility,
)


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").rstrip("/")
API_TOKEN = os.environ.get("BACKEND_API_TOKEN", "").strip()
DIRECT_ISSUE = os.environ.get(
    "BACKEND_SAFETY_SMOKE_DIRECT_ISSUE",
    "",
).strip().lower() in {"1", "true", "yes"}
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(path, *, method="GET", payload=None, token=None, expected=(200,)):
    headers = {"Accept": "application/json"}
    bearer_token = API_TOKEN if token is None else str(token or "").strip()
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
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


def make_user_fixture():
    """Create an isolated user principal for routes that reject machine tokens."""

    require(
        DIRECT_ISSUE,
        "BACKEND_SAFETY_SMOKE_DIRECT_ISSUE=1 is required for protected safety routes",
    )
    require(DATABASE_URL, "DATABASE_URL is required for direct fixture issue")
    suffix = secrets.token_hex(8)
    store = PostgresStore(
        dsn=DATABASE_URL,
        pool_min_size=1,
        pool_max_size=2,
        pool_timeout_seconds=2.0,
    )
    store.open_pool(wait=True)
    user = store.upsert_user(
        phone=f"198{secrets.randbelow(10**8):08d}",
        nickname="safety policy smoke",
    )
    user_id = str(user["id"])
    auth = AuthSessionService(
        store,
        access_ttl_seconds=300,
        refresh_ttl_seconds=900,
    ).issue(user_id)

    def cleanup():
        try:
            with store.request_unit_of_work(
                correlation_id=f"safety-policy-smoke-cleanup-{suffix}",
                command_id="cleanupSafetyPolicySmoke",
            ) as unit_of_work:
                with unit_of_work.connection.cursor() as cursor:
                    for table in ("auth_sessions", "token_families"):
                        cursor.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        finally:
            store.close_pool()

    return {"userId": user_id, "accessToken": auth["accessToken"], "cleanup": cleanup}


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

    fixture = make_user_fixture()
    user_id = fixture["userId"]
    user_token = fixture["accessToken"]
    raw_expression = "我真的撑不住了。"
    try:
        _, context = request_json(
            "/context/build",
            method="POST",
            token=user_token,
            payload={
                "userId": user_id,
                "intent": "echo_chat",
                "query": raw_expression,
                "personaScope": "personal",
                "digitalHumanId": user_id,
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
            token=user_token,
            payload={
                "userId": user_id,
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
            token=user_token,
            payload={
                "userId": user_id,
                "personaId": "minor_persona",
                "scene": "echo",
                "lifecycleMode": "sunlight",
                "subjectEligibility": eligibility("digitalHuman", age_status="minor"),
            },
            expected=(403,),
        )
        local_minor = evaluate_subject_eligibility(
            SubjectEligibilityEvidence.model_validate(
                {
                    **eligibility("digitalHuman", age_status="minor"),
                    "capability": HighRiskCapability.DIGITAL_HUMAN,
                }
            )
        )
        require(local_minor.allowed is False, "minor digital human must never be eligible")
        require(
            local_minor.reason == SubjectEligibilityReason.MINOR,
            "minor digital human eligibility reason changed",
        )
        minor_detail = minor_dh.get("detail") or {}
        minor_code = minor_detail.get("code")
        if minor_code == "release_policy_denied":
            require(
                minor_detail.get("feature") == "digitalHumanLivePanel",
                "default-closed M2 route must identify the digital human feature",
            )
        else:
            require(
                minor_code == "subject_eligibility_hard_denied",
                "enabled digital human route must hard deny minor eligibility",
            )
    finally:
        fixture["cleanup"]()

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
