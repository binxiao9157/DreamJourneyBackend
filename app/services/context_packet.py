import hashlib
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.config import Settings
from app.services.privacy import AI_PROCESSABLE_SCOPES
from app.services.runtime_config import RuntimeConfigService


class ContextPacketBuilder:
    schema_version = 1
    generation_context_version = "echo-generation-context-v1"
    generation_context_max_chars = 12000
    generation_context_sources = ("archive", "kbFact", "persona", "care")

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
        family_viewer_active = self._family_viewer_active(user_id, viewer_family_member_id)

        all_archive_items = self.store.list_archive_items(user_id)
        archive_context = self._build_archive_context(
            all_archive_items,
            user_id=user_id,
            persona_scope=persona_scope,
            digital_human_id=digital_human_id,
            viewer_family_member_id=viewer_family_member_id,
            family_viewer_active=family_viewer_active,
            query=query,
        )
        included_archive_items = archive_context["archiveItems"]
        filtered_context = archive_context["filteredContext"]
        archive_trace_candidates = archive_context["traceCandidates"]
        kb_graph = self.store.get_kb_snapshot(user_id) or {}
        care_snapshot = None
        if family_viewer_active:
            care_snapshot = self._summarized_care_snapshot(
                self._latest_care_snapshot(user_id, persona_scope, viewer_family_member_id)
            )
        supplemental_trace_candidates = self._supplemental_context_candidates(
            kb_graph=kb_graph,
            persona_scope=persona_scope,
            digital_human_id=digital_human_id,
            lifecycle_mode=lifecycle_mode,
            viewer_family_member_id=viewer_family_member_id,
            family_viewer_active=family_viewer_active,
            care_snapshot=care_snapshot,
            query=query,
        )
        selected_context, ranking_trace = self._rank_context_candidates(
            archive_trace_candidates + supplemental_trace_candidates
        )
        generation_context = self._build_generation_context(
            selected_context=selected_context,
            archive_candidates=archive_trace_candidates,
            kb_graph=kb_graph,
            care_snapshot=care_snapshot,
        )
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
        if viewer_family_member_id and not family_viewer_active:
            fallbacks.append("family_viewer_not_active")
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
            family_viewer_active=family_viewer_active,
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
            selected_context=selected_context,
            filtered_context=filtered_context,
            ranking_trace=ranking_trace,
        )
        return {
            "schemaVersion": self.schema_version,
            "contextVersion": "echo-context-v2",
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
            "selectedContext": selected_context,
            "filteredContext": filtered_context,
            "rankingTrace": ranking_trace,
            "generationContext": generation_context,
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
                "canUseFamilyData": persona_scope == "family" and family_viewer_active,
                "familyViewerActive": family_viewer_active,
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
                    "archiveItemsFiltered": len(filtered_context),
                    "rankingTraceItems": len(ranking_trace),
                    "selectedContextTotal": len(selected_context),
                    "selectedContextArchive": self._selected_context_source_count(selected_context, "archive"),
                    "selectedContextKbFacts": self._selected_context_source_count(selected_context, "kbFact"),
                    "selectedContextPersona": self._selected_context_source_count(selected_context, "persona"),
                    "selectedContextCare": self._selected_context_source_count(selected_context, "care"),
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

    @staticmethod
    def _summarized_care_snapshot(item: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        snapshot = item.get("snapshot")
        if not isinstance(snapshot, dict):
            return {
                key: deepcopy(item[key])
                for key in ("id", "userId", "viewerFamilyMemberID", "createdAt")
                if key in item
            }

        safe_snapshot = {
            key: deepcopy(snapshot[key])
            for key in ("riskLevel", "summary", "suggestions", "trendSummary", "metadataOnly", "contentRedacted")
            if key in snapshot
        }
        summarized = {
            key: deepcopy(item[key])
            for key in ("id", "userId", "viewerFamilyMemberID", "createdAt")
            if key in item
        }
        summarized["snapshot"] = safe_snapshot
        return summarized

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

    def _build_archive_context(
        self,
        archive_items: List[Dict[str, Any]],
        *,
        user_id: str,
        persona_scope: str,
        digital_human_id: str,
        viewer_family_member_id: Optional[str],
        family_viewer_active: bool,
        query: str,
    ) -> Dict[str, Any]:
        candidates: List[Dict[str, Any]] = []
        filtered_context: List[Dict[str, Any]] = []

        for index, item in enumerate(archive_items):
            reason = self._archive_filter_reason(
                item,
                user_id=user_id,
                persona_scope=persona_scope,
                digital_human_id=digital_human_id,
                viewer_family_member_id=viewer_family_member_id,
                family_viewer_active=family_viewer_active,
            )
            if reason:
                filtered_context.append(self._filtered_archive_context(item, reason))
                continue

            candidate = self._archive_candidate(item, query=query, original_index=index)
            if candidate is None:
                filtered_context.append(self._filtered_archive_context(item, "empty_context"))
                continue
            candidates.append(candidate)

        candidates.sort(key=lambda item: (-item["score"], item["originalIndex"], item["refId"]))
        selected_candidates = candidates[:8]

        return {
            "archiveItems": [self._archive_summary(candidate["item"]) for candidate in selected_candidates],
            "filteredContext": filtered_context,
            "traceCandidates": selected_candidates,
        }

    def _archive_filter_reason(
        self,
        item: Dict[str, Any],
        *,
        user_id: str,
        persona_scope: str,
        digital_human_id: str,
        viewer_family_member_id: Optional[str],
        family_viewer_active: bool,
    ) -> Optional[str]:
        if viewer_family_member_id and not family_viewer_active:
            return "family_viewer_not_active"

        if not self._archive_matches_scope(
            item,
            user_id=user_id,
            persona_scope=persona_scope,
            digital_human_id=digital_human_id,
        ):
            return "scope_mismatch"

        if str(item.get("kind") or "").strip() == "timeLetter":
            delivery_state = self._time_letter_delivery_state(item)
            if delivery_state in {"draft", "editing"}:
                return "time_letter_draft"
            if (
                viewer_family_member_id
                and self._time_letter_targets_viewer(item, viewer_family_member_id)
                and self._time_letter_opens_in_future(item)
            ):
                return "time_letter_not_open_for_recipient"

        analysis_status = str(item.get("analysisStatus") or "").strip()
        if analysis_status == "failed" and not self._archive_has_usable_context(item):
            return "analysis_failed_empty_context"

        if not self._archive_has_usable_context(item):
            return "empty_context"
        return None

    @staticmethod
    def _filtered_archive_context(item: Dict[str, Any], reason: str) -> Dict[str, Any]:
        return {
            "source": "archive",
            "refId": str(item.get("id") or ""),
            "kind": str(item.get("kind") or "unknown"),
            "reason": reason,
            "analysisStatus": str(item.get("analysisStatus") or "unknown"),
            "personaScope": str(item.get("personaScope") or "personal"),
            "digitalHumanId": str(item.get("digitalHumanId") or ""),
        }

    def _archive_candidate(
        self,
        item: Dict[str, Any],
        *,
        query: str,
        original_index: int,
    ) -> Optional[Dict[str, Any]]:
        text_fields = self._archive_text_fields(item)
        clue_fields = self._archive_clue_fields(item)
        if not text_fields and not any(clue_fields.values()):
            return None

        score_breakdown = self._archive_score_breakdown(text_fields, clue_fields, query)
        score = sum(score_breakdown.values())
        return {
            "item": item,
            "source": "archive",
            "refId": str(item.get("id") or ""),
            "kind": str(item.get("kind") or "unknown"),
            "title": self._optional_text(item.get("title")),
            "reason": "scope_and_relevance_match",
            "score": score,
            "scoreBreakdown": score_breakdown,
            "confidence": round(min(0.98, max(0.25, score / 100.0)), 2),
            "originalIndex": original_index,
            "analysisStatus": str(item.get("analysisStatus") or "unknown"),
            "signals": {
                "hasUserText": bool(text_fields),
                "peopleCount": len(clue_fields["people"]),
                "locationCount": len(clue_fields["locations"]),
                "sceneCount": len(clue_fields["scenes"]),
                "tagCount": len(clue_fields["tags"]),
            },
        }

    def _supplemental_context_candidates(
        self,
        *,
        kb_graph: Dict[str, Any],
        persona_scope: str,
        digital_human_id: str,
        lifecycle_mode: str,
        viewer_family_member_id: Optional[str],
        family_viewer_active: bool,
        care_snapshot: Optional[Dict[str, Any]],
        query: str,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        candidates.extend(self._kb_fact_candidates(kb_graph, query=query))
        candidates.append(
            self._persona_candidate(
                persona_scope=persona_scope,
                digital_human_id=digital_human_id,
                lifecycle_mode=lifecycle_mode,
                viewer_family_member_id=viewer_family_member_id,
                family_viewer_active=family_viewer_active,
            )
        )
        care_candidate = self._care_candidate(care_snapshot, query=query)
        if care_candidate is not None:
            candidates.append(care_candidate)
        return candidates

    def _kb_fact_candidates(self, kb_graph: Dict[str, Any], *, query: str) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        facts = kb_graph.get("facts") or []
        for index, fact in enumerate(facts[:8]):
            if not isinstance(fact, dict):
                continue
            statement = str(fact.get("statement") or "").strip()
            if not statement:
                continue
            ref_id = str(fact.get("id") or f"kb_fact_{index + 1}").strip()
            score_breakdown = {
                "base": 32,
                "userText": 12,
                "analysisSignals": 0,
                "queryMatch": self._query_match_score([statement], {}, query),
            }
            score = sum(score_breakdown.values())
            candidates.append(
                {
                    "source": "kbFact",
                    "refId": ref_id,
                    "kind": "fact",
                    "title": statement[:80],
                    "reason": "kblite_fact_available",
                    "score": score,
                    "scoreBreakdown": score_breakdown,
                    "confidence": round(min(0.95, max(0.3, score / 100.0)), 2),
                    "originalIndex": 2000 + index,
                    "analysisStatus": "notApplicable",
                    "signals": {
                        "statementAvailable": True,
                    },
                }
            )
        return candidates

    @staticmethod
    def _persona_candidate(
        *,
        persona_scope: str,
        digital_human_id: str,
        lifecycle_mode: str,
        viewer_family_member_id: Optional[str],
        family_viewer_active: bool,
    ) -> Dict[str, Any]:
        score_breakdown = {
            "base": 36,
            "userText": 0,
            "analysisSignals": 12,
            "queryMatch": 0,
        }
        score = sum(score_breakdown.values())
        return {
            "source": "persona",
            "refId": f"persona:{persona_scope}:{digital_human_id}",
            "kind": lifecycle_mode,
            "title": f"{persona_scope}:{digital_human_id}",
            "reason": "active_persona_scope",
            "score": score,
            "scoreBreakdown": score_breakdown,
            "confidence": 0.7,
            "originalIndex": 3000,
            "analysisStatus": "notApplicable",
            "signals": {
                "personaScope": persona_scope,
                "digitalHumanId": digital_human_id,
                "lifecycleMode": lifecycle_mode,
                "viewerFamilyMemberID": viewer_family_member_id,
                "familyViewerActive": family_viewer_active,
            },
        }

    def _care_candidate(self, care_snapshot: Optional[Dict[str, Any]], *, query: str) -> Optional[Dict[str, Any]]:
        if not isinstance(care_snapshot, dict):
            return None
        snapshot = care_snapshot.get("snapshot")
        if not isinstance(snapshot, dict):
            return None
        summary = str(snapshot.get("summary") or "").strip()
        trend_summary = str(snapshot.get("trendSummary") or "").strip()
        risk_level = str(snapshot.get("riskLevel") or "").strip()
        suggestions = snapshot.get("suggestions") if isinstance(snapshot.get("suggestions"), list) else []
        searchable_text = [text for text in [summary, trend_summary, risk_level] if text]
        score_breakdown = {
            "base": 34,
            "userText": 10 if searchable_text else 0,
            "analysisSignals": 8 if risk_level else 0,
            "queryMatch": self._query_match_score(searchable_text, {}, query),
        }
        score = sum(score_breakdown.values())
        return {
            "source": "care",
            "refId": "care:latest",
            "kind": "snapshot",
            "title": summary[:80] if summary else risk_level or "care snapshot",
            "reason": "care_snapshot_summary_available",
            "score": score,
            "scoreBreakdown": score_breakdown,
            "confidence": round(min(0.92, max(0.3, score / 100.0)), 2),
            "originalIndex": 4000,
            "analysisStatus": "notApplicable",
            "signals": {
                "snapshotId": str(care_snapshot.get("id") or ""),
                "riskLevel": risk_level,
                "hasSummary": bool(summary),
                "suggestionsCount": len(suggestions),
                "hasTrendSummary": bool(trend_summary),
            },
        }

    def _rank_context_candidates(self, candidates: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        ranked = sorted(candidates, key=lambda item: (-item["score"], item["originalIndex"], item["refId"]))
        selected = [
            self._selected_context_entry(candidate, rank=index + 1)
            for index, candidate in enumerate(ranked[:16])
        ]
        ranking = [
            self._ranking_trace_entry(candidate, rank=index + 1)
            for index, candidate in enumerate(ranked[:24])
        ]
        return selected, ranking

    def _build_generation_context(
        self,
        *,
        selected_context: List[Dict[str, Any]],
        archive_candidates: List[Dict[str, Any]],
        kb_graph: Dict[str, Any],
        care_snapshot: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        archive_by_ref: Dict[str, Dict[str, Any]] = {}
        for candidate in archive_candidates:
            item = candidate.get("item")
            ref_id = str(candidate.get("refId") or "").strip()
            if ref_id and isinstance(item, dict) and self._generation_allowed(item):
                archive_by_ref.setdefault(ref_id, item)

        kb_fact_by_ref: Dict[str, Dict[str, Any]] = {}
        for index, fact in enumerate((kb_graph.get("facts") or [])[:8]):
            if not isinstance(fact, dict) or not self._generation_allowed(fact):
                continue
            statement = self._generation_text_value(fact.get("statement"))
            if not statement:
                continue
            ref_id = str(fact.get("id") or f"kb_fact_{index + 1}").strip()
            if ref_id:
                kb_fact_by_ref.setdefault(ref_id, fact)

        rendered_entries: List[tuple[Dict[str, str], str]] = []
        seen_refs = set()
        for selected in selected_context:
            source = str(selected.get("source") or "").strip()
            ref_id = str(selected.get("refId") or "").strip()
            ref_key = (source, ref_id)
            if source not in self.generation_context_sources or not ref_id or ref_key in seen_refs:
                continue

            line = ""
            if source == "archive":
                line = self._archive_generation_line(archive_by_ref.get(ref_id))
            elif source == "kbFact":
                line = self._kb_fact_generation_line(kb_fact_by_ref.get(ref_id))
            elif source == "persona":
                line = self._persona_generation_line(selected)
            elif source == "care" and ref_id == "care:latest":
                line = self._care_generation_line(care_snapshot)
            if not line:
                continue

            seen_refs.add(ref_key)
            rendered_entries.append(
                (
                    {
                        "source": source,
                        "refId": ref_id,
                        "kind": str(selected.get("kind") or "unknown"),
                    },
                    line,
                )
            )

        text, source_refs, truncated = self._bounded_generation_text(rendered_entries)
        source_counts = {source: 0 for source in self.generation_context_sources}
        for source_ref in source_refs:
            source = source_ref["source"]
            source_counts[source] += 1

        return {
            "version": self.generation_context_version,
            "text": text,
            "sourceRefs": source_refs,
            "sourceCounts": source_counts,
            "contentHash": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "maxChars": self.generation_context_max_chars,
            "truncated": truncated,
        }

    @staticmethod
    def _generation_allowed(item: Dict[str, Any]) -> bool:
        privacy_metadata = item.get("privacyMetadata")
        if not isinstance(privacy_metadata, dict):
            return False
        return str(privacy_metadata.get("scope") or "") in AI_PROCESSABLE_SCOPES

    @staticmethod
    def _generation_text_value(value: Any) -> str:
        return " ".join(str(value or "").split())

    @classmethod
    def _generation_text_list(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        result: List[str] = []
        for entry in value:
            text = cls._generation_text_value(entry)
            if text and text not in result:
                result.append(text)
        return result

    @classmethod
    def _archive_generation_line(cls, item: Optional[Dict[str, Any]]) -> str:
        if not isinstance(item, dict):
            return ""

        parts: List[str] = []
        for key, label in (("title", "title"), ("note", "note"), ("description", "description")):
            text = cls._generation_text_value(item.get(key))
            if text:
                parts.append(f"{label}={text}")
        for key, label in (
            ("detectedPeople", "people"),
            ("detectedLocations", "locations"),
            ("detectedScenes", "scenes"),
            ("tags", "tags"),
        ):
            values = cls._generation_text_list(item.get(key))
            if values:
                parts.append(f"{label}={', '.join(values)}")
        if not parts:
            return ""

        kind = cls._generation_text_value(item.get("kind")) or "unknown"
        return f"[archive] kind={kind}; " + "; ".join(parts)

    @classmethod
    def _kb_fact_generation_line(cls, fact: Optional[Dict[str, Any]]) -> str:
        if not isinstance(fact, dict):
            return ""
        statement = cls._generation_text_value(fact.get("statement"))
        return f"[kbFact] {statement}" if statement else ""

    @classmethod
    def _persona_generation_line(cls, selected: Dict[str, Any]) -> str:
        signals = selected.get("signals")
        if not isinstance(signals, dict):
            return ""
        parts = []
        for key, label in (("personaScope", "scope"), ("lifecycleMode", "lifecycle")):
            text = cls._generation_text_value(signals.get(key))
            if text:
                parts.append(f"{label}={text}")
        return "[persona] " + "; ".join(parts) if parts else ""

    @classmethod
    def _care_generation_line(cls, care_snapshot: Optional[Dict[str, Any]]) -> str:
        if not isinstance(care_snapshot, dict):
            return ""
        snapshot = care_snapshot.get("snapshot")
        if not isinstance(snapshot, dict):
            return ""

        parts: List[str] = []
        for key, label in (("riskLevel", "risk"), ("summary", "summary"), ("trendSummary", "trend")):
            text = cls._generation_text_value(snapshot.get(key))
            if text:
                parts.append(f"{label}={text}")
        suggestions = cls._generation_text_list(snapshot.get("suggestions"))
        if suggestions:
            parts.append(f"suggestions={'; '.join(suggestions)}")
        return "[care] " + "; ".join(parts) if parts else ""

    @classmethod
    def _bounded_generation_text(
        cls,
        entries: List[tuple[Dict[str, str], str]],
    ) -> tuple[str, List[Dict[str, str]], bool]:
        lines: List[str] = []
        source_refs: List[Dict[str, str]] = []
        truncated = False
        current_length = 0

        for source_ref, line in entries:
            separator_length = 1 if lines else 0
            available = cls.generation_context_max_chars - current_length - separator_length
            if available <= 0:
                truncated = True
                break
            if len(line) > available:
                truncated = True
                if not lines:
                    lines.append(line[:available])
                    source_refs.append(source_ref)
                break
            lines.append(line)
            source_refs.append(source_ref)
            current_length += separator_length + len(line)

        return "\n".join(lines), source_refs, truncated

    @staticmethod
    def _selected_context_entry(candidate: Dict[str, Any], *, rank: int) -> Dict[str, Any]:
        return {
            "source": candidate["source"],
            "refId": candidate["refId"],
            "kind": candidate["kind"],
            "title": candidate.get("title"),
            "rank": rank,
            "reason": candidate["reason"],
            "score": candidate["score"],
            "confidence": candidate["confidence"],
            "analysisStatus": candidate["analysisStatus"],
            "signals": deepcopy(candidate["signals"]),
        }

    @staticmethod
    def _ranking_trace_entry(candidate: Dict[str, Any], *, rank: int) -> Dict[str, Any]:
        return {
            "source": candidate["source"],
            "refId": candidate["refId"],
            "kind": candidate["kind"],
            "rank": rank,
            "score": candidate["score"],
            "scoreBreakdown": deepcopy(candidate["scoreBreakdown"]),
            "reason": candidate["reason"],
        }

    def _archive_score_breakdown(
        self,
        text_fields: List[str],
        clue_fields: Dict[str, List[str]],
        query: str,
    ) -> Dict[str, int]:
        breakdown = {
            "base": 40,
            "userText": 16 if text_fields else 0,
            "analysisSignals": min(24, 4 * sum(len(values) for values in clue_fields.values())),
            "queryMatch": self._query_match_score(text_fields, clue_fields, query),
        }
        return breakdown

    @staticmethod
    def _query_match_score(
        text_fields: List[str],
        clue_fields: Dict[str, List[str]],
        query: str,
    ) -> int:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return 0

        searchable_values = text_fields + [
            value
            for values in clue_fields.values()
            for value in values
        ]
        score = 0
        for value in searchable_values:
            text = str(value or "").strip()
            if not text:
                continue
            if text in normalized_query or normalized_query in text:
                score += 8
        return min(score, 24)

    def _archive_has_usable_context(self, item: Dict[str, Any]) -> bool:
        return bool(self._archive_text_fields(item) or any(self._archive_clue_fields(item).values()))

    @staticmethod
    def _archive_text_fields(item: Dict[str, Any]) -> List[str]:
        values = []
        for key in ("note", "description", "title"):
            text = str(item.get(key) or "").strip()
            if text:
                values.append(text)
        return values

    @staticmethod
    def _archive_clue_fields(item: Dict[str, Any]) -> Dict[str, List[str]]:
        def string_list(key: str) -> List[str]:
            value = item.get(key)
            if not isinstance(value, list):
                return []
            return [str(entry).strip() for entry in value if str(entry or "").strip()]

        return {
            "people": string_list("detectedPeople"),
            "locations": string_list("detectedLocations"),
            "scenes": string_list("detectedScenes"),
            "tags": string_list("tags"),
        }

    @staticmethod
    def _time_letter_delivery_state(item: Dict[str, Any]) -> str:
        metadata = item.get("metadata")
        metadata_state = ""
        if isinstance(metadata, dict):
            metadata_state = str(
                metadata.get("deliveryState")
                or metadata.get("deliveryStatus")
                or metadata.get("timeLetterStatus")
                or ""
            ).strip()
        return str(
            item.get("deliveryState")
            or item.get("deliveryStatus")
            or metadata_state
            or ""
        ).strip()

    @staticmethod
    def _time_letter_targets_viewer(item: Dict[str, Any], viewer_family_member_id: str) -> bool:
        viewer_id = str(viewer_family_member_id or "").strip()
        if not viewer_id:
            return False

        recipients = item.get("recipients")
        if isinstance(recipients, list):
            for recipient in recipients:
                if not isinstance(recipient, dict):
                    continue
                if str(recipient.get("id") or "").strip() == viewer_id:
                    return True

        metadata = item.get("metadata")
        if isinstance(metadata, dict):
            recipient_ids = str(metadata.get("recipientIds") or "").strip()
            if recipient_ids:
                tokens = {
                    token.strip()
                    for token in recipient_ids.replace(",", "|").split("|")
                    if token.strip()
                }
                return viewer_id in tokens

        return False

    @staticmethod
    def _time_letter_opens_in_future(item: Dict[str, Any]) -> bool:
        metadata = item.get("metadata")
        metadata_open_at = ""
        if isinstance(metadata, dict):
            metadata_open_at = str(metadata.get("openAt") or "").strip()
        raw_open_at = str(item.get("openAt") or metadata_open_at).strip()
        if not raw_open_at:
            return False
        normalized = raw_open_at.replace("Z", "+00:00")
        try:
            open_at = datetime.fromisoformat(normalized)
        except ValueError:
            return False
        if open_at.tzinfo is None:
            open_at = open_at.replace(tzinfo=timezone.utc)
        return open_at > datetime.now(timezone.utc)

    def _family_viewer_active(self, user_id: str, viewer_family_member_id: Optional[str]) -> bool:
        if viewer_family_member_id is None:
            return True
        viewer_id = str(viewer_family_member_id or "").strip()
        if not viewer_id:
            return True
        for member in self.store.list_family_members(user_id):
            if str(member.get("id") or "").strip() != viewer_id:
                continue
            return member.get("accessStatus") == "active" and member.get("invitationStatus") == "accepted"
        return False

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
        family_viewer_active: bool,
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
            "canUseFamilyData": persona_scope == "family" and family_viewer_active,
            "familyViewerActive": family_viewer_active,
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
        selected_context: List[Dict[str, Any]],
        filtered_context: List[Dict[str, Any]],
        ranking_trace: List[Dict[str, Any]],
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
            "selectedContextCount": len(selected_context),
            "filteredContextCount": len(filtered_context),
            "rankingTraceCount": len(ranking_trace),
            "selectedContextRefs": [
                str(item.get("refId"))
                for item in selected_context
                if item.get("refId")
            ],
            "selectedContextSourceCounts": ContextPacketBuilder._context_source_counts(selected_context),
            "filteredContextReasons": [
                {
                    "refId": str(item.get("refId") or ""),
                    "reason": str(item.get("reason") or "unknown"),
                }
                for item in filtered_context
            ],
        }

    @staticmethod
    def _context_source_counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for item in items:
            source = str(item.get("source") or "unknown")
            counts[source] = counts.get(source, 0) + 1
        return counts

    @staticmethod
    def _selected_context_source_count(items: List[Dict[str, Any]], source: str) -> int:
        return sum(1 for item in items if str(item.get("source") or "") == source)

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
