import unittest
from copy import deepcopy

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.core.config import Settings
from app.services.context_packet import ContextPacketBuilder
from app.services.knowledge_governance import (
    KnowledgeGovernanceNotFound,
    KnowledgeGovernanceValidationError,
    build_knowledge_governance_mutation,
)


USER_ID = "governance-user"
DECIDED_AT = "2026-07-11T10:30:00+08:00"
SOURCE_REF = {"kind": "memoryArchiveItem", "id": "archive-1", "title": "redacted"}


def knowledge_graph():
    identity = {
        "ownerUserId": USER_ID,
        "personaScope": "personal",
        "digitalHumanId": USER_ID,
        "privacyMetadata": {
            "scope": "generationAllowed",
            "sourceRefs": [deepcopy(SOURCE_REF)],
        },
        "sourceSessionIds": [7],
        "sourceTurnIndices": [2],
        "evidenceStatus": "candidate",
    }
    return {
        "version": 1,
        "people": [
            {
                "id": "person-1",
                "name": "Original Person",
                "aliases": ["Old Alias"],
                **deepcopy(identity),
            }
        ],
        "places": [
            {
                "id": "place-1",
                "name": "Original Place",
                "latitude": 30.0,
                **deepcopy(identity),
            }
        ],
        "events": [
            {
                "id": "event-1",
                "title": "Original Event",
                "year": 1990,
                "participantIds": ["person-1"],
                "locationId": "place-1",
                **deepcopy(identity),
            }
        ],
        "facts": [
            {
                "id": "fact-1",
                "statement": "Original Fact Body",
                "confidence": "low",
                "relatedPersonIds": ["person-1"],
                **deepcopy(identity),
            }
        ],
    }


ENTITY_CASES = {
    "people": ("person-1", {"name": "Corrected Person"}, "name"),
    "places": ("place-1", {"name": "Corrected Place"}, "name"),
    "events": ("event-1", {"title": "Corrected Event", "month": 6}, "title"),
    "facts": ("fact-1", {"statement": "Corrected Fact Body"}, "statement"),
}


