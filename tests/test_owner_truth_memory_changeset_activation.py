from __future__ import annotations

from hashlib import sha256
import json
import unittest
from uuid import uuid4

from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateSnapshot
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.memory_changeset import OwnerTruthCurrentFormalMemory
from app.domain.owner_truth.memory_changeset_activation import (
    build_memory_changeset_activation_plan,
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    enrich_memory_payload_v5,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthMemoryChangeSetActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-changeset-activation"
        self.owner_id = "subject-changeset-activation"

    def _content(self, *, source_mode: str = "selfReport") -> dict[str, object]:
        return enrich_memory_payload_v5(
            kind=MemoryKind.KNOWLEDGE,
            payload={
                "statement": "我在杭州时喜欢吃东坡肉",
                "knowledgeType": "personal_preference",
                "domains": ["饮食"],
                "factType": "preference",
                "predicate": "prefers",
                "object": {"label": "东坡肉", "category": "dish"},
                "qualifiers": {
                    "polarity": "positive",
                    "currentApplicability": "historical",
                    "validTime": {"precision": "year", "expression": "2016年"},
                },
            },
            provenance={"mode": source_mode},
            memory_subject_id="person-owner",
            claim_subject_id="person-owner",
        )

    def _candidate(
        self,
        *,
        content: dict[str, object],
        source_id: str,
        decision: CandidateDecision = CandidateDecision.ACCEPTED,
    ) -> OwnerTruthCandidateSnapshot:
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.KNOWLEDGE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=decision,
            policy_version="owner-truth-v5",
            authority_epoch=0,
            row_version=2,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
                "evidenceRefs": [
                    {"sourceId": source_id, "sourceVersion": 1, "span": {"start": 0, "end": 10}}
                ],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    def _current(self, *, content: dict[str, object], source_id: str) -> OwnerTruthCurrentFormalMemory:
        return OwnerTruthCurrentFormalMemory(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            version_number=2,
            memory_kind=MemoryKind.KNOWLEDGE,
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
            content=content,
            evidence_refs=(
                {"sourceId": source_id, "sourceVersion": 1, "span": {"start": 0, "end": 10}},
            ),
        )

    def test_new_evidence_revises_one_existing_memory_and_merges_lineage(self) -> None:
        old_source = str(uuid4())
        new_source = str(uuid4())
        current = self._current(content=self._content(), source_id=old_source)
        candidate = self._candidate(content=self._content(source_mode="familyReport"), source_id=new_source)

        plan = build_memory_changeset_activation_plan(
            candidate=candidate,
            receipt_id=str(uuid4()),
            receipt_decision=CandidateDecision.ACCEPTED,
            receipt_after_hash=candidate.content_hash,
            current_memories=(current,),
            base_memory_revision=7,
        )

        self.assertEqual(plan.outcome, "revised")
        self.assertEqual(plan.memory_id, current.memory_id)
        self.assertEqual(plan.memory_version, 3)
        self.assertEqual(plan.supersedes_version_id, current.memory_version_id)
        self.assertEqual(len(plan.payload["evidenceRefs"]), 2)
        self.assertEqual(plan.change_set.operation.kind.value, "addEvidence")

    def test_duplicate_creates_no_new_version(self) -> None:
        source_id = str(uuid4())
        content = self._content()
        current = self._current(content=content, source_id=source_id)
        candidate = self._candidate(content=content, source_id=source_id)

        plan = build_memory_changeset_activation_plan(
            candidate=candidate,
            receipt_id=str(uuid4()),
            receipt_decision=CandidateDecision.ACCEPTED,
            receipt_after_hash=candidate.content_hash,
            current_memories=(current,),
            base_memory_revision=7,
        )

        self.assertEqual(plan.outcome, "duplicate")
        self.assertFalse(plan.writes_memory_version)
        self.assertEqual(plan.memory_version_id, current.memory_version_id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
