import unittest

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.knowledge_source_ref_audit import audit_knowledge_source_refs


class KnowledgeSourceRefAuditTests(unittest.TestCase):
    def test_classifies_refs_without_exposing_source_or_entity_content(self):
        audit = audit_knowledge_source_refs(
            {
                "revision": 8,
                "graph": {
                    "people": [
                        {
                            "id": "ENTITY_ID_SENTINEL",
                            "name": "ENTITY_TEXT_SENTINEL",
                            "privacyMetadata": {
                                "sourceRefs": [
                                    {
                                        "kind": "conversationTurn",
                                        "id": "SOURCE_ID_SENTINEL",
                                        "title": "SOURCE_TITLE_SENTINEL",
                                    },
                                    {"kind": "conversationSession", "id": "legacy-1"},
                                    {"kind": "futureSource", "id": "future-1"},
                                ]
                            },
                        },
                        {
                            "id": "p2",
                            "privacyMetadata": {
                                "sourceRefs": [
                                    {"kind": "conversationPhoto", "id": "photo-1"},
                                    {"kind": "memoryArchiveItem", "id": "archive-1"},
                                ]
                            },
                        },
                    ],
                    "places": [{"id": "place-without-refs"}],
                    "events": [],
                    "facts": [],
                },
            }
        )

        self.assertEqual(audit["schemaVersion"], 1)
        self.assertEqual(audit["revision"], 8)
        self.assertEqual(
            audit["counts"],
            {
                "entities": {
                    "total": 3,
                    "withSourceRefs": 2,
                    "withCanonicalRefs": 2,
                    "withLegacyRefs": 1,
                    "withUnknownRefs": 1,
                },
                "sourceRefs": {
                    "total": 5,
                    "canonical": 3,
                    "legacy": 1,
                    "unknown": 1,
                },
            },
        )
        self.assertEqual(audit["recommendedAction"], "reviewUnknownSourceRefs")
        serialized = str(audit)
        self.assertNotIn("ENTITY_ID_SENTINEL", serialized)
        self.assertNotIn("ENTITY_TEXT_SENTINEL", serialized)
        self.assertNotIn("SOURCE_ID_SENTINEL", serialized)
        self.assertNotIn("SOURCE_TITLE_SENTINEL", serialized)

    def test_legacy_only_recommends_planning_without_applying_migration(self):
        audit = audit_knowledge_source_refs(
            {
                "revision": 3,
                "graph": {
                    "people": [
                        {
                            "privacyMetadata": {
                                "sourceRefs": [
                                    {"kind": "archiveImageAnalysis", "id": "legacy-1"}
                                ]
                            }
                        }
                    ]
                },
            }
        )

        self.assertEqual(audit["counts"]["sourceRefs"]["legacy"], 1)
        self.assertEqual(audit["recommendedAction"], "planLegacySourceRefMigration")


class KnowledgeSourceRefAuditEndpointTests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        self.previous_backend_token = main_module.BACKEND_API_TOKEN
        self.previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
        self.previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
        main_module.store = InMemoryStore()
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        self.client = TestClient(app)

    def tearDown(self):
        main_module.store = self.previous_store
        main_module.BACKEND_API_TOKEN = self.previous_backend_token
        main_module.AUTH_OWNERSHIP_MODE = self.previous_ownership_mode
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = self.previous_legacy_phone_login

    def _login(self, phone: str):
        response = self.client.post(
            "/auth/login",
            json={"phone": phone, "nickname": "测试用户", "password": "password123"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def _headers(login_body):
        return {"Authorization": f"Bearer {login_body['auth']['accessToken']}"}

    def test_endpoint_is_owner_bound_read_only_and_redacted(self):
        owner = self._login("13800138001")
        other = self._login("13800138002")
        owner_id = owner["user"]["id"]
        main_module.store.apply_kb_mutation(
            owner_id,
            {
                "people": [
                    {
                        "id": "private-entity-id",
                        "name": "PRIVATE_ENTITY_TEXT",
                        "privacyMetadata": {
                            "scope": "generationAllowed",
                            "sourceRefs": [
                                {
                                    "kind": "conversationSession",
                                    "id": "PRIVATE_SOURCE_ID",
                                    "title": "PRIVATE_SOURCE_TITLE",
                                }
                            ],
                        },
                    }
                ]
            },
            operation_id="seed-source-ref-audit",
            base_revision=0,
        )
        before = main_module.store.get_kb_snapshot_record(owner_id)

        denied = self.client.get(
            f"/kb/source-ref-audit/{owner_id}",
            headers=self._headers(other),
        )
        allowed = self.client.get(
            f"/kb/source-ref-audit/{owner_id}",
            headers=self._headers(owner),
        )
        after = main_module.store.get_kb_snapshot_record(owner_id)

        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        payload = allowed.json()
        self.assertEqual(
            set(payload),
            {"userId", "revision", "schemaVersion", "counts", "recommendedAction"},
        )
        self.assertEqual(payload["userId"], owner_id)
        self.assertEqual(payload["revision"], 1)
        self.assertEqual(payload["counts"]["sourceRefs"]["legacy"], 1)
        self.assertEqual(payload["recommendedAction"], "planLegacySourceRefMigration")
        self.assertEqual(after, before)
        self.assertNotIn("graph", payload)
        self.assertNotIn("sourceRefs", payload)
        self.assertNotIn("id", payload)
        self.assertNotIn("title", payload)
        self.assertNotIn("PRIVATE_ENTITY_TEXT", str(payload))
        self.assertNotIn("PRIVATE_SOURCE_ID", str(payload))
        self.assertNotIn("PRIVATE_SOURCE_TITLE", str(payload))


if __name__ == "__main__":
    unittest.main()
