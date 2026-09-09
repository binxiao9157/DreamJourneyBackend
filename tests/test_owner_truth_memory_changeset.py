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
from app.domain.owner_truth.memory_changeset import (
    OwnerTruthCurrentFormalMemory,
    OwnerTruthMemoryChangeOperationKind,
    build_memory_changeset,
    build_memory_changeset_proposal,
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    enrich_memory_payload_v5,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class OwnerTruthMemoryChangeSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-changeset"
        self.owner_id = "subject-changeset"

    def _content(
        self,
        *,
        statement: str = "我在杭州时喜欢吃东坡肉",
        object_label: str = "东坡肉",
        polarity: str = "positive",
        current: str = "historical",
        time: str | None = "2016年",
        subject_id: str | None = "person-owner",
    ) -> dict[str, object]:
        return enrich_memory_payload_v5(
            kind=MemoryKind.KNOWLEDGE,
            payload={
                "statement": statement,
                "knowledgeType": "personal_preference",
                "domains": ["饮食"],
                "factType": "preference",
                "predicate": "prefers",
                "object": {"label": object_label, "category": "dish"},
                "qualifiers": {
                    "polarity": polarity,
                    "currentApplicability": current,
                    "validTime": {
                        "precision": "year" if time else "unknown",
                        "expression": time,
                    },
                },
            },
            provenance={"mode": "selfReport"},
            memory_subject_id=subject_id,
            claim_subject_id=subject_id,
        )

    def _candidate(
        self,
        *,
        content: dict[str, object],
        source_id: str | None = None,
        source_version: int = 1,
        review_mode: str = "single",
        correction_of_memory_version_id: str | None = None,
    ) -> OwnerTruthCandidateSnapshot:
        source_id = source_id or str(uuid4())
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=MemoryKind.KNOWLEDGE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version="owner-truth-v5",
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
                "evidenceRefs": [
                    {"sourceId": source_id, "sourceVersion": source_version, "span": {"start": 0, "end": 9}}
                ],
                "reviewMode": review_mode,
                "schemaVersion": "owner-truth-candidate-proposal-v1",
                **(
                    {"correctionOfMemoryVersionId": correction_of_memory_version_id}
                    if correction_of_memory_version_id
                    else {}
                ),
            },
        )

    def _current(
        self,
        *,
        content: dict[str, object],
        source_id: str,
        source_version: int = 1,
        version: int = 1,
    ) -> OwnerTruthCurrentFormalMemory:
        return OwnerTruthCurrentFormalMemory(
            memory_id=str(uuid4()),
            memory_version_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            version_number=version,
            memory_kind=MemoryKind.KNOWLEDGE,
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
            content=content,
            evidence_refs=(
                {"sourceId": source_id, "sourceVersion": source_version, "span": {"start": 0, "end": 9}},
            ),
        )

    def test_past_and_current_preference_is_a_temporal_change_not_a_conflict(self) -> None:
        source_id = str(uuid4())
        current = self._current(content=self._content(), source_id=source_id)
        candidate = self._candidate(
            content=self._content(
                statement="现在我不喜欢吃东坡肉",
                polarity="negative",
                current="current",
                time=None,
            )
        )

        result = build_memory_changeset(
            candidate=candidate,
            current_memories=(current,),
            base_memory_revision=4,
        )

        self.assertEqual(result.operation.kind, OwnerTruthMemoryChangeOperationKind.TEMPORAL_CHANGE)
        self.assertEqual(result.operation.target_memory_id, current.memory_id)

    def test_same_fact_with_same_evidence_is_deduplicated(self) -> None:
        source_id = str(uuid4())
        content = self._content()
        current = self._current(content=content, source_id=source_id)
        candidate = self._candidate(content=content, source_id=source_id)

        result = build_memory_changeset(
            candidate=candidate,
            current_memories=(current,),
            base_memory_revision=5,
        )

        self.assertEqual(result.operation.kind, OwnerTruthMemoryChangeOperationKind.DUPLICATE)
        self.assertEqual(result.operation.added_evidence_count, 0)

    def test_same_fact_with_a_new_source_adds_evidence(self) -> None:
        current = self._current(content=self._content(), source_id=str(uuid4()))
        candidate = self._candidate(content=self._content())

        result = build_memory_changeset(
            candidate=candidate,
            current_memories=(current,),
            base_memory_revision=5,
        )

        self.assertEqual(result.operation.kind, OwnerTruthMemoryChangeOperationKind.ADD_EVIDENCE)
        self.assertEqual(result.operation.target_memory_version_id, current.memory_version_id)
        self.assertEqual(result.operation.added_evidence_count, 1)

    def test_same_scope_opposite_polarity_is_a_dispute(self) -> None:
        current = self._current(content=self._content(), source_id=str(uuid4()))
        candidate = self._candidate(
            content=self._content(statement="我不喜欢吃东坡肉", polarity="negative")
        )

        result = build_memory_changeset(
            candidate=candidate,
            current_memories=(current,),
            base_memory_revision=5,
        )

        self.assertEqual(result.operation.kind, OwnerTruthMemoryChangeOperationKind.DISPUTE)

    def test_unknown_or_different_subject_is_not_merged(self) -> None:
        current = self._current(content=self._content(), source_id=str(uuid4()))
        candidate = self._candidate(
            content=self._content(
                statement="父亲喜欢吃东坡肉",
                subject_id="person-father",
            )
        )

        result = build_memory_changeset(
            candidate=candidate,
            current_memories=(current,),
            base_memory_revision=5,
        )

        self.assertEqual(result.operation.kind, OwnerTruthMemoryChangeOperationKind.ADD)
        self.assertIsNone(result.operation.target_memory_id)

    def test_question_is_not_promoted_to_a_cross_session_fact(self) -> None:
        candidate = self._candidate(
            content=self._content(statement="我是哪所学校毕业的？", object_label="")
        )

        result = build_memory_changeset(
            candidate=candidate,
            current_memories=(),
            base_memory_revision=0,
        )

        self.assertEqual(result.operation.kind, OwnerTruthMemoryChangeOperationKind.NO_PERSONAL_FACT)

    def test_explicit_correction_targets_current_version(self) -> None:
        current = self._current(content=self._content(), source_id=str(uuid4()), version=3)
        candidate = self._candidate(
            content=self._content(statement="我现在更喜欢吃西湖醋鱼", object_label="西湖醋鱼"),
            review_mode="correction",
            correction_of_memory_version_id=current.memory_version_id,
        )

        result = build_memory_changeset(
            candidate=candidate,
            current_memories=(current,),
            base_memory_revision=8,
        )

        self.assertEqual(result.operation.kind, OwnerTruthMemoryChangeOperationKind.CORRECT)
        self.assertEqual(result.operation.target_memory_id, current.memory_id)

    def test_same_input_and_revision_produces_the_same_changeset_id(self) -> None:
        candidate = self._candidate(content=self._content())
        first = build_memory_changeset(candidate=candidate, current_memories=(), base_memory_revision=2)
        second = build_memory_changeset(candidate=candidate, current_memories=(), base_memory_revision=2)

        self.assertEqual(first.change_set_id, second.change_set_id)

    def test_owner_visible_proposal_binds_target_before_after_and_evidence_diff(self) -> None:
        current = self._current(content=self._content(), source_id=str(uuid4()), version=2)
        candidate = self._candidate(
            content=self._content(
                statement="我现在不喜欢吃东坡肉",
                polarity="negative",
                current="current",
                time=None,
            ),
            review_mode="correction",
            correction_of_memory_version_id=current.memory_version_id,
        )

        proposal = build_memory_changeset_proposal(
            candidate=candidate,
            current_memories=(current,),
            base_memory_revision=7,
        )

        operation = proposal.payload()["operations"][0]
        self.assertEqual(operation["targetMemoryVersionId"], current.memory_version_id)
        self.assertEqual(operation["targetMemoryVersion"], 2)
        self.assertEqual(operation["factDiff"]["before"], current.typed_content)
        self.assertEqual(
            operation["factDiff"]["candidate"]["statement"],
            "我现在不喜欢吃东坡肉",
        )
        self.assertEqual(operation["factDiff"]["evidence"]["beforeCount"], 1)
        self.assertEqual(operation["factDiff"]["evidence"]["afterCount"], 2)
        self.assertEqual(operation["anticipatedActivation"], "revise")

    def test_owner_visible_proposal_hash_changes_when_rendered_fact_diff_changes(self) -> None:
        current = self._current(content=self._content(), source_id=str(uuid4()), version=1)
        candidate = self._candidate(content=self._content())
        first = build_memory_changeset_proposal(
            candidate=candidate,
            current_memories=(current,),
            base_memory_revision=2,
        )
        changed_current = OwnerTruthCurrentFormalMemory(
            memory_id=current.memory_id,
            memory_version_id=current.memory_version_id,
            vault_id=current.vault_id,
            owner_subject_id=current.owner_subject_id,
            version_number=current.version_number,
            memory_kind=current.memory_kind,
            content_schema_version=current.content_schema_version,
            content=self._content(statement="我在杭州时喜欢吃片儿川"),
            evidence_refs=current.evidence_refs,
        )
        second = build_memory_changeset_proposal(
            candidate=candidate,
            current_memories=(changed_current,),
            base_memory_revision=2,
        )

        self.assertNotEqual(first.proposal_hash, second.proposal_hash)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