class KnowledgeGovernanceBuilderTests(unittest.TestCase):
    def build(self, kind, entity_type, *, correction=None, operation_id=None, graph=None):
        entity_id = ENTITY_CASES[entity_type][0]
        action = {
            "kind": kind,
            "entityType": entity_type,
            "entityId": entity_id,
            "decidedAt": DECIDED_AT,
        }
        if correction is not None:
            action["correction"] = correction
        return build_knowledge_governance_mutation(
            user_id=USER_ID,
            operation_id=operation_id or f"{kind}-{entity_type}",
            base_revision=7,
            action=action,
            snapshot={"revision": 7, "graph": graph or knowledge_graph()},
        )

    def test_confirm_preserves_all_four_entity_types_and_sources(self):
        for entity_type, (entity_id, _, _) in ENTITY_CASES.items():
            with self.subTest(entity_type=entity_type):
                result = self.build("confirm", entity_type)
                entity = result["upserts"][entity_type][0]

                self.assertEqual(entity["id"], entity_id)
                self.assertEqual(entity["evidenceStatus"], "confirmed")
                self.assertEqual(entity["privacyMetadata"]["sourceRefs"], [SOURCE_REF])
                self.assertEqual(entity["sourceSessionIds"], [7])
                if entity_type == "facts":
                    self.assertEqual(entity["confidence"], "confirmed")
                self.assertNotIn("Original", str(entity["governanceMetadata"]))

    def test_reject_preserves_all_four_entity_types_and_sources(self):
        for entity_type, (entity_id, _, _) in ENTITY_CASES.items():
            with self.subTest(entity_type=entity_type):
                result = self.build("reject", entity_type)
                entity = result["upserts"][entity_type][0]

                self.assertEqual(entity["id"], entity_id)
                self.assertEqual(entity["evidenceStatus"], "rejected")
                self.assertEqual(entity["privacyMetadata"]["sourceRefs"], [SOURCE_REF])
                self.assertNotIn("Original", str(result["summary"]))

    def test_correct_supersedes_all_four_entity_types_with_stable_confirmed_replacements(self):
        for entity_type, (entity_id, correction, primary_field) in ENTITY_CASES.items():
            with self.subTest(entity_type=entity_type):
                result = self.build("correct", entity_type, correction=correction)
                repeated_build = self.build("correct", entity_type, correction=correction)
                original, replacement = result["upserts"][entity_type]

                self.assertEqual(original["id"], entity_id)
                self.assertEqual(original["evidenceStatus"], "superseded")
                self.assertNotEqual(replacement["id"], entity_id)
                self.assertEqual(
                    replacement["id"],
                    repeated_build["upserts"][entity_type][1]["id"],
                )
                self.assertEqual(replacement[primary_field], correction[primary_field])
                self.assertEqual(replacement["evidenceStatus"], "confirmed")
                self.assertEqual(replacement["ownerUserId"], USER_ID)
                self.assertEqual(replacement["personaScope"], "personal")
                self.assertEqual(replacement["digitalHumanId"], USER_ID)
                self.assertEqual(replacement["privacyMetadata"]["sourceRefs"], [SOURCE_REF])
                self.assertEqual(
                    original["governanceMetadata"]["replacement"]["entityId"],
                    replacement["id"],
                )
                if entity_type == "facts":
                    self.assertEqual(replacement["confidence"], "confirmed")

    def test_correct_rejects_identity_privacy_source_and_governance_fields(self):
        forbidden = {
            "id": "client-id",
            "ownerUserId": "other-user",
            "personaScope": "family",
            "digitalHumanId": "other-persona",
            "privacyMetadata": {"scope": "familyCircle"},
            "sourceSessionIds": [99],
            "sourceTurnIndices": [99],
            "governanceMetadata": {"action": "confirm"},
            "evidenceStatus": "confirmed",
        }
        for field, value in forbidden.items():
            with self.subTest(field=field):
                with self.assertRaises(KnowledgeGovernanceValidationError):
                    self.build("correct", "facts", correction={field: value})

    def test_builder_validates_timestamp_target_owner_and_persona(self):
        base_action = {
            "kind": "confirm",
            "entityType": "facts",
            "entityId": "fact-1",
            "decidedAt": "2026-07-11T10:30:00",
        }
        with self.assertRaisesRegex(KnowledgeGovernanceValidationError, "timezone"):
            build_knowledge_governance_mutation(
                user_id=USER_ID,
                operation_id="invalid-time",
                base_revision=7,
                action=base_action,
                snapshot={"revision": 7, "graph": knowledge_graph()},
            )

        missing_action = {**base_action, "decidedAt": DECIDED_AT, "entityId": "missing"}
        with self.assertRaises(KnowledgeGovernanceNotFound):
            build_knowledge_governance_mutation(
                user_id=USER_ID,
                operation_id="missing-target",
                base_revision=7,
                action=missing_action,
                snapshot={"revision": 7, "graph": knowledge_graph()},
            )

        for field, value in (("ownerUserId", "other"), ("digitalHumanId", "other")):
            with self.subTest(field=field):
                graph = knowledge_graph()
                graph["facts"][0][field] = value
                with self.assertRaises(KnowledgeGovernanceValidationError):
                    self.build("confirm", "facts", graph=graph)

    def test_governance_upgrades_legacy_personal_identity(self):
        graph = knowledge_graph()
        target = graph["facts"][0]
        target.pop("ownerUserId")
        target.pop("personaScope")
        target.pop("digitalHumanId")

        result = self.build("confirm", "facts", graph=graph)
        confirmed = result["upserts"]["facts"][0]

        self.assertEqual(confirmed["ownerUserId"], USER_ID)
        self.assertEqual(confirmed["personaScope"], "personal")
        self.assertEqual(confirmed["digitalHumanId"], USER_ID)

    def test_correct_rejects_missing_and_cross_persona_relationships(self):
        with self.assertRaises(KnowledgeGovernanceNotFound):
            self.build(
                "correct",
                "facts",
                correction={"relatedPersonIds": ["missing-person"]},
            )

        graph = knowledge_graph()
        graph["people"][0]["personaScope"] = "family"
        graph["people"][0]["digitalHumanId"] = "family-persona"
        with self.assertRaisesRegex(KnowledgeGovernanceValidationError, "persona"):
            self.build(
                "correct",
                "facts",
                correction={"relatedPersonIds": ["person-1"]},
                graph=graph,
            )

    def test_delete_source_builder_supersedes_matches_without_deleting_entities(self):
        result = build_knowledge_governance_mutation(
            user_id=USER_ID,
            operation_id="delete-source-1",
            base_revision=7,
            action={
                "kind": "deleteSource",
                "sourceRef": {"kind": "memoryArchiveItem", "id": "archive-1"},
                "decidedAt": DECIDED_AT,
            },
            snapshot={"revision": 7, "graph": knowledge_graph()},
        )

        self.assertEqual(result["summary"]["affectedEntityCount"], 4)
        for entity_type in ENTITY_CASES:
            entity = result["upserts"][entity_type][0]
            self.assertEqual(entity["evidenceStatus"], "superseded")
            self.assertEqual(entity["privacyMetadata"]["sourceRefs"], [])


