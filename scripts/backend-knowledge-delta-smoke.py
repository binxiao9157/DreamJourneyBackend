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
        user_id = "knowledge_delta_smoke_user"
        synced = client.post(
            "/kb/sync",
            json={"userId": user_id, "graph": {"people": [], "places": [], "events": [], "facts": []}},
        )
        require(synced.status_code == 200, synced.text)
        require(synced.json().get("revision") == 1, "legacy sync should create revision 1")

        mutation = {
            "userId": user_id,
            "operationId": "knowledge-delta-smoke-op-1",
            "baseRevision": 1,
            "graph": {
                "people": [],
                "places": [],
                "events": [],
                "facts": [
                    {
                        "id": "knowledge_delta_fact",
                        "statement": "院子里曾经种过桂花树。",
                        "confidence": "high",
                        "privacyMetadata": {"scope": "generationAllowed"},
                    }
                ],
            },
        }
        applied = client.post("/kb/mutations", json=mutation)
        repeated = client.post("/kb/mutations", json=mutation)
        require(applied.status_code == 200, applied.text)
        require(applied.json().get("revision") == 2, "mutation should create revision 2")
        require(applied.json().get("duplicate") is False, "first mutation should be applied")
        require(repeated.json().get("duplicate") is True, "repeated operation should be idempotent")

        changes = client.get(f"/kb/changes/{user_id}?sinceRevision=1")
        require(changes.status_code == 200, changes.text)
        body = changes.json()
        require(body.get("currentRevision") == 2, "change feed should report current revision")
        require(len(body.get("changes") or []) == 1, "change feed should return one mutation")
        require(body["changes"][0]["operationId"] == mutation["operationId"], "operation id should round-trip")

        conflict = client.post(
            "/kb/mutations",
            json={**mutation, "operationId": "knowledge-delta-smoke-op-2", "baseRevision": 1},
        )
        require(conflict.status_code == 409, "stale base revision should conflict")
        require(conflict.json()["detail"]["currentRevision"] == 2, "conflict should expose current revision")
        print("Backend knowledge delta smoke passed")
    finally:
        main_module.store = previous_store


if __name__ == "__main__":
    main()
