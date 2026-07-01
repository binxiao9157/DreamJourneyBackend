import time
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional

from app.core.config import Settings
from app.services.runtime_config import RuntimeConfigService


class ContextPacketBuilder:
    schema_version = 1

    def __init__(self, store: Any, settings: Settings):
        self.store = store
        self.settings = settings

    def build(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        user_id = self._required_text(payload, "userId")
        intent = self._text(payload.get("intent"), "echo_chat")
        query = self._text(payload.get("query"), "")
        persona_scope = self._normal_persona_scope(payload.get("personaScope"))
        digital_human_id = self._text(payload.get("digitalHumanId"), user_id)
        lifecycle_mode = self._text(payload.get("lifecycleMode"), "sunlight")
        viewer_family_member_id = self._optional_text(payload.get("viewerFamilyMemberID"))

        all_archive_items = self.store.list_archive_items(user_id)
        included_archive_items = [
            self._archive_summary(item)
            for item in all_archive_items
            if self._archive_matches_scope(
                item,
                user_id=user_id,
                persona_scope=persona_scope,
                digital_human_id=digital_human_id,
            )
        ][:8]
        kb_graph = self.store.get_kb_snapshot(user_id) or {}
        care_snapshot = self._latest_care_snapshot(user_id, persona_scope, viewer_family_member_id)
        voice_profiles = self.store.list_voice_profiles(user_id)
        usable_voice_profile = self._first_usable_voice_profile(voice_profiles, persona_scope, digital_human_id, user_id)
        runtime_config = RuntimeConfigService(self.settings).public_config()
        voice_runtime = runtime_config.get("voiceClone") or {}
        tencent_audio_drive = voice_runtime.get("tencentAudioDrive") or {}
        digital_human_runtime = runtime_config.get("digitalHuman") or {}
        synthesis_ready = bool(voice_runtime.get("synthesisProviderReady") and tencent_audio_drive.get("supported"))
        clone_ready = usable_voice_profile is not None and synthesis_ready
        digital_human_ready = bool(digital_human_runtime.get("realProviderReady"))

        fallbacks: List[str] = []
        if not included_archive_items:
            fallbacks.append("no_archive_context")
        if not clone_ready:
            fallbacks.append("voice_clone_not_ready")
        if not digital_human_ready:
            fallbacks.append("digital_human_not_ready")

        latency_ms = int((time.perf_counter() - started) * 1000)
        privacy_scope = self._privacy_scope(
            user_id=user_id,
            persona_scope=persona_scope,
            digital_human_id=digital_human_id,
            viewer_family_member_id=viewer_family_member_id,
            cross_scope_archive_included=self._has_cross_scope_archive(
                included_archive_items,
                persona_scope=persona_scope,
                digital_human_id=digital_human_id,
                user_id=user_id,
            ),
        )
        trace = self._trace_summary(
            archive_items=included_archive_items,
            kb_graph=kb_graph,
            usable_voice_profile=usable_voice_profile,
            clone_ready=clone_ready,
            digital_human_ready=digital_human_ready,
            digital_human_provider_mode=digital_human_runtime.get("providerMode") or "mockContract",
            privacy_scope=privacy_scope,
            fallbacks=fallbacks,
            latency_ms=latency_ms,
        )
        return {
            "schemaVersion": self.schema_version,
            "traceId": "ctx_" + uuid.uuid4().hex[:24],
            "intent": intent,
            "userId": user_id,
            "query": query,
            "persona": {
                "personaScope": persona_scope,
                "digitalHumanId": digital_human_id,
                "lifecycleMode": lifecycle_mode,
                "viewerFamilyMemberID": viewer_family_member_id,
            },
            "memory": {
                "archiveItems": included_archive_items,
                "kbPeople": self._kb_people(kb_graph),
                "kbPlaces": self._kb_places(kb_graph),
                "kbEvents": self._kb_events(kb_graph),
                "kbFacts": self._kb_facts(kb_graph),
            },
            "care": {
                "latest": care_snapshot,
                "viewerFamilyMemberID": viewer_family_member_id,
            },
            "voice": {
                "cloneReady": clone_ready,
                "voiceProfileId": usable_voice_profile.get("voiceProfileId") if usable_voice_profile else None,
                "sampleStatus": usable_voice_profile.get("sampleStatus") if usable_voice_profile else "notProvided",
                "qualityAcceptanceRequired": bool(
                    usable_voice_profile.get("qualityAcceptanceRequired") if usable_voice_profile else False
                ),
                "synthesisProviderReady": synthesis_ready,
                "outputMode": "tencentAudioDrive",
            },
            "digitalHuman": {
                "sessionReady": digital_human_ready,
                "provider": digital_human_runtime.get("provider") or "tencent",
                "providerMode": digital_human_runtime.get("providerMode") or "mockContract",
                "driveModes": digital_human_runtime.get("driveModes") or [],
                "fallbackMode": digital_human_runtime.get("fallbackMode") or "audioOnly",
            },
            "policy": {
                "privacyMode": "standard",
                "canUseFamilyData": persona_scope == "family",
                "canUseVoiceClone": clone_ready,
                "crossScopeArchiveIncluded": privacy_scope["crossScopeArchiveIncluded"],
                "privacyScope": privacy_scope,
            },
            "trace": trace,
            "fallbacks": fallbacks,
            "debug": {
                "sourceCounts": {
                    "archiveItemsAvailable": len(all_archive_items),
                    "archiveItemsIncluded": len(included_archive_items),
                    "kbPeople": len(kb_graph.get("people") or []),
                    "kbPlaces": len(kb_graph.get("places") or []),
                    "kbEvents": len(kb_graph.get("events") or []),
                    "kbFacts": len(kb_graph.get("facts") or []),
                    "voiceProfiles": len(voice_profiles),
                    "careSnapshotAvailable": 1 if care_snapshot else 0,
                },
                "latencyMs": latency_ms,
            },
        }

    @staticmethod
    def _required_text(payload: Dict[str, Any], key: str) -> str:
        value = str(payload.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} is required")
        return value

    @staticmethod
    def _text(value: Any, default: str) -> str:
        text = str(value or "").strip()
        return text or default

    @staticmethod
    def _optional_text(value: Any) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _normal_persona_scope(value: Any) -> str:
        scope = str(value or "personal").strip()
        return "family" if scope == "family" else "personal"

    def _latest_care_snapshot(
        self,
        user_id: str,
        persona_scope: str,
        viewer_family_member_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if persona_scope == "family" and viewer_family_member_id:
            return self.store.get_latest_care_snapshot(user_id, viewer_family_member_id=viewer_family_member_id)
        return self.store.get_latest_care_snapshot(user_id)

    def _archive_matches_scope(
        self,
        item: Dict[str, Any],
        *,
        user_id: str,
        persona_scope: str,
        digital_human_id: str,
    ) -> bool:
        item_scope = self._normal_persona_scope(item.get("personaScope"))
        item_digital_human_id = self._text(item.get("digitalHumanId"), user_id)
        if persona_scope == "family":
            return item_scope == "family" and item_digital_human_id == digital_human_id
        return item_scope == "personal" and item_digital_human_id in {user_id, digital_human_id}

    def _has_cross_scope_archive(
        self,
        items: List[Dict[str, Any]],
        *,
        persona_scope: str,
        digital_human_id: str,
        user_id: str,
    ) -> bool:
        return any(
            not self._archive_matches_scope(
                item,
                user_id=user_id,
                persona_scope=persona_scope,
                digital_human_id=digital_human_id,
            )
            for item in items
        )

    def _privacy_scope(
        self,
        *,
        user_id: str,
        persona_scope: str,
        digital_human_id: str,
        viewer_family_member_id: Optional[str],
        cross_scope_archive_included: bool,
    ) -> Dict[str, Any]:
        if persona_scope == "family":
            allowed_archive_scopes = ["family"]
            allowed_digital_human_ids = [digital_human_id]
            scope_label = f"family:{digital_human_id}"
        else:
            allowed_archive_scopes = ["personal"]
            allowed_digital_human_ids = sorted({user_id, digital_human_id})
            scope_label = f"personal:{digital_human_id}"

        return {
            "scope": persona_scope,
            "scopeLabel": scope_label,
            "viewerUserId": user_id,
            "ownerUserId": user_id,
            "digitalHumanId": digital_human_id,
            "viewerFamilyMemberID": viewer_family_member_id,
            "allowedArchiveScopes": allowed_archive_scopes,
            "allowedDigitalHumanIds": allowed_digital_human_ids,
            "canUseFamilyData": persona_scope == "family",
            "crossScopeArchiveIncluded": cross_scope_archive_included,
        }

    @staticmethod
    def _trace_summary(
        *,
        archive_items: List[Dict[str, Any]],
        kb_graph: Dict[str, Any],
        usable_voice_profile: Optional[Dict[str, Any]],
        clone_ready: bool,
        digital_human_ready: bool,
        digital_human_provider_mode: str,
        privacy_scope: Dict[str, Any],
        fallbacks: List[str],
        latency_ms: int,
    ) -> Dict[str, Any]:
        return {
            "archiveItemIds": [str(item.get("id")) for item in archive_items if item.get("id")],
            "archiveItemKinds": [str(item.get("kind") or "unknown") for item in archive_items],
            "archiveItemsIncluded": len(archive_items),
            "kbFactCount": len(kb_graph.get("facts") or []),
            "voiceProfileId": usable_voice_profile.get("voiceProfileId") if usable_voice_profile else None,
            "voiceCloneReady": clone_ready,
            "voiceOutputMode": "tencentAudioDrive",
            "digitalHumanSessionReady": digital_human_ready,
            "digitalHumanProviderMode": digital_human_provider_mode,
            "fallbacks": list(fallbacks),
            "privacyScope": privacy_scope["scopeLabel"],
            "crossScopeArchiveIncluded": privacy_scope["crossScopeArchiveIncluded"],
            "latencyMs": latency_ms,
        }

    @staticmethod
    def _archive_summary(item: Dict[str, Any]) -> Dict[str, Any]:
        allowed_keys = [
            "id",
            "kind",
            "title",
            "note",
            "description",
            "analysisStatus",
            "analysisRetryable",
            "detectedPeople",
            "detectedLocations",
            "detectedScenes",
            "tags",
            "personaScope",
            "digitalHumanId",
            "createdAt",
            "updatedAt",
        ]
        return {key: deepcopy(item[key]) for key in allowed_keys if key in item}

    @staticmethod
    def _kb_people(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"id": item.get("id"), "name": item.get("name"), "relation": item.get("relation")}
            for item in (graph.get("people") or [])[:8]
            if isinstance(item, dict)
        ]

    @staticmethod
    def _kb_places(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"id": item.get("id"), "name": item.get("name"), "category": item.get("category")}
            for item in (graph.get("places") or [])[:8]
            if isinstance(item, dict)
        ]

    @staticmethod
    def _kb_events(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"id": item.get("id"), "title": item.get("title"), "date": item.get("date")}
            for item in (graph.get("events") or [])[:8]
            if isinstance(item, dict)
        ]

    @staticmethod
    def _kb_facts(graph: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"id": item.get("id"), "statement": item.get("statement")}
            for item in (graph.get("facts") or [])[:8]
            if isinstance(item, dict)
        ]

    def _first_usable_voice_profile(
        self,
        profiles: List[Dict[str, Any]],
        persona_scope: str,
        digital_human_id: str,
        user_id: str,
    ) -> Optional[Dict[str, Any]]:
        for profile in profiles:
            if str(profile.get("sampleStatus") or "") != "ready":
                continue
            if not bool(profile.get("isEnabled")):
                continue
            if not bool(profile.get("realCloneProviderReady")):
                continue
            if bool(profile.get("qualityAcceptanceRequired")):
                continue
            if self._normal_persona_scope(profile.get("personaScope")) != persona_scope:
                continue
            profile_digital_human_id = self._text(profile.get("digitalHumanId"), user_id)
            if profile_digital_human_id not in {digital_human_id, user_id}:
                continue
            return profile
        return None
