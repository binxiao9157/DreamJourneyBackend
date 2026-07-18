from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import unittest
from uuid import uuid4

from app.async_effects.consumer_repository import InMemoryAsyncEffectConsumerRepository
from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.target_admission import InMemoryOwnerTruthSourceTargetAdmissionRepository
from app.domain.owner_truth.candidate_extraction import (
    CandidateEvidenceSpan,
    CandidateProposal,
    CandidateReviewMode,
    ExtractionResultStatus,
    OwnerTruthCandidateExtractionConflict,
    SyntheticCandidateExtractionCommand,
)
from app.domain.owner_truth.contracts import (
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.services.owner_truth_candidate_extraction import (
    InMemoryOwnerTruthCandidateExtractionRepository,
    OwnerTruthCandidateExtractionService,
)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class _CandidateExtractionStore:
    def __init__(self, *, vault_id: str, owner_subject_id: str, source_id: str) -> None:
        self.admission = InMemoryOwnerTruthSourceTargetAdmissionRepository()
        self.admission.seed_vault(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=2,
            status="active",
        )
        self.admission.seed_source(
            vault_id=vault_id,
            source_id=source_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=2,
            source_version=1,
            state="active",
        )
        self.candidates = InMemoryOwnerTruthCandidateExtractionRepository()
        self.consumer = InMemoryAsyncEffectConsumerRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        del correlation_id, command_id
        yield

    def owner_truth_source_target_admission_repository(self):
        return self.admission

    def owner_truth_candidate_extraction_repository(self):
        return self.candidates

    def async_effect_consumer_repository(self):
        return self.consumer


class OwnerTruthCandidateExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault_id = "vault-candidate-extraction"
        self.owner_subject_id = "owner-candidate-extraction"
        self.source_id = str(uuid4())
        self.source_text = "我小时候常在河边听外公讲故事，也记得那条河很安静。"
        self.intent = AsyncEffectIntent(
            operation_type="ownerTruth.source.created",
            target=AsyncEffectTarget(
                owner_subject_id=self.owner_subject_id,
                vault_id=self.vault_id,
                resource_type="source",
                resource_id=self.source_id,
                resource_version=1,
                purpose="candidateExtraction",
                authority_epoch=2,
            ),
            payload_hash=digest("candidate-extraction-source-command"),
        )
        self.store = _CandidateExtractionStore(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            source_id=self.source_id,
        )
        self.service = OwnerTruthCandidateExtractionService(self.store)

    def _proposal(
        self,
        *,
        kind: MemoryKind,
        content: dict[str, str],
        start: int,
        end: int,
    ) -> CandidateProposal:
        return CandidateProposal(
            memory_kind=kind,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            content=content,
            evidence_span=CandidateEvidenceSpan(start=start, end=end),
            confidence=0.72,
            review_mode=CandidateReviewMode.BATCH,
        )

    def _command(
        self,
        *,
        status: ExtractionResultStatus = ExtractionResultStatus.SUCCEEDED,
        proposals: tuple[CandidateProposal, ...] | None = None,
        failure_code: str | None = None,
        retryable: bool = False,
    ) -> SyntheticCandidateExtractionCommand:
        if proposals is None:
            proposals = (
                self._proposal(
                    kind=MemoryKind.EXPERIENCE,
                    content={"summary": "童年常在河边听外公讲故事。"},
                    start=0,
                    end=16,
                ),
                self._proposal(
                    kind=MemoryKind.KNOWLEDGE,
                    content={"claim": "外公常在河边讲故事。"},
                    start=7,
                    end=16,
                ),
            )
        return SyntheticCandidateExtractionCommand(
            intent=self.intent,
            extractor_id="deterministicFake",
            model_id="fixture-v1",
            prompt_version="candidate-prompt-v1",
            policy_version="owner-truth-v1",
            source_content_hash=digest(self.source_text),
            status=status,
            proposals=proposals,
            failure_code=failure_code,
            retryable=retryable,
        )

    def test_successful_extraction_creates_atomic_pending_candidates_and_replays(self) -> None:
        command = self._command()

        created = self.service.record(command)
        replayed = self.service.record(command)

        self.assertEqual(created.outcome, "created")
        self.assertEqual(replayed.outcome, "deduplicated")
        self.assertEqual(created.extraction_id, replayed.extraction_id)
        self.assertEqual(len(created.candidate_ids), 2)
        self.assertEqual(replayed.candidate_ids, created.candidate_ids)
        snapshot = self.store.candidates.snapshot()
        self.assertEqual(len(snapshot["extractions"]), 1)
        self.assertEqual(len(snapshot["candidates"]), 2)
        for candidate in snapshot["candidates"].values():
            self.assertEqual(candidate["decisionStatus"], "pending")
            self.assertEqual(candidate["sourceId"], self.source_id)
            self.assertEqual(candidate["sourceVersion"], 1)
            self.assertEqual(candidate["payload"]["evidenceRefs"][0]["sourceId"], self.source_id)
            self.assertEqual(candidate["payload"]["evidenceRefs"][0]["sourceVersion"], 1)
        self.assertEqual(created.consumer.outcome, "accepted")
        self.assertEqual(replayed.consumer.outcome, "deduplicated")

    def test_failed_retryable_extraction_records_no_candidate(self) -> None:
        command = self._command(
            status=ExtractionResultStatus.FAILED,
            proposals=(),
            failure_code="providerUnavailable",
            retryable=True,
        )

        result = self.service.record(command)

        self.assertEqual(result.outcome, "created")
        self.assertEqual(result.status, ExtractionResultStatus.FAILED)
        self.assertEqual(result.candidate_ids, ())
        snapshot = self.store.candidates.snapshot()
        extraction = snapshot["extractions"][result.extraction_id]
        self.assertEqual(extraction["status"], "failed")
        self.assertTrue(extraction["payload"]["retryable"])
        self.assertEqual(snapshot["candidates"], {})

    def test_inactive_source_records_typed_block_without_an_extraction(self) -> None:
        self.store.admission.seed_source(
            vault_id=self.vault_id,
            source_id=self.source_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=2,
            source_version=1,
            state="deleted",
        )

        result = self.service.record(self._command())

        self.assertEqual(result.outcome, "blocked")
        self.assertEqual(result.reason_code, "sourceInactive")
        self.assertIsNone(result.extraction_id)
        self.assertEqual(self.store.candidates.snapshot()["extractions"], {})
        self.assertEqual(result.consumer.business_outcome, "blocked")

    def test_same_source_processor_cannot_silently_replace_a_persisted_result(self) -> None:
        self.service.record(self._command())
        replacement = self._command(
            proposals=(
                self._proposal(
                    kind=MemoryKind.EMOTION,
                    content={"label": "怀念"},
                    start=0,
                    end=8,
                ),
            ),
        )

        with self.assertRaises(OwnerTruthCandidateExtractionConflict):
            self.service.record(replacement)

    def test_candidate_requires_a_nonempty_source_span(self) -> None:
        with self.assertRaises(ValueError):
            CandidateEvidenceSpan(start=9, end=9)


if __name__ == "__main__":
    unittest.main()
