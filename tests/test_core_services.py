import base64
import os
import urllib.error
import unittest
import wave
from io import BytesIO
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.core.config import Settings
from app.services.in_memory_store import InMemoryStore
from app.services.postgres_store import PostgresStore
from app.services.privacy import filter_syncable_graph, sanitize_care_snapshot_payload
from app.services.runtime_config import RuntimeConfigService
from app.services.store_factory import make_store
from app.services.tokens import TokenService
from app.services.tts import VolcTTSProxy, VolcVoiceCloneTTSProxy
from app.services.user_identity import stable_user_id
from app.services.voice_clone import VolcEngineVoiceCloneV3Provider, VoiceCloneProviderFactory
from app.services.amap import AMapDistrictProxy
from app.services.deepseek import DeepSeekImageAnalysisProxy, DeepSeekKnowledgeExtractionProxy


class PrivacyFilteringTests(unittest.TestCase):
    def test_backend_sync_filters_local_only_entities(self):
        graph = {
            "people": [
                {"id": "p1", "name": "测试用户", "privacyMetadata": {"scope": "generationAllowed"}},
                {"id": "p2", "name": "林桂芳", "privacyMetadata": {"scope": "localOnly"}},
            ],
            "places": [
                {"id": "l1", "name": "绍兴", "privacyMetadata": {"scope": "familyCircle"}},
                {"id": "l2", "name": "私密地址", "privacyMetadata": {"scope": "localOnly"}},
            ],
            "events": [
                {
                    "id": "e1",
                    "title": "开照相馆",
                    "participantIds": ["p1", "p2"],
                    "locationId": "l1",
                    "privacyMetadata": {"scope": "generationAllowed"},
                }
            ],
            "facts": [
                {
                    "id": "f1",
                    "statement": "可同步事实",
                    "relatedPersonIds": ["p1", "p2"],
                    "relatedPlaceIds": ["l1", "l2"],
                    "relatedEventIds": ["e1"],
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
                {"id": "f2", "statement": "本机事实", "privacyMetadata": {"scope": "localOnly"}},
            ],
        }

        filtered = filter_syncable_graph(graph)

        self.assertEqual([p["id"] for p in filtered["people"]], ["p1"])
        self.assertEqual([p["id"] for p in filtered["places"]], ["l1"])
        self.assertEqual(filtered["events"][0]["participantIds"], ["p1"])
        self.assertEqual(filtered["facts"][0]["relatedPersonIds"], ["p1"])
        self.assertEqual(filtered["facts"][0]["relatedPlaceIds"], ["l1"])
        self.assertEqual([f["id"] for f in filtered["facts"]], ["f1"])

    def test_backend_sync_redacts_source_ref_titles(self):
        graph = {
            "people": [
                {
                    "id": "p1",
                    "name": "陈建国",
                    "privacyMetadata": {
                        "scope": "generationAllowed",
                        "sourceRefs": [
                            {
                                "kind": "conversationTurn",
                                "id": "conversation-1",
                                "title": "用户对话 1：我叫陈建国，1968年住在绍兴越城区仓桥直街。",
                            }
                        ],
                    },
                }
            ],
            "places": [
                {
                    "id": "l1",
                    "name": "绍兴",
                    "privacyMetadata": {
                        "scope": "familyCircle",
                        "sourceRefs": [
                            {
                                "kind": "memoryArchiveItem",
                                "id": "archive-1",
                                "title": "仓桥直街旧照片",
                            }
                        ],
                    },
                }
            ],
            "events": [],
            "facts": [],
        }

        filtered = filter_syncable_graph(graph)
        serialized = str(filtered)

        self.assertNotIn("1968年住在绍兴越城区仓桥直街", serialized)
        self.assertNotIn("仓桥直街旧照片", serialized)
        self.assertEqual(
            filtered["people"][0]["privacyMetadata"]["sourceRefs"][0]["title"],
            "对话来源",
        )
        self.assertEqual(
            filtered["places"][0]["privacyMetadata"]["sourceRefs"][0]["title"],
            "档案素材",
        )

    def test_care_snapshot_sanitizer_keeps_only_aggregate_fields(self):
        snapshot = {
            "generatedAt": "2026-06-13T00:00:00Z",
            "windowStart": "2026-06-07T00:00:00Z",
            "windowEnd": "2026-06-13T00:00:00Z",
            "windowDayCount": 7,
            "dataCoverageSummary": "近 7 天 6 轮授权对话",
            "totalTurns": 10,
            "userTurnCount": 6,
            "characterCount": 180,
            "uniqueTokenCount": 55,
            "lexicalDiversity": 0.61,
            "negativeEmotionMentions": 1,
            "sleepMentions": 3,
            "bodyDiscomfortMentions": 2,
            "repetitionRatio": 0.25,
            "riskLevel": "watch",
            "summary": "睡眠和身体不适信号较多。",
            "suggestions": ["建议女儿今晚打电话确认近况。"],
            "weeklyHighlights": ["连续提到睡不好。"],
            "riskSignalDescriptions": ["睡眠信号 3 次。"],
            "dailyTrend": [
                {
                    "date": "2026-06-12T00:00:00Z",
                    "userTurnCount": 6,
                    "negativeEmotionMentions": 1,
                    "sleepMentions": 3,
                    "bodyDiscomfortMentions": 2,
                    "repetitionRatio": 0.25,
                    "signalScore": 6,
                    "rawText": "CARE_RAW_SENTINEL 不应保存",
                }
            ],
            "trendSummary": "近 7 天睡眠信号较集中。",
            "rawTranscript": "CARE_RAW_SENTINEL 原始对话不应保存",
            "messages": [{"role": "user", "text": "CARE_RAW_SENTINEL"}],
            "sourceTexts": ["CARE_RAW_SENTINEL"],
            "metadata": {"transcript": "CARE_RAW_SENTINEL"},
        }

        sanitized = sanitize_care_snapshot_payload(snapshot)
        serialized = str(sanitized)

        self.assertEqual(sanitized["riskLevel"], "watch")
        self.assertEqual(sanitized["summary"], "睡眠和身体不适信号较多。")
        self.assertEqual(sanitized["dailyTrend"][0]["signalScore"], 6)
        self.assertNotIn("rawTranscript", sanitized)
        self.assertNotIn("messages", sanitized)
        self.assertNotIn("sourceTexts", sanitized)
        self.assertNotIn("metadata", sanitized)
        self.assertNotIn("rawText", sanitized["dailyTrend"][0])
        self.assertNotIn("CARE_RAW_SENTINEL", serialized)


class RuntimeConfigTests(unittest.TestCase):
    def test_runtime_config_exposes_capabilities_not_secrets(self):
        settings = Settings(
            deepseek_api_key="deepseek-secret",
            volcengine_api_key="volc-secret",
            volcengine_voice_type="zh_female_cancan_mars_bigtts",
            amap_web_service_key="amap-secret",
        )
        config = RuntimeConfigService(settings).public_config()

        serialized = str(config)
        self.assertNotIn("deepseek-secret", serialized)
        self.assertNotIn("volc-secret", serialized)
        self.assertNotIn("amap-secret", serialized)
        self.assertTrue(config["capabilities"]["deepseekProxy"])
        self.assertTrue(config["capabilities"]["ttsProxy"])
        self.assertEqual(config["voice"]["voiceType"], "zh_female_cancan_mars_bigtts")
        voice_clone = config["voiceClone"]
        self.assertEqual(voice_clone["synthesisEndpoint"], "/voice/synthesis")
        self.assertFalse(voice_clone["synthesisProviderReady"])
        self.assertEqual(
            voice_clone["lipSyncTimeline"],
            {
                "field": "visemeTimeline",
                "source": "providerOptional",
                "supported": False,
                "fallbackMode": "avAudioPlayerMetering",
                "contractVersion": 1,
            },
        )
        archive = config["archive"]
        self.assertEqual(archive["storageProvider"], "mockObjectStorage")
        self.assertEqual(archive["providerDisplayName"], "Mock Object Storage")
        self.assertEqual(archive["providerMode"], "mock")
        self.assertFalse(archive["requiresClientUpload"])
        self.assertEqual(archive["uploadURLScheme"], "mock")
        self.assertFalse(archive["realProviderReady"])
        self.assertEqual(archive["providerSwitchContractVersion"], 1)
        self.assertEqual(archive["clientUploadAction"], "metadataOnly")

    def test_runtime_config_separates_voice_clone_training_and_synthesis_capabilities(self):
        settings = Settings(
            volcengine_voice_clone_api_key="voice-clone-train-key",
            volcengine_voice_clone_tts_api_key=None,
        )

        config = RuntimeConfigService(settings).public_config()
        voice_clone = config["voiceClone"]

        self.assertTrue(config["capabilities"]["voiceClone"])
        self.assertTrue(voice_clone["enabled"])
        self.assertTrue(voice_clone["realProviderReady"])
        self.assertFalse(voice_clone["synthesisProviderReady"])
        self.assertEqual(voice_clone["fallbackMode"], "providerV3")
        self.assertEqual(voice_clone["speakerIdMode"], "customSpeakerId")
        self.assertFalse(voice_clone["consoleSpeakerIdConfigured"])

    def test_runtime_config_exposes_archive_image_analysis_capability(self):
        settings = Settings(deepseek_api_key="deepseek-secret")

        config = RuntimeConfigService(settings).public_config()

        capability = config["archiveImageAnalysis"]
        self.assertTrue(config["capabilities"]["archiveImageAnalysis"])
        self.assertTrue(capability["enabled"])
        self.assertEqual(capability["endpoint"], "/archive/image-analysis")
        self.assertEqual(capability["provider"], "deepseek/text-only")
        self.assertFalse(capability["supportsVision"])
        self.assertEqual(capability["fallbackMode"], "retryableFailure")
        self.assertEqual(
            capability["statuses"],
            ["pending", "analyzing", "analyzed", "failed", "retryable"],
        )
        self.assertNotIn("deepseek-secret", str(config))

    def test_runtime_config_marks_archive_image_analysis_unavailable_without_key(self):
        config = RuntimeConfigService(Settings(deepseek_api_key=None)).public_config()

        capability = config["archiveImageAnalysis"]
        self.assertFalse(config["capabilities"]["archiveImageAnalysis"])
        self.assertFalse(capability["enabled"])
        self.assertEqual(capability["provider"], "deepseek/text-only")
        self.assertFalse(capability["supportsVision"])
        self.assertEqual(capability["fallbackMode"], "retryableFailure")


class TokenAndProxyTests(unittest.TestCase):
    def test_realtime_token_uses_legacy_credentials_without_exposing_app_token(self):
        settings = Settings(
            volcengine_app_id="test-app-id",
            volcengine_app_key="PlgvMymc7f3tQnJ6",
            volcengine_app_token="access-token-secret",
        )

        payload = TokenService(settings).realtime_config(user_id="u1")

        self.assertEqual(payload["authMode"], "legacy")
        self.assertEqual(payload["appID"], "test-app-id")
        self.assertEqual(payload["appKey"], "PlgvMymc7f3tQnJ6")
        self.assertEqual(payload["appToken"], "access-token-secret")
        self.assertEqual(payload["resourceID"], "volc.speech.dialog")
        self.assertEqual(payload["address"], "wss://openspeech.bytedance.com")
        self.assertEqual(payload["uri"], "/api/v3/realtime/dialogue")
        self.assertEqual(payload["expiresInSeconds"], 3600)
        expires_at = datetime.fromisoformat(payload["expiresAt"].replace("Z", "+00:00"))
        self.assertGreater(expires_at, datetime.now(timezone.utc))
        self.assertEqual(payload["fallback"]["mode"], "localBuildSettings")
        self.assertTrue(payload["fallback"]["enabled"])
        self.assertNotIn("tokenRef", payload)

    def test_runtime_config_documents_realtime_token_endpoint_and_fallback(self):
        settings = Settings(volcengine_app_id="test-app-id", volcengine_app_token="access-token-secret")

        config = RuntimeConfigService(settings).public_config()

        self.assertTrue(config["capabilities"]["realtimeToken"])
        self.assertEqual(config["voice"]["runtimeConfigEndpoint"], "/voice/realtime-token")
        self.assertEqual(config["voice"]["fallback"]["mode"], "localBuildSettings")
        self.assertTrue(config["voice"]["fallback"]["enabled"])

    def test_tts_proxy_builds_volcengine_request(self):
        settings = Settings(
            volcengine_api_key="volc-secret",
            volcengine_voice_type="speaker-id",
        )
        proxy = VolcTTSProxy(settings)

        request = proxy.build_request(text="你好", user_id="u1")

        self.assertEqual(request["url"], "https://openspeech.bytedance.com/api/v1/tts")
        self.assertEqual(request["headers"]["x-api-key"], "volc-secret")
        self.assertEqual(request["json"]["audio"]["voice_type"], "speaker-id")
        self.assertEqual(request["json"]["request"]["text"], "你好")

    def test_voice_clone_tts_proxy_builds_v1_request_from_official_guide(self):
        settings = Settings(
            volcengine_voice_clone_api_key="voice-clone-train-secret",
            volcengine_voice_clone_tts_api_key="voice-clone-tts-secret",
            volcengine_voice_clone_tts_url="https://example.com/api/v1/tts",
            volcengine_voice_clone_tts_cluster="volcano_icl_custom",
        )
        proxy = VolcVoiceCloneTTSProxy(settings)

        request = proxy.build_synthesis_request(
            text="你好，欢迎回家。",
            user_id="u1",
            voice_profile_id="S_voice_001",
            audio_format="mp3",
            sample_rate=24000,
            speech_rate=-10,
            loudness_rate=10,
        )
        audio = proxy.parse_tts_response({"code": 3000, "message": "Success", "data": "U09VTkQ="})
        timeline = proxy.parse_viseme_timeline(
            {
                "duration": 0.48,
                "frames": [
                    {"timeOffset": 0.24, "mouthShape": "oo", "intensity": 1.4},
                    {"timeOffset": 0.0, "mouthShape": "neutral", "intensity": 0.1},
                    {"timeOffset": 0.12, "mouthShape": "aa", "intensity": -0.2},
                ],
            }
        )

        self.assertEqual(request["url"], "https://example.com/api/v1/tts")
        self.assertEqual(request["headers"]["x-api-key"], "voice-clone-tts-secret")
        self.assertNotEqual(request["headers"]["x-api-key"], "voice-clone-train-secret")
        self.assertNotIn("X-Api-Resource-Id", request["headers"])
        self.assertEqual(request["headers"]["Resource-Id"], "seed-icl-2.0")
        self.assertEqual(request["json"]["app"]["cluster"], "volcano_icl_custom")
        self.assertEqual(request["json"]["user"]["uid"], "u1")
        self.assertEqual(request["json"]["audio"]["voice_type"], "S_voice_001")
        self.assertEqual(request["json"]["audio"]["encoding"], "mp3")
        self.assertEqual(request["json"]["audio"]["speed_ratio"], 0.9)
        self.assertEqual(request["json"]["request"]["text"], "你好，欢迎回家。")
        self.assertEqual(request["json"]["request"]["operation"], "query")
        self.assertEqual(audio, b"SOUND")
        self.assertEqual(timeline["source"], "providerVisemeTimeline")
        self.assertEqual(timeline["duration"], 0.48)
        self.assertEqual(
            timeline["frames"],
            [
                {"timeOffset": 0.0, "mouthShape": "neutral", "intensity": 0.1},
                {"timeOffset": 0.12, "mouthShape": "aa", "intensity": 0.0},
                {"timeOffset": 0.24, "mouthShape": "oo", "intensity": 1.0},
            ],
        )

    def test_voice_clone_tts_proxy_attaches_provider_request_and_log_ids(self):
        settings = Settings(
            volcengine_voice_clone_tts_api_key="voice-clone-tts-secret",
            volcengine_voice_clone_tts_url="https://example.com/api/v1/tts",
        )
        proxy = VolcVoiceCloneTTSProxy(settings)

        class FakeResponse:
            headers = {"X-Tt-Logid": "tts-logid-123"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"code":3000,"message":"Success","data":"U09VTkQ="}'

        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            result = proxy.synthesize(
                text="你好，欢迎回家。",
                user_id="u1",
                voice_profile_id="S_voice_001",
                audio_format="mp3",
                sample_rate=24000,
                speech_rate=-10,
                loudness_rate=10,
            )

        self.assertEqual(result["providerLogId"], "tts-logid-123")
        self.assertTrue(result["providerRequestId"])
        self.assertEqual(result["voiceProfileId"], "S_voice_001")

    def test_voice_clone_synthesis_endpoint_returns_base64_audio_without_exposing_provider_key(self):
        class FakeVoiceCloneTTSProvider:
            provider_mode = "volcengineVoiceCloneV1TTS"
            is_configured = True

            def synthesize(self, *, text, user_id, voice_profile_id, audio_format, sample_rate, speech_rate, loudness_rate):
                return {
                    "audioBase64": "U09VTkQ=",
                    "audioFormat": audio_format,
                    "byteCount": 5,
                    "providerMode": self.provider_mode,
                    "voiceProfileId": voice_profile_id,
                    "providerRequestId": "req-synthesis-001",
                    "providerLogId": "log-synthesis-001",
                    "visemeTimeline": {
                        "source": "providerVisemeTimeline",
                        "duration": 0.36,
                        "frames": [
                            {"timeOffset": 0.0, "mouthShape": "neutral", "intensity": 0.1},
                            {"timeOffset": 0.18, "mouthShape": "aa", "intensity": 0.85},
                        ],
                    },
                }

        with patch("app.main.VoiceCloneTTSProviderFactory") as factory:
            factory.return_value.make.return_value = FakeVoiceCloneTTSProvider()
            response = TestClient(app).post(
                "/voice/synthesis",
                json={
                    "userId": "u1",
                    "voiceProfileId": "S_voice_001",
                    "text": "你好，欢迎回家。",
                    "format": "mp3",
                    "sampleRate": 24000,
                    "speechRate": -10,
                    "loudnessRate": 10,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "synthesized")
        self.assertEqual(payload["voiceProfileId"], "S_voice_001")
        self.assertEqual(payload["audio"]["data"], "U09VTkQ=")
        self.assertEqual(payload["audio"]["format"], "mp3")
        self.assertEqual(payload["providerMode"], "volcengineVoiceCloneV1TTS")
        self.assertEqual(payload["providerRequestId"], "req-synthesis-001")
        self.assertEqual(payload["providerLogId"], "log-synthesis-001")
        self.assertEqual(payload["visemeTimeline"]["source"], "providerVisemeTimeline")
        self.assertEqual(payload["visemeTimeline"]["frames"][1]["mouthShape"], "aa")
        self.assertNotIn("X-Api-Key", response.text)
        self.assertNotIn("voice-clone-secret", response.text)

    def test_voice_clone_synthesis_can_return_tencent_audio_drive_pcm_contract(self):
        source_wav = BytesIO()
        with wave.open(source_wav, "wb") as wav:
            wav.setnchannels(2)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes((b"\x00\x10\x00\x10" * 2400))

        class FakeVoiceCloneTTSProvider:
            provider_mode = "volcengineVoiceCloneV1TTS"
            is_configured = True

            def synthesize(self, *, text, user_id, voice_profile_id, audio_format, sample_rate, speech_rate, loudness_rate):
                self.requested_audio_format = audio_format
                self.requested_sample_rate = sample_rate
                return {
                    "audioBase64": base64.b64encode(source_wav.getvalue()).decode("ascii"),
                    "audioFormat": audio_format,
                    "byteCount": len(source_wav.getvalue()),
                    "providerMode": self.provider_mode,
                    "voiceProfileId": voice_profile_id,
                    "visemeTimeline": None,
                }

        fake_provider = FakeVoiceCloneTTSProvider()
        with patch("app.main.VoiceCloneTTSProviderFactory") as factory:
            factory.return_value.make.return_value = fake_provider
            response = TestClient(app).post(
                "/voice/synthesis",
                json={
                    "userId": "u1",
                    "voiceProfileId": "S_voice_001",
                    "text": "你好，欢迎回家。",
                    "format": "mp3",
                    "sampleRate": 24000,
                    "outputMode": "tencentAudioDrive",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        audio = payload["audio"]
        pcm = base64.b64decode(audio["data"])
        self.assertEqual(fake_provider.requested_audio_format, "wav")
        self.assertEqual(fake_provider.requested_sample_rate, 16000)
        self.assertEqual(payload["outputMode"], "tencentAudioDrive")
        self.assertEqual(audio["format"], "pcm16kMono")
        self.assertEqual(audio["sampleRate"], 16000)
        self.assertEqual(audio["bitsPerSample"], 16)
        self.assertEqual(audio["channelCount"], 1)
        self.assertEqual(audio["byteCount"], len(pcm))
        self.assertGreater(audio["durationSeconds"], 0)
        self.assertNotEqual(pcm[:4], b"RIFF")
        self.assertEqual(len(pcm) % 2, 0)
        self.assertNotIn("X-Api-Key", response.text)
        self.assertNotIn("voice-clone-secret", response.text)

    def test_amap_proxy_adds_server_side_key(self):
        settings = Settings(amap_web_service_key="amap-secret")
        proxy = AMapDistrictProxy(settings)

        url = proxy.build_url(keyword="绍兴市")

        self.assertIn("key=amap-secret", url)
        self.assertIn("keywords=", url)
        self.assertIn("%E7%BB%8D%E5%85%B4%E5%B8%82", url)

    def test_knowledge_extraction_proxy_builds_redacted_deepseek_request(self):
        settings = Settings(deepseek_api_key="deepseek-secret")
        proxy = DeepSeekKnowledgeExtractionProxy(settings)

        request = proxy.redacted_request(
            transcript="[长辈]: 我叫陈建国，1968年住在绍兴越城区仓桥直街。",
            existing_summary="（暂无已有知识）",
        )

        serialized = str(request)
        self.assertEqual(request["headers"]["Authorization"], "Bearer <server-side>")
        self.assertNotIn("deepseek-secret", serialized)
        self.assertIn("陈建国", serialized)
        self.assertIn("严格的 JSON", serialized)

    def test_kb_extract_endpoint_rejects_non_ai_privacy_scope(self):
        client = TestClient(app)

        response = client.post(
            "/kb/extract?dryRun=true",
            json={
                "userId": "u1",
                "transcript": "[长辈]: 本机私密内容",
                "existingSummary": "（暂无已有知识）",
                "privacyMetadata": {"scope": "localOnly"},
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_kb_extract_endpoint_dry_run_returns_redacted_request(self):
        client = TestClient(app)

        response = client.post(
            "/kb/extract?dryRun=true",
            json={
                "userId": "u1",
                "transcript": "[长辈]: 我叫陈建国，1968年住在绍兴越城区仓桥直街。",
                "existingSummary": "（暂无已有知识）",
                "privacyMetadata": {
                    "scope": "generationAllowed",
                    "sourceRefs": [
                        {
                            "kind": "conversationTurn",
                            "id": "turn-1",
                            "title": "用户对话原文不应出现在服务端上下文",
                        }
                    ],
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        serialized = str(payload)
        self.assertEqual(payload["provider"], "deepseek")
        self.assertIn("kbExtract", payload["capability"])
        self.assertNotIn("deepseek-secret", serialized)
        self.assertNotIn("用户对话原文不应出现在服务端上下文", serialized)
        self.assertEqual(
            payload["context"]["privacyMetadata"]["sourceRefs"][0]["title"],
            "对话来源",
        )


class StoreTests(unittest.TestCase):
    def test_store_factory_uses_postgres_by_default(self):
        store = make_store(Settings(database_url="postgresql://example"))

        self.assertIsInstance(store, PostgresStore)

    def test_store_factory_allows_explicit_memory_backend(self):
        store = make_store(Settings(store_backend="memory"))

        self.assertIsInstance(store, InMemoryStore)

    def test_store_keeps_user_snapshots_separate(self):
        store = InMemoryStore()

        store.save_kb_snapshot("u1", {"people": [{"id": "p1"}]})
        store.save_kb_snapshot("u2", {"people": [{"id": "p2"}]})

        self.assertEqual(store.get_kb_snapshot("u1")["people"][0]["id"], "p1")
        self.assertEqual(store.get_kb_snapshot("u2")["people"][0]["id"], "p2")

    def test_store_keeps_latest_care_snapshot_by_user_and_viewer(self):
        store = InMemoryStore()

        all_family = store.save_care_snapshot(
            "u1",
            {"riskLevel": "stable", "summary": "全家视角"},
            viewer_family_member_id=None,
        )
        daughter = store.save_care_snapshot(
            "u1",
            {"riskLevel": "watch", "summary": "女儿视角"},
            viewer_family_member_id="fm_daughter",
        )

        self.assertEqual(all_family["snapshot"]["summary"], "全家视角")
        self.assertEqual(daughter["viewerFamilyMemberID"], "fm_daughter")
        self.assertEqual(store.get_latest_care_snapshot("u1")["snapshot"]["summary"], "全家视角")
        self.assertEqual(
            store.get_latest_care_snapshot("u1", viewer_family_member_id="fm_daughter")["snapshot"]["summary"],
            "女儿视角",
        )
        self.assertEqual(
            [item["snapshot"]["summary"] for item in store.list_care_snapshots("u1", limit=10)],
            ["全家视角"],
        )
        self.assertEqual(
            [item["snapshot"]["summary"] for item in store.list_care_snapshots("u1", viewer_family_member_id="fm_daughter", limit=10)],
            ["女儿视角"],
        )
        self.assertIsNone(store.get_latest_care_snapshot("u2"))

    def test_store_keeps_family_member_revoke_internal_only(self):
        store = InMemoryStore()

        member = store.add_family_member("u1", {"name": "陈岚", "phone": "13900001111"})
        revoked = store.revoke_family_member("u1", member["id"])

        self.assertEqual(revoked["accessStatus"], "revoked")
        self.assertEqual(revoked["invitationStatus"], "revoked")
        self.assertFalse(revoked["isOnline"])
        self.assertIn("revokedAt", revoked)
        self.assertEqual(store.list_family_members("u1")[0]["accessStatus"], "revoked")

    def test_store_marks_family_member_accepted(self):
        store = InMemoryStore()

        member = store.add_family_member("u1", {"name": "陈岚", "phone": "13900001111"})
        accepted = store.accept_family_member("u1", member["id"], phone="13900001111")

        self.assertEqual(accepted["accessStatus"], "active")
        self.assertEqual(accepted["invitationStatus"], "accepted")
        self.assertTrue(accepted["isOnline"])
        self.assertIn("acceptedAt", accepted)
        self.assertEqual(store.list_family_members("u1")[0]["invitationStatus"], "accepted")

    def test_store_persists_profile_metadata_by_user(self):
        store = InMemoryStore()

        first = store.save_profile(
            "profile_user_1",
            {
                "nickname": "陈建国",
                "gender": "男",
                "region": "绍兴",
                "avatarName": "person.crop.circle.fill",
            },
        )
        store.save_profile(
            "profile_user_2",
            {
                "nickname": "林桂芳",
                "gender": "女",
                "region": "上海",
                "avatarName": "person.circle.fill",
            },
        )
        updated = store.save_profile(
            "profile_user_1",
            {
                "nickname": "陈伯伯",
                "gender": "不便透露",
                "region": "杭州",
                "avatarName": "person.crop.circle",
            },
        )

        self.assertEqual(first["userId"], "profile_user_1")
        self.assertEqual(updated["nickname"], "陈伯伯")
        self.assertEqual(updated["gender"], "不便透露")
        self.assertEqual(updated["region"], "杭州")
        self.assertEqual(updated["avatarName"], "person.crop.circle")
        self.assertEqual(store.get_profile("profile_user_1")["nickname"], "陈伯伯")
        self.assertEqual(store.get_profile("profile_user_2")["nickname"], "林桂芳")
        self.assertIsNone(store.get_profile("missing_user"))

    def test_store_lists_archive_items_by_user(self):
        store = InMemoryStore()

        old_item = store.add_archive_item(
            "u1",
            {
                "id": "archive-old",
                "kind": "textNote",
                "title": "旧记录",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        new_item = store.add_archive_item(
            "u1",
            {
                "id": "archive-new",
                "kind": "voiceSample",
                "title": "语音样本",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        store.add_archive_item(
            "u2",
            {
                "id": "archive-other",
                "kind": "textNote",
                "title": "其他用户",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        self.assertEqual(old_item["userId"], "u1")
        self.assertEqual([item["id"] for item in store.list_archive_items("u1")], ["archive-new", "archive-old"])
        self.assertEqual([item["id"] for item in store.list_archive_items("u2")], ["archive-other"])

    def test_store_lists_mailbox_letters_by_user(self):
        store = InMemoryStore()

        store.add_mailbox_letter("u1", {"id": "letter_1", "title": "第一封", "privacyMetadata": {"scope": "familyCircle"}})
        store.add_mailbox_letter("u1", {"id": "letter_2", "title": "第二封", "privacyMetadata": {"scope": "generationAllowed"}})
        store.add_mailbox_letter("u2", {"id": "letter_3", "title": "其他用户", "privacyMetadata": {"scope": "familyCircle"}})
        store.add_mailbox_letter("u1", {"id": "letter_1", "title": "第一封已读", "status": "read", "privacyMetadata": {"scope": "familyCircle"}})

        self.assertEqual([item["title"] for item in store.list_mailbox_letters("u1")], ["第一封已读", "第二封"])
        self.assertEqual(store.list_mailbox_letters("u1")[0]["status"], "read")
        self.assertEqual([item["title"] for item in store.list_mailbox_letters("u2")], ["其他用户"])


class ProfileAPITests(unittest.TestCase):
    def test_profile_api_saves_and_returns_account_metadata(self):
        client = TestClient(app)

        response = client.post(
            "/profile",
            json={
                "userId": "profile_api_user",
                "nickname": "陈建国",
                "gender": "男",
                "region": "绍兴",
                "avatarName": "person.crop.circle.fill",
            },
        )
        loaded = client.get("/profile/profile_api_user")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "saved")
        profile = response.json()["profile"]
        self.assertEqual(profile["userId"], "profile_api_user")
        self.assertEqual(profile["nickname"], "陈建国")
        self.assertEqual(profile["gender"], "男")
        self.assertEqual(profile["region"], "绍兴")
        self.assertEqual(profile["avatarName"], "person.crop.circle.fill")
        self.assertIn("updatedAt", profile)
        self.assertEqual(loaded.status_code, 200)
        self.assertEqual(loaded.json()["profile"]["nickname"], "陈建国")

    def test_profile_api_rejects_missing_user_empty_nickname_and_invalid_gender(self):
        client = TestClient(app)

        missing_user = client.post("/profile", json={"nickname": "陈建国"})
        empty_nickname = client.post("/profile", json={"userId": "u1", "nickname": "  "})
        invalid_gender = client.post(
            "/profile",
            json={"userId": "u1", "nickname": "陈建国", "gender": "未知"},
        )
        missing_profile = client.get("/profile/missing_user")

        self.assertEqual(missing_user.status_code, 400)
        self.assertEqual(empty_nickname.status_code, 400)
        self.assertEqual(invalid_gender.status_code, 400)
        self.assertEqual(missing_profile.status_code, 404)


class PasswordAPITests(unittest.TestCase):
    def test_password_login_sets_credential_and_change_requires_old_password(self):
        client = TestClient(app)
        phone = "13900007777"

        first_login = client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "密码用户", "password": "old-password-1"},
        )
        self.assertEqual(first_login.status_code, 200)
        user = first_login.json()["user"]
        user_id = user["id"]
        self.assertTrue(user["passwordConfigured"])
        encoded_user = str(first_login.json())
        self.assertNotIn("passwordHash", encoded_user)
        self.assertNotIn("passwordSalt", encoded_user)

        wrong_login = client.post("/auth/login", json={"phone": phone, "password": "wrong-password"})
        self.assertEqual(wrong_login.status_code, 401)

        wrong_change = client.post(
            "/auth/password",
            json={"userId": user_id, "oldPassword": "wrong-password", "newPassword": "new-password-1"},
        )
        self.assertEqual(wrong_change.status_code, 401)

        changed = client.post(
            "/auth/password",
            json={"userId": user_id, "oldPassword": "old-password-1", "newPassword": "new-password-1"},
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["status"], "changed")
        self.assertEqual(changed.json()["userId"], user_id)

        old_password_login = client.post("/auth/login", json={"phone": phone, "password": "old-password-1"})
        new_password_login = client.post("/auth/login", json={"phone": phone, "password": "new-password-1"})
        self.assertEqual(old_password_login.status_code, 401)
        self.assertEqual(new_password_login.status_code, 200)
        self.assertTrue(new_password_login.json()["user"]["passwordConfigured"])

    def test_password_change_rejects_invalid_and_unconfigured_credentials(self):
        client = TestClient(app)

        missing_user = client.post(
            "/auth/password",
            json={"oldPassword": "old-password-1", "newPassword": "new-password-1"},
        )
        short_password = client.post(
            "/auth/password",
            json={"userId": "password_unconfigured_user", "oldPassword": "old-password-1", "newPassword": "short"},
        )
        unconfigured = client.post(
            "/auth/password",
            json={"userId": "password_unconfigured_user", "oldPassword": "old-password-1", "newPassword": "new-password-1"},
        )

        self.assertEqual(missing_user.status_code, 400)
        self.assertEqual(short_password.status_code, 400)
        self.assertEqual(unconfigured.status_code, 409)


class AccountDeletionAPITests(unittest.TestCase):
    def test_account_delete_soft_deletes_and_login_restores_once_by_phone(self):
        client = TestClient(app)
        phone = "13900008881"

        created = client.post("/auth/login", json={"phone": phone, "nickname": "注销用户"})
        user_id = created.json()["user"]["id"]
        deleted = client.post(
            "/auth/delete",
            json={
                "userId": user_id,
                "phone": phone,
                "firstConfirmation": True,
                "secondConfirmation": True,
            },
        )
        restored = client.post("/auth/login", json={"phone": phone, "nickname": "恢复用户"})
        deleted_again = client.post(
            "/auth/delete",
            json={
                "userId": user_id,
                "phone": phone,
                "firstConfirmation": True,
                "secondConfirmation": True,
            },
        )
        second_restore = client.post("/auth/login", json={"phone": phone, "nickname": "第二次恢复"})

        self.assertEqual(created.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        deletion = deleted.json()["deletion"]
        self.assertEqual(deleted.json()["status"], "softDeleted")
        self.assertEqual(deletion["deletionState"], "softDeleted")
        self.assertEqual(deletion["retentionDays"], 30)
        self.assertFalse(deletion["dataExportSupported"])
        self.assertIn("deletedAt", deletion)
        self.assertIn("purgeAfter", deletion)
        self.assertIn("restoreDeadline", deletion)
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()["status"], "restored")
        self.assertEqual(restored.json()["user"]["restoreCount"], 1)
        self.assertEqual(restored.json()["user"]["deletionState"], "active")
        self.assertEqual(deleted_again.status_code, 200)
        self.assertEqual(second_restore.status_code, 410)
        self.assertEqual(second_restore.json()["detail"], "account restore chance already used")

    def test_account_delete_requires_two_confirmations_and_restore_rejects_expired_window(self):
        client = TestClient(app)
        phone = "13900008882"

        created = client.post("/auth/login", json={"phone": phone, "nickname": "超期用户"})
        user_id = created.json()["user"]["id"]
        missing_second_confirm = client.post(
            "/auth/delete",
            json={"userId": user_id, "phone": phone, "firstConfirmation": True},
        )
        expired_deleted = main_module.store.soft_delete_user(
            user_id,
            phone=phone,
            requested_at_iso="2026-01-01T00:00:00+00:00",
        )
        restore = client.post("/auth/restore", json={"phone": phone})

        self.assertEqual(missing_second_confirm.status_code, 400)
        self.assertEqual(missing_second_confirm.json()["detail"], "two deletion confirmations are required")
        self.assertEqual(expired_deleted["deletionState"], "softDeleted")
        self.assertEqual(restore.status_code, 410)
        self.assertEqual(restore.json()["detail"], "account restore deadline expired")


class CareSnapshotAPITests(unittest.TestCase):
    def _care_snapshot(
        self,
        *,
        summary: str,
        risk_level: str = "stable",
        user_turn_count: int = 3,
    ) -> dict:
        return {
            "generatedAt": "2026-06-13T10:00:00Z",
            "windowStart": "2026-06-07T00:00:00Z",
            "windowEnd": "2026-06-13T10:00:00Z",
            "windowDayCount": 7,
            "dataCoverageSummary": "近 7 天 3 轮授权对话",
            "totalTurns": 5,
            "userTurnCount": user_turn_count,
            "characterCount": 96,
            "uniqueTokenCount": 32,
            "lexicalDiversity": 0.67,
            "negativeEmotionMentions": 0,
            "sleepMentions": 1,
            "bodyDiscomfortMentions": 0,
            "repetitionRatio": 0.0,
            "averageWordsPerMinute": 88.5,
            "slowSpeechTurnCount": 1,
            "longPauseTurnCount": 1,
            "emotionVolatilityScore": 0.25,
            "riskLevel": risk_level,
            "summary": summary,
            "suggestions": ["今晚主动电话问候。"],
            "weeklyHighlights": ["睡眠信号 1 次。"],
            "riskSignalDescriptions": [],
            "dailyTrend": [
                {
                    "date": "2026-06-13T00:00:00Z",
                    "userTurnCount": user_turn_count,
                    "negativeEmotionMentions": 0,
                    "sleepMentions": 1,
                    "bodyDiscomfortMentions": 0,
                    "repetitionRatio": 0.0,
                    "averageWordsPerMinute": 88.5,
                    "slowSpeechTurnCount": 1,
                    "longPauseTurnCount": 1,
                    "emotionVolatilityScore": 0.25,
                    "signalScore": 1,
                }
            ],
            "trendSummary": "近 7 天有轻微信号。",
        }

    def _accept_family_member(self, client: TestClient, user_id: str, phone: str = "13900001111") -> str:
        created = client.post(
            "/family/invite",
            json={
                "userId": user_id,
                "name": "陈岚",
                "relation": "女儿",
                "phone": phone,
            },
        )
        self.assertEqual(created.status_code, 200)
        member_id = created.json()["member"]["id"]
        accepted = client.post(
            f"/family/members/{user_id}/{member_id}/accept",
            json={"phone": phone},
        )
        self.assertEqual(accepted.status_code, 200)
        return member_id

    def test_care_snapshot_api_saves_and_returns_latest_by_viewer(self):
        client = TestClient(app)
        member_id = self._accept_family_member(client, "care_user_1")

        all_family = client.post(
            "/care/snapshots",
            json={
                "userId": "care_user_1",
                "snapshot": self._care_snapshot(summary="全家视角", risk_level="stable"),
            },
        )
        daughter = client.post(
            "/care/snapshots",
            json={
                "userId": "care_user_1",
                "viewerFamilyMemberID": member_id,
                "snapshot": self._care_snapshot(summary="女儿视角", risk_level="watch"),
            },
        )

        self.assertEqual(all_family.status_code, 200)
        self.assertEqual(daughter.status_code, 200)
        self.assertEqual(daughter.json()["item"]["viewerFamilyMemberID"], member_id)

        latest_all = client.get("/care/snapshots/latest/care_user_1")
        latest_daughter = client.get(
            "/care/snapshots/latest/care_user_1",
            params={"viewerFamilyMemberID": member_id, "requesterPhone": "13900001111"},
        )

        self.assertEqual(latest_all.status_code, 200)
        self.assertEqual(latest_all.json()["item"]["snapshot"]["summary"], "全家视角")
        self.assertEqual(latest_daughter.status_code, 200)
        self.assertEqual(latest_daughter.json()["item"]["snapshot"]["summary"], "女儿视角")

    def test_care_snapshot_api_404_for_missing_user(self):
        client = TestClient(app)

        response = client.get("/care/snapshots/latest/missing_user")

        self.assertEqual(response.status_code, 404)

    def test_care_snapshot_history_api_returns_recent_snapshots_by_viewer(self):
        client = TestClient(app)
        member_id = self._accept_family_member(client, "care_history_user")

        for index in range(3):
            response = client.post(
                "/care/snapshots",
                json={
                    "userId": "care_history_user",
                    "viewerFamilyMemberID": member_id,
                    "snapshot": self._care_snapshot(summary=f"女儿视角 {index}", risk_level="watch"),
                },
            )
            self.assertEqual(response.status_code, 200)
        client.post(
            "/care/snapshots",
            json={
                "userId": "care_history_user",
                "snapshot": self._care_snapshot(summary="全家视角", risk_level="stable"),
            },
        )

        history = client.get(
            "/care/snapshots/care_history_user",
            params={"viewerFamilyMemberID": member_id, "requesterPhone": "13900001111", "limit": 2},
        )
        all_family_history = client.get("/care/snapshots/care_history_user", params={"limit": 10})

        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["items"][0]["snapshot"]["summary"], "女儿视角 2")
        self.assertEqual(len(history.json()["items"]), 2)
        self.assertEqual(all_family_history.status_code, 200)
        self.assertEqual([item["snapshot"]["summary"] for item in all_family_history.json()["items"]], ["全家视角"])

    def test_care_snapshot_api_requires_active_family_viewer(self):
        client = TestClient(app)
        user_id = "care_access_user"

        created = client.post(
            "/family/invite",
            json={
                "userId": user_id,
                "name": "陈岚",
                "relation": "女儿",
                "phone": "13900001111",
            },
        )
        self.assertEqual(created.status_code, 200)
        member_id = created.json()["member"]["id"]

        pending_write = client.post(
            "/care/snapshots",
            json={
                "userId": user_id,
                "viewerFamilyMemberID": member_id,
                "snapshot": {"summary": "待接受成员不可写入"},
            },
        )
        unknown_write = client.post(
            "/care/snapshots",
            json={
                "userId": user_id,
                "viewerFamilyMemberID": "family_missing",
                "snapshot": {"summary": "未知成员不可写入"},
            },
        )

        self.assertEqual(pending_write.status_code, 403)
        self.assertEqual(unknown_write.status_code, 403)

        accepted = client.post(
            f"/family/members/{user_id}/{member_id}/accept",
            json={"phone": "13900001111"},
        )
        active_write = client.post(
            "/care/snapshots",
            json={
                "userId": user_id,
                "viewerFamilyMemberID": member_id,
                "snapshot": self._care_snapshot(summary="已接受成员可写入"),
            },
        )
        active_read = client.get(
            f"/care/snapshots/latest/{user_id}",
            params={"viewerFamilyMemberID": member_id, "requesterPhone": "13900001111"},
        )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(active_write.status_code, 200)
        self.assertEqual(active_read.status_code, 200)

        revoke = client.post(f"/family/members/{user_id}/{member_id}/revoke")
        still_active_read = client.get(
            f"/care/snapshots/latest/{user_id}",
            params={"viewerFamilyMemberID": member_id, "requesterPhone": "13900001111"},
        )

        self.assertEqual(revoke.status_code, 409)
        self.assertEqual(revoke.json()["detail"], "family member removal is not supported")
        self.assertEqual(still_active_read.status_code, 200)

    def test_care_snapshot_member_reads_require_requester_phone(self):
        client = TestClient(app)
        user_id = "care_requester_user"
        member_id = self._accept_family_member(client, user_id, phone="13900001111")
        saved = client.post(
            "/care/snapshots",
            json={
                "userId": user_id,
                "viewerFamilyMemberID": member_id,
                "snapshot": self._care_snapshot(summary="女儿视角"),
            },
        )
        self.assertEqual(saved.status_code, 200)

        missing_requester = client.get(
            f"/care/snapshots/latest/{user_id}",
            params={"viewerFamilyMemberID": member_id},
        )
        wrong_requester = client.get(
            f"/care/snapshots/latest/{user_id}",
            params={"viewerFamilyMemberID": member_id, "requesterPhone": "13999999999"},
        )
        matching_requester = client.get(
            f"/care/snapshots/latest/{user_id}",
            params={"viewerFamilyMemberID": member_id, "requesterPhone": "13900001111"},
        )
        history = client.get(
            f"/care/snapshots/{user_id}",
            params={"viewerFamilyMemberID": member_id, "requesterPhone": "13900001111"},
        )

        self.assertEqual(missing_requester.status_code, 403)
        self.assertEqual(wrong_requester.status_code, 403)
        self.assertEqual(matching_requester.status_code, 200)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(matching_requester.json()["item"]["snapshot"]["summary"], "女儿视角")

    def test_care_snapshot_api_never_persists_raw_conversation_payload(self):
        client = TestClient(app)
        snapshot = self._care_snapshot(summary="需要尽快确认近况。", risk_level="attention", user_turn_count=8)
        snapshot.update({
            "rawTranscript": "CARE_RAW_SENTINEL 这段原始对话不能出现在响应或历史里。",
            "messages": [{"role": "user", "text": "CARE_RAW_SENTINEL"}],
            "sourceTexts": ["CARE_RAW_SENTINEL"],
            "rawAudioURL": "file:///private/raw_audio.m4a",
        })
        snapshot["dailyTrend"][0]["rawText"] = "CARE_RAW_SENTINEL"

        response = client.post(
            "/care/snapshots",
            json={
                "userId": "care_privacy_user",
                "snapshot": snapshot,
            },
        )
        history = client.get("/care/snapshots/care_privacy_user")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["item"]["snapshot"]["riskLevel"], "attention")
        self.assertEqual(response.json()["item"]["snapshot"]["averageWordsPerMinute"], 88.5)
        self.assertEqual(response.json()["item"]["snapshot"]["dailyTrend"][0]["longPauseTurnCount"], 1)
        self.assertNotIn("CARE_RAW_SENTINEL", response.text)
        self.assertNotIn("raw_audio", response.text)
        self.assertEqual(history.status_code, 200)
        self.assertNotIn("CARE_RAW_SENTINEL", history.text)
        self.assertNotIn("raw_audio", history.text)

    def test_care_snapshot_api_rejects_missing_required_fields(self):
        client = TestClient(app)

        response = client.post(
            "/care/snapshots",
            json={
                "userId": "care_schema_user",
                "snapshot": {"riskLevel": "stable", "summary": "字段不足"},
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("missing", response.text)

    def test_care_snapshot_api_rejects_raw_text_inside_allowed_fields(self):
        client = TestClient(app)
        snapshot = self._care_snapshot(
            summary="CARE_RAW_SENTINEL 原始对话：我昨晚整夜睡不着。",
            risk_level="watch",
        )

        response = client.post(
            "/care/snapshots",
            json={
                "userId": "care_raw_text_user",
                "snapshot": snapshot,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("raw", response.text.lower())


class BackendAuthTests(unittest.TestCase):
    def test_backend_api_token_required_when_configured(self):
        previous_settings = main_module.settings
        main_module.settings = Settings(store_backend="memory", backend_api_token="server-secret")
        client = TestClient(app)
        try:
            health = client.get("/health")
            missing = client.post("/kb/sync", json={"userId": "u1", "graph": {}})
            invalid = client.post(
                "/kb/sync",
                headers={"Authorization": "Bearer wrong-secret"},
                json={"userId": "u1", "graph": {}},
            )
            valid = client.post(
                "/kb/sync",
                headers={"Authorization": "Bearer server-secret"},
                json={"userId": "u1", "graph": {}},
            )
        finally:
            main_module.settings = previous_settings

        self.assertEqual(health.status_code, 200)
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(valid.status_code, 200)


class BackendUserIdentityTests(unittest.TestCase):
    def test_auth_login_uses_stable_full_phone_hash_not_last_four_digits(self):
        previous_store = main_module.store
        main_module.store = InMemoryStore()
        client = TestClient(app)
        try:
            first = client.post(
                "/auth/login",
                json={"phone": "19357579157", "nickname": "陈建国"},
            )
            second = client.post(
                "/auth/login",
                json={"phone": "18300009157", "nickname": "林桂芳"},
            )
        finally:
            main_module.store = previous_store

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_user_id = first.json()["user"]["id"]
        second_user_id = second.json()["user"]["id"]
        self.assertEqual(first_user_id, "user_aef88d2439c15d38")
        self.assertNotEqual(first_user_id, "user_9157")
        self.assertNotEqual(first_user_id, second_user_id)


class EchoDelayedReplyAPITests(unittest.TestCase):
    def test_push_device_token_api_registers_without_returning_raw_token(self):
        previous_store = main_module.store
        main_module.store = InMemoryStore()
        client = TestClient(app)
        raw_token = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        try:
            registered = client.post(
                "/devices/push-token",
                json={
                    "userId": "echo_user_1",
                    "deviceToken": raw_token,
                    "platform": "ios",
                    "environment": "sandbox",
                    "deviceId": "iphone-qa-1",
                },
            )
            created = client.post(
                "/echo/delayed-replies",
                json={
                    "userId": "echo_user_1",
                    "delayedReplyId": "reply_with_device_token",
                    "deliverAt": "2026-06-18T12:05:00Z",
                    "minutes": 7,
                    "trigger": "tenRoundBaseline",
                    "deviceTokenId": registered.json()["item"]["deviceTokenId"],
                },
            )
            listed = client.get("/echo/delayed-replies/echo_user_1")
        finally:
            main_module.store = previous_store

        self.assertEqual(registered.status_code, 200)
        self.assertEqual(registered.json()["status"], "registered")
        token_item = registered.json()["item"]
        self.assertEqual(token_item["userId"], "echo_user_1")
        self.assertEqual(token_item["platform"], "ios")
        self.assertEqual(token_item["environment"], "sandbox")
        self.assertEqual(token_item["deviceId"], "iphone-qa-1")
        self.assertEqual(token_item["deliveryProviderState"], "pending")
        self.assertIn("deviceTokenId", token_item)
        self.assertIn("deviceTokenHash", token_item)
        self.assertNotIn("deviceToken", token_item)
        self.assertNotIn(raw_token, str(registered.json()))

        self.assertEqual(created.status_code, 200)
        delayed_item = created.json()["item"]
        self.assertEqual(delayed_item["deviceTokenId"], token_item["deviceTokenId"])
        self.assertNotIn(raw_token, str(delayed_item))
        self.assertNotIn(raw_token, str(listed.json()))

    def test_push_device_token_api_rejects_invalid_payloads(self):
        client = TestClient(app)

        for payload in [
            {"deviceToken": "0123456789abcdef", "platform": "ios", "environment": "sandbox"},
            {"userId": "echo_user_1", "platform": "ios", "environment": "sandbox"},
            {"userId": "echo_user_1", "deviceToken": "not hex", "platform": "ios", "environment": "sandbox"},
            {"userId": "echo_user_1", "deviceToken": "0123456789abcdef", "platform": "android", "environment": "sandbox"},
            {"userId": "echo_user_1", "deviceToken": "0123456789abcdef", "platform": "ios", "environment": "qa"},
        ]:
            response = client.post("/devices/push-token", json=payload)
            self.assertEqual(response.status_code, 400, payload)

    def test_echo_delayed_reply_api_schedules_and_lists_contract(self):
        previous_store = main_module.store
        main_module.store = InMemoryStore()
        client = TestClient(app)
        try:
            created = client.post(
                "/echo/delayed-replies",
                json={
                    "userId": "echo_user_1",
                    "delayedReplyId": "reply_1",
                    "deliverAt": "2026-06-18T12:05:00Z",
                    "minutes": 7,
                    "trigger": "tenRoundBaseline",
                    "rawTranscript": "ECHO_RAW_SENTINEL should not persist",
                },
            )
            listed = client.get("/echo/delayed-replies/echo_user_1")
        finally:
            main_module.store = previous_store

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["status"], "scheduled")
        item = created.json()["item"]
        self.assertEqual(item["id"], "reply_1")
        self.assertEqual(item["delayedReplyId"], "reply_1")
        self.assertEqual(item["userId"], "echo_user_1")
        self.assertEqual(item["deliveryState"], "scheduled")
        self.assertEqual(item["deliverAt"], "2026-06-18T12:05:00Z")
        self.assertEqual(item["minutes"], 7)
        self.assertEqual(item["trigger"], "tenRoundBaseline")
        self.assertNotIn("rawTranscript", item)

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["id"], "reply_1")
        self.assertNotIn("ECHO_RAW_SENTINEL", str(listed.json()))

    def test_echo_delayed_reply_dispatch_due_marks_only_due_items(self):
        previous_store = main_module.store
        main_module.store = InMemoryStore()
        client = TestClient(app)
        try:
            due = client.post(
                "/echo/delayed-replies",
                json={
                    "userId": "echo_user_due",
                    "delayedReplyId": "reply_due",
                    "deliverAt": "2026-06-18T12:05:00Z",
                    "minutes": 7,
                    "trigger": "tenRoundBaseline",
                    "rawTranscript": "ECHO_DISPATCH_RAW_SENTINEL should not persist",
                },
            )
            future = client.post(
                "/echo/delayed-replies",
                json={
                    "userId": "echo_user_due",
                    "delayedReplyId": "reply_future",
                    "deliverAt": "2026-06-18T12:20:00Z",
                    "minutes": 7,
                    "trigger": "contentSignal",
                },
            )
            dispatched = client.post(
                "/echo/delayed-replies/dispatch-due",
                json={"now": "2026-06-18T12:06:00Z", "limit": 10},
            )
            listed = client.get("/echo/delayed-replies/echo_user_due")
        finally:
            main_module.store = previous_store

        self.assertEqual(due.status_code, 200)
        self.assertEqual(future.status_code, 200)
        self.assertEqual(dispatched.status_code, 200)
        dispatch_body = dispatched.json()
        self.assertEqual(dispatch_body["status"], "queued")
        self.assertEqual(dispatch_body["itemCount"], 1)
        self.assertFalse(dispatch_body["providerDeliveryAttempted"])
        item = dispatch_body["items"][0]
        self.assertEqual(item["id"], "reply_due")
        self.assertEqual(item["deliveryState"], "readyForProvider")
        self.assertEqual(item["pushProviderState"], "queued")
        self.assertEqual(item["dispatchAttemptedAt"], "2026-06-18T12:06:00Z")
        self.assertNotIn("rawTranscript", item)
        self.assertNotIn("ECHO_DISPATCH_RAW_SENTINEL", str(dispatch_body))

        listed_items = {item["id"]: item for item in listed.json()["items"]}
        self.assertEqual(listed_items["reply_due"]["deliveryState"], "readyForProvider")
        self.assertEqual(listed_items["reply_future"]["deliveryState"], "scheduled")

    def test_echo_delayed_reply_api_rejects_missing_required_fields(self):
        client = TestClient(app)

        for payload in [
            {"delayedReplyId": "reply_missing_user", "deliverAt": "2026-06-18T12:05:00Z", "minutes": 7, "trigger": "tenRoundBaseline"},
            {"userId": "echo_user_2", "deliverAt": "2026-06-18T12:05:00Z", "minutes": 7, "trigger": "tenRoundBaseline"},
            {"userId": "echo_user_2", "delayedReplyId": "reply_missing_deliver", "minutes": 7, "trigger": "tenRoundBaseline"},
            {"userId": "echo_user_2", "delayedReplyId": "reply_missing_minutes", "deliverAt": "2026-06-18T12:05:00Z", "trigger": "tenRoundBaseline"},
            {"userId": "echo_user_2", "delayedReplyId": "reply_missing_trigger", "deliverAt": "2026-06-18T12:05:00Z", "minutes": 7},
        ]:
            response = client.post("/echo/delayed-replies", json=payload)
            self.assertEqual(response.status_code, 400, payload)

    def test_echo_delayed_reply_api_rejects_invalid_minutes_and_trigger(self):
        client = TestClient(app)

        invalid_minutes = client.post(
            "/echo/delayed-replies",
            json={
                "userId": "echo_user_3",
                "delayedReplyId": "reply_invalid_minutes",
                "deliverAt": "2026-06-18T12:05:00Z",
                "minutes": 0,
                "trigger": "tenRoundBaseline",
            },
        )
        invalid_trigger = client.post(
            "/echo/delayed-replies",
            json={
                "userId": "echo_user_3",
                "delayedReplyId": "reply_invalid_trigger",
                "deliverAt": "2026-06-18T12:05:00Z",
                "minutes": 7,
                "trigger": "manual",
            },
        )

        self.assertEqual(invalid_minutes.status_code, 400)
        self.assertEqual(invalid_trigger.status_code, 400)


class VoiceCloneProfileAPITests(unittest.TestCase):
    def test_runtime_config_exposes_volcengine_voice_clone_v3_capability(self):
        configured = Settings(
            volcengine_voice_clone_api_key="test-voice-clone-key",
            volcengine_voice_clone_train_url="https://example.com/voice_clone",
            volcengine_voice_clone_query_url="https://example.com/get_voice",
        )

        config = RuntimeConfigService(configured).public_config()
        voice_clone = config["voiceClone"]

        self.assertTrue(voice_clone["enabled"])
        self.assertEqual(voice_clone["provider"], "volcengineVoiceCloneV3")
        self.assertTrue(voice_clone["realProviderReady"])
        self.assertEqual(voice_clone["trainEndpoint"], "/voice/profiles")
        self.assertEqual(voice_clone["queryEndpoint"], "/voice/profiles/{user_id}/{voice_profile_id}/refresh")
        self.assertFalse(voice_clone["defaultReleaseVisible"])
        self.assertEqual(voice_clone["speakerIdMode"], "customSpeakerId")
        self.assertFalse(voice_clone["consoleSpeakerIdConfigured"])
        self.assertFalse(voice_clone["speakerIdPoolConfigured"])
        self.assertEqual(voice_clone["modelType"], 5)
        self.assertEqual(voice_clone["ttsResourceId"], "seed-icl-2.0")
        self.assertFalse(voice_clone["voiceClone2TrialReady"])

    def test_runtime_config_exposes_voice_clone_2_trial_readiness(self):
        configured = Settings(
            volcengine_voice_clone_api_key="test-voice-clone-key",
            volcengine_voice_clone_speaker_id_mode="trialSpeakerIdPool",
            volcengine_voice_clone_speaker_ids="S_trial_001,S_trial_002",
            volcengine_voice_clone_model_type=5,
            volcengine_voice_clone_tts_api_key="voice-clone-tts-secret",
            volcengine_voice_clone_tts_resource_id="seed-icl-2.0",
        )

        config = RuntimeConfigService(configured).public_config()
        voice_clone = config["voiceClone"]

        self.assertTrue(voice_clone["voiceClone2TrialReady"])
        self.assertTrue(voice_clone["speakerIdPoolConfigured"])
        self.assertEqual(voice_clone["speakerIdPoolCount"], 2)
        self.assertEqual(voice_clone["speakerIdMode"], "trialSpeakerIdPool")
        self.assertEqual(voice_clone["modelType"], 5)
        self.assertEqual(voice_clone["ttsResourceId"], "seed-icl-2.0")

    def test_volcengine_voice_clone_v3_provider_builds_training_request(self):
        configured = Settings(
            volcengine_voice_clone_api_key="test-voice-clone-key",
            volcengine_voice_clone_train_url="https://example.com/voice_clone",
            volcengine_voice_clone_query_url="https://example.com/get_voice",
        )
        provider = VolcEngineVoiceCloneV3Provider(configured)

        request = provider.build_training_request(
            voice_profile_id="voice_profile_contract_1",
            audio_base64="BASE64_AUDIO_SAMPLE",
            audio_format="wav",
            language=0,
        )

        self.assertEqual(request["url"], "https://example.com/voice_clone")
        self.assertEqual(request["headers"]["X-Api-Key"], "test-voice-clone-key")
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        self.assertIn("X-Api-Request-Id", request["headers"])
        self.assertNotIn("X-Api-Resource-Id", request["headers"])
        self.assertEqual(request["json"]["speaker_id"], "custom_speaker_id")
        self.assertEqual(request["json"]["custom_speaker_id"], "voice_profile_contract_1")
        self.assertEqual(request["json"]["audio"]["data"], "BASE64_AUDIO_SAMPLE")
        self.assertEqual(request["json"]["audio"]["format"], "wav")
        self.assertEqual(request["json"]["language"], 0)
        self.assertEqual(request["json"]["model_type"], 5)
        self.assertEqual(request["json"]["extra_params"]["voice_clone_denoise_model_id"], "")

    def test_volcengine_voice_clone_v3_provider_builds_console_speaker_training_request(self):
        configured = Settings(
            volcengine_voice_clone_api_key="test-voice-clone-key",
            volcengine_voice_clone_train_url="https://example.com/voice_clone",
            volcengine_voice_clone_query_url="https://example.com/get_voice",
            volcengine_voice_clone_speaker_id_mode="consoleSpeakerId",
            volcengine_voice_clone_speaker_id="S_console_001",
        )
        provider = VolcEngineVoiceCloneV3Provider(configured)

        request = provider.build_training_request(
            voice_profile_id="S_client_generated",
            audio_base64="BASE64_AUDIO_SAMPLE",
            audio_format="wav",
            language=0,
        )

        self.assertEqual(request["json"]["speaker_id"], "S_console_001")
        self.assertNotIn("custom_speaker_id", request["json"])
        self.assertEqual(request["json"]["model_type"], 5)

    def test_volcengine_voice_clone_v3_provider_builds_trial_speaker_pool_training_request(self):
        configured = Settings(
            volcengine_voice_clone_api_key="test-voice-clone-key",
            volcengine_voice_clone_train_url="https://example.com/voice_clone",
            volcengine_voice_clone_query_url="https://example.com/get_voice",
            volcengine_voice_clone_speaker_id_mode="trialSpeakerIdPool",
            volcengine_voice_clone_speaker_ids="S_trial_001,S_trial_002,S_trial_003",
        )
        provider = VolcEngineVoiceCloneV3Provider(configured)

        request = provider.build_training_request(
            voice_profile_id="voice_profile_contract_1",
            audio_base64="BASE64_AUDIO_SAMPLE",
            audio_format="wav",
            language=0,
        )

        self.assertIn(request["json"]["speaker_id"], {"S_trial_001", "S_trial_002", "S_trial_003"})
        self.assertNotIn("custom_speaker_id", request["json"])
        self.assertEqual(request["json"]["model_type"], 5)

    def test_volcengine_voice_clone_v3_provider_requires_console_speaker_id_for_console_mode(self):
        configured = Settings(
            volcengine_voice_clone_api_key="test-voice-clone-key",
            volcengine_voice_clone_speaker_id_mode="consoleSpeakerId",
            volcengine_voice_clone_speaker_id=None,
        )
        provider = VolcEngineVoiceCloneV3Provider(configured)

        with self.assertRaises(ValueError) as context:
            provider.build_training_request(
                voice_profile_id="S_client_generated",
                audio_base64="BASE64_AUDIO_SAMPLE",
                audio_format="wav",
                language=0,
            )

        self.assertIn("VOLCENGINE_VOICE_CLONE_SPEAKER_ID", str(context.exception))

    def test_volcengine_voice_clone_v3_provider_normalizes_nested_speaker_status(self):
        configured = Settings(volcengine_voice_clone_api_key="test-voice-clone-key")
        provider = VolcEngineVoiceCloneV3Provider(configured)

        result = provider._normalize_response(
            {
                "code": 0,
                "message": "success",
                "speaker_status": {
                    "speaker_id": "S_trial_001",
                    "status": 4,
                    "model_type": 5,
                },
                "_providerRequestId": "request-1",
                "_providerLogId": "log-1",
            },
            fallback_voice_profile_id="voice_profile_contract_1",
        )

        self.assertEqual(result["voiceProfileId"], "S_trial_001")
        self.assertEqual(result["providerStatus"], "4")
        self.assertEqual(result["sampleStatus"], "ready")
        self.assertEqual(result["providerLogId"], "log-1")

    def test_volcengine_voice_clone_v3_provider_uses_dedicated_clone_api_key(self):
        configured = Settings(
            volcengine_voice_clone_api_key="test-voice-clone-key",
            volcengine_app_id="test-app-id",
            volcengine_app_token="test-access-token",
        )
        provider = VolcEngineVoiceCloneV3Provider(configured)

        request = provider.build_training_request(
            voice_profile_id="voice_profile_contract_1",
            audio_base64="BASE64_AUDIO_SAMPLE",
            audio_format="wav",
            language=1,
        )

        self.assertEqual(request["headers"]["X-Api-Key"], "test-voice-clone-key")
        self.assertNotIn("X-Api-App-Key", request["headers"])
        self.assertNotIn("X-Api-Access-Key", request["headers"])

    def test_volcengine_voice_clone_v3_provider_attaches_request_and_log_ids_to_http_error(self):
        configured = Settings(
            volcengine_voice_clone_api_key="test-voice-clone-key",
            volcengine_voice_clone_train_url="https://example.com/voice_clone",
        )
        provider = VolcEngineVoiceCloneV3Provider(configured)
        headers = {"X-Tt-Logid": "upstream-logid-123"}
        http_error = urllib.error.HTTPError(
            url="https://example.com/voice_clone",
            code=500,
            msg="Internal Server Error",
            hdrs=headers,
            fp=BytesIO(b'{"code":55000000,"message":"resource mismatch"}'),
        )

        with patch("urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(ValueError) as context:
                provider.submit_training(
                    voice_profile_id="voice_profile_contract_1",
                    audio_base64="BASE64_AUDIO_SAMPLE",
                    audio_format="wav",
                    language=0,
                )

        self.assertEqual(getattr(context.exception, "provider_log_id", None), "upstream-logid-123")
        self.assertTrue(getattr(context.exception, "provider_request_id", ""))
        self.assertIn("resource mismatch", str(context.exception))

    def test_voice_clone_provider_factory_rejects_realtime_app_auth_without_clone_key(self):
        configured = Settings(
            volcengine_app_id="test-app-id",
            volcengine_app_token="test-access-token",
            volcengine_voice_clone_api_key=None,
        )

        provider = VoiceCloneProviderFactory(configured).make()

        self.assertEqual(provider.provider_mode, "mockContract")
        self.assertFalse(provider.is_configured)

    def test_voice_clone_profile_uses_configured_provider_without_persisting_raw_audio(self):
        class FakeProvider:
            is_configured = True
            provider_mode = "volcengineVoiceCloneV3"

            def submit_training(self, *, voice_profile_id, audio_base64, audio_format, language):
                return {
                    "voiceProfileId": voice_profile_id,
                    "providerRequestId": "provider-request-1",
                    "providerStatus": "pending",
                    "sampleStatus": "pending",
                }

        configured = Settings(volcengine_voice_clone_api_key="test-voice-clone-key")
        user_id = "voice_clone_provider_user"
        voice_profile_id = "voice_profile_provider_1"

        with patch("app.main.settings", configured), patch("app.main.VoiceCloneProviderFactory") as factory:
            factory.return_value.make.return_value = FakeProvider()
            created = TestClient(app).post(
                "/voice/profiles",
                json={
                    "userId": user_id,
                    "voiceProfileId": voice_profile_id,
                    "sampleStatus": "pending",
                    "sampleCount": 1,
                    "authorizationConfirmed": True,
                    "personaScope": "family",
                    "digitalHumanId": "family_default",
                    "audioBase64": "RAW_SAMPLE_BASE64",
                    "audioFormat": "wav",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            )

        self.assertEqual(created.status_code, 200)
        profile = created.json()["profile"]
        self.assertEqual(profile["voiceProfileId"], voice_profile_id)
        self.assertEqual(profile["providerMode"], "volcengineVoiceCloneV3")
        self.assertTrue(profile["realCloneProviderReady"])
        self.assertEqual(profile["providerRequestId"], "provider-request-1")
        self.assertEqual(profile["providerStatus"], "pending")
        self.assertNotIn("audioBase64", profile)
        self.assertNotIn("rawSampleURL", profile)
        self.assertNotIn("sampleLocalPath", profile)

    def test_voice_clone_profile_persists_provider_failure_message(self):
        class FailingProvider:
            is_configured = True
            provider_mode = "volcengineVoiceCloneV3"

            def submit_training(self, *, voice_profile_id, audio_base64, audio_format, language):
                raise ValueError("voice clone provider HTTP 401: Invalid X-Api-Key")

        configured = Settings(volcengine_voice_clone_api_key="test-voice-clone-key")
        user_id = "voice_clone_failure_user"
        voice_profile_id = "voice_profile_failure_1"

        with patch("app.main.settings", configured), patch("app.main.VoiceCloneProviderFactory") as factory:
            factory.return_value.make.return_value = FailingProvider()
            created = TestClient(app).post(
                "/voice/profiles",
                json={
                    "userId": user_id,
                    "voiceProfileId": voice_profile_id,
                    "sampleStatus": "pending",
                    "sampleCount": 1,
                    "authorizationConfirmed": True,
                    "personaScope": "personal",
                    "digitalHumanId": user_id,
                    "audioBase64": "RAW_SAMPLE_BASE64",
                    "audioFormat": "wav",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            )

        self.assertEqual(created.status_code, 200)
        profile = created.json()["profile"]
        self.assertEqual(profile["voiceProfileId"], voice_profile_id)
        self.assertEqual(profile["sampleStatus"], "failed")
        self.assertEqual(profile["providerStatus"], "failed")
        self.assertIn("Invalid X-Api-Key", profile["providerMessage"])
        self.assertNotIn("audioBase64", profile)

    def test_voice_clone_profile_persists_provider_request_and_log_ids_on_failure(self):
        class ProviderFailure(ValueError):
            provider_request_id = "req-train-123"
            provider_log_id = "logid-train-456"

        class FailingProvider:
            is_configured = True
            provider_mode = "volcengineVoiceCloneV3"

            def submit_training(self, *, voice_profile_id, audio_base64, audio_format, language):
                raise ProviderFailure("voice clone provider HTTP 500: resource mismatch")

        configured = Settings(volcengine_voice_clone_api_key="test-voice-clone-key")
        user_id = "voice_clone_failure_log_user"
        voice_profile_id = "voice_profile_failure_log_1"

        with patch("app.main.settings", configured), patch("app.main.VoiceCloneProviderFactory") as factory:
            factory.return_value.make.return_value = FailingProvider()
            created = TestClient(app).post(
                "/voice/profiles",
                json={
                    "userId": user_id,
                    "voiceProfileId": voice_profile_id,
                    "sampleStatus": "pending",
                    "sampleCount": 1,
                    "authorizationConfirmed": True,
                    "personaScope": "personal",
                    "digitalHumanId": user_id,
                    "audioBase64": "RAW_SAMPLE_BASE64",
                    "audioFormat": "wav",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            )

        self.assertEqual(created.status_code, 200)
        profile = created.json()["profile"]
        self.assertEqual(profile["sampleStatus"], "failed")
        self.assertEqual(profile.get("providerRequestId"), "req-train-123")
        self.assertEqual(profile.get("providerLogId"), "logid-train-456")
        self.assertNotIn("audioBase64", profile)

    def test_voice_clone_profile_contract_requires_authorization_and_persists_lifecycle(self):
        client = TestClient(app)
        user_id = "voice_clone_contract_user"
        voice_profile_id = "voice_profile_contract_1"

        unauthorized = client.post(
            "/voice/profiles",
            json={
                "userId": user_id,
                "voiceProfileId": voice_profile_id,
                "sampleStatus": "pending",
                "authorizationConfirmed": False,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        created = client.post(
            "/voice/profiles",
            json={
                "userId": user_id,
                "voiceProfileId": voice_profile_id,
                "sampleStatus": "pending",
                "sampleCount": 2,
                "authorizationConfirmed": True,
                "authorizationVersion": "voice-clone-consent-v1",
                "authorizationText": "用户确认提交声音样本，仅用于家庭数字人声音壳层合同。",
                "personaScope": "family",
                "digitalHumanId": "family_default",
                "rawSampleURL": "file:///private/var/mobile/voice/raw.m4a",
                "sampleLocalPath": "/private/var/mobile/voice/raw.m4a",
                "audioBase64": "RAW_SAMPLE_BASE64",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        listed = client.get(f"/voice/profiles/{user_id}")
        disabled = client.post(f"/voice/profiles/{user_id}/{voice_profile_id}/disable")
        deleted = client.delete(f"/voice/profiles/{user_id}/{voice_profile_id}")
        listed_after_delete = client.get(f"/voice/profiles/{user_id}")

        self.assertEqual(unauthorized.status_code, 403)
        self.assertEqual(created.status_code, 200)
        profile = created.json()["profile"]
        self.assertEqual(created.json()["status"], "saved")
        self.assertEqual(profile["userId"], user_id)
        self.assertEqual(profile["voiceProfileId"], voice_profile_id)
        self.assertEqual(profile["sampleStatus"], "pending")
        self.assertEqual(profile["sampleCount"], 2)
        self.assertTrue(profile["authorizationConfirmed"])
        self.assertEqual(profile["authorizationVersion"], "voice-clone-consent-v1")
        self.assertEqual(profile["personaScope"], "family")
        self.assertEqual(profile["digitalHumanId"], "family_default")
        self.assertFalse(profile["isEnabled"])
        self.assertFalse(profile["realCloneProviderReady"])
        self.assertEqual(profile["providerMode"], "mockContract")
        self.assertEqual(profile["contractVersion"], 1)
        self.assertIn("disableVoiceProfile", profile["disableContract"])
        self.assertIn("deleteVoiceProfile", profile["deleteContract"])
        self.assertNotIn("rawSampleURL", profile)
        self.assertNotIn("sampleLocalPath", profile)
        self.assertNotIn("audioBase64", profile)

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["profiles"][0]["voiceProfileId"], voice_profile_id)
        self.assertEqual(disabled.status_code, 200)
        disabled_profile = disabled.json()["profile"]
        self.assertEqual(disabled.json()["status"], "disabled")
        self.assertEqual(disabled_profile["sampleStatus"], "disabled")
        self.assertFalse(disabled_profile["isEnabled"])
        self.assertIn("disabledAt", disabled_profile)

        self.assertEqual(deleted.status_code, 200)
        deleted_profile = deleted.json()["profile"]
        self.assertEqual(deleted.json()["status"], "deleted")
        self.assertEqual(deleted_profile["sampleStatus"], "deleted")
        self.assertEqual(deleted_profile["deletionState"], "deleted")
        self.assertFalse(deleted_profile["isEnabled"])
        self.assertIn("deletedAt", deleted_profile)
        matching = [
            item for item in listed_after_delete.json()["profiles"]
            if item.get("voiceProfileId") == voice_profile_id
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["sampleStatus"], "deleted")

    def test_voice_clone_profile_contract_rejects_unsupported_status_and_local_only(self):
        client = TestClient(app)

        unsupported = client.post(
            "/voice/profiles",
            json={
                "userId": "voice_clone_invalid_user",
                "voiceProfileId": "voice_profile_invalid_status",
                "sampleStatus": "training",
                "authorizationConfirmed": True,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        local_only = client.post(
            "/voice/profiles",
            json={
                "userId": "voice_clone_local_only_user",
                "voiceProfileId": "voice_profile_local_only",
                "sampleStatus": "pending",
                "authorizationConfirmed": True,
                "privacyMetadata": {"scope": "localOnly"},
            },
        )

        self.assertEqual(unsupported.status_code, 400)
        self.assertIn("unsupported sampleStatus", unsupported.text)
        self.assertEqual(local_only.status_code, 403)

    def test_voice_clone_profile_quality_acceptance_marks_ready_profile_as_usable(self):
        previous_store = main_module.store
        main_module.store = InMemoryStore()
        try:
            client = TestClient(app)
            user_id = "voice_clone_quality_user"
            voice_profile_id = "S_quality_acceptance_1"
            main_module.store.save_voice_profile(
                user_id,
                {
                    "id": voice_profile_id,
                    "voiceProfileId": voice_profile_id,
                    "userId": user_id,
                    "sampleStatus": "ready",
                    "isEnabled": True,
                    "realCloneProviderReady": True,
                    "qualityAcceptanceRequired": True,
                    "providerMode": "volcengineVoiceCloneV3",
                    "providerStatus": "2",
                    "authorizationConfirmed": True,
                    "authorizationCopy": "用户已授权本人声音样本。",
                    "disableContract": main_module.VOICE_CLONE_DISABLE_CONTRACT,
                    "deleteContract": main_module.VOICE_CLONE_DELETE_CONTRACT,
                    "contractVersion": main_module.VOICE_CLONE_CONTRACT_VERSION,
                },
            )

            accepted = client.post(f"/voice/profiles/{user_id}/{voice_profile_id}/quality-acceptance")
            listed = client.get(f"/voice/profiles/{user_id}")

            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(accepted.json()["status"], "accepted")
            profile = accepted.json()["profile"]
            self.assertFalse(profile["qualityAcceptanceRequired"])
            self.assertEqual(profile["qualityAcceptanceState"], "accepted")
            self.assertEqual(profile["qualityAcceptedBy"], user_id)
            self.assertIn("qualityAcceptedAt", profile)
            self.assertEqual(listed.json()["profiles"][0]["qualityAcceptanceRequired"], False)
        finally:
            main_module.store = previous_store

    def test_voice_clone_profile_quality_acceptance_rejects_not_ready_profile(self):
        previous_store = main_module.store
        main_module.store = InMemoryStore()
        try:
            client = TestClient(app)
            user_id = "voice_clone_quality_pending_user"
            voice_profile_id = "S_quality_pending_1"
            main_module.store.save_voice_profile(
                user_id,
                {
                    "id": voice_profile_id,
                    "voiceProfileId": voice_profile_id,
                    "userId": user_id,
                    "sampleStatus": "pending",
                    "isEnabled": False,
                    "realCloneProviderReady": True,
                    "qualityAcceptanceRequired": True,
                    "providerMode": "volcengineVoiceCloneV3",
                    "providerStatus": "1",
                    "authorizationConfirmed": True,
                    "authorizationCopy": "用户已授权本人声音样本。",
                    "disableContract": main_module.VOICE_CLONE_DISABLE_CONTRACT,
                    "deleteContract": main_module.VOICE_CLONE_DELETE_CONTRACT,
                    "contractVersion": main_module.VOICE_CLONE_CONTRACT_VERSION,
                },
            )

            accepted = client.post(f"/voice/profiles/{user_id}/{voice_profile_id}/quality-acceptance")
            persisted = main_module.store.get_voice_profile(user_id, voice_profile_id)

            self.assertEqual(accepted.status_code, 409)
            self.assertTrue(persisted["qualityAcceptanceRequired"])
        finally:
            main_module.store = previous_store


class ArchiveAPITests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        self.previous_settings = main_module.settings
        main_module.store = InMemoryStore()
        main_module.settings = Settings(
            store_backend="memory",
            volcengine_voice_clone_tts_api_key="voice-clone-tts-secret",
            tencent_digital_human_app_key="dh-appkey",
            tencent_digital_human_access_token="dh-token",
            tencent_digital_human_virtualman_project_id="dh-project",
        )

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.settings = self.previous_settings

    def test_archive_items_api_saves_sanitized_metadata_and_lists_by_user(self):
        client = TestClient(app)

        created = client.post(
            "/archive/items",
            json={
                "userId": "archive_user_1",
                "id": "archive-text-1",
                "kind": "textNote",
                "title": "仓桥直街",
                "note": "1968 年住在绍兴越城区仓桥直街。",
                "localPath": "/private/var/mobile/archive_photo.jpg",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        listed = client.get("/archive/items/archive_user_1")

        self.assertEqual(created.status_code, 200)
        item = created.json()["item"]
        self.assertEqual(item["id"], "archive-text-1")
        self.assertEqual(item["title"], "仓桥直街")
        self.assertEqual(item["personaScope"], "personal")
        self.assertEqual(item["digitalHumanId"], "archive_user_1")
        self.assertNotIn("localPath", item)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["id"], "archive-text-1")
        self.assertEqual(listed.json()["items"][0]["personaScope"], "personal")
        self.assertEqual(listed.json()["items"][0]["digitalHumanId"], "archive_user_1")
        self.assertNotIn("localPath", listed.json()["items"][0])

    def test_context_build_returns_cfl_lite_packet_without_cross_scope_archive_leak(self):
        client = TestClient(app)
        user_id = "context_user_1"

        client.post(
            "/kb/sync",
            json={
                "userId": user_id,
                "graph": {
                    "people": [
                        {
                            "id": "person_1",
                            "name": "陈建国",
                            "privacyMetadata": {"scope": "generationAllowed"},
                        }
                    ],
                    "places": [
                        {
                            "id": "place_1",
                            "name": "绍兴",
                            "privacyMetadata": {"scope": "generationAllowed"},
                        }
                    ],
                    "events": [],
                    "facts": [
                        {
                            "id": "fact_1",
                            "statement": "小时候常去仓桥直街。",
                            "privacyMetadata": {"scope": "generationAllowed"},
                        }
                    ],
                },
            },
        )
        client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "archive_personal_1",
                "kind": "photo",
                "title": "仓桥直街旧照",
                "note": "相册导入的一张照片",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "detectedPeople": ["陈建国"],
                "detectedLocations": ["绍兴"],
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "archive_family_1",
                "kind": "photo",
                "title": "外婆家的照片",
                "personaScope": "family",
                "digitalHumanId": "family_elder_1",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        main_module.store.save_voice_profile(
            user_id,
            {
                "userId": user_id,
                "voiceProfileId": "S_context_ready",
                "sampleStatus": "ready",
                "isEnabled": True,
                "realCloneProviderReady": True,
                "qualityAcceptanceRequired": False,
                "personaScope": "personal",
                "digitalHumanId": user_id,
            },
        )
        main_module.store.save_care_snapshot(
            user_id,
            {
                "riskLevel": "watch",
                "summary": "最近睡眠线索较多。",
                "suggestions": ["晚间轻声询问。"],
            },
        )

        response = client.post(
            "/context/build",
            json={
                "userId": user_id,
                "intent": "echo_chat",
                "query": "还记得仓桥直街吗？",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "lifecycleMode": "sunlight",
            },
        )

        self.assertEqual(response.status_code, 200)
        packet = response.json()["contextPacket"]
        self.assertEqual(packet["intent"], "echo_chat")
        self.assertEqual(packet["userId"], user_id)
        self.assertTrue(packet["traceId"].startswith("ctx_"))
        self.assertEqual(packet["schemaVersion"], 1)
        self.assertEqual(packet["persona"]["personaScope"], "personal")
        self.assertEqual(packet["memory"]["archiveItems"][0]["id"], "archive_personal_1")
        self.assertNotIn("archive_family_1", str(packet["memory"]["archiveItems"]))
        self.assertEqual(packet["memory"]["kbFacts"][0]["statement"], "小时候常去仓桥直街。")
        self.assertEqual(packet["care"]["latest"]["snapshot"]["riskLevel"], "watch")
        self.assertTrue(packet["voice"]["cloneReady"])
        self.assertEqual(packet["voice"]["voiceProfileId"], "S_context_ready")
        self.assertEqual(packet["voice"]["outputMode"], "tencentAudioDrive")
        self.assertIn("sessionReady", packet["digitalHuman"])
        self.assertFalse(packet["policy"]["crossScopeArchiveIncluded"])
        self.assertEqual(packet["policy"]["privacyScope"]["scope"], "personal")
        self.assertEqual(packet["policy"]["privacyScope"]["scopeLabel"], f"personal:{user_id}")
        self.assertEqual(packet["policy"]["privacyScope"]["allowedArchiveScopes"], ["personal"])
        self.assertIn(user_id, packet["policy"]["privacyScope"]["allowedDigitalHumanIds"])
        self.assertFalse(packet["policy"]["privacyScope"]["canUseFamilyData"])
        self.assertFalse(packet["policy"]["privacyScope"]["crossScopeArchiveIncluded"])
        self.assertEqual(packet["trace"]["archiveItemIds"], ["archive_personal_1"])
        self.assertEqual(packet["trace"]["archiveItemsIncluded"], 1)
        self.assertEqual(packet["trace"]["kbFactCount"], 1)
        self.assertEqual(packet["trace"]["voiceProfileId"], "S_context_ready")
        self.assertTrue(packet["trace"]["voiceCloneReady"])
        self.assertEqual(packet["trace"]["voiceOutputMode"], "tencentAudioDrive")
        self.assertEqual(packet["trace"]["privacyScope"], f"personal:{user_id}")
        self.assertFalse(packet["trace"]["crossScopeArchiveIncluded"])
        self.assertGreaterEqual(packet["debug"]["sourceCounts"]["archiveItemsAvailable"], 2)
        self.assertEqual(packet["debug"]["sourceCounts"]["archiveItemsIncluded"], 1)
        self.assertGreaterEqual(packet["debug"]["latencyMs"], 0)

    def test_context_build_emits_v2_selected_filtered_and_ranking_trace(self):
        client = TestClient(app)
        user_id = "context_user_v2_trace"

        client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "archive_trace_ready_1",
                "kind": "photo",
                "title": "西湖旧照",
                "note": "这张照片记录了和妈妈在西湖边散步。",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "analysisStatus": "analyzed",
                "detectedPeople": ["妈妈"],
                "detectedLocations": ["西湖"],
                "detectedScenes": ["散步"],
                "tags": ["相册影像", "亲情"],
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "archive_trace_failed_empty",
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
        client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "archive_trace_family_blocked",
                "kind": "photo",
                "title": "外婆家的照片",
                "personaScope": "family",
                "digitalHumanId": "family_elder_trace",
                "analysisStatus": "analyzed",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "archive_trace_time_draft",
                "kind": "timeLetter",
                "title": "写给未来的自己",
                "note": "草稿不应该进入回响。",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "deliveryStatus": "draft",
                "metadata": {
                    "contentKind": "time_letter",
                    "timeLetterStatus": "draft",
                    "deliveryStatus": "draft",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        response = client.post(
            "/context/build",
            json={
                "userId": user_id,
                "intent": "echo_chat",
                "query": "妈妈和西湖的照片有什么线索？",
                "personaScope": "personal",
                "digitalHumanId": user_id,
            },
        )

        self.assertEqual(response.status_code, 200)
        packet = response.json()["contextPacket"]
        self.assertEqual(packet["schemaVersion"], 1)
        self.assertEqual(packet["contextVersion"], "echo-context-v2")

        selected_refs = [item["refId"] for item in packet["selectedContext"]]
        self.assertIn("archive_trace_ready_1", selected_refs)
        self.assertEqual(packet["memory"]["archiveItems"][0]["id"], "archive_trace_ready_1")

        filtered_by_ref = {item["refId"]: item["reason"] for item in packet["filteredContext"]}
        self.assertEqual(filtered_by_ref["archive_trace_family_blocked"], "scope_mismatch")
        self.assertEqual(filtered_by_ref["archive_trace_failed_empty"], "analysis_failed_empty_context")
        self.assertEqual(filtered_by_ref["archive_trace_time_draft"], "time_letter_draft")

        ranking_by_ref = {item["refId"]: item for item in packet["rankingTrace"]}
        self.assertIn("archive_trace_ready_1", ranking_by_ref)
        self.assertGreater(ranking_by_ref["archive_trace_ready_1"]["score"], 0)
        self.assertIn("scoreBreakdown", ranking_by_ref["archive_trace_ready_1"])

        self.assertEqual(packet["trace"]["selectedContextCount"], len(packet["selectedContext"]))
        self.assertEqual(packet["trace"]["filteredContextCount"], len(packet["filteredContext"]))
        self.assertEqual(packet["trace"]["rankingTraceCount"], len(packet["rankingTrace"]))
        self.assertEqual(
            packet["debug"]["sourceCounts"]["archiveItemsIncluded"],
            len([item for item in packet["selectedContext"] if item["source"] == "archive"]),
        )

    def test_context_build_includes_kblite_persona_and_care_signals_in_v2_trace(self):
        client = TestClient(app)
        user_id = "context_user_v2_sources"

        client.post(
            "/kb/sync",
            json={
                "userId": user_id,
                "graph": {
                    "people": [],
                    "places": [],
                    "events": [],
                    "facts": [
                        {
                            "id": "fact_context_1",
                            "statement": "妈妈喜欢在西湖边散步。",
                            "privacyMetadata": {"scope": "generationAllowed"},
                        },
                        {
                            "id": "fact_context_2",
                            "statement": "用户小时候常听越剧。",
                            "privacyMetadata": {"scope": "generationAllowed"},
                        },
                    ],
                },
            },
        )
        client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "archive_context_sources_1",
                "kind": "photo",
                "title": "西湖照片",
                "note": "和妈妈在西湖边散步。",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "analysisStatus": "analyzed",
                "detectedPeople": ["妈妈"],
                "detectedLocations": ["西湖"],
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        main_module.store.save_care_snapshot(
            user_id,
            {
                "riskLevel": "watch",
                "summary": "近期对母亲相关回忆更敏感。",
                "suggestions": ["用温和语气回应。"],
                "trendSummary": "最近 7 天有轻微信号。",
                "dailyTrend": [{"date": "2026-07-01", "signalScore": 1}],
                "internalDebug": "不应进入 trace",
            },
        )

        response = client.post(
            "/context/build",
            json={
                "userId": user_id,
                "intent": "echo_chat",
                "query": "妈妈和西湖的记忆有哪些？",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "lifecycleMode": "sunlight",
            },
        )

        self.assertEqual(response.status_code, 200)
        packet = response.json()["contextPacket"]
        selected_by_ref = {item["refId"]: item for item in packet["selectedContext"]}
        selected_sources = {item["source"] for item in packet["selectedContext"]}
        ranking_by_ref = {item["refId"]: item for item in packet["rankingTrace"]}

        self.assertIn("archive", selected_sources)
        self.assertIn("kbFact", selected_sources)
        self.assertIn("persona", selected_sources)
        self.assertIn("care", selected_sources)
        self.assertIn("fact_context_1", selected_by_ref)
        self.assertIn(f"persona:personal:{user_id}", selected_by_ref)
        self.assertIn("care:latest", selected_by_ref)

        self.assertEqual(selected_by_ref["fact_context_1"]["kind"], "fact")
        self.assertEqual(selected_by_ref[f"persona:personal:{user_id}"]["kind"], "sunlight")
        self.assertEqual(selected_by_ref["care:latest"]["kind"], "snapshot")
        self.assertEqual(selected_by_ref["care:latest"]["signals"]["riskLevel"], "watch")

        self.assertIn("scoreBreakdown", ranking_by_ref["fact_context_1"])
        self.assertIn("scoreBreakdown", ranking_by_ref[f"persona:personal:{user_id}"])
        self.assertIn("scoreBreakdown", ranking_by_ref["care:latest"])
        self.assertEqual(packet["trace"]["selectedContextRefs"], [item["refId"] for item in packet["selectedContext"]])
        self.assertEqual(packet["trace"]["selectedContextSourceCounts"]["kbFact"], 2)
        self.assertEqual(packet["trace"]["selectedContextSourceCounts"]["persona"], 1)
        self.assertEqual(packet["trace"]["selectedContextSourceCounts"]["care"], 1)
        self.assertEqual(packet["debug"]["sourceCounts"]["selectedContextKbFacts"], 2)
        self.assertEqual(packet["debug"]["sourceCounts"]["selectedContextPersona"], 1)
        self.assertEqual(packet["debug"]["sourceCounts"]["selectedContextCare"], 1)
        self.assertNotIn("internalDebug", str(packet["selectedContext"]))
        self.assertNotIn("dailyTrend", str(packet["selectedContext"]))

    def test_context_build_filters_unopened_time_letter_for_family_recipient(self):
        client = TestClient(app)
        user_id = "context_user_time_letter_policy"
        member = client.post(
            "/family/invite",
            json={
                "userId": user_id,
                "id": "family_time_recipient",
                "name": "林静文",
                "relation": "女儿",
                "phone": "13900001111",
            },
        )
        self.assertEqual(member.status_code, 200)
        accepted = client.post(
            f"/family/members/{user_id}/family_time_recipient/accept",
            json={"phone": "13900001111"},
        )
        self.assertEqual(accepted.status_code, 200)
        client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "archive_time_letter_future",
                "kind": "timeLetter",
                "title": "写给未来的一封信",
                "note": "还没到打开时间。",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "deliveryState": "sealed",
                "deliveryStatus": "scheduled",
                "openAt": "2999-01-01T00:00:00Z",
                "recipients": [{"id": "family_time_recipient", "name": "林静文", "type": "family"}],
                "metadata": {
                    "timeLetterStatus": "sealed",
                    "deliveryStatus": "scheduled",
                    "openAt": "2999-01-01T00:00:00Z",
                    "recipientIds": "family_time_recipient",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        response = client.post(
            "/context/build",
            json={
                "userId": user_id,
                "intent": "echo_chat",
                "query": "这封信写了什么？",
                "personaScope": "personal",
                "digitalHumanId": user_id,
                "viewerFamilyMemberID": "family_time_recipient",
            },
        )

        self.assertEqual(response.status_code, 200)
        packet = response.json()["contextPacket"]
        self.assertEqual(packet["memory"]["archiveItems"], [])
        filtered_by_ref = {item["refId"]: item["reason"] for item in packet["filteredContext"]}
        self.assertEqual(filtered_by_ref["archive_time_letter_future"], "time_letter_not_open_for_recipient")

    def test_context_build_blocks_pending_family_viewer_and_summarizes_care_snapshot(self):
        client = TestClient(app)
        user_id = "context_user_family_policy"
        member = client.post(
            "/family/invite",
            json={
                "userId": user_id,
                "id": "family_pending_context",
                "name": "陈岚",
                "relation": "女儿",
                "phone": "13900001111",
                "personaScope": "family",
                "digitalHumanId": "family_context_elder",
            },
        )
        self.assertEqual(member.status_code, 200)
        client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "id": "archive_family_pending_blocked",
                "kind": "photo",
                "title": "外婆家的照片",
                "note": "家庭成员未接受前不应可用。",
                "personaScope": "family",
                "digitalHumanId": "family_context_elder",
                "analysisStatus": "analyzed",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        main_module.store.save_care_snapshot(
            user_id,
            {
                "riskLevel": "watch",
                "summary": "家庭关怀摘要",
                "suggestions": ["轻声询问近况"],
                "dailyTrend": [{"date": "2026-07-01", "signalScore": 1}],
                "internalDebug": "不应进入 context packet",
            },
            viewer_family_member_id="family_pending_context",
        )

        pending_response = client.post(
            "/context/build",
            json={
                "userId": user_id,
                "intent": "echo_chat",
                "personaScope": "family",
                "digitalHumanId": "family_context_elder",
                "viewerFamilyMemberID": "family_pending_context",
            },
        )

        self.assertEqual(pending_response.status_code, 200)
        pending_packet = pending_response.json()["contextPacket"]
        self.assertEqual(pending_packet["memory"]["archiveItems"], [])
        self.assertIsNone(pending_packet["care"]["latest"])
        self.assertFalse(pending_packet["policy"]["canUseFamilyData"])
        self.assertFalse(pending_packet["policy"]["familyViewerActive"])
        self.assertIn("family_viewer_not_active", pending_packet["fallbacks"])
        filtered_by_ref = {item["refId"]: item["reason"] for item in pending_packet["filteredContext"]}
        self.assertEqual(filtered_by_ref["archive_family_pending_blocked"], "family_viewer_not_active")

        accepted = client.post(
            f"/family/members/{user_id}/family_pending_context/accept",
            json={"phone": "13900001111"},
        )
        self.assertEqual(accepted.status_code, 200)
        active_response = client.post(
            "/context/build",
            json={
                "userId": user_id,
                "intent": "echo_chat",
                "personaScope": "family",
                "digitalHumanId": "family_context_elder",
                "viewerFamilyMemberID": "family_pending_context",
            },
        )

        self.assertEqual(active_response.status_code, 200)
        active_packet = active_response.json()["contextPacket"]
        self.assertEqual(active_packet["memory"]["archiveItems"][0]["id"], "archive_family_pending_blocked")
        self.assertTrue(active_packet["policy"]["canUseFamilyData"])
        self.assertTrue(active_packet["policy"]["familyViewerActive"])
        self.assertEqual(active_packet["care"]["latest"]["snapshot"]["summary"], "家庭关怀摘要")
        self.assertEqual(active_packet["care"]["latest"]["snapshot"]["suggestions"], ["轻声询问近况"])
        self.assertNotIn("dailyTrend", active_packet["care"]["latest"]["snapshot"])
        self.assertNotIn("internalDebug", str(active_packet["care"]["latest"]))

    def test_context_build_reports_fallbacks_when_voice_and_digital_human_are_unavailable(self):
        client = TestClient(app)
        user_id = "context_user_no_voice"
        response = client.post(
            "/context/build",
            json={
                "userId": user_id,
                "intent": "echo_chat",
                "personaScope": "personal",
                "digitalHumanId": user_id,
            },
        )

        self.assertEqual(response.status_code, 200)
        packet = response.json()["contextPacket"]
        self.assertFalse(packet["voice"]["cloneReady"])
        self.assertIn("voice_clone_not_ready", packet["fallbacks"])
        self.assertIn("no_archive_context", packet["fallbacks"])
        self.assertFalse(packet["policy"]["crossScopeArchiveIncluded"])

    def test_archive_items_api_persists_family_persona_visibility_contract(self):
        client = TestClient(app)

        created = client.post(
            "/archive/items",
            json={
                "userId": "archive_family_user",
                "viewerUserId": "viewer_1",
                "ownerId": "elder_1",
                "ownerUserId": "viewer_1",
                "id": "archive-family-1",
                "kind": "photo",
                "title": "外婆的老照片",
                "personaScope": "family",
                "digitalHumanId": "family_default",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        listed = client.get("/archive/items/archive_family_user")

        self.assertEqual(created.status_code, 200)
        item = created.json()["item"]
        self.assertEqual(item["personaScope"], "family")
        self.assertEqual(item["digitalHumanId"], "family_default")
        self.assertEqual(listed.json()["items"][0]["personaScope"], "family")
        self.assertEqual(listed.json()["items"][0]["digitalHumanId"], "family_default")

    def test_archive_items_api_persists_audio_contract_fields(self):
        client = TestClient(app)

        created = client.post(
            "/archive/items",
            json={
                "userId": "archive_audio_user",
                "viewerUserId": "viewer_audio_1",
                "ownerId": "elder_audio_1",
                "ownerUserId": "viewer_audio_1",
                "uploadedByUserId": "viewer_audio_1",
                "uploaderUserId": "viewer_audio_1",
                "id": "archive-audio-1",
                "kind": "audio",
                "title": "外婆讲绍兴",
                "note": "讲到仓桥直街。",
                "localPath": "/private/var/mobile/archive-audio/raw.m4a",
                "rawAudioURL": "file:///private/var/mobile/archive-audio/raw.m4a",
                "rawTranscript": "这是一段不应作为原文暴露的未审转写",
                "transcriptText": "外婆讲到仓桥直街。",
                "analysisStatus": "pending",
                "personaScope": "family",
                "digitalHumanId": "family_default",
                "metadata": {
                    "contentKind": "audio",
                    "uploadStatus": "pending",
                    "transcriptionStatus": "completed",
                    "transcriptText": "外婆讲到仓桥直街。",
                    "durationSeconds": "18",
                    "localPath": "/private/var/mobile/archive-audio/raw.m4a",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        listed = client.get("/archive/items/archive_audio_user")

        self.assertEqual(created.status_code, 200)
        item = created.json()["item"]
        self.assertEqual(item["kind"], "audio")
        self.assertEqual(item["ownerUserId"], "viewer_audio_1")
        self.assertEqual(item["uploadedByUserId"], "viewer_audio_1")
        self.assertEqual(item["uploaderUserId"], "viewer_audio_1")
        self.assertEqual(item["personaScope"], "family")
        self.assertEqual(item["digitalHumanId"], "family_default")
        self.assertEqual(item["analysisStatus"], "pending")
        self.assertEqual(item["transcriptText"], "外婆讲到仓桥直街。")
        self.assertEqual(item["metadata"]["uploadStatus"], "pending")
        self.assertEqual(item["metadata"]["transcriptionStatus"], "completed")
        self.assertEqual(item["metadata"]["transcriptText"], "外婆讲到仓桥直街。")
        self.assertNotIn("localPath", item)
        self.assertNotIn("rawAudioURL", item)
        self.assertNotIn("rawTranscript", item)
        self.assertNotIn("localPath", item["metadata"])
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["id"], "archive-audio-1")
        self.assertNotIn("rawAudioURL", listed.json()["items"][0])

    def test_archive_items_api_persists_video_contract_fields(self):
        client = TestClient(app)

        created = client.post(
            "/archive/items",
            json={
                "userId": "archive_video_user",
                "ownerUserId": "archive_video_user",
                "uploadedByUserId": "archive_video_user",
                "uploaderUserId": "archive_video_user",
                "id": "archive-video-1",
                "kind": "video",
                "title": "生日视频片段",
                "note": "一段待分析的视频。",
                "localPath": "/private/var/mobile/archive-video/raw.mov",
                "rawVideoURL": "file:///private/var/mobile/archive-video/raw.mov",
                "thumbnailPath": "/private/var/mobile/archive-video/thumb.jpg",
                "localThumbnailPath": "/private/var/mobile/archive-video/thumb.jpg",
                "thumbnailObjectKey": "archive/video/archive-video-1/thumb.jpg",
                "fileSizeBytes": 7340032,
                "fileSizeLimitMB": 200,
                "analysisStatus": "pending",
                "metadata": {
                    "contentKind": "video",
                    "uploadStatus": "pending",
                    "thumbnailStatus": "generated",
                    "thumbnailObjectKey": "archive/video/archive-video-1/thumb.jpg",
                    "fileSizeBytes": "7340032",
                    "fileSizeLimitMB": "200",
                    "thumbnailPath": "/private/var/mobile/archive-video/thumb.jpg",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        listed = client.get("/archive/items/archive_video_user")

        self.assertEqual(created.status_code, 200)
        item = created.json()["item"]
        self.assertEqual(item["kind"], "video")
        self.assertEqual(item["thumbnailObjectKey"], "archive/video/archive-video-1/thumb.jpg")
        self.assertEqual(item["fileSizeLimitMB"], 200)
        self.assertEqual(item["metadata"]["thumbnailStatus"], "generated")
        self.assertEqual(item["metadata"]["thumbnailObjectKey"], "archive/video/archive-video-1/thumb.jpg")
        self.assertEqual(item["metadata"]["fileSizeLimitMB"], "200")
        self.assertNotIn("localPath", item)
        self.assertNotIn("rawVideoURL", item)
        self.assertNotIn("thumbnailPath", item)
        self.assertNotIn("localThumbnailPath", item)
        self.assertNotIn("thumbnailPath", item["metadata"])
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["id"], "archive-video-1")
        self.assertNotIn("rawVideoURL", listed.json()["items"][0])

    def test_archive_items_api_persists_time_letter_shell_contract(self):
        client = TestClient(app)
        open_at = "2026-07-01T09:30:00Z"
        sealed_at = "2026-06-21T10:00:00Z"
        recipients = [
            {"id": "self", "name": "我", "type": "self"},
            {"id": "family-001", "name": "林静文", "type": "family"},
        ]

        created = client.post(
            "/archive/items",
            json={
                "userId": "archive_time_letter_user",
                "ownerUserId": "archive_time_letter_user",
                "id": "archive-time-letter-1",
                "kind": "timeLetter",
                "title": "写给未来的一封信",
                "note": "这段正文只在客户端壳层使用。",
                "analysisStatus": "manual",
                "deliveryState": "sealed",
                "deliveryPolicy": "scheduled_local_and_in_app",
                "openAt": open_at,
                "recipients": recipients,
                "sealedAt": sealed_at,
                "deliveryStatus": "scheduled",
                "deliveryNotificationScheduled": True,
                "metadata": {
                    "contentKind": "time_letter",
                    "deliveryState": "sealed",
                    "timeLetterStatus": "sealed",
                    "deliveryPolicy": "scheduled_local_and_in_app",
                    "openAt": open_at,
                    "recipientIds": "self|family-001",
                    "recipientNames": "我、林静文",
                    "sealedAt": sealed_at,
                    "deliveryStatus": "scheduled",
                    "deliveryExecutionState": "scheduled",
                    "deliveryDecisionState": "confirmed",
                    "deliveryScheduleState": "scheduled",
                    "deliveryProviderState": "local_notification_and_in_app",
                    "deliveryNotificationScheduled": "true",
                    "localPath": "/private/var/mobile/time-letter.txt",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        listed = client.get("/archive/items/archive_time_letter_user")

        self.assertEqual(created.status_code, 200)
        item = created.json()["item"]
        self.assertEqual(item["kind"], "timeLetter")
        self.assertEqual(item["analysisStatus"], "manual")
        self.assertEqual(item["deliveryState"], "sealed")
        self.assertEqual(item["deliveryPolicy"], "scheduled_local_and_in_app")
        self.assertEqual(item["openAt"], open_at)
        self.assertEqual(item["recipients"], recipients)
        self.assertEqual(item["sealedAt"], sealed_at)
        self.assertEqual(item["deliveryStatus"], "scheduled")
        self.assertEqual(item["deliveryNotificationScheduled"], True)
        self.assertEqual(item["metadata"]["deliveryState"], "sealed")
        self.assertEqual(item["metadata"]["timeLetterStatus"], "sealed")
        self.assertEqual(item["metadata"]["deliveryPolicy"], "scheduled_local_and_in_app")
        self.assertEqual(item["metadata"]["openAt"], open_at)
        self.assertEqual(item["metadata"]["recipientIds"], "self|family-001")
        self.assertEqual(item["metadata"]["recipientNames"], "我、林静文")
        self.assertEqual(item["metadata"]["sealedAt"], sealed_at)
        self.assertEqual(item["metadata"]["deliveryStatus"], "scheduled")
        self.assertEqual(item["metadata"]["deliveryExecutionState"], "scheduled")
        self.assertEqual(item["metadata"]["deliveryDecisionState"], "confirmed")
        self.assertEqual(item["metadata"]["deliveryScheduleState"], "scheduled")
        self.assertEqual(item["metadata"]["deliveryProviderState"], "local_notification_and_in_app")
        self.assertEqual(item["metadata"]["deliveryNotificationScheduled"], "true")
        self.assertEqual(item["metadataOnly"], True)
        self.assertNotIn("localPath", item["metadata"])
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["id"], "archive-time-letter-1")

    def test_archive_items_api_upserts_time_letter_draft_and_sealed_contract(self):
        client = TestClient(app)
        user_id = "archive_time_letter_lifecycle_user"
        item_id = "archive-time-letter-lifecycle-1"

        draft = {
            "userId": user_id,
            "ownerUserId": user_id,
            "id": item_id,
            "kind": "timeLetter",
            "title": "时间信件草稿",
            "note": "第一版草稿",
            "analysisStatus": "manual",
            "deliveryState": "draft",
            "deliveryPolicy": "draft",
            "openAt": "2026-07-02T09:00:00Z",
            "recipients": [{"id": "self", "name": "我", "type": "self"}],
            "deliveryStatus": "draft",
            "metadata": {
                "contentKind": "time_letter",
                "deliveryState": "draft",
                "timeLetterStatus": "draft",
                "deliveryPolicy": "draft",
                "openAt": "2026-07-02T09:00:00Z",
                "recipientIds": "self",
                "recipientNames": "我",
                "deliveryStatus": "draft",
                "deliveryExecutionState": "draft",
                "deliveryScheduleState": "not_scheduled",
                "localPath": "/private/var/mobile/time-letter-draft.txt",
            },
            "privacyMetadata": {"scope": "generationAllowed"},
        }
        sealed = {
            **draft,
            "title": "时间信件",
            "note": "已经封存的正文",
            "deliveryState": "sealed",
            "deliveryPolicy": "scheduled_local_and_in_app",
            "sealedAt": "2026-06-21T10:10:00Z",
            "deliveryStatus": "scheduled",
            "deliveryNotificationScheduled": True,
            "metadata": {
                **draft["metadata"],
                "deliveryState": "sealed",
                "timeLetterStatus": "sealed",
                "deliveryPolicy": "scheduled_local_and_in_app",
                "sealedAt": "2026-06-21T10:10:00Z",
                "deliveryStatus": "scheduled",
                "deliveryExecutionState": "scheduled",
                "deliveryDecisionState": "confirmed",
                "deliveryScheduleState": "scheduled",
                "deliveryProviderState": "local_notification_and_in_app",
                "deliveryNotificationScheduled": "true",
            },
        }

        draft_response = client.post("/archive/items", json=draft)
        sealed_response = client.post("/archive/items", json=sealed)
        listed = client.get(f"/archive/items/{user_id}")

        self.assertEqual(draft_response.status_code, 200)
        self.assertEqual(sealed_response.status_code, 200)
        self.assertEqual(listed.status_code, 200)
        matching = [item for item in listed.json()["items"] if item.get("id") == item_id]
        self.assertEqual(len(matching), 1)
        item = matching[0]
        self.assertEqual(item["kind"], "timeLetter")
        self.assertEqual(item["note"], "已经封存的正文")
        self.assertEqual(item["deliveryState"], "sealed")
        self.assertEqual(item["deliveryPolicy"], "scheduled_local_and_in_app")
        self.assertEqual(item["sealedAt"], "2026-06-21T10:10:00Z")
        self.assertEqual(item["deliveryStatus"], "scheduled")
        self.assertEqual(item["deliveryNotificationScheduled"], True)
        self.assertEqual(item["metadata"]["deliveryState"], "sealed")
        self.assertEqual(item["metadata"]["timeLetterStatus"], "sealed")
        self.assertEqual(item["metadata"]["deliveryPolicy"], "scheduled_local_and_in_app")
        self.assertEqual(item["metadata"]["deliveryStatus"], "scheduled")
        self.assertEqual(item["metadataOnly"], True)
        self.assertNotIn("localPath", item["metadata"])

    def test_archive_items_api_deletes_time_letter_by_user_and_id(self):
        client = TestClient(app)
        user_id = "archive_time_letter_delete_user"
        item_id = "archive-time-letter-delete-1"

        created = client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "ownerUserId": user_id,
                "id": item_id,
                "kind": "timeLetter",
                "title": "待删除的时间信件",
                "note": "草稿删除后不应再被拉取。",
                "analysisStatus": "manual",
                "deliveryState": "draft",
                "deliveryPolicy": "pending_product_decision",
                "metadata": {
                    "contentKind": "time_letter",
                    "deliveryState": "draft",
                    "timeLetterStatus": "draft",
                    "deliveryPolicy": "pending_product_decision",
                    "deliveryDecisionRequired": "true",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        deleted = client.delete(f"/archive/items/{user_id}/{item_id}")
        listed = client.get(f"/archive/items/{user_id}")
        missing = client.delete(f"/archive/items/{user_id}/{item_id}")

        self.assertEqual(created.status_code, 200)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["status"], "deleted")
        self.assertEqual(deleted.json()["id"], item_id)
        self.assertEqual(listed.status_code, 200)
        self.assertFalse(any(item.get("id") == item_id for item in listed.json()["items"]))
        self.assertEqual(missing.status_code, 404)

    def test_archive_items_api_rejects_sealed_time_letter_delete(self):
        client = TestClient(app)
        user_id = "archive_time_letter_sealed_delete_user"
        item_id = "archive-time-letter-sealed-delete-1"

        created = client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "ownerUserId": user_id,
                "id": item_id,
                "kind": "timeLetter",
                "title": "不可删除的时间信件",
                "note": "封存后需要保留。",
                "analysisStatus": "manual",
                "deliveryState": "sealed",
                "deliveryPolicy": "scheduled_local_and_in_app",
                "openAt": "2026-07-03T08:00:00Z",
                "recipients": [{"id": "self", "name": "我", "type": "self"}],
                "sealedAt": "2026-06-21T11:00:00Z",
                "deliveryStatus": "scheduled",
                "deliveryNotificationScheduled": True,
                "metadata": {
                    "contentKind": "time_letter",
                    "deliveryState": "sealed",
                    "timeLetterStatus": "sealed",
                    "deliveryPolicy": "scheduled_local_and_in_app",
                    "openAt": "2026-07-03T08:00:00Z",
                    "recipientIds": "self",
                    "recipientNames": "我",
                    "sealedAt": "2026-06-21T11:00:00Z",
                    "deliveryStatus": "scheduled",
                    "deliveryNotificationScheduled": "true",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        deleted = client.delete(f"/archive/items/{user_id}/{item_id}")
        listed = client.get(f"/archive/items/{user_id}")

        self.assertEqual(created.status_code, 200)
        self.assertEqual(deleted.status_code, 409)
        self.assertEqual(deleted.json()["detail"], "sealed timeLetter cannot be deleted")
        self.assertTrue(any(item.get("id") == item_id for item in listed.json()["items"]))

    def test_time_letter_dispatch_due_delivers_once_and_creates_in_app_reminders(self):
        client = TestClient(app)
        user_id = "archive_time_letter_dispatch_user"
        recipient_phone = "+86 138 1000 9001"
        recipient_user_id = stable_user_id(recipient_phone)

        invited = client.post(
            "/family/invite",
            json={
                "userId": user_id,
                "id": "family-recipient-1",
                "name": "林静文",
                "relation": "女儿",
                "phone": recipient_phone,
            },
        )
        accepted = client.post(
            f"/family/members/{user_id}/family-recipient-1/accept",
            json={"phone": recipient_phone},
        )
        due_created = client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "ownerUserId": user_id,
                "id": "archive-time-letter-due",
                "kind": "timeLetter",
                "title": "十八岁生日信",
                "note": "这段正文不应该进入提醒列表。",
                "analysisStatus": "manual",
                "deliveryState": "sealed",
                "deliveryPolicy": "scheduled_local_and_in_app",
                "openAt": "2026-07-02T08:00:00Z",
                "recipients": [
                    {"id": "self", "name": "我", "type": "self"},
                    {"id": "family-recipient-1", "name": "林静文", "type": "family"},
                ],
                "sealedAt": "2026-06-21T11:00:00Z",
                "deliveryStatus": "scheduled",
                "deliveryNotificationScheduled": True,
                "metadata": {
                    "contentKind": "time_letter",
                    "deliveryState": "sealed",
                    "timeLetterStatus": "sealed",
                    "deliveryPolicy": "scheduled_local_and_in_app",
                    "openAt": "2026-07-02T08:00:00Z",
                    "recipientIds": "self|family-recipient-1",
                    "recipientNames": "我、林静文",
                    "sealedAt": "2026-06-21T11:00:00Z",
                    "deliveryStatus": "scheduled",
                    "deliveryExecutionState": "scheduled",
                    "deliveryProviderState": "local_notification_and_in_app",
                    "deliveryNotificationScheduled": "true",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        future_created = client.post(
            "/archive/items",
            json={
                "userId": user_id,
                "ownerUserId": user_id,
                "id": "archive-time-letter-future",
                "kind": "timeLetter",
                "title": "未来信",
                "note": "未到时间不应对收件人可见。",
                "analysisStatus": "manual",
                "deliveryState": "sealed",
                "deliveryPolicy": "scheduled_local_and_in_app",
                "openAt": "2999-01-01T00:00:00Z",
                "recipients": [{"id": "family-recipient-1", "name": "林静文", "type": "family"}],
                "sealedAt": "2026-06-21T11:00:00Z",
                "deliveryStatus": "scheduled",
                "deliveryNotificationScheduled": True,
                "metadata": {
                    "contentKind": "time_letter",
                    "deliveryState": "sealed",
                    "timeLetterStatus": "sealed",
                    "deliveryPolicy": "scheduled_local_and_in_app",
                    "openAt": "2999-01-01T00:00:00Z",
                    "recipientIds": "family-recipient-1",
                    "recipientNames": "林静文",
                    "sealedAt": "2026-06-21T11:00:00Z",
                    "deliveryStatus": "scheduled",
                    "deliveryExecutionState": "scheduled",
                    "deliveryProviderState": "local_notification_and_in_app",
                    "deliveryNotificationScheduled": "true",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        dispatched = client.post(
            "/archive/time-letters/dispatch-due",
            json={"now": "2026-07-02T09:00:00Z", "limit": 10},
        )
        repeated = client.post(
            "/archive/time-letters/dispatch-due",
            json={"now": "2026-07-02T09:00:00Z", "limit": 10},
        )
        owner_mailbox = client.get(f"/mailbox/letters/{user_id}")
        recipient_mailbox = client.get(f"/mailbox/letters/{recipient_user_id}")
        listed = client.get(f"/archive/items/{user_id}")

        self.assertEqual(invited.status_code, 200)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(due_created.status_code, 200)
        self.assertEqual(future_created.status_code, 200)
        self.assertEqual(dispatched.status_code, 200)
        self.assertEqual(dispatched.json()["status"], "dispatched")
        self.assertEqual(dispatched.json()["itemCount"], 1)
        self.assertEqual(dispatched.json()["reminderCount"], 2)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()["itemCount"], 0)
        self.assertEqual(repeated.json()["reminderCount"], 0)

        updated_due = next(item for item in listed.json()["items"] if item["id"] == "archive-time-letter-due")
        updated_future = next(item for item in listed.json()["items"] if item["id"] == "archive-time-letter-future")
        self.assertEqual(updated_due["deliveryStatus"], "delivered")
        self.assertEqual(updated_due["metadata"]["deliveryStatus"], "delivered")
        self.assertEqual(updated_due["metadata"]["deliveryExecutionState"], "delivered")
        self.assertEqual(updated_due["metadata"]["deliveryProviderState"], "local_notification_and_in_app")
        self.assertEqual(updated_due["metadata"]["deliveredAt"], "2026-07-02T09:00:00Z")
        self.assertEqual(updated_future["deliveryStatus"], "scheduled")

        owner_items = owner_mailbox.json()["items"]
        recipient_items = recipient_mailbox.json()["items"]
        self.assertEqual(len(owner_items), 1)
        self.assertEqual(len(recipient_items), 1)
        self.assertEqual(owner_items[0]["id"], "time-letter-archive-time-letter-due-self")
        self.assertEqual(recipient_items[0]["id"], "time-letter-archive-time-letter-due-family-recipient-1")
        self.assertEqual(owner_items[0]["status"], "unread")
        self.assertEqual(recipient_items[0]["sourceArchiveItemId"], "archive-time-letter-due")
        self.assertEqual(recipient_items[0]["recipientRole"], "recipient")
        self.assertTrue(recipient_items[0]["metadataOnly"])
        self.assertTrue(recipient_items[0]["contentRedacted"])
        self.assertNotIn("这段正文", recipient_mailbox.text)
        self.assertNotIn("archive-time-letter-future", recipient_mailbox.text)

    def test_archive_items_api_persists_structured_analysis_contract(self):
        client = TestClient(app)

        created = client.post(
            "/archive/items",
            json={
                "userId": "archive_analysis_user",
                "id": "archive-analysis-1",
                "kind": "photo",
                "title": "老家院子合影",
                "note": "外婆和家人在杭州老家院子里合影。",
                "analysisStatus": "analyzed",
                "analysisSummary": "识别到家人、杭州老家和院子合影场景。",
                "detectedPeople": ["外婆", "家人", ""],
                "detectedLocations": ["杭州", "老家", "院子"],
                "detectedScenes": ["合影", "家庭聚会"],
                "tags": ["相册影像", "场景线索", ""],
                "analysisFailureReason": "",
                "analysisRetryable": False,
                "metadata": {
                    "analysisLocationClues": "杭州、老家、院子",
                    "analysisSceneClues": "合影、家庭聚会",
                },
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        failed = client.post(
            "/archive/items",
            json={
                "userId": "archive_analysis_user",
                "id": "archive-analysis-failed",
                "kind": "photo",
                "title": "待重试照片",
                "note": "模型暂时不可用。",
                "analysisStatus": "failed",
                "analysisFailureReason": "provider_timeout",
                "analysisRetryable": True,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        listed = client.get("/archive/items/archive_analysis_user")

        self.assertEqual(created.status_code, 200)
        self.assertEqual(failed.status_code, 200)
        item = created.json()["item"]
        self.assertEqual(item["analysisStatus"], "analyzed")
        self.assertEqual(item["analysisSummary"], "识别到家人、杭州老家和院子合影场景。")
        self.assertEqual(item["detectedPeople"], ["外婆", "家人"])
        self.assertEqual(item["detectedLocations"], ["杭州", "老家", "院子"])
        self.assertEqual(item["detectedScenes"], ["合影", "家庭聚会"])
        self.assertEqual(item["tags"], ["相册影像", "场景线索"])
        self.assertFalse(item["analysisRetryable"])
        failed_item = failed.json()["item"]
        self.assertEqual(failed_item["analysisStatus"], "failed")
        self.assertEqual(failed_item["analysisFailureReason"], "provider_timeout")
        self.assertTrue(failed_item["analysisRetryable"])
        listed_items = {item["id"]: item for item in listed.json()["items"]}
        self.assertEqual(listed_items["archive-analysis-1"]["detectedLocations"], ["杭州", "老家", "院子"])
        self.assertEqual(listed_items["archive-analysis-1"]["detectedScenes"], ["合影", "家庭聚会"])
        self.assertEqual(listed_items["archive-analysis-failed"]["analysisFailureReason"], "provider_timeout")
        self.assertTrue(listed_items["archive-analysis-failed"]["analysisRetryable"])

    def test_archive_media_upload_intent_returns_mock_contract(self):
        client = TestClient(app)

        response = client.post(
            "/archive/media/upload-intent",
            json={
                "userId": "archive_upload_user",
                "archiveItemId": "archive-audio-upload-1",
                "kind": "audio",
                "fileName": "voice.m4a",
                "contentType": "audio/mp4",
                "fileSizeBytes": 1048576,
                "personaScope": "family",
                "digitalHumanId": "family_default",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        self.assertEqual(response.status_code, 200)
        intent = response.json()["uploadIntent"]
        self.assertEqual(response.json()["status"], "mock_ready")
        self.assertEqual(intent["archiveItemId"], "archive-audio-upload-1")
        self.assertEqual(intent["kind"], "audio")
        self.assertEqual(intent["storageProvider"], "mockObjectStorage")
        self.assertEqual(intent["providerDisplayName"], "Mock Object Storage")
        self.assertEqual(intent["providerMode"], "mock")
        self.assertFalse(intent["requiresClientUpload"])
        self.assertEqual(intent["uploadURLScheme"], "mock")
        self.assertFalse(intent["realProviderReady"])
        self.assertEqual(intent["providerSwitchContractVersion"], 1)
        self.assertEqual(intent["clientUploadAction"], "metadataOnly")
        self.assertEqual(intent["personaScope"], "family")
        self.assertEqual(intent["digitalHumanId"], "family_default")
        self.assertTrue(intent["uploadIntentId"].startswith("upload_intent_"))
        self.assertIn("archive_upload_user/family/family_default/audio/archive-audio-upload-1", intent["objectKey"])
        self.assertTrue(intent["uploadURL"].startswith("mock://archive-media/"))
        self.assertEqual(intent["requiredHeaders"]["Content-Type"], "audio/mp4")
        self.assertEqual(intent["maxFileSizeBytes"], 50 * 1024 * 1024)
        self.assertEqual(intent["expiresInSeconds"], 900)
        self.assertIn("expiresAt", intent)
        self.assertNotIn("localPath", intent)
        self.assertNotIn("file://", str(intent))

    def test_archive_media_upload_intent_rejects_unsupported_kind_or_size(self):
        client = TestClient(app)

        unsupported = client.post(
            "/archive/media/upload-intent",
            json={
                "userId": "archive_upload_user",
                "archiveItemId": "archive-text-upload-1",
                "kind": "text",
                "fileName": "note.txt",
                "contentType": "text/plain",
                "fileSizeBytes": 10,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        oversized = client.post(
            "/archive/media/upload-intent",
            json={
                "userId": "archive_upload_user",
                "archiveItemId": "archive-video-upload-1",
                "kind": "video",
                "fileName": "too-large.mov",
                "contentType": "video/quicktime",
                "fileSizeBytes": 201 * 1024 * 1024,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        mismatched_content_type = client.post(
            "/archive/media/upload-intent",
            json={
                "userId": "archive_upload_user",
                "archiveItemId": "archive-audio-mismatch-1",
                "kind": "audio",
                "fileName": "../voice.m4a",
                "contentType": "text/plain",
                "fileSizeBytes": 1024,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        local_only = client.post(
            "/archive/media/upload-intent",
            json={
                "userId": "archive_upload_user",
                "archiveItemId": "archive-audio-local-1",
                "kind": "audio",
                "fileName": "private.m4a",
                "contentType": "audio/mp4",
                "fileSizeBytes": 1024,
                "privacyMetadata": {"scope": "localOnly"},
            },
        )

        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(mismatched_content_type.status_code, 400)
        self.assertEqual(local_only.status_code, 403)
        self.assertIn("unsupported", unsupported.text)
        self.assertIn("file too large", oversized.text)
        self.assertIn("contentType does not match media kind", mismatched_content_type.text)

    def test_archive_items_api_rejects_unknown_persona_scope(self):
        client = TestClient(app)

        response = client.post(
            "/archive/items",
            json={
                "userId": "archive_family_user",
                "id": "archive-invalid-scope",
                "kind": "photo",
                "title": "错误可见性",
                "personaScope": "public",
                "digitalHumanId": "family_default",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_archive_items_api_rejects_private_or_local_items(self):
        client = TestClient(app)

        private_response = client.post(
            "/archive/items",
            json={
                "userId": "archive_user_2",
                "id": "archive-private",
                "kind": "textNote",
                "title": "私密素材",
                "privacyMetadata": {"scope": "privateOnly"},
            },
        )
        local_response = client.post(
            "/archive/items",
            json={
                "userId": "archive_user_2",
                "id": "archive-local",
                "kind": "textNote",
                "title": "本机素材",
                "privacyMetadata": {"scope": "localOnly"},
            },
        )

        self.assertEqual(private_response.status_code, 403)
        self.assertEqual(local_response.status_code, 403)


class ArchiveImageAnalysisAPITests(unittest.TestCase):
    def test_archive_analysis_status_enum_contract(self):
        from app.services.deepseek import ArchiveAnalysisStatus

        self.assertEqual(
            ArchiveAnalysisStatus.values(),
            ["pending", "analyzing", "analyzed", "failed", "retryable"],
        )

    def test_image_analysis_provider_adapter_exposes_text_only_capabilities(self):
        from app.services.deepseek import ArchiveImageAnalysisProviderFactory

        adapter = ArchiveImageAnalysisProviderFactory(Settings(deepseek_api_key="deepseek-secret")).make()

        self.assertTrue(adapter.enabled)
        self.assertEqual(adapter.provider_id, "deepseek/text-only")
        self.assertFalse(adapter.supports_vision)
        self.assertEqual(adapter.fallback_mode, "retryableFailure")
        failure = adapter.failure_contract(provider_message="vision provider unavailable")
        self.assertEqual(failure["analysisStatus"], "failed")
        self.assertEqual(failure["analysisFailureReason"], "provider_unavailable")
        self.assertTrue(failure["analysisRetryable"])
        self.assertEqual(failure["provider"], "deepseek/text-only")
        self.assertIn("vision provider unavailable", failure["providerMessage"])

    def test_image_analysis_parse_requires_structured_json(self):
        proxy = DeepSeekImageAnalysisProxy(Settings(deepseek_api_key="deepseek-secret"))

        with self.assertRaisesRegex(ValueError, "non-JSON"):
            proxy.parse_analysis("这是一张照片，有三个人，像是在老家门口。")

    def test_image_analysis_parse_returns_archive_insight_contract(self):
        proxy = DeepSeekImageAnalysisProxy(Settings(deepseek_api_key="deepseek-secret"))

        parsed = proxy.parse_analysis(
            """
            ```json
            {
              "description": "外婆和家人在杭州老家院子里合影。",
              "detectedPeople": ["外婆", "家人"],
              "detectedLocations": ["杭州", "老家", "院子"],
              "detectedScenes": ["合影", "家庭聚会"],
              "tags": ["相册影像", "场景线索"],
              "scene": "院子",
              "occasion": "家庭聚会",
              "mood": "温暖",
              "estimatedDecade": 1990
            }
            ```
            """
        )

        self.assertEqual(parsed["analysisStatus"], "analyzed")
        self.assertEqual(parsed["analysisSummary"], "外婆和家人在杭州老家院子里合影。")
        self.assertEqual(parsed["detectedPeople"], ["外婆", "家人"])
        self.assertEqual(parsed["detectedLocations"], ["杭州", "老家", "院子"])
        self.assertEqual(parsed["detectedScenes"], ["合影", "家庭聚会"])
        self.assertEqual(parsed["tags"], ["相册影像", "场景线索"])
        self.assertEqual(parsed["analysisFailureReason"], "")
        self.assertFalse(parsed["analysisRetryable"])

    def test_archive_image_analysis_dry_run_redacts_secret(self):
        client = TestClient(app)

        response = client.post(
            "/archive/image-analysis",
            params={"dryRun": "true"},
            json={
                "userId": "archive_image_user",
                "archiveItemId": "archive-photo-1",
                "imageBase64": "abc123",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )

        self.assertEqual(response.status_code, 200)
        serialized = str(response.json())
        self.assertIn("data:image/jpeg;base64,abc123", serialized)
        self.assertIn("Authorization", serialized)
        self.assertIn("Bearer <server-side>", serialized)
        self.assertNotIn("DEEPSEEK_API_KEY", serialized)
        self.assertIn("responseContract", response.json())
        self.assertEqual(response.json()["responseContract"]["detectedLocations"], [])
        self.assertEqual(response.json()["responseContract"]["detectedScenes"], [])
        self.assertEqual(response.json()["responseContract"]["analysisFailureReason"], "")
        self.assertTrue(response.json()["responseContract"]["analysisRetryable"])
        self.assertEqual(response.json()["provider"], "deepseek/text-only")
        self.assertEqual(response.json()["capability"]["provider"], "deepseek/text-only")
        self.assertFalse(response.json()["capability"]["supportsVision"])
        self.assertEqual(response.json()["capability"]["fallbackMode"], "retryableFailure")

    def test_archive_image_analysis_requires_image_base64(self):
        client = TestClient(app)

        response = client.post("/archive/image-analysis", json={})

        self.assertEqual(response.status_code, 400)

    def test_archive_image_analysis_requires_user_and_archive_item(self):
        client = TestClient(app)
        base_payload = {
            "imageBase64": "abc123",
            "privacyMetadata": {"scope": "generationAllowed"},
        }

        missing_user = client.post(
            "/archive/image-analysis",
            json={**base_payload, "archiveItemId": "archive-photo-1"},
        )
        missing_item = client.post(
            "/archive/image-analysis",
            json={**base_payload, "userId": "archive_image_user"},
        )

        self.assertEqual(missing_user.status_code, 400)
        self.assertEqual(missing_item.status_code, 400)

    def test_archive_image_analysis_rejects_non_generation_allowed_privacy(self):
        client = TestClient(app)

        for scope in ("privateOnly", "localOnly", "familyCircle"):
            response = client.post(
                "/archive/image-analysis",
                params={"dryRun": "true"},
                json={
                    "userId": "archive_image_user",
                    "archiveItemId": "archive-photo-1",
                    "imageBase64": "abc123",
                    "privacyMetadata": {"scope": scope},
                },
            )
            self.assertEqual(response.status_code, 403, scope)

    def test_archive_image_analysis_without_key_returns_unavailable(self):
        client = TestClient(app)
        original_settings = main_module.settings
        main_module.settings = Settings(deepseek_api_key=None)
        try:
            response = client.post(
                "/archive/image-analysis",
                json={
                    "userId": "archive_image_user",
                    "archiveItemId": "archive-photo-1",
                    "imageBase64": "abc123",
                    "privacyMetadata": {"scope": "generationAllowed"},
                },
            )
        finally:
            main_module.settings = original_settings

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["analysisStatus"], "failed")
        self.assertEqual(payload["analysisSummary"], "")
        self.assertEqual(payload["description"], "")
        self.assertEqual(payload["detectedPeople"], [])
        self.assertEqual(payload["detectedLocations"], [])
        self.assertEqual(payload["detectedScenes"], [])
        self.assertEqual(payload["tags"], [])
        self.assertEqual(payload["analysisFailureReason"], "provider_unavailable")
        self.assertTrue(payload["analysisRetryable"])
        self.assertEqual(payload["provider"], "deepseek/text-only")
        self.assertIn("providerMessage", payload)
        self.assertIn("DEEPSEEK_API_KEY is not configured", payload["providerMessage"])


class MailboxAPITests(unittest.TestCase):
    def test_mailbox_letters_api_saves_sanitized_metadata_and_lists_by_user(self):
        client = TestClient(app)

        response = client.post(
            "/mailbox/letters",
            json={
                "userId": "mailbox_user",
                "id": "letter_sync_1",
                "recipientName": "林桂芳",
                "title": "想说的话",
                "body": "MAILBOX_PRIVATE_BODY_SENTINEL 这是一封完整正文，不应该返回。",
                "bodyPreview": "MAILBOX_PRIVATE_BODY_SENTINEL 这是一封正文预览，也不应该返回。",
                "replyText": "ECHO_SENTINEL 不是逝者真实回复，但这段回声不应同步。",
                "createdAt": "2026-06-13T00:00:00Z",
                "deliverAt": "2026-06-14T00:00:00Z",
                "status": "sealed",
                "boundaryAcknowledged": True,
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        self.assertEqual(response.status_code, 200)
        item = response.json()["item"]
        self.assertEqual(item["id"], "letter_sync_1")
        self.assertTrue(item["metadataOnly"])
        self.assertTrue(item["contentRedacted"])
        self.assertNotIn("body", item)
        self.assertNotIn("bodyPreview", item)
        self.assertNotIn("replyText", item)
        response_text = response.text
        self.assertNotIn("MAILBOX_PRIVATE_BODY_SENTINEL", response_text)
        self.assertNotIn("ECHO_SENTINEL", response_text)

        listed = client.get("/mailbox/letters/mailbox_user")
        self.assertEqual(listed.status_code, 200)
        listed_item = listed.json()["items"][0]
        self.assertEqual(listed_item["id"], "letter_sync_1")
        self.assertNotIn("body", listed_item)
        self.assertNotIn("bodyPreview", listed_item)
        self.assertNotIn("replyText", listed_item)
        listed_text = listed.text
        self.assertNotIn("MAILBOX_PRIVATE_BODY_SENTINEL", listed_text)
        self.assertNotIn("ECHO_SENTINEL", listed_text)

    def test_mailbox_letters_api_rejects_private_or_local_letters(self):
        client = TestClient(app)

        for scope in ["localOnly", "privateOnly"]:
            response = client.post(
                "/mailbox/letters",
                json={
                    "userId": "mailbox_private_user",
                    "id": f"letter_{scope}",
                    "recipientName": "林桂芳",
                    "title": "私密信件",
                    "body": "不应离开本机",
                    "privacyMetadata": {"scope": scope},
                },
            )
            self.assertEqual(response.status_code, 403)


class FamilyAPITests(unittest.TestCase):
    def test_family_member_api_defaults_hidden_digital_human_contract(self):
        client = TestClient(app)

        created = client.post(
            "/family/invite",
            json={
                "userId": "u_family_contract_default",
                "name": "林桂芳",
                "relation": "祖母",
                "phone": "13900001111",
            },
        )

        self.assertEqual(created.status_code, 200)
        member = created.json()["member"]
        self.assertEqual(member["personaScope"], "family")
        self.assertEqual(member["digitalHumanId"], "family_default")
        self.assertEqual(member["digitalHumanMode"], "sunlight")
        self.assertEqual(member["digitalHumanModeLabel"], "阳光")
        self.assertEqual(member["backendContractMode"], "mockFamilyPersona")
        self.assertEqual(member["familyPersonaContractVersion"], 1)
        self.assertFalse(member["defaultReleaseVisible"])

    def test_family_member_api_persists_digital_human_mode_contract(self):
        client = TestClient(app)

        created = client.post(
            "/family/invite",
            json={
                "userId": "u_family_contract_star",
                "name": "陈岚",
                "relation": "女儿",
                "phone": "13900001111",
                "personaScope": "family",
                "digitalHumanId": "family_chenlan",
                "digitalHumanMode": "star",
            },
        )
        member = created.json()["member"]
        accepted = client.post(
            f"/family/members/u_family_contract_star/{member['id']}/accept",
            json={"phone": "13900001111"},
        )
        listed = client.get("/family/members/u_family_contract_star")

        self.assertEqual(created.status_code, 200)
        self.assertEqual(member["digitalHumanMode"], "star")
        self.assertEqual(member["digitalHumanModeLabel"], "星辰")
        self.assertEqual(member["digitalHumanId"], "family_chenlan")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["member"]["digitalHumanMode"], "star")
        listed_member = next(item for item in listed.json()["members"] if item["id"] == member["id"])
        self.assertEqual(listed_member["digitalHumanMode"], "star")
        self.assertEqual(listed_member["digitalHumanModeLabel"], "星辰")
        self.assertFalse(listed_member["defaultReleaseVisible"])

    def test_family_member_api_rejects_invalid_digital_human_mode_contract(self):
        client = TestClient(app)

        created = client.post(
            "/family/invite",
            json={
                "userId": "u_family_contract_invalid",
                "name": "陈岚",
                "relation": "女儿",
                "phone": "13900001111",
                "digitalHumanMode": "storm",
            },
        )

        self.assertEqual(created.status_code, 400)

    def test_family_member_accept_api_marks_member_active(self):
        client = TestClient(app)

        created = client.post(
            "/family/invite",
            json={
                "userId": "u1",
                "name": "陈岚",
                "relation": "女儿",
                "phone": "13900001111",
            },
        )
        member_id = created.json()["member"]["id"]
        accepted = client.post(
            f"/family/members/u1/{member_id}/accept",
            json={"phone": "13900001111"},
        )
        listed = client.get("/family/members/u1")

        self.assertEqual(created.status_code, 200)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["member"]["accessStatus"], "active")
        self.assertEqual(accepted.json()["member"]["invitationStatus"], "accepted")
        self.assertIn("acceptedAt", accepted.json()["member"])
        listed_member = next(item for item in listed.json()["members"] if item["id"] == member_id)
        self.assertEqual(listed_member["invitationStatus"], "accepted")

    def test_family_invitation_code_accept_api_marks_member_active(self):
        client = TestClient(app)

        created = client.post(
            "/family/invite",
            json={
                "userId": "u_inviter",
                "name": "陈岚",
                "relation": "女儿",
                "phone": "13900001111",
            },
        )
        member = created.json()["member"]
        accepted = client.post(
            f"/family/invitations/{member['invitationCode']}/accept",
            json={"phone": "13900001111"},
        )
        listed = client.get("/family/members/u_inviter")

        self.assertEqual(created.status_code, 200)
        self.assertIn("invitationCode", member)
        self.assertIn("invitationURL", member)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["member"]["id"], member["id"])
        self.assertEqual(accepted.json()["member"]["ownerUserId"], "u_inviter")
        self.assertEqual(accepted.json()["member"]["accessStatus"], "active")
        listed_member = next(item for item in listed.json()["members"] if item["id"] == member["id"])
        self.assertEqual(listed_member["invitationStatus"], "accepted")

    def test_family_member_revoke_api_is_blocked_by_product_rule(self):
        client = TestClient(app)

        created = client.post(
            "/family/invite",
            json={
                "userId": "u_revoke_blocked",
                "name": "陈岚",
                "relation": "女儿",
                "phone": "13900001111",
            },
        )
        member = created.json()["member"]
        revoked = client.post(f"/family/members/u_revoke_blocked/{member['id']}/revoke")
        accepted = client.post(
            f"/family/invitations/{member['invitationCode']}/accept",
            json={"phone": "13900001111"},
        )

        self.assertEqual(revoked.status_code, 409)
        self.assertEqual(revoked.json()["detail"], "family member removal is not supported")
        self.assertEqual(accepted.status_code, 200)

    def test_family_member_direct_accept_still_works_after_blocked_revoke_attempt(self):
        client = TestClient(app)

        created = client.post(
            "/family/invite",
            json={
                "userId": "u_revoked_direct_accept",
                "name": "陈岚",
                "relation": "女儿",
                "phone": "13900001111",
            },
        )
        member = created.json()["member"]
        revoked = client.post(f"/family/members/u_revoked_direct_accept/{member['id']}/revoke")
        accepted = client.post(
            f"/family/members/u_revoked_direct_accept/{member['id']}/accept",
            json={"phone": "13900001111"},
        )

        self.assertEqual(revoked.status_code, 409)
        self.assertEqual(accepted.status_code, 200)


if __name__ == "__main__":
    unittest.main()
