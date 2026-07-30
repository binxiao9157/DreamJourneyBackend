from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
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
from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionAccessDenied
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_answer_citation import (
    InMemoryOwnerTruthAnswerCitationRepository,
    OwnerTruthAnswerCitationCommand,
    OwnerTruthAnswerCitationConflict,
    OwnerTruthAnswerCitationService,
)
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    OwnerTruthCandidateReviewService,
)
from app.services.owner_truth_context_citation_offline_evaluation import (
    ContextCitationWriteDisposition,
    OWNER_TRUTH_CONTEXT_CITATION_EVALUATION_METRIC_ALLOWLIST,
    OWNER_TRUTH_CONTEXT_CITATION_FORBIDDEN_ENGAGEMENT_METRICS,
    OWNER_TRUTH_CONTEXT_CITATION_OFFLINE_EVALUATION_SCHEMA_VERSION,
    OwnerTruthContextCitationEvaluationCase,
    OwnerTruthContextCitationEvaluationObservation,
    OwnerTruthContextCitationOfflineEvaluator,
)
from app.services.owner_truth_context_shadow_build import OwnerTruthContextShadowBuildService
from app.services.owner_truth_memory_projection import (
    InMemoryOwnerTruthMemoryProjectionRepository,
    OwnerTruthMemoryProjectionService,
)
from app.services.owner_truth_memory_search_projection import (
    InMemoryOwnerTruthMemorySearchDocumentProjectionRepository,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures/owner_truth/context_citation_offline_evaluation_v1.json"
)
OWNER = "subject-context-citation-evaluation-a"
VAULT = "vault-context-citation-evaluation-a"
OTHER_VAULT = "vault-context-citation-evaluation-b"


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
        self.search_projection_repository = (
            InMemoryOwnerTruthMemorySearchDocumentProjectionRepository(
                self.projection_repository
            )
        )
        self.answer_repository = InMemoryOwnerTruthAnswerCitationRepository()

    @contextmanager
    def request_unit_of_work(self, *, correlation_id: str, command_id: str):
        yield

    def owner_truth_candidate_review_repository(self):
        return self.review_repository

    def owner_truth_memory_projection_repository(self):
        return self.projection_repository

    def owner_truth_memory_search_document_projection_repository(self):
        return self.search_projection_repository

    def owner_truth_answer_citation_repository(self):
        return self.answer_repository


class _InvalidateProjectionBeforeSecondRead:
    """Invalidate one Source after Context build and before receipt persistence."""

    def __init__(self, delegate, *, invalidate) -> None:
        self._delegate = delegate
        self._invalidate = invalidate
        self._read_count = 0

    def read(self, *, context):
        self._read_count += 1
        if self._read_count == 2:
            self._invalidate()
        return self._delegate.read(context=context)

    def __getattr__(self, name):
        return getattr(self._delegate, name)


class OwnerTruthContextCitationOfflineEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = OwnerTruthContextCitationOfflineEvaluator()

    @staticmethod
    def _load_cases() -> tuple[OwnerTruthContextCitationEvaluationCase, ...]:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        if payload.get("schemaVersion") != OWNER_TRUTH_CONTEXT_CITATION_OFFLINE_EVALUATION_SCHEMA_VERSION:
            raise AssertionError("fixture schema version is not current")
        if payload.get("syntheticOnly") is not True:
            raise AssertionError("fixture must remain synthetic-only")
        return tuple(
            OwnerTruthContextCitationEvaluationCase.from_mapping(item)
            for item in payload.get("cases", ())
        )

    @staticmethod
    def _context(*, vault_id: str = VAULT, actor_id: str = OWNER) -> OwnerTruthCommandContext:
        return OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=OWNER,
            actor_subject_id=actor_id,
            policy_version="owner-truth-v1",
        )

    @staticmethod
    def _candidate(
        *,
        marker: str,
        sensitivity: SensitivityLevel = SensitivityLevel.STANDARD,
        perspective_type: PerspectiveType = PerspectiveType.FIRST_PERSON,
    ) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        content = {"summary": f"{marker}: synthetic owner memory"}
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=VAULT,
            owner_subject_id=OWNER,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=perspective_type,
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

    @staticmethod
    def _activate(
        *,
        store: _Store,
        context: OwnerTruthCommandContext,
        candidate: OwnerTruthCandidateSnapshot,
        command_id: str,
    ) -> None:
        store.review_repository.seed(candidate)
        OwnerTruthCandidateReviewService(store).decide_and_activate(
            command=OwnerTruthCandidateReviewCommand(
                command_id=command_id,
                candidate_id=candidate.candidate_id,
                expected_candidate_version=candidate.row_version,
                action=CandidateReviewAction.ACCEPT,
                corrected_value=None,
                corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                reason_code="ownerReviewed",
            ),
            context=context,
        )

    @staticmethod
    def _build(
        *,
        store: _Store,
        context: OwnerTruthCommandContext,
        payload: dict[str, object],
    ) -> dict[str, object]:
        return OwnerTruthContextShadowBuildService(store, enabled=True).build(
            context=context,
            payload=payload,
        )

    @staticmethod
    def _record(
        *,
        store: _Store,
        context: OwnerTruthCommandContext,
        command_id: str,
        payload: dict[str, object],
    ):
        return OwnerTruthAnswerCitationService(store, enabled=True).record(
            context=context,
            command=OwnerTruthAnswerCitationCommand(
                command_id=command_id,
                answer_text="synthetic answer must never enter value-free evidence",
            ),
            context_payload=payload,
        )

    def _observation_for(
        self,
        case: OwnerTruthContextCitationEvaluationCase,
    ) -> OwnerTruthContextCitationEvaluationObservation:
        store = _Store()
        owner_context = self._context()

        if case.scenario == "queryMatch":
            matched = self._candidate(marker="synthetic-private-orbit-a query-orbit-a")
            unmatched = self._candidate(marker="synthetic-private-other-memory")
            self._activate(store=store, context=owner_context, candidate=matched, command_id="offline-query-match-a")
            self._activate(store=store, context=owner_context, candidate=unmatched, command_id="offline-query-match-b")
            OwnerTruthMemoryProjectionService(store).rebuild(context=owner_context)
            store.search_projection_repository.rebuild(context=owner_context)
            payload = {
                "intent": "echo_chat",
                "query": "query-orbit-a",
                "selectionMode": "deterministicTextFallback",
            }
            return OwnerTruthContextCitationEvaluationObservation(
                disposition=ContextCitationWriteDisposition.RECORDED,
                context_build=self._build(store=store, context=owner_context, payload=payload),
                answer_result=self._record(
                    store=store,
                    context=owner_context,
                    command_id="offline-query-match-record",
                    payload=payload,
                ),
            )

        if case.scenario == "queryNoMatch":
            candidate = self._candidate(marker="synthetic-private-orbit-b")
            self._activate(store=store, context=owner_context, candidate=candidate, command_id="offline-query-no-match")
            OwnerTruthMemoryProjectionService(store).rebuild(context=owner_context)
            store.search_projection_repository.rebuild(context=owner_context)
            payload = {
                "intent": "echo_chat",
                "query": "query-no-match-token",
                "selectionMode": "deterministicTextFallback",
            }
            return OwnerTruthContextCitationEvaluationObservation(
                disposition=ContextCitationWriteDisposition.RECORDED,
                context_build=self._build(store=store, context=owner_context, payload=payload),
                answer_result=self._record(
                    store=store,
                    context=owner_context,
                    command_id="offline-query-no-match-record",
                    payload=payload,
                ),
            )

        if case.scenario == "projectionUnavailable":
            candidate = self._candidate(marker="synthetic-private-projection-unavailable")
            self._activate(store=store, context=owner_context, candidate=candidate, command_id="offline-projection-unavailable")
            payload = {"intent": "echo_chat"}
            return OwnerTruthContextCitationEvaluationObservation(
                disposition=ContextCitationWriteDisposition.RECORDED,
                context_build=self._build(store=store, context=owner_context, payload=payload),
                answer_result=self._record(
                    store=store,
                    context=owner_context,
                    command_id="offline-projection-unavailable-record",
                    payload=payload,
                ),
            )

        if case.scenario == "filteredEligibility":
            standard = self._candidate(marker="synthetic-private-standard")
            sensitive = self._candidate(
                marker="synthetic-private-sensitive",
                sensitivity=SensitivityLevel.SENSITIVE,
            )
            ai_only = self._candidate(
                marker="synthetic-private-ai-only",
                perspective_type=PerspectiveType.INFERRED,
            )
            for index, candidate in enumerate((standard, sensitive, ai_only), start=1):
                self._activate(
                    store=store,
                    context=owner_context,
                    candidate=candidate,
                    command_id=f"offline-filtered-{index}",
                )
            OwnerTruthMemoryProjectionService(store).rebuild(context=owner_context)
            payload = {"intent": "echo_chat"}
            return OwnerTruthContextCitationEvaluationObservation(
                disposition=ContextCitationWriteDisposition.RECORDED,
                context_build=self._build(store=store, context=owner_context, payload=payload),
                answer_result=self._record(
                    store=store,
                    context=owner_context,
                    command_id="offline-filtered-record",
                    payload=payload,
                ),
            )

        if case.scenario == "crossOwnerDenied":
            candidate = self._candidate(marker="synthetic-private-cross-owner")
            self._activate(store=store, context=owner_context, candidate=candidate, command_id="offline-cross-owner")
            OwnerTruthMemoryProjectionService(store).rebuild(context=owner_context)
            non_owner = self._context(actor_id="different-subject")
            with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
                self._build(store=store, context=non_owner, payload={"intent": "echo_chat"})
            with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
                self._record(
                    store=store,
                    context=non_owner,
                    command_id="offline-cross-owner-record",
                    payload={"intent": "echo_chat"},
                )
            self.assertEqual(store.answer_repository.snapshot()["records"], [])
            return OwnerTruthContextCitationEvaluationObservation(
                disposition=ContextCitationWriteDisposition.ACCESS_DENIED,
                context_build=None,
            )

        if case.scenario == "crossVaultDenied":
            candidate = self._candidate(marker="synthetic-private-cross-vault")
            self._activate(store=store, context=owner_context, candidate=candidate, command_id="offline-cross-vault")
            OwnerTruthMemoryProjectionService(store).rebuild(context=owner_context)
            other_vault_context = self._context(vault_id=OTHER_VAULT)
            with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
                self._build(
                    store=store,
                    context=other_vault_context,
                    payload={"intent": "echo_chat"},
                )
            with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
                self._record(
                    store=store,
                    context=other_vault_context,
                    command_id="offline-cross-vault-record",
                    payload={"intent": "echo_chat"},
                )
            self.assertEqual(store.answer_repository.snapshot()["records"], [])
            return OwnerTruthContextCitationEvaluationObservation(
                disposition=ContextCitationWriteDisposition.ACCESS_DENIED,
                context_build=None,
            )

        if case.scenario == "sourceInvalidated":
            candidate = self._candidate(marker="synthetic-private-invalidated-source")
            self._activate(store=store, context=owner_context, candidate=candidate, command_id="offline-invalidated")
            OwnerTruthMemoryProjectionService(store).rebuild(context=owner_context)
            payload = {"intent": "echo_chat"}
            context_build = self._build(store=store, context=owner_context, payload=payload)
            original_repository = store.projection_repository
            store.projection_repository = _InvalidateProjectionBeforeSecondRead(
                original_repository,
                invalidate=lambda: store.review_repository._source_states.__setitem__(
                    (VAULT, candidate.source_id),
                    "deleted",
                ),
            )
            with self.assertRaises(OwnerTruthAnswerCitationConflict):
                self._record(
                    store=store,
                    context=owner_context,
                    command_id="offline-invalidated-record",
                    payload=payload,
                )
            self.assertEqual(store.answer_repository.snapshot()["records"], [])
            return OwnerTruthContextCitationEvaluationObservation(
                disposition=ContextCitationWriteDisposition.REJECTED,
                context_build=context_build,
            )

        self.fail(f"unsupported Context/Citation offline scenario: {case.scenario}")

    def test_versioned_synthetic_negative_corpus_passes_against_real_context_and_receipt_paths(self) -> None:
        cases = self._load_cases()
        self.assertEqual(len(cases), 7)
        self.assertEqual(len({case.case_id for case in cases}), len(cases))
        rows = []
        for case in cases:
            with self.subTest(case_id=case.case_id):
                observation = self._observation_for(case)
                result = self.evaluator.evaluate_case(case=case, observation=observation)
                self.assertTrue(result.passed, result.value_free_summary())
                self.assertEqual(result.metrics["privateInputLeakageCount"], 0)
                self.assertEqual(result.metrics["crossScopeCitationCount"], 0)
                self.assertEqual(result.metrics["legacyReadObservedCount"], 0)
                rows.append((case, observation))
        report = self.evaluator.evaluate(rows)
        self.assertTrue(report.passed, report.value_free_summary())
        rendered = json.dumps(report.value_free_summary(), ensure_ascii=False, sort_keys=True)
        for case in cases:
            for marker in case.private_markers:
                self.assertNotIn(marker, rendered)

    def test_evaluator_rejects_leakage_or_legacy_read_even_when_count_expectations_match(self) -> None:
        case = next(case for case in self._load_cases() if case.scenario == "queryMatch")
        observation = self._observation_for(case)
        assert observation.context_build is not None
        corrupted = deepcopy(dict(observation.context_build))
        corrupted["legacyContextRead"] = True
        corrupted["selectedContext"][0]["debugMarker"] = case.private_markers[0]
        result = self.evaluator.evaluate_case(
            case=case,
            observation=OwnerTruthContextCitationEvaluationObservation(
                disposition=ContextCitationWriteDisposition.RECORDED,
                context_build=corrupted,
                answer_result=observation.answer_result,
            ),
        )
        self.assertFalse(result.passed)
        self.assertIn("legacyReadObserved", result.violation_codes)
        self.assertIn("privateInputLeakage", result.violation_codes)

    def test_metric_and_fixture_boundaries_are_non_engagement_and_value_free(self) -> None:
        self.assertEqual(
            OWNER_TRUTH_CONTEXT_CITATION_EVALUATION_METRIC_ALLOWLIST
            & OWNER_TRUTH_CONTEXT_CITATION_FORBIDDEN_ENGAGEMENT_METRICS,
            frozenset(),
        )
        cases = self._load_cases()
        rendered = json.dumps([case.case_id for case in cases], ensure_ascii=False, sort_keys=True)
        for case in cases:
            for marker in case.private_markers:
                self.assertNotIn(marker, rendered)


if __name__ == "__main__":
    unittest.main()
