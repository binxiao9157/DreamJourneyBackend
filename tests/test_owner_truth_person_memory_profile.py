from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION_V2
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_person_memory_profile import (
    PERSON_MEMORY_PROFILE_SCHEMA_VERSION,
    OwnerTruthPersonMemoryProfileService,
)


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _facets(**values: list[str]) -> dict[str, object]:
    names = (
        "people",
        "time",
        "places",
        "relationships",
        "emotions",
        "values",
        "personality",
    )
    return {
        **{
            name: [
                {"value": value, "evidenceMode": "ownerStated", "confidence": 1.0}
                for value in values.get(name, [])
            ]
            for name in names
        },
        "confidence": 1.0,
    }


class OwnerTruthPersonMemoryProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.context = OwnerTruthCommandContext(
            vault_id="vault-person-memory-profile",
            owner_subject_id="owner-person-memory-profile",
            actor_subject_id="owner-person-memory-profile",
        )
        self.review = OwnerTruthCandidateReviewService(self.store)
        self.service = OwnerTruthPersonMemoryProfileService(self.store)

    def _activate(
        self,
        *,
        kind: MemoryKind,
        content: dict[str, object],
        created_at: str,
    ) -> str:
        source_id = str(uuid4())
        candidate_id = str(uuid4())
        candidate = OwnerTruthCandidateSnapshot(
            candidate_id=candidate_id,
            vault_id=self.context.vault_id,
            owner_subject_id=self.context.owner_subject_id,
            source_id=source_id,
            memory_kind=kind,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=self.context.policy_version,
            authority_epoch=0,
            row_version=1,
            content_hash=_digest(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
            payload={
                "schemaVersion": "owner-truth-candidate-v1",
                "candidateKind": kind.value,
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V2,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
            },
        )
        self.store.owner_truth_candidate_review_repository().seed(
            candidate,
            created_at=created_at,
        )
        result = self.review.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=f"activate-{candidate_id}",
                candidate_id=candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )
        return str(result.memory_activation.memory_id)

    def test_profile_aggregates_current_memories_into_dimension_narratives(self) -> None:
        first_experience_id = self._activate(
            kind=MemoryKind.EXPERIENCE,
            content={
                "summary": "小时候常在老院子里听外祖父讲故事",
                "facets": _facets(
                    people=["外祖父"],
                    places=["老院子"],
                    relationships=["祖孙"],
                    values=["重视家人"],
                    personality=["耐心"],
                ),
            },
            created_at="2026-08-18T10:00:00+00:00",
        )
        second_experience_id = self._activate(
            kind=MemoryKind.EXPERIENCE,
            content={
                "summary": "第一次独自去外地工作让我学会承担责任",
                "facets": _facets(values=["责任"], personality=["独立"]),
            },
            created_at="2026-08-19T10:00:00+00:00",
        )
        knowledge_id = self._activate(
            kind=MemoryKind.KNOWLEDGE,
            content={
                "claim": "遇到复杂工作时先拆分问题再逐项验证",
                "facets": _facets(values=["务实"]),
            },
            created_at="2026-08-20T10:00:00+00:00",
        )
        emotion_id = self._activate(
            kind=MemoryKind.EMOTION,
            content={
                "label": "想起外祖父时会感到怀念和温暖",
                "facets": _facets(people=["外祖父"], emotions=["怀念", "温暖"]),
            },
            created_at="2026-08-21T10:00:00+00:00",
        )

        profile = self.service.read(context=self.context)
        contract = profile.public_contract()
        self.assertEqual(contract["schemaVersion"], PERSON_MEMORY_PROFILE_SCHEMA_VERSION)
        self.assertEqual(contract["state"], "ready")
        self.assertEqual(contract["memoryCount"], 4)
        self.assertIsInstance(contract["updatedAt"], str)
        self.assertEqual(contract["lifeRecord"]["format"], "plainText")
        self.assertEqual(contract["lifeRecord"]["state"], "ready")
        self.assertEqual(contract["lifeRecord"]["title"], "我的人生记录")
        self.assertEqual(
            contract["lifeRecord"]["text"],
            "\n\n".join(contract["lifeRecord"]["paragraphs"]),
        )
        self.assertNotIn("#", contract["lifeRecord"]["text"])
        self.assertEqual(
            [item["dimension"] for item in contract["dimensions"]],
            [
                "lifeExperience",
                "knowledgeAndSkills",
                "emotionsAndAttachments",
                "importantRelationships",
                "personality",
                "valuesAndChoices",
            ],
        )

        dimensions = {item["dimension"]: item for item in contract["dimensions"]}
        experience = dimensions["lifeExperience"]
        self.assertEqual(experience["status"], "ready")
        self.assertEqual(
            set(experience["supportingMemoryIds"]),
            {first_experience_id, second_experience_id},
        )
        self.assertIn("小时候常在老院子里听外祖父讲故事", experience["narrative"])
        self.assertIn("第一次独自去外地工作", experience["narrative"])
        self.assertIn("老院子", experience["narrative"])
        self.assertIn("重视家人", experience["narrative"])
        self.assertIn("责任", experience["narrative"])

        self.assertEqual(
            dimensions["knowledgeAndSkills"]["supportingMemoryIds"],
            [knowledge_id],
        )
        self.assertEqual(
            set(dimensions["emotionsAndAttachments"]["supportingMemoryIds"]),
            {emotion_id},
        )
        self.assertIn("怀念、温暖", dimensions["emotionsAndAttachments"]["narrative"])
        self.assertIn("外祖父", dimensions["importantRelationships"]["narrative"])
        self.assertIn("耐心", dimensions["personality"]["narrative"])
        self.assertIn("重视家人", dimensions["valuesAndChoices"]["narrative"])

        replay = self.service.read(context=self.context)
        self.assertEqual(replay.profile_version, profile.profile_version)

    def test_unpopulated_dimensions_are_explicitly_empty(self) -> None:
        memory_id = self._activate(
            kind=MemoryKind.KNOWLEDGE,
            content={
                "claim": "做决定前要先核对事实",
                "facets": _facets(),
            },
            created_at="2026-08-20T10:00:00+00:00",
        )

        contract = self.service.read(context=self.context).public_contract()
        self.assertEqual(contract["lifeRecord"]["state"], "ready")
        self.assertEqual(contract["lifeRecord"]["paragraphCount"], 1)
        self.assertIn("核对事实", contract["lifeRecord"]["text"])
        dimensions = {item["dimension"]: item for item in contract["dimensions"]}
        self.assertEqual(
            dimensions["knowledgeAndSkills"]["supportingMemoryIds"],
            [memory_id],
        )
        self.assertEqual(dimensions["lifeExperience"]["status"], "empty")
        self.assertIsNone(dimensions["lifeExperience"]["narrative"])
        self.assertEqual(dimensions["lifeExperience"]["supportingMemoryIds"], [])

    def test_empty_profile_exposes_an_empty_plain_text_life_record(self) -> None:
        contract = self.service.read(context=self.context).public_contract()

        self.assertEqual(contract["state"], "empty")
        self.assertEqual(contract["lifeRecord"]["format"], "plainText")
        self.assertEqual(contract["lifeRecord"]["state"], "empty")
        self.assertEqual(contract["lifeRecord"]["paragraphCount"], 0)
        self.assertEqual(contract["lifeRecord"]["paragraphs"], [])
        self.assertIsNone(contract["lifeRecord"]["text"])


if __name__ == "__main__":
    unittest.main()
