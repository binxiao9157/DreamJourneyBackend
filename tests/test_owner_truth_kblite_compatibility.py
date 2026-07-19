from __future__ import annotations

from contextlib import contextmanager
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
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)
from app.services.owner_truth_kblite_compatibility import (
    OwnerTruthKBLiteCompatibilityReadService,
    compatibility_read_envelope,
    compatibility_summary,
)
from app.services.owner_truth_memory_projection import (
    InMemoryOwnerTruthMemoryProjectionRepository,
    OwnerTruthMemoryProjectionService,
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Store:
    def __init__(self) -> None:
        self.review_repository = InMemoryOwnerTruthCandidateReviewRepository()
        self.projection_repository = InMemoryOwnerTruthMemoryProjectionRepository(
            self.review_repository
        )

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository


class OwnerTruthKBLiteCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-kblite-compatibility"
        self.owner_id = "subject-kblite-compatibility"
        self.context = OwnerTruthCommandContext(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            actor_subject_id=self.owner_id,
        )
        self.store = _Store()
        self.review_service = OwnerTruthCandidateReviewService(self.store)
        self.projection_service = OwnerTruthMemoryProjectionService(self.store)
        self.adapter = OwnerTruthKBLiteCompatibilityReadService(self.store, enabled=True)

    def _candidate(
        self,
        *,
        kind: MemoryKind,
        content: dict[str, str],
        sensitivity: SensitivityLevel = SensitivityLevel.STANDARD,
    ) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=self.vault_id,
            owner_subject_id=self.owner_id,
            source_id=source_id,
            memory_kind=kind,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=sensitivity,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [
                    {
                        "sourceId": source_id,
                        "sourceVersion": 1,
                        "span": {"start": 0, "end": 10},
                    }
                ],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    def _activate(self, candidate: OwnerTruthCandidateSnapshot, *, command_id: str) -> None:
        self.store.review_repository.seed(candidate)
        self.review_service.decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=command_id,
                candidate_id=candidate.candidate_id,
                expected_candidate_version=1,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=self.context,
        )

    def test_disabled_adapter_does_not_read_projection_or_return_legacy_data(self) -> None:
        disabled = OwnerTruthKBLiteCompatibilityReadService(self.store, enabled=False)

        result = disabled.read(context=self.context)

        self.assertEqual(result["state"], "disabled")
        self.assertEqual(result["graph"], {"people": [], "places": [], "events": [], "facts": []})
        self.assertEqual(result["factCount"], 0)
        self.assertEqual(result["filteredEntries"], [])

    def test_ready_projection_maps_only_explicit_knowledge_claims_to_read_only_facts(self) -> None:
        knowledge = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "父亲总会先修好自行车，再带我去公园"},
        )
        experience = self._candidate(
            kind=MemoryKind.EXPERIENCE,
            content={"summary": "小时候在院子里听雨"},
        )
        emotion = self._candidate(
            kind=MemoryKind.EMOTION,
            content={"label": "怀念"},
        )
        self._activate(knowledge, command_id="kblite-compatibility-knowledge-001")
        self._activate(experience, command_id="kblite-compatibility-experience-001")
        self._activate(emotion, command_id="kblite-compatibility-emotion-001")
        self.projection_service.rebuild(context=self.context)

        result = self.adapter.read(context=self.context)

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["factCount"], 1)
        self.assertEqual(result["graph"]["people"], [])
        self.assertEqual(result["graph"]["places"], [])
        self.assertEqual(result["graph"]["events"], [])
        fact = result["graph"]["facts"][0]
        self.assertEqual(fact["statement"], knowledge.content["claim"])
        self.assertEqual(fact["confidence"], "confirmed")
        self.assertEqual(fact["evidenceStatus"], "confirmed")
        self.assertEqual(fact["citation"]["sourceId"], knowledge.source_id)
        self.assertEqual(fact["citation"]["memoryVersion"], 1)
        self.assertNotIn("decisionReceiptId", str(fact))
        self.assertNotIn("rationale", str(fact))

        filtered = {item["memoryKind"]: item["reason"] for item in result["filteredEntries"]}
        self.assertEqual(filtered["experience"], "memory_kind_not_compatibility_fact")
        self.assertEqual(filtered["emotion"], "memory_kind_not_compatibility_fact")
        self.assertNotIn(experience.content["summary"], str(result["filteredEntries"]))
        self.assertNotIn(emotion.content["label"], str(result["filteredEntries"]))

    def test_missing_or_stale_projection_fails_closed_without_compatibility_facts(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "外婆喜欢在晚饭后讲院子里的桂花"},
        )
        self._activate(candidate, command_id="kblite-compatibility-stale-001")

        missing = self.adapter.read(context=self.context)
        self.assertEqual(missing["state"], "rebuilding")
        self.assertEqual(missing["graph"]["facts"], [])

        self.projection_service.rebuild(context=self.context)
        self._activate(
            self._candidate(
                kind=MemoryKind.EXPERIENCE,
                content={"summary": "夏天一起去河边散步"},
            ),
            command_id="kblite-compatibility-stale-002",
        )
        stale = self.adapter.read(context=self.context)
        self.assertEqual(stale["state"], "rebuilding")
        self.assertEqual(stale["graph"]["facts"], [])

    def test_summary_keeps_confirmed_claim_text_out_of_qa_response(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "只允许 Owner 使用这条确认事实"},
        )
        self._activate(candidate, command_id="kblite-compatibility-summary-001")
        self.projection_service.rebuild(context=self.context)

        summary = compatibility_summary(self.adapter.read(context=self.context))

        self.assertEqual(summary["state"], "ready")
        self.assertEqual(summary["factCount"], 1)
        self.assertEqual(summary["facts"][0]["citation"]["sourceId"], candidate.source_id)
        self.assertNotIn(candidate.content["claim"], str(summary))
        self.assertNotIn("statement", str(summary))

    def test_read_envelope_exposes_only_standard_confirmed_facts_with_cache_integrity(self) -> None:
        standard = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "外婆总会把桂花晒干后装进布袋。"},
        )
        sensitive = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "这条敏感事实不得进入兼容缓存。"},
            sensitivity=SensitivityLevel.SENSITIVE,
        )
        self._activate(standard, command_id="kblite-read-envelope-standard-001")
        self._activate(sensitive, command_id="kblite-read-envelope-sensitive-001")
        rebuilt = self.projection_service.rebuild(context=self.context)

        envelope = compatibility_read_envelope(self.adapter.read(context=self.context))

        self.assertEqual(
            envelope["schemaVersion"],
            "owner-truth-kblite-read-envelope-v1",
        )
        self.assertEqual(envelope["projectionSource"], "v4")
        self.assertEqual(envelope["state"], "ready")
        self.assertEqual(envelope["cacheDisposition"], "replace")
        self.assertEqual(envelope["vaultId"], self.vault_id)
        self.assertEqual(envelope["ownerSubjectId"], self.owner_id)
        self.assertEqual(envelope["authorityEpoch"], 0)
        self.assertEqual(envelope["projectionCheckpoint"], rebuilt.snapshot["checkpoint"])
        self.assertTrue(envelope["contentHash"])
        self.assertEqual(len(envelope["graph"]["facts"]), 1)
        self.assertEqual(envelope["graph"]["facts"][0]["statement"], standard.content["claim"])
        self.assertNotIn(sensitive.content["claim"], str(envelope))
        self.assertIn(
            "sensitivity_not_cacheable",
            [item["reason"] for item in envelope["filteredEntries"]],
        )

    def test_nonready_read_envelope_forces_a_client_cache_discard(self) -> None:
        candidate = self._candidate(
            kind=MemoryKind.KNOWLEDGE,
            content={"claim": "尚未重建的确认事实不得从旧缓存读取。"},
        )
        self._activate(candidate, command_id="kblite-read-envelope-rebuilding-001")

        envelope = compatibility_read_envelope(self.adapter.read(context=self.context))

        self.assertEqual(envelope["state"], "rebuilding")
        self.assertEqual(envelope["cacheDisposition"], "discard")
        self.assertIsNone(envelope["contentHash"])
        self.assertEqual(envelope["graph"], {"people": [], "places": [], "events": [], "facts": []})


if __name__ == "__main__":
    unittest.main()
