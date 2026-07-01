#!/usr/bin/env python3
import json
from typing import Any, Dict, List

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.main import app
from app.services.in_memory_store import InMemoryStore


def post_archive(client: TestClient, payload: Dict[str, Any]) -> None:
    response = client.post("/archive/items", json=payload)
    if response.status_code != 200:
        raise AssertionError(f"/archive/items failed: {response.status_code} {response.text}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def context_packet(client: TestClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    response = client.post("/context/build", json=payload)
    if response.status_code != 200:
        raise AssertionError(f"/context/build failed: {response.status_code} {response.text}")
    packet = response.json().get("contextPacket")
    if not isinstance(packet, dict):
        raise AssertionError("/context/build response missing contextPacket")
    return packet


def reasons_by_ref(packet: Dict[str, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for item in packet.get("filteredContext") or []:
        if isinstance(item, dict):
            result[str(item.get("refId") or "")] = str(item.get("reason") or "")
    return result


def selected_refs(packet: Dict[str, Any]) -> List[str]:
    return [
        str(item.get("refId") or "")
        for item in (packet.get("selectedContext") or [])
        if isinstance(item, dict) and item.get("refId")
    ]


def main() -> None:
    previous_store = main_module.store
    previous_settings = main_module.settings
    main_module.store = InMemoryStore()
    main_module.settings = Settings(
        store_backend="memory",
        volcengine_voice_clone_tts_api_key="voice-clone-tts-secret",
        tencent_digital_human_app_key="dh-appkey",
        tencent_digital_human_access_token="dh-token",
        tencent_digital_human_virtualman_project_id="dh-project",
    )

    try:
        client = TestClient(app)
        user_id = "echo_context_v2_smoke_user"

        created_member = client.post(
            "/family/invite",
            json={
                "userId": user_id,
                "id": "family_smoke_recipient",
                "name": "林静文",
                "relation": "女儿",
                "phone": "13900001111",
                "personaScope": "family",
                "digitalHumanId": "family_smoke_elder",
            },
        )
        require(created_member.status_code == 200, "family invite should succeed")
        kb_synced = client.post(
            "/kb/sync",
            json={
                "userId": user_id,
                "graph": {
                    "people": [],
                    "places": [],
                    "events": [],
                    "facts": [
                        {
                            "id": "fact_v2_smoke_1",
                            "statement": "妈妈喜欢在西湖边散步。",
                            "privacyMetadata": {"scope": "generationAllowed"},
                        }
                    ],
                },
            },
        )
        require(kb_synced.status_code == 200, "kb sync should succeed")
        main_module.store.save_care_snapshot(
            user_id,
            {
                "riskLevel": "watch",
                "summary": "近期对母亲相关回忆更敏感。",
                "suggestions": ["用温和语气回应。"],
                "trendSummary": "最近 7 天有轻微信号。",
                "dailyTrend": [{"date": "2026-07-01", "signalScore": 1}],
                "internalDebug": "should not enter trace",
            },
        )

        post_archive(
            client,
            {
                "userId": user_id,
                "id": "archive_v2_selected",
                "kind": "photo",
                "title": "西湖旧照",
                "note": "妈妈和我在西湖边散步。",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "analysisStatus": "analyzed",
                "detectedPeople": ["妈妈"],
                "detectedLocations": ["西湖"],
                "detectedScenes": ["散步"],
                "tags": ["亲情"],
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        post_archive(
            client,
            {
                "userId": user_id,
                "id": "archive_v2_failed_empty",
                "kind": "photo",
                "title": "",
                "note": "",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "analysisStatus": "failed",
                "analysisRetryable": True,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        post_archive(
            client,
            {
                "userId": user_id,
                "id": "archive_v2_future_letter",
                "kind": "timeLetter",
                "title": "写给未来的一封信",
                "note": "还没到打开时间。",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "deliveryState": "sealed",
                "deliveryStatus": "scheduled",
                "openAt": "2999-01-01T00:00:00Z",
                "recipients": [{"id": "family_smoke_recipient", "name": "林静文", "type": "family"}],
                "metadata": {
                    "timeLetterStatus": "sealed",
                    "deliveryStatus": "scheduled",
                    "openAt": "2999-01-01T00:00:00Z",
                    "recipientIds": "family_smoke_recipient",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        post_archive(
            client,
            {
                "userId": user_id,
                "id": "archive_v2_family_pending",
                "kind": "photo",
                "title": "家庭照片",
                "note": "pending family viewer 不可用。",
                "personaScope": "family",
                "digitalHumanId": "family_smoke_elder",
                "analysisStatus": "analyzed",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        owner_packet = context_packet(
            client,
            {
                "userId": user_id,
                "intent": "echo_chat",
                "query": "西湖和妈妈有什么线索？",
                "personaScope": "personal",
                "digitalHumanId": user_id,
            },
        )
        require(owner_packet.get("schemaVersion") == 1, "schemaVersion should remain v1-compatible")
        require(owner_packet.get("contextVersion") == "echo-context-v2", "contextVersion should be echo-context-v2")
        require("archive_v2_selected" in selected_refs(owner_packet), "selected archive should enter context")
        owner_sources = {
            str(item.get("source") or "")
            for item in owner_packet.get("selectedContext") or []
            if isinstance(item, dict)
        }
        require("kbFact" in owner_sources, "KBLite fact should enter selectedContext")
        require("persona" in owner_sources, "persona signal should enter selectedContext")
        require("care" in owner_sources, "care signal should enter selectedContext")
        require("fact_v2_smoke_1" in selected_refs(owner_packet), "KBLite fact ref should be preserved")
        require("care:latest" in selected_refs(owner_packet), "care latest ref should be preserved")
        require(
            "internalDebug" not in json.dumps(owner_packet.get("selectedContext") or [], ensure_ascii=False),
            "care selectedContext should not leak internal debug fields",
        )
        owner_reasons = reasons_by_ref(owner_packet)
        require(
            owner_reasons.get("archive_v2_failed_empty") == "analysis_failed_empty_context",
            "failed empty image analysis should be filtered",
        )
        require(owner_packet.get("rankingTrace"), "rankingTrace should be emitted")

        pending_family_packet = context_packet(
            client,
            {
                "userId": user_id,
                "intent": "echo_chat",
                "personaScope": "family",
                "digitalHumanId": "family_smoke_elder",
                "viewerFamilyMemberID": "family_smoke_recipient",
            },
        )
        pending_reasons = reasons_by_ref(pending_family_packet)
        require(
            pending_reasons.get("archive_v2_family_pending") == "family_viewer_not_active",
            "pending family viewer should not use family archive context",
        )
        require(
            pending_family_packet.get("policy", {}).get("canUseFamilyData") is False,
            "pending family viewer should not use family data",
        )

        accepted = client.post(
            f"/family/members/{user_id}/family_smoke_recipient/accept",
            json={"phone": "13900001111"},
        )
        require(accepted.status_code == 200, "family accept should succeed")
        recipient_packet = context_packet(
            client,
            {
                "userId": user_id,
                "intent": "echo_chat",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "viewerFamilyMemberID": "family_smoke_recipient",
            },
        )
        recipient_reasons = reasons_by_ref(recipient_packet)
        require(
            recipient_reasons.get("archive_v2_future_letter") == "time_letter_not_open_for_recipient",
            "future timeLetter should be hidden from recipient",
        )

        result = {
            "completed": True,
            "contextVersion": owner_packet.get("contextVersion"),
            "schemaVersion": owner_packet.get("schemaVersion"),
            "selectedContextRefs": selected_refs(owner_packet),
            "selectedContextSources": sorted(owner_sources),
            "selectedContextSourceCounts": owner_packet.get("trace", {}).get("selectedContextSourceCounts"),
            "ownerFilteredReasons": owner_reasons,
            "pendingFamilyFilteredReasons": pending_reasons,
            "recipientFilteredReasons": recipient_reasons,
            "rankingTraceCount": len(owner_packet.get("rankingTrace") or []),
            "policy": {
                "pendingFamilyCanUseFamilyData": pending_family_packet.get("policy", {}).get("canUseFamilyData"),
                "recipientFamilyViewerActive": recipient_packet.get("policy", {}).get("familyViewerActive"),
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        main_module.store = previous_store
        main_module.settings = previous_settings


if __name__ == "__main__":
    main()
