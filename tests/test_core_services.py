import os
import unittest

from app.core.config import Settings
from app.services.in_memory_store import InMemoryStore
from app.services.postgres_store import PostgresStore
from app.services.privacy import filter_syncable_graph
from app.services.runtime_config import RuntimeConfigService
from app.services.store_factory import make_store
from app.services.tokens import TokenService
from app.services.tts import VolcTTSProxy
from app.services.amap import AMapDistrictProxy


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
        self.assertEqual(payload["headers"]["X-Api-App-ID"], "test-app-id")
        self.assertEqual(payload["headers"]["X-Api-App-Key"], "PlgvMymc7f3tQnJ6")
        self.assertNotIn("access-token-secret", str(payload))
        self.assertIn("tokenRef", payload)

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


if __name__ == "__main__":
    unittest.main()
