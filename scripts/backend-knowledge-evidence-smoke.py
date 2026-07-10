#!/usr/bin/env python3
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    client = TestClient(app)
    v2_payload = {
        "userId": "knowledge_evidence_smoke_user",
        "extractionSchemaVersion": 2,
        "sourcePolicy": "userEvidenceOnly",
        "turns": [
            {"index": 0, "role": "user", "text": "我父亲年轻时在南京工作。"},
            {"index": 1, "role": "assistant", "text": "他一定很喜欢南京。"},
        ],
        "existingSummary": "已有知识正文不应进入响应 context",
        "privacyMetadata": {"scope": "generationAllowed"},
    }

    dry_run = client.post("/kb/extract?dryRun=true", json=v2_payload)
    require(dry_run.status_code == 200, dry_run.text)
    dry_body = dry_run.json()
    dry_policy = dry_body.get("evidencePolicy") or {}
    require(dry_body.get("extractionSchemaVersion") == 2, "v2 schema version missing")
    require(dry_policy.get("sourcePolicy") == "userEvidenceOnly", "source policy mismatch")
    require(dry_policy.get("userTurnCount") == 1, "user turn count mismatch")
    require("turns" not in (dry_body.get("context") or {}), "context leaked structured turns")
    require("existingSummary" not in (dry_body.get("context") or {}), "context leaked summary text")
    prompt = dry_body["request"]["json"]["messages"][1]["content"]
    require("sourcePolicy=userEvidenceOnly" in prompt, "provider prompt lacks source policy")
    require("role=assistant" in prompt and "不得作为证据" in prompt, "assistant evidence rule missing")

    provider_extraction = {
        "people": [{"name": "父亲", "sourceTurnIndices": [0]}],
        "places": [{"name": "无来源地点"}],
        "events": [{"title": "助手推断事件", "sourceTurnIndices": [1]}],
        "facts": [{"statement": "越界事实", "sourceTurnIndices": [2]}],
    }
    with patch(
        "app.main.DeepSeekKnowledgeExtractionProxy.request_extraction",
        return_value=provider_extraction,
    ):
        extracted = client.post("/kb/extract", json=v2_payload)

    require(extracted.status_code == 200, extracted.text)
    extracted_body = extracted.json()
    extraction = extracted_body.get("extraction") or {}
    policy = extracted_body.get("evidencePolicy") or {}
    require(len(extraction.get("people") or []) == 1, "valid user-sourced entity was removed")
    require(not extraction.get("places"), "missing-source entity was accepted")
    require(not extraction.get("events"), "assistant-sourced entity was accepted")
    require(not extraction.get("facts"), "out-of-range entity was accepted")
    require(policy.get("acceptedEntityCount") == 1, "accepted entity count mismatch")
    require(policy.get("filteredEntityCount") == 3, "filtered entity count mismatch")

    malformed = client.post(
        "/kb/extract?dryRun=true",
        json={**v2_payload, "turns": [{"index": 0, "role": "system", "text": "invalid"}]},
    )
    require(malformed.status_code == 400, "malformed turn role should be rejected")

    legacy = client.post(
        "/kb/extract?dryRun=true",
        json={
            "userId": "knowledge_evidence_legacy_user",
            "transcript": "[长辈]: 旧版 transcript 继续可用。",
            "privacyMetadata": {"scope": "generationAllowed"},
        },
    )
    require(legacy.status_code == 200, legacy.text)
    require(
        legacy.json()["evidencePolicy"]["sourcePolicy"] == "legacyTranscript",
        "legacy transcript policy mismatch",
    )
    print("Backend knowledge evidence smoke passed")


if __name__ == "__main__":
    main()
