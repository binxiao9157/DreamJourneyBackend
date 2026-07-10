#!/usr/bin/env python3
import hashlib
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
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read().decode("utf-8", errors="replace")
    if status != expected:
        raise AssertionError(f"{method} {path} expected {expected}, got {status}: {body[:240]}")
    return json.loads(body) if body else {}


def main():
    suffix = str(int(time.time()))[-8:]
    login = request_json(
        "POST",
        "/auth/login",
        {
            "phone": f"134{suffix}",
            "nickname": "knowledge deployed smoke",
            "password": "knowledge-smoke-123",
        },
    )
    user_id = str((login.get("user") or {}).get("id") or "")
    access_token = str((login.get("auth") or {}).get("accessToken") or "")
    require(user_id, "login must return user id")
    require(access_token.startswith("dja_"), "login must return opaque access token")

    first_statement = f"桂花树知识基线 {suffix}"
    initial_graph = {
        "version": 1,
        "sessionCount": 1,
        "people": [],
        "places": [],
        "events": [],
        "facts": [
            {
                "id": f"knowledge_deployed_fact_{suffix}",
                "statement": first_statement,
                "confidence": "high",
                "privacyMetadata": {
                    "scope": "generationAllowed",
                    "sourceRefs": [
                        {"kind": "qaSmoke", "id": suffix, "title": "部署验收"},
                    ],
                },
            },
        ],
    }
    synced = request_json(
        "POST",
        "/kb/sync",
        {"userId": user_id, "graph": initial_graph},
        access_token=access_token,
    )
    base_revision = int(synced.get("revision") or 0)
    require(base_revision >= 1, "sync must return a positive revision")

    second_statement = f"院子里曾经种过桂花树 {suffix}"
    mutation_fact_id = f"knowledge_deployed_mutation_{suffix}"
    seeded_fact_id = f"knowledge_deployed_fact_{suffix}"
    operation_id = f"knowledge-deployed-smoke-{suffix}"
    mutation_payload = {
        "userId": user_id,
        "operationId": operation_id,
        "baseRevision": base_revision,
        "mutationSchemaVersion": 2,
        "upserts": {
            "people": [],
            "places": [],
            "events": [],
            "facts": [
                {
                    "id": mutation_fact_id,
                    "statement": second_statement,
                    "confidence": "high",
                    "privacyMetadata": {
                        "scope": "generationAllowed",
                        "sourceRefs": [
                            {"kind": "qaSmoke", "id": f"mutation-{suffix}", "title": "部署验收"},
                        ],
                    },
                },
            ],
        },
        "tombstones": [
            {
                "entityType": "facts",
                "entityId": seeded_fact_id,
                "deletedAt": "2026-07-11T00:00:00Z",
            },
        ],
    }
    applied = request_json(
        "POST",
        "/kb/mutations",
        mutation_payload,
        access_token=access_token,
    )
    repeated = request_json(
        "POST",
        "/kb/mutations",
        mutation_payload,
        access_token=access_token,
    )
    applied_revision = int(applied.get("revision") or 0)
    require(applied_revision == base_revision + 1, "mutation must advance revision once")
    require(applied.get("duplicate") is False, "first mutation must apply")
    require(repeated.get("duplicate") is True, "repeated operation must be idempotent")
    require(int(repeated.get("revision") or 0) == applied_revision, "duplicate must keep revision")
    require(applied.get("mutationSchemaVersion") == 2, "mutation response must expose V2 metadata")
    require(repeated.get("mutation") == applied.get("mutation"), "duplicate must preserve mutation metadata")
    authoritative_facts = (applied.get("graph") or {}).get("facts") or []
    require(
        [item.get("id") for item in authoritative_facts] == [mutation_fact_id],
        "V2 tombstone/upsert must return the authoritative graph",
    )

    changes = request_json(
        "GET",
        f"/kb/changes/{user_id}?sinceRevision={base_revision}",
        access_token=access_token,
    )
    require(int(changes.get("currentRevision") or 0) == applied_revision, "change feed revision mismatch")
    change_items = changes.get("changes") or []
    require(len(change_items) == 1, "change feed must contain one mutation")
    require(change_items[0].get("operationId") == operation_id, "operation id must round-trip")
    require(change_items[0].get("mutationSchemaVersion") == 2, "change feed must expose V2 metadata")
    require(change_items[0].get("mutation") == applied.get("mutation"), "change feed mutation mismatch")

    legacy_retry = request_json(
        "POST",
        "/kb/sync",
        {"userId": user_id, "graph": initial_graph},
        access_token=access_token,
    )
    require(legacy_retry.get("compatibilityNoOp") is True, "stale legacy sync must become a no-op")
    require(int(legacy_retry.get("revision") or 0) == applied_revision, "legacy no-op must keep revision")
    snapshot = request_json(
        "GET",
        f"/kb/snapshot/{user_id}",
        access_token=access_token,
    )
    snapshot_facts = (snapshot.get("graph") or {}).get("facts") or []
    require(
        any(item.get("statement") == second_statement for item in snapshot_facts),
        "stale legacy sync must not overwrite the latest graph",
    )
    require(
        all(item.get("statement") != first_statement for item in snapshot_facts),
        "tombstoned knowledge must not be restored by stale legacy sync",
    )

    context = request_json(
        "POST",
        "/context/build",
        {
            "userId": user_id,
            "intent": "echo_chat",
            "query": "院子里的桂花树",
            "personaScope": "personal",
            "digitalHumanId": user_id,
            "lifecycleMode": "sunlight",
        },
        access_token=access_token,
    )
    packet = context.get("contextPacket") or {}
    generation = packet.get("generationContext") or {}
    generation_text = str(generation.get("text") or "")
    require(generation.get("version") == "echo-generation-context-v1", "generation version mismatch")
    require(second_statement in generation_text, "mutated fact must enter generation context")
    expected_hash = "sha256:" + hashlib.sha256(generation_text.encode("utf-8")).hexdigest()
    require(generation.get("contentHash") == expected_hash, "generation hash mismatch")

    conflict = request_json(
        "POST",
        "/kb/mutations",
        {
            **mutation_payload,
            "operationId": f"knowledge-deployed-conflict-{suffix}",
            "baseRevision": base_revision,
        },
        expected=409,
        access_token=access_token,
    )
    require((conflict.get("detail") or {}).get("code") == "knowledgeRevisionConflict", "conflict code mismatch")
    require(
        int((conflict.get("detail") or {}).get("currentRevision") or 0) == applied_revision,
        "conflict must expose current revision",
    )

    print(
        json.dumps(
            {
                "completed": True,
                "baseURL": BASE_URL,
                "revisionAdvanced": True,
                "idempotentRetry": True,
                "mutationSchemaVersion": 2,
                "tombstoneVerified": True,
                "legacySyncNoOpVerified": True,
                "changeCount": len(change_items),
                "generationVersion": generation.get("version"),
                "generationSourceCount": len(generation.get("sourceRefs") or []),
                "generationHashVerified": True,
                "conflictVerified": True,
                "tokensRedacted": True,
                "userIdentifiersRedacted": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
