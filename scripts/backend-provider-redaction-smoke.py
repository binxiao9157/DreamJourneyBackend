#!/usr/bin/env python3
"""Exercise provider dry-run responses without emitting private canary values."""

from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


POLICY_VERSION = "providerDryRun-v2"
CANARIES = (
    "KB_TRANSCRIPT_CANARY",
    "KB_SUMMARY_CANARY",
    "IMAGE_BASE64_CANARY",
    "TTS_TEXT_CANARY",
    "MAP_QUERY_CANARY",
    "dry-run-user-id",
    "dry-run-archive-id",
    "dry-run-deepseek-key",
    "dry-run-volc-key",
    "dry-run-voice-type",
    "dry-run-amap-key",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    client = TestClient(app)
    settings = Settings(
        deepseek_api_key="dry-run-deepseek-key",
        volcengine_api_key="dry-run-volc-key",
        volcengine_voice_type="dry-run-voice-type",
        amap_web_service_key="dry-run-amap-key",
    )
    release_policy = ReleasePolicyService(
        shadow_mode=True,
        enforce_default_closed_stages=False,
    )
    with patch.object(main_module, "settings", settings), patch.object(
        main_module,
        "RELEASE_POLICY_SERVICE",
        release_policy,
    ), patch.object(
        main_module,
        "RELEASE_POLICY_COMMAND_GATE",
        ReleasePolicyCommandGate(release_policy),
    ):
        responses = [
            client.post(
                "/kb/extract?dryRun=true",
                json={
                    "userId": "dry-run-user-id",
                    "transcript": "KB_TRANSCRIPT_CANARY",
                    "existingSummary": "KB_SUMMARY_CANARY",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            ),
            client.post(
                "/archive/image-analysis?dryRun=true",
                json={
                    "userId": "dry-run-user-id",
                    "archiveItemId": "dry-run-archive-id",
                    "imageBase64": "IMAGE_BASE64_CANARY",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            ),
            client.post(
                "/tts?dryRun=true",
                json={
                    "userId": "dry-run-user-id",
                    "text": "TTS_TEXT_CANARY",
                    "voiceType": "dry-run-voice-type",
                },
            ),
            client.get("/maps/district?dryRun=true&keyword=MAP_QUERY_CANARY"),
        ]

    for response in responses:
        require(response.status_code == 200, "provider dry-run must succeed")
        payload = response.json()
        require("request" not in payload, "dry-run response must not include upstream request")
        require(
            (payload.get("dryRun") or {}).get("redactionPolicyVersion") == POLICY_VERSION,
            "dry-run policy version missing",
        )
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        require(not any(canary in serialized for canary in CANARIES), "private input leaked")

    print(json.dumps({"policyVersion": POLICY_VERSION, "status": "passed", "surfaces": 4}, sort_keys=True))


if __name__ == "__main__":
    main()
