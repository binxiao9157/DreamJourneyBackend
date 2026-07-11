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

        first_page = client.get(f"/kb/changes/{user_id}?sinceRevision=0&limit=1")
        require(first_page.status_code == 200, first_page.text)
        first_page_body = first_page.json()
        require(first_page_body.get("targetRevision") == 2, "first page should pin revision 2")
        require(first_page_body.get("nextSinceRevision") == 1, "first page cursor should advance")
        require(first_page_body.get("hasMore") is True, "first page should report more changes")
        require(len(first_page_body.get("changes") or []) == 1, "non-terminal page must not be empty")

        conflict = client.post(
            "/kb/mutations",
            json={**mutation, "operationId": "knowledge-delta-smoke-op-2", "baseRevision": 1},
        )
        require(conflict.status_code == 409, "stale base revision should conflict")
        require(conflict.json()["detail"]["currentRevision"] == 2, "conflict should expose current revision")

        post_target = client.post(
            "/kb/mutations",
            json={
                **mutation,
                "operationId": "knowledge-delta-smoke-op-3",
                "baseRevision": 2,
            },
        )
        require(post_target.status_code == 200, post_target.text)
        require(post_target.json().get("revision") == 3, "new write should create revision 3")

        second_page = client.get(
            f"/kb/changes/{user_id}?sinceRevision=1&targetRevision=2&limit=1"
        )
        require(second_page.status_code == 200, second_page.text)
        second_page_body = second_page.json()
        require(second_page_body.get("currentRevision") == 2, "paged current must stay pinned")
        require(second_page_body.get("targetRevision") == 2, "target should remain stable")
        require(second_page_body.get("nextSinceRevision") == 2, "final cursor should reach target")
        require(second_page_body.get("hasMore") is False, "target page should be terminal")
        require(
            [item.get("revision") for item in second_page_body.get("changes") or []] == [2],
            "writes after target must not enter the pagination run",
        )

        main_module.store._kb_changes[user_id] = [
            change
            for change in main_module.store._kb_changes[user_id]
            if change["revision"] > 2
        ]
        main_module.store._kb_change_feed_minimum_since_revisions[user_id] = 2
        compacted = client.get(f"/kb/changes/{user_id}?sinceRevision=1&limit=10")
        require(compacted.status_code == 410, compacted.text)
        compacted_detail = compacted.json().get("detail") or {}
        require(
            compacted_detail.get("code") == "knowledgeChangeFeedCompacted",
            "compacted feed should return the structured error code",
        )
        require(
            compacted_detail.get("minimumSinceRevision") == 2,
            "compacted feed should expose its minimum cursor",
        )
        retained = client.get(f"/kb/changes/{user_id}?sinceRevision=2&limit=10")
        require(retained.status_code == 200, retained.text)
        require(
            [item.get("revision") for item in retained.json().get("changes") or []] == [3],
            "the minimum cursor should continue from retained changes",
        )
        print("Backend knowledge delta smoke passed")
    finally:
        main_module.store = previous_store


if __name__ == "__main__":
    main()
