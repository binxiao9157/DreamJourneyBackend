import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.services.in_memory_store import InMemoryStore
from app.services.knowledge_proposal import (
    KnowledgeProposalValidationError,
    build_knowledge_mutation_proposal,
)
from app.services.knowledge_store import normalize_kb_mutation_v2


def safe_context(session_id=12):
    return {
        "sessionId": session_id,
        "privacyMetadata": {
            "scope": "generationAllowed",
            "sourceRefs": [
                {
                    "kind": "conversationTurn",
                    "id": f"session-{session_id}",
                    "title": "RAW_TRANSCRIPT_SENTINEL",
                    "text": "RAW_TURN_SENTINEL",
                }
            ],
        },
    }


class KnowledgeProposalBuilderTests(unittest.TestCase):
    def test_stable_ids_use_normalized_natural_keys_and_reuse_personal_legacy_id(self):
        first = build_knowledge_mutation_proposal(
            user_id="user-1",
            persona_scope="personal",
            digital_human_id="ignored-persona",
            extraction={"people": [{"name": "Ａlice\u3000Smith", "sourceTurnIndices": [0]}]},
            safe_context=safe_context(),
            snapshot=None,
        )
        repeated = build_knowledge_mutation_proposal(
            user_id="user-1",
            persona_scope="personal",
            digital_human_id=None,
            extraction={"people": [{"name": "alice   smith", "sourceTurnIndices": [0]}]},
            safe_context=safe_context(),
            snapshot=None,
        )

        first_person = first["upserts"]["people"][0]
        self.assertEqual(first_person["id"], repeated["upserts"]["people"][0]["id"])
        self.assertTrue(first_person["id"].startswith("kb_person_"))
        self.assertEqual(first["digitalHumanId"], "user-1")

        legacy = build_knowledge_mutation_proposal(
            user_id="user-1",
            persona_scope="personal",
            digital_human_id=None,
            extraction={"people": [{"name": " ALICE smith ", "sourceTurnIndices": [0]}]},
            safe_context=safe_context(13),
            snapshot={
                "revision": 4,
                "graph": {"people": [{"id": "legacy-person-id", "name": "Alice Smith"}]},
            },
        )

        self.assertEqual(legacy["baseRevision"], 4)
        self.assertEqual(legacy["upserts"]["people"][0]["id"], "legacy-person-id")
        self.assertEqual(legacy["proposalPolicy"]["reusedEntityCount"], 1)

    def test_family_reuses_only_explicit_matching_family_identity(self):
        proposal = build_knowledge_mutation_proposal(
            user_id="owner-1",
            persona_scope="family",
            digital_human_id="family-target",
            extraction={
                "people": [
                    {"name": "Legacy Person", "sourceTurnIndices": [0]},
                    {"name": "Other Persona", "sourceTurnIndices": [0]},
                    {"name": "Matching Persona", "sourceTurnIndices": [0]},
                ]
            },
            safe_context=safe_context(),
            snapshot={
                "revision": 8,
                "graph": {
                    "people": [
                        {"id": "legacy-id", "name": "Legacy Person"},
                        {
                            "id": "other-id",
                            "name": "Other Persona",
                            "ownerUserId": "owner-1",
                            "personaScope": "family",
                            "digitalHumanId": "family-other",
                        },
                        {
                            "id": "matching-id",
                            "name": "Matching Persona",
                            "ownerUserId": "owner-1",
                            "personaScope": "family",
                            "digitalHumanId": "family-target",
                        },
                    ]
                },
            },
        )
        ids_by_name = {item["name"]: item["id"] for item in proposal["upserts"]["people"]}

        self.assertNotEqual(ids_by_name["Legacy Person"], "legacy-id")
        self.assertNotEqual(ids_by_name["Other Persona"], "other-id")
        self.assertEqual(ids_by_name["Matching Persona"], "matching-id")
        self.assertEqual(proposal["proposalPolicy"]["reusedEntityCount"], 1)
        self.assertEqual(proposal["proposalPolicy"]["generatedEntityCount"], 2)

    def test_family_requires_explicit_digital_human_id(self):
        with self.assertRaises(KnowledgeProposalValidationError):
            build_knowledge_mutation_proposal(
                user_id="owner-1",
                persona_scope="family",
                digital_human_id="",
                extraction={"people": []},
                safe_context=safe_context(),
                snapshot=None,
            )

    def test_existing_rejected_evidence_is_not_revived_by_reextraction(self):
        proposal = build_knowledge_mutation_proposal(
            user_id="user-1",
            persona_scope="personal",
            digital_human_id=None,
            extraction={
                "facts": [
                    {
                        "statement": "用户已经拒绝的事实",
                        "confidence": "high",
                        "sourceTurnIndices": [0],
                    }
                ]
            },
            safe_context=safe_context(14),
            snapshot={
                "revision": 9,
                "graph": {
                    "facts": [
                        {
                            "id": "rejected-fact",
                            "statement": "用户已经拒绝的事实",
                            "confidence": "high",
                            "evidenceStatus": "rejected",
                        }
                    ]
                },
            },
        )

        fact = proposal["upserts"]["facts"][0]
        self.assertEqual(fact["id"], "rejected-fact")
        self.assertEqual(fact["evidenceStatus"], "rejected")

    def test_relationships_metadata_evidence_and_mutation_validation(self):
        proposal = build_knowledge_mutation_proposal(
            user_id="user-1",
            persona_scope="personal",
            digital_human_id=None,
            extraction={
                "people": [{"name": "陈建国", "sourceTurnIndices": [0]}],
                "places": [
                    {
                        "name": "南京",
                        "relatedPeople": ["陈建国", "未知人物"],
                        "sourceTurnIndices": [0],
                    }
                ],
                "events": [
                    {
                        "title": "年轻时工作",
                        "year": 1968,
                        "month": 5,
                        "participants": ["陈建国", "未知人物"],
                        "location": "南京",
                        "sourceTurnIndices": [0],
                    }
                ],
                "facts": [
                    {
                        "statement": "陈建国年轻时在南京工作",
                        "confidence": "high",
                        "relatedPeople": ["陈建国"],
                        "relatedPlaces": ["南京"],
                        "relatedEvents": ["年轻时工作"],
                        "sourceTurnIndices": [0],
                    },
                    {
                        "statement": "可能喜欢南京",
                        "confidence": "low",
                        "relatedPlaces": ["不存在的地点"],
                        "sourceTurnIndices": [0],
                    },
                ],
            },
            safe_context=safe_context(42),
            snapshot=None,
        )
        person = proposal["upserts"]["people"][0]
        place = proposal["upserts"]["places"][0]
        event = proposal["upserts"]["events"][0]
        fact, candidate = proposal["upserts"]["facts"]

        self.assertEqual(place["relatedPersonIds"], [person["id"]])
        self.assertEqual(event["participantIds"], [person["id"]])
        self.assertEqual(event["locationId"], place["id"])
        self.assertEqual(fact["relatedPersonIds"], [person["id"]])
        self.assertEqual(fact["relatedPlaceIds"], [place["id"]])
        self.assertEqual(fact["relatedEventIds"], [event["id"]])
        self.assertEqual(candidate["relatedPlaceIds"], [])

        for entity_type in ("people", "places", "events", "facts"):
            for entity in proposal["upserts"][entity_type]:
                self.assertEqual(entity["ownerUserId"], "user-1")
                self.assertEqual(entity["personaScope"], "personal")
                self.assertEqual(entity["digitalHumanId"], "user-1")
                self.assertEqual(entity["sourceSessionIds"], [42])
                self.assertEqual(entity["sourceTurnIndices"], [0])
                self.assertEqual(entity["privacyMetadata"]["scope"], "generationAllowed")
                self.assertEqual(
                    entity["privacyMetadata"]["sourceRefs"][0]["title"], "对话来源"
                )
        self.assertEqual(person["evidenceStatus"], "observed")
        self.assertEqual(fact["evidenceStatus"], "observed")
        self.assertEqual(candidate["evidenceStatus"], "candidate")
        self.assertNotIn("RAW_", str(proposal))
        self.assertNotIn("陈建国", str(proposal["proposalPolicy"]))
        normalize_kb_mutation_v2(proposal["upserts"], proposal["tombstones"])


