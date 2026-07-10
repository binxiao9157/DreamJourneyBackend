#!/usr/bin/env python3
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    previous_store = main_module.store
    main_module.store = InMemoryStore()
    try:
        client = TestClient(app)
        user_id = "knowledge_v2_smoke_user"
        metadata = {"privacyMetadata": {"scope": "generationAllowed"}}
        seeded = client.post(
            "/kb/mutations",
            json={
                "userId": user_id,
                "operationId": "knowledge-v2-seed",
                "baseRevision": 0,
                "graph": {
                    "people": [{"id": "shared", "name": "Person", **metadata}],
                    "places": [{"id": "place-1", "name": "Garden", **metadata}],
                    "events": [],
                    "facts": [
                        {"id": "shared", "statement": "Old fact", **metadata},
                        {"id": "gone", "statement": "Delete me", **metadata},
                    ],
                },
            },
        )
        require(seeded.status_code == 200, seeded.text)
        require(seeded.json().get("revision") == 1, "v1 seed should create revision 1")

        mutation = {
            "userId": user_id,
            "operationId": "knowledge-v2-op",
            "baseRevision": 1,
            "mutationSchemaVersion": 2,
            "upserts": {
                "events": [
                    {
                        "id": "event-1",
                        "title": "Garden visit",
                        "participantIds": ["shared"],
                        "locationId": "place-1",
                        **metadata,
                    }
                ],
                "facts": [{"id": "shared", "statement": "Recreated fact", **metadata}],
            },
            "tombstones": [
                {"entityType": "facts", "entityId": "shared", "deletedAt": "2026-07-11T00:00:00Z"},
                {"entityType": "facts", "entityId": "gone", "deletedAt": "2026-07-11T00:00:00Z"},
            ],
        }
        applied = client.post("/kb/mutations", json=mutation)
        repeated = client.post("/kb/mutations", json=mutation)
        require(applied.status_code == 200, applied.text)
        require(applied.json().get("revision") == 2, "v2 mutation should create revision 2")
        require(applied.json().get("mutationSchemaVersion") == 2, "v2 schema metadata missing")
        graph = applied.json().get("graph") or {}
        require([item["id"] for item in graph.get("people", [])] == ["shared"], "tombstone crossed entity type")
        require([item["id"] for item in graph.get("facts", [])] == ["shared"], "fact tombstones/upsert order failed")
        require(graph["facts"][0].get("statement") == "Recreated fact", "fact upsert did not win")
        require(graph["events"][0].get("participantIds") == ["shared"], "existing person relation was filtered")
        require(graph["events"][0].get("locationId") == "place-1", "existing place relation was filtered")
        require(repeated.status_code == 200, repeated.text)
        require(repeated.json().get("duplicate") is True, "repeated operation should be idempotent")
        require(repeated.json().get("mutation") == applied.json().get("mutation"), "duplicate metadata changed")

        stale = client.post(
            "/kb/mutations",
            json={**mutation, "operationId": "knowledge-v2-stale", "baseRevision": 1},
        )
        require(stale.status_code == 409, "stale v2 mutation should conflict")
        require(stale.json()["detail"].get("currentRevision") == 2, "conflict revision mismatch")

        local_only = client.post(
            "/kb/mutations",
            json={
                "userId": user_id,
                "operationId": "knowledge-v2-local-only",
                "baseRevision": 2,
                "mutationSchemaVersion": 2,
                "upserts": {
                    "facts": [
                        {
                            "id": "local-fact",
                            "statement": "Must stay local",
                            "privacyMetadata": {"scope": "localOnly"},
                        }
                    ]
                },
                "tombstones": [],
            },
        )
        require(local_only.status_code == 400, "localOnly upsert should be rejected")

        changes = client.get(f"/kb/changes/{user_id}?sinceRevision=1")
        require(changes.status_code == 200, changes.text)
        body = changes.json()
        require(body.get("currentRevision") == 2, "rejected mutations must not advance revision")
        require(len(body.get("changes") or []) == 1, "change feed should contain one v2 mutation")
        change = body["changes"][0]
        require(change.get("mutationSchemaVersion") == 2, "change schema metadata missing")

        empty_mutation = client.post(
            "/kb/mutations",
            json={
                "userId": user_id,
                "operationId": "knowledge-v2-empty",
                "baseRevision": applied.json()["revision"],
                "mutationSchemaVersion": 2,
                "upserts": {},
                "tombstones": [],
            },
        )
        require(empty_mutation.status_code == 400, "empty v2 mutation should be rejected")
        require(change.get("mutation") == applied.json().get("mutation"), "change mutation metadata mismatch")
        print("Backend knowledge v2 smoke passed")
    finally:
        main_module.store = previous_store


if __name__ == "__main__":
    main()
