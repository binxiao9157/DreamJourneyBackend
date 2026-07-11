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
SERVICE_TOKEN = os.environ.get(
    "BACKEND_API_TOKEN",
    os.environ.get("DREAMJOURNEY_BACKEND_API_TOKEN", ""),
)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(method, path, payload=None, expected=200, access_token=None):
    headers = {"Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    elif SERVICE_TOKEN:
        headers["X-DreamJourney-Api-Token"] = SERVICE_TOKEN
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        response_body = error.read().decode("utf-8", errors="replace")
    if status != expected:
        raise AssertionError(
            f"{method} {path} expected {expected}, got {status}: {response_body[:240]}"
        )
    return json.loads(response_body) if response_body else {}


def login(phone, nickname):
    payload = request_json(
        "POST",
        "/auth/login",
        {"phone": phone, "nickname": nickname, "password": "source-audit-smoke-123"},
    )
    user_id = str((payload.get("user") or {}).get("id") or "")
    access_token = str((payload.get("auth") or {}).get("accessToken") or "")
    require(user_id, "login must return user id")
    require(access_token.startswith("dja_"), "login must return opaque access token")
    return user_id, access_token


def main():
    suffix = str(int(time.time()))[-8:]
    owner_id, owner_token = login(f"136{suffix}", "source audit owner")
    _, attacker_token = login(f"137{suffix}", "source audit attacker")

    mutation = {
        "userId": owner_id,
        "operationId": f"source-audit-deployed-{suffix}",
        "baseRevision": 0,
        "mutationSchemaVersion": 2,
        "upserts": {
            "people": [],
            "places": [],
            "events": [],
            "facts": [
                {
                    "id": f"source_audit_fact_{suffix}",
                    "statement": f"来源审计部署验证 {suffix}",
                    "confidence": "high",
                    "privacyMetadata": {
                        "scope": "generationAllowed",
                        "sourceRefs": [
                            {
                                "kind": "conversationTurn",
                                "id": f"session-{suffix}:turn-0",
                                "title": "对话来源",
                            }
                        ],
                    },
                }
            ],
        },
        "tombstones": [],
    }
    applied = request_json(
        "POST",
        "/kb/mutations",
        mutation,
        access_token=owner_token,
    )
    require(applied.get("revision") == 1, "fresh audit mutation must create revision 1")

    denied = request_json(
        "GET",
        f"/kb/source-ref-audit/{owner_id}",
        expected=403,
        access_token=attacker_token,
    )
    require((denied.get("detail") or {}).get("code") == "authorizationDenied", "cross-user audit must be denied")

    audit = request_json(
        "GET",
        f"/kb/source-ref-audit/{owner_id}",
        access_token=owner_token,
    )
    require(
        set(audit) == {"userId", "schemaVersion", "revision", "counts", "recommendedAction"},
        "audit response must remain aggregate-only",
    )
    require(audit.get("schemaVersion") == 1, "audit schema version mismatch")
    require(audit.get("revision") == 1, "audit revision mismatch")
    counts = audit.get("counts") or {}
    source_refs = counts.get("sourceRefs") or {}
    require(source_refs.get("total") == 1, "audit source ref total mismatch")
    require(source_refs.get("canonical") == 1, "canonical source ref was not classified")
    require(source_refs.get("legacy") == 0, "fresh canonical mutation must not create legacy refs")
    require(source_refs.get("unknown") == 0, "fresh canonical mutation must not create unknown refs")
    require(audit.get("recommendedAction") == "none", "fresh canonical data must need no migration")
    serialized = json.dumps(audit, ensure_ascii=False)
    for forbidden in ["graph", "sourceRefs", "statement", "source_audit_fact_", "session-"]:
        require(forbidden not in serialized, f"audit leaked forbidden field: {forbidden}")

    print(
        json.dumps(
            {
                "completed": True,
                "baseURL": BASE_URL,
                "schemaVersion": audit.get("schemaVersion"),
                "revision": audit.get("revision"),
                "canonicalSourceRefCount": source_refs.get("canonical"),
                "recommendedAction": audit.get("recommendedAction"),
                "crossAccountDenied": True,
                "aggregateOnly": True,
                "tokensRedacted": True,
                "userIdentifiersRedacted": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