class KnowledgeProposalEndpointTests(unittest.TestCase):
    def setUp(self):
        self.previous_store = main_module.store
        main_module.store = InMemoryStore()
        self.client = TestClient(app)

    def tearDown(self):
        main_module.store = self.previous_store

    def test_v2_extract_adds_proposal_without_persisting_snapshot(self):
        main_module.store.apply_kb_mutation(
            "user-1",
            {"people": [{"id": "legacy-person", "name": "陈建国"}]},
            operation_id="seed",
            base_revision=0,
        )
        snapshot_before = main_module.store.get_kb_snapshot_record("user-1")
        changes_before = main_module.store.list_kb_changes("user-1", 0)
        provider_extraction = {
            "people": [{"name": "陈建国", "sourceTurnIndices": [0]}],
            "places": [],
            "events": [],
            "facts": [],
        }

        with patch(
            "app.main.DeepSeekKnowledgeExtractionProxy.request_extraction",
            return_value=provider_extraction,
        ):
            response = self.client.post(
                "/kb/extract",
                json={
                    "userId": "user-1",
                    "extractionSchemaVersion": 2,
                    "sourcePolicy": "userEvidenceOnly",
                    "sessionId": 12,
                    "turns": [{"index": 0, "role": "user", "text": "我叫陈建国。"}],
                    "privacyMetadata": {
                        "scope": "generationAllowed",
                        "sourceRefs": [
                            {
                                "kind": "conversationTurn",
                                "id": "turn-0",
                                "title": "正文哨兵",
                            }
                        ],
                    },
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["extraction"], provider_extraction)
        self.assertEqual(payload["evidencePolicy"]["acceptedEntityCount"], 1)
        self.assertEqual(payload["mutationProposal"]["baseRevision"], 1)
        self.assertEqual(
            payload["mutationProposal"]["upserts"]["people"][0]["id"], "legacy-person"
        )
        self.assertEqual(
            main_module.store.get_kb_snapshot_record("user-1"), snapshot_before
        )
        self.assertEqual(main_module.store.list_kb_changes("user-1", 0), changes_before)


if __name__ == "__main__":
    unittest.main()