class KnowledgeGovernanceAPITests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        main_module.store = InMemoryStore()
        main_module.store.apply_kb_mutation(
            USER_ID,
            knowledge_graph(),
            operation_id="seed-governance",
            base_revision=0,
        )
        self.client = TestClient(app)

    def tearDown(self):
        main_module.store = self.previous_store

    @staticmethod
    def payload(*, operation_id="govern-fact", base_revision=1, entity_id="fact-1"):
        return {
            "governanceSchemaVersion": 1,
            "userId": USER_ID,
            "operationId": operation_id,
            "baseRevision": base_revision,
            "action": {
                "kind": "correct",
                "entityType": "facts",
                "entityId": entity_id,
                "correction": {"statement": "Authoritative Corrected Fact"},
                "decidedAt": DECIDED_AT,
            },
        }

    def test_endpoint_returns_authoritative_graph_mutation_and_body_free_summary(self):
        response = self.client.post("/kb/governance/actions", json=self.payload())

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["governanceSchemaVersion"], 1)
        self.assertEqual(body["mutationSchemaVersion"], 2)
        self.assertEqual(body["revision"], 2)
        self.assertFalse(body["duplicate"])
        self.assertEqual(body["summary"]["action"], "correct")
        self.assertNotIn("Authoritative Corrected Fact", str(body["summary"]))
        self.assertEqual(len(body["mutation"]["upserts"]["facts"]), 2)
        self.assertEqual(
            [item["evidenceStatus"] for item in body["graph"]["facts"]],
            ["superseded", "confirmed"],
        )

    def test_endpoint_keeps_duplicate_operation_idempotent_and_maps_revision_conflict(self):
        first = self.client.post("/kb/governance/actions", json=self.payload())
        duplicate = self.client.post("/kb/governance/actions", json=self.payload())
        conflict = self.client.post(
            "/kb/governance/actions",
            json=self.payload(operation_id="stale-operation", base_revision=1),
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(duplicate.json()["revision"], 2)
        self.assertEqual(duplicate.json()["mutation"], first.json()["mutation"])
        self.assertEqual(duplicate.json()["summary"], first.json()["summary"])
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(conflict.json()["detail"]["code"], "knowledgeRevisionConflict")

    def test_endpoint_maps_invalid_correction_and_missing_target(self):
        invalid = self.payload(operation_id="invalid-correction")
        invalid["action"]["correction"] = {"ownerUserId": "other"}
        invalid_response = self.client.post("/kb/governance/actions", json=invalid)
        missing_response = self.client.post(
            "/kb/governance/actions",
            json=self.payload(operation_id="missing-target", entity_id="missing"),
        )
        missing_revision = self.payload(operation_id="missing-revision")
        missing_revision.pop("baseRevision")
        revision_response = self.client.post("/kb/governance/actions", json=missing_revision)

        self.assertEqual(invalid_response.status_code, 400)
        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(revision_response.status_code, 400)

    def test_endpoint_requires_integer_governance_schema_version(self):
        for version in (None, True, 1.0, 2):
            with self.subTest(version=version):
                payload = self.payload(operation_id=f"invalid-version-{version}")
                payload["governanceSchemaVersion"] = version
                response = self.client.post("/kb/governance/actions", json=payload)

                self.assertEqual(response.status_code, 400)

    def test_archive_endpoint_rejects_cross_owner_id_reuse_without_disclosing_owner(self):
        first = self.client.post(
            "/archive/items",
            json={
                "userId": USER_ID,
                "id": "shared-archive",
                "kind": "photo",
                "privacyMetadata": {"scope": "generationAllowed", "sourceRefs": []},
            },
        )
        conflict = self.client.post(
            "/archive/items",
            json={
                "userId": "other-user",
                "id": "shared-archive",
                "kind": "photo",
                "privacyMetadata": {"scope": "generationAllowed", "sourceRefs": []},
            },
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(conflict.status_code, 409, conflict.text)
        self.assertEqual(
            conflict.json()["detail"],
            {"code": "archiveItemOwnershipConflict"},
        )
        self.assertNotIn(USER_ID, conflict.text)

    def test_archive_delete_cascades_source_revocation_and_is_idempotent(self):
        main_module.store.add_archive_item(
            USER_ID,
            {"id": "archive-1", "kind": "photo", "title": "source"},
        )

        first = self.client.delete(
            f"/archive/items/{USER_ID}/archive-1?operationId=delete-archive-1"
        )
        repeated = self.client.delete(
            f"/archive/items/{USER_ID}/archive-1?operationId=delete-archive-1"
        )

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["cascade"]["affectedEntityCount"], 4)
        self.assertEqual(first.json()["cascade"]["revision"], 2)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertTrue(repeated.json()["cascade"]["duplicate"])
        self.assertIsNone(repeated.json()["item"])
        self.assertEqual(main_module.store.list_archive_items(USER_ID), [])

        snapshot = main_module.store.get_kb_snapshot_record(USER_ID)
        for entity_type in ENTITY_CASES:
            self.assertEqual(snapshot["graph"][entity_type][0]["evidenceStatus"], "superseded")
            self.assertEqual(
                snapshot["graph"][entity_type][0]["privacyMetadata"]["sourceRefs"],
                [],
            )
        self.assertEqual(len(main_module.store.list_kb_changes(USER_ID, 0)), 2)

        packet = ContextPacketBuilder(
            main_module.store,
            Settings(store_backend="memory"),
        ).build(
            {
                "userId": USER_ID,
                "intent": "echo_chat",
                "query": "Original Fact Body",
                "personaScope": "personal",
                "digitalHumanId": USER_ID,
            }
        )
        self.assertEqual(packet["memory"]["kbFacts"], [])
        self.assertNotIn("Original Fact Body", packet["generationContext"]["text"])

    def test_archive_delete_without_knowledge_reference_keeps_revision(self):
        main_module.store.add_archive_item(
            USER_ID,
            {"id": "unreferenced", "kind": "photo"},
        )

        response = self.client.delete(f"/archive/items/{USER_ID}/unreferenced")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cascade"]["affectedEntityCount"], 0)
        self.assertFalse(response.json()["cascade"]["sourceMatched"])
        self.assertEqual(response.json()["cascade"]["revision"], 1)
        self.assertEqual(len(main_module.store.list_kb_changes(USER_ID, 0)), 1)


if __name__ == "__main__":
    unittest.main()
