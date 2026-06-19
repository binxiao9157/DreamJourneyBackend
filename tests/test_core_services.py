import os
import unittest
from datetime import datetime, timezone

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
from app.services.tts import VolcTTSProxy
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

    def test_store_marks_family_member_revoked(self):
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

        revoked = client.post(f"/family/members/{user_id}/{member_id}/revoke")
        revoked_write = client.post(
            "/care/snapshots",
            json={
                "userId": user_id,
                "viewerFamilyMemberID": member_id,
                "snapshot": {"summary": "撤销后不可写入"},
            },
        )
        revoked_latest = client.get(
            f"/care/snapshots/latest/{user_id}",
            params={"viewerFamilyMemberID": member_id, "requesterPhone": "13900001111"},
        )
        revoked_history = client.get(
            f"/care/snapshots/{user_id}",
            params={"viewerFamilyMemberID": member_id, "requesterPhone": "13900001111"},
        )

        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked_write.status_code, 403)
        self.assertEqual(revoked_latest.status_code, 403)
        self.assertEqual(revoked_history.status_code, 403)

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


class ArchiveAPITests(unittest.TestCase):
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
    def test_image_analysis_parse_requires_structured_json(self):
        proxy = DeepSeekImageAnalysisProxy(Settings(deepseek_api_key="deepseek-secret"))

        with self.assertRaisesRegex(ValueError, "non-JSON"):
            proxy.parse_analysis("这是一张照片，有三个人，像是在老家门口。")

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

        self.assertEqual(response.status_code, 503)
        self.assertIn("DEEPSEEK_API_KEY is not configured", response.text)


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

    def test_family_invitation_code_rejects_revoked_member(self):
        client = TestClient(app)

        created = client.post(
            "/family/invite",
            json={
                "userId": "u_revoked_inviter",
                "name": "陈岚",
                "relation": "女儿",
                "phone": "13900001111",
            },
        )
        member = created.json()["member"]
        revoked = client.post(f"/family/members/u_revoked_inviter/{member['id']}/revoke")
        accepted = client.post(
            f"/family/invitations/{member['invitationCode']}/accept",
            json={"phone": "13900001111"},
        )

        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(accepted.status_code, 404)

    def test_family_member_direct_accept_rejects_revoked_member(self):
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

        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(accepted.status_code, 404)

    def test_family_member_revoke_api_marks_member_revoked(self):
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
        revoked = client.post(f"/family/members/u1/{member_id}/revoke")
        listed = client.get("/family/members/u1")

        self.assertEqual(created.status_code, 200)
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(revoked.json()["member"]["accessStatus"], "revoked")
        self.assertEqual(revoked.json()["member"]["invitationStatus"], "revoked")
        self.assertIn("revokedAt", revoked.json()["member"])
        listed_member = next(item for item in listed.json()["members"] if item["id"] == member_id)
        self.assertEqual(listed_member["accessStatus"], "revoked")


if __name__ == "__main__":
    unittest.main()
