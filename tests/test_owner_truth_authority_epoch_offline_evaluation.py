"""Synthetic G0 corpus for Owner Truth authority-epoch invalidation.

The scenarios exercise real in-memory Owner Truth and async-effect admission
components.  The report deliberately retains only synthetic case IDs, counters
and reason codes; it is not a production cutover or retrieval-quality claim.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import unittest
from uuid import uuid4

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.async_effects.target_admission import InMemoryOwnerTruthSourceTargetAdmissionRepository
from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewAccessDenied,
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
from app.services.in_memory_store import InMemoryStore
from app.services.owner_truth_answer_citation import (
    OwnerTruthAnswerCitationCommand,
    OwnerTruthAnswerCitationConflict,
    OwnerTruthAnswerCitationService,
)
from app.services.owner_truth_candidate_review import OwnerTruthCandidateReviewService
from app.services.owner_truth_context_shadow_build import OwnerTruthContextShadowBuildService
from app.services.owner_truth_correction_request import (
    OwnerTruthCorrectionRequestCommand,
    OwnerTruthCorrectionRequestService,
    OwnerTruthCorrectionResolutionCommand,
    OwnerTruthCorrectionResolutionStale,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


SCHEMA_VERSION = "owner-truth-authority-epoch-offline-evaluation-v1"
FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures/owner_truth/authority_epoch_offline_evaluation_v1.json"
)
OWNER = "subject-authority-epoch-evaluation-a"
VAULT = "vault-authority-epoch-evaluation-a"
OTHER_OWNER = "subject-authority-epoch-evaluation-b"
OTHER_VAULT = "vault-authority-epoch-evaluation-b"
METRIC_ALLOWLIST = frozenset(
    {
        "asyncCallbackBlockedCount",
        "candidateWriteRejectedCount",
        "projectionFallbackCount",
        "contextRebuildingCount",
        "citationWriteRejectedCount",
        "correctionWriteRejectedCount",
        "crossScopeDeniedCount",
        "legacyReadObservedCount",
        "privateInputLeakageCount",
        "policyViolationCount",
    }
)
FORBIDDEN_ENGAGEMENT_METRICS = frozenset(
    {"conversationDuration", "messageCount", "clickThroughRate", "activeDays"}
)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _empty_metrics() -> dict[str, int]:
    return {key: 0 for key in METRIC_ALLOWLIST}


@dataclass(frozen=True)
class _Case:
    case_id: str
    scenario: str
    private_markers: tuple[str, ...]
    expected_disposition: str
    expected_reason_codes: tuple[str, ...]
    expected_nonzero_metrics: dict[str, int]

    @classmethod
    def from_mapping(cls, value: object) -> "_Case":
        if not isinstance(value, dict):
            raise AssertionError("authority epoch fixture case must be an object")
        expected = value.get("expected")
        if not isinstance(expected, dict):
            raise AssertionError("authority epoch fixture expected must be an object")
        expected_metrics = expected.get("nonzeroMetrics")
        if not isinstance(expected_metrics, dict):
            raise AssertionError("authority epoch expected metrics must be an object")
        return cls(
            case_id=str(value.get("caseId") or ""),
            scenario=str(value.get("scenario") or ""),
            private_markers=tuple(str(item) for item in value.get("privateMarkers") or ()),
            expected_disposition=str(expected.get("disposition") or ""),
            expected_reason_codes=tuple(str(item) for item in expected.get("reasonCodes") or ()),
            expected_nonzero_metrics={str(key): int(item) for key, item in expected_metrics.items()},
        )


@dataclass(frozen=True)
class _Observation:
    disposition: str
    reason_codes: tuple[str, ...]
    metrics: dict[str, int]

    def __post_init__(self) -> None:
        if set(self.metrics) != METRIC_ALLOWLIST:
            raise AssertionError("authority epoch observation metrics must use the allowlist")
        if set(self.metrics) & FORBIDDEN_ENGAGEMENT_METRICS:
            raise AssertionError("authority epoch observation cannot emit engagement metrics")
        if any(not isinstance(value, int) or value < 0 for value in self.metrics.values()):
            raise AssertionError("authority epoch observation metrics must be non-negative integers")


@dataclass(frozen=True)
class _Result:
    case_id: str
    metrics: dict[str, int]
    violation_codes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.violation_codes

    def value_free_summary(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "metrics": dict(self.metrics),
            "passed": self.passed,
            "schemaVersion": SCHEMA_VERSION,
            "violationCodes": list(self.violation_codes),
        }


class _Evaluator:
    def evaluate(self, *, case: _Case, observation: _Observation) -> _Result:
        violations: list[str] = []
        if observation.disposition != case.expected_disposition:
            violations.append("dispositionMismatch")
        if tuple(sorted(observation.reason_codes)) != tuple(sorted(case.expected_reason_codes)):
            violations.append("reasonCodesMismatch")
        for key, expected in case.expected_nonzero_metrics.items():
            if observation.metrics.get(key) != expected:
                violations.append(f"metricMismatch:{key}")
        for key in ("legacyReadObservedCount", "privateInputLeakageCount", "policyViolationCount"):
            if observation.metrics[key] != 0:
                violations.append(f"nonzeroForbiddenMetric:{key}")
        return _Result(
            case_id=case.case_id,
            metrics=deepcopy(observation.metrics),
            violation_codes=tuple(violations),
        )

    @staticmethod
    def report(results: list[_Result]) -> dict[str, object]:
        totals = _empty_metrics()
        for result in results:
            for key, value in result.metrics.items():
                totals[key] += value
        return {
            "caseCount": len(results),
            "failedCaseIds": [result.case_id for result in results if not result.passed],
            "metricTotals": totals,
            "passed": all(result.passed for result in results),
            "schemaVersion": SCHEMA_VERSION,
            "syntheticOnly": True,
        }


class _BumpEpochOnSecondProjectionRead:
    """Simulate an authority change after a Context build, before receipt write."""

    def __init__(self, delegate, *, bump_epoch) -> None:
        self._delegate = delegate
        self._bump_epoch = bump_epoch
        self._read_count = 0

    def read(self, *, context):
        self._read_count += 1
        if self._read_count == 2:
            self._bump_epoch()
        return self._delegate.read(context=context)

    def __getattr__(self, name):
        return getattr(self._delegate, name)


class OwnerTruthAuthorityEpochOfflineEvaluationTests(unittest.TestCase):
    @staticmethod
    def _load_cases() -> tuple[_Case, ...]:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        if payload.get("schemaVersion") != SCHEMA_VERSION:
            raise AssertionError("authority epoch fixture schema version is not current")
        if payload.get("syntheticOnly") is not True:
            raise AssertionError("authority epoch fixture must remain synthetic-only")
        return tuple(_Case.from_mapping(item) for item in payload.get("cases") or ())

    @staticmethod
    def _context(*, vault_id: str = VAULT, owner_id: str = OWNER, actor_id: str = OWNER):
        return OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_id,
            actor_subject_id=actor_id,
        )

    @staticmethod
    def _candidate(*, marker: str) -> OwnerTruthCandidateSnapshot:
        source_id = str(uuid4())
        content = {"summary": f"{marker}: synthetic owner truth memory"}
        return OwnerTruthCandidateSnapshot(
            candidate_id=str(uuid4()),
            vault_id=VAULT,
            owner_subject_id=OWNER,
            source_id=source_id,
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=PerspectiveType.FIRST_PERSON,
            epistemic_status=EpistemicStatus.RECALLED,
            sensitivity=SensitivityLevel.STANDARD,
            decision=CandidateDecision.PENDING,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            authority_epoch=0,
            row_version=1,
            content_hash=_hash(content),
            content_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            payload={
                "content": content,
                "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION,
                "evidenceRefs": [{"sourceId": source_id, "sourceVersion": 1}],
                "reviewMode": "single",
                "schemaVersion": "owner-truth-candidate-proposal-v1",
            },
        )

    @staticmethod
    def _activate(*, store: InMemoryStore, candidate: OwnerTruthCandidateSnapshot, command_id: str):
        context = OwnerTruthAuthorityEpochOfflineEvaluationTests._context()
        review_repository = store.owner_truth_candidate_review_repository()
        review_repository.seed(candidate)
        return OwnerTruthCandidateReviewService(store).decide_and_activate(
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
    def _bump_epoch(store: InMemoryStore) -> None:
        repository = store.owner_truth_candidate_review_repository()
        repository._vault_states[VAULT] = (OWNER, "active", 1)

    @staticmethod
    def _source_intent(*, source_id: str, owner_id: str = OWNER, vault_id: str = VAULT):
        return AsyncEffectIntent(
            operation_type="ownerTruth.source.created",
            target=AsyncEffectTarget(
                owner_subject_id=owner_id,
                vault_id=vault_id,
                resource_type="source",
                resource_id=source_id,
                resource_version=1,
                purpose="candidateExtraction",
                authority_epoch=0,
            ),
            payload_hash=_hash("synthetic-authority-epoch-async-callback"),
        )

    def _observation_for(self, case: _Case) -> _Observation:
        if case.scenario == "staleSourceAsyncCallback":
            repository = InMemoryOwnerTruthSourceTargetAdmissionRepository()
            source_id = str(uuid4())
            repository.seed_vault(
                vault_id=VAULT,
                owner_subject_id=OWNER,
                authority_epoch=0,
                status="active",
            )
            repository.seed_source(
                vault_id=VAULT,
                source_id=source_id,
                owner_subject_id=OWNER,
                authority_epoch=0,
                source_version=1,
                state="active",
            )
            repository.seed_vault(
                vault_id=VAULT,
                owner_subject_id=OWNER,
                authority_epoch=1,
                status="active",
            )
            result = repository.admit_owner_truth_source(self._source_intent(source_id=source_id))
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_code, "authorityEpochChanged")
            metrics = _empty_metrics()
            metrics["asyncCallbackBlockedCount"] = 1
            return _Observation("blocked", (result.reason_code,), metrics)

        if case.scenario == "staleCandidateCommand":
            store = InMemoryStore()
            candidate = self._candidate(marker=case.private_markers[0])
            repository = store.owner_truth_candidate_review_repository()
            repository.seed(candidate)
            self._bump_epoch(store)
            with self.assertRaises(OwnerTruthCandidateReviewAccessDenied):
                OwnerTruthCandidateReviewService(store).decide(
                    command=OwnerTruthCandidateReviewCommand(
                        command_id="authority-epoch-stale-candidate",
                        candidate_id=candidate.candidate_id,
                        expected_candidate_version=candidate.row_version,
                        action=CandidateReviewAction.ACCEPT,
                        corrected_value=None,
                        corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                        reason_code="ownerReviewed",
                    ),
                    context=self._context(),
                )
            snapshot = repository.snapshot()
            self.assertEqual(snapshot["candidates"][candidate.candidate_id]["decision"], "pending")
            self.assertEqual(snapshot["receipts"], {})
            metrics = _empty_metrics()
            metrics["candidateWriteRejectedCount"] = 1
            return _Observation("rejected", ("candidateAuthorityEpochStale",), metrics)

        if case.scenario == "staleProjectionContextCache":
            store = InMemoryStore()
            candidate = self._candidate(marker=case.private_markers[0])
            self._activate(store=store, candidate=candidate, command_id="authority-epoch-projection")
            projection_service = OwnerTruthMemoryProjectionService(store)
            baseline = projection_service.rebuild(context=self._context()).snapshot
            self.assertEqual(baseline["state"], "ready")
            self._bump_epoch(store)
            projection = projection_service.read(context=self._context())
            context_build = OwnerTruthContextShadowBuildService(store, enabled=True).build(
                context=self._context(),
                payload={"intent": "echo_chat"},
            )
            self.assertEqual(projection["state"], "rebuilding")
            self.assertEqual(projection["authorityEpoch"], 1)
            self.assertEqual(projection["entries"], [])
            self.assertEqual(context_build["authority"]["state"], "rebuilding")
            self.assertEqual(context_build["selectedContext"], [])
            self.assertEqual(
                context_build["fallbacks"],
                ["owner_truth_context_unavailable_no_personal_memory"],
            )
            metrics = _empty_metrics()
            metrics["projectionFallbackCount"] = 1
            metrics["contextRebuildingCount"] = 1
            return _Observation("rebuilding", ("projectionAuthorityEpochChanged",), metrics)

        if case.scenario == "staleContextCitationReceipt":
            store = InMemoryStore()
            candidate = self._candidate(marker=case.private_markers[0])
            self._activate(store=store, candidate=candidate, command_id="authority-epoch-citation")
            OwnerTruthMemoryProjectionService(store).rebuild(context=self._context())
            original_repository = store.owner_truth_memory_projection_repository()
            store._owner_truth_memory_projection_repository = _BumpEpochOnSecondProjectionRead(
                original_repository,
                bump_epoch=lambda: self._bump_epoch(store),
            )
            with self.assertRaises(OwnerTruthAnswerCitationConflict):
                OwnerTruthAnswerCitationService(store, enabled=True).record(
                    context=self._context(),
                    command=OwnerTruthAnswerCitationCommand(
                        command_id="authority-epoch-citation-receipt",
                        answer_text="synthetic answer is never retained in the evaluation report",
                    ),
                    context_payload={"intent": "echo_chat"},
                )
            self.assertEqual(store.owner_truth_answer_citation_repository().snapshot()["records"], [])
            metrics = _empty_metrics()
            metrics["citationWriteRejectedCount"] = 1
            return _Observation("rejected", ("citationAuthorityEpochChanged",), metrics)

        if case.scenario == "staleCorrectionResolution":
            store = InMemoryStore()
            candidate = self._candidate(marker=case.private_markers[0])
            self._activate(store=store, candidate=candidate, command_id="authority-epoch-correction")
            OwnerTruthMemoryProjectionService(store).rebuild(context=self._context())
            answer = OwnerTruthAnswerCitationService(store, enabled=True).record(
                context=self._context(),
                command=OwnerTruthAnswerCitationCommand(
                    command_id="authority-epoch-correction-answer",
                    answer_text="synthetic answer is never retained in the evaluation report",
                ),
                context_payload={"intent": "echo_chat"},
            )
            citation = answer.citations[0]
            fields = citation["citation"]
            correction_service = OwnerTruthCorrectionRequestService(store, enabled=True)
            request = correction_service.request(
                context=self._context(),
                command=OwnerTruthCorrectionRequestCommand(
                    command_id="authority-epoch-correction-request",
                    answer_id=answer.answer_id,
                    citation_id=citation["citationId"],
                    memory_id=fields["memoryId"],
                    expected_memory_version_id=fields["memoryVersionId"],
                    correction_text="synthetic correction is never retained in the evaluation report",
                    reason_code="ownerReportedCorrection",
                ),
            )
            self._bump_epoch(store)
            with self.assertRaises(OwnerTruthCorrectionResolutionStale):
                correction_service.resolve(
                    context=self._context(),
                    correction_request_id=request.correction_request_id,
                    command=OwnerTruthCorrectionResolutionCommand(
                        command_id="authority-epoch-correction-resolution",
                        expected_candidate_version=1,
                        expected_memory_version_id=request.expected_memory_version_id,
                        action=CandidateReviewAction.CORRECT,
                        corrected_value={"summary": "synthetic corrected value"},
                        corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
                        reason_code="ownerConfirmedCorrection",
                    ),
                )
            correction_snapshot = store.owner_truth_correction_request_repository().snapshot()
            review_snapshot = store.owner_truth_candidate_review_repository().snapshot()
            self.assertEqual(correction_snapshot["resolutions"], [])
            self.assertEqual(correction_snapshot["outdatedEvents"], [])
            self.assertEqual(review_snapshot["candidates"][request.candidate_id]["decision"], "pending")
            metrics = _empty_metrics()
            metrics["correctionWriteRejectedCount"] = 1
            return _Observation("rejected", ("correctionAuthorityEpochStale",), metrics)

        if case.scenario == "crossOwnerAsyncReplay":
            repository = InMemoryOwnerTruthSourceTargetAdmissionRepository()
            source_id = str(uuid4())
            repository.seed_vault(
                vault_id=VAULT,
                owner_subject_id=OWNER,
                authority_epoch=0,
                status="active",
            )
            repository.seed_source(
                vault_id=VAULT,
                source_id=source_id,
                owner_subject_id=OWNER,
                authority_epoch=0,
                source_version=1,
                state="active",
            )
            result = repository.admit_owner_truth_source(
                self._source_intent(source_id=source_id, owner_id=OTHER_OWNER)
            )
            self.assertFalse(result.allowed)
            self.assertEqual(result.reason_code, "vaultOwnerMismatch")
            metrics = _empty_metrics()
            metrics["asyncCallbackBlockedCount"] = 1
            metrics["crossScopeDeniedCount"] = 1
            return _Observation("blocked", (result.reason_code,), metrics)

        if case.scenario == "crossVaultContextReplay":
            store = InMemoryStore()
            candidate = self._candidate(marker=case.private_markers[0])
            self._activate(store=store, candidate=candidate, command_id="authority-epoch-cross-vault")
            OwnerTruthMemoryProjectionService(store).rebuild(context=self._context())
            cross_vault_context = self._context(vault_id=OTHER_VAULT)
            with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
                OwnerTruthContextShadowBuildService(store, enabled=True).build(
                    context=cross_vault_context,
                    payload={"intent": "echo_chat"},
                )
            with self.assertRaises(OwnerTruthMemoryProjectionAccessDenied):
                OwnerTruthAnswerCitationService(store, enabled=True).record(
                    context=cross_vault_context,
                    command=OwnerTruthAnswerCitationCommand(
                        command_id="authority-epoch-cross-vault-citation",
                        answer_text="synthetic answer is never retained in the evaluation report",
                    ),
                    context_payload={"intent": "echo_chat"},
                )
            self.assertEqual(store.owner_truth_answer_citation_repository().snapshot()["records"], [])
            metrics = _empty_metrics()
            metrics["crossScopeDeniedCount"] = 1
            return _Observation("accessDenied", ("vaultScopeMismatch",), metrics)

        self.fail(f"unsupported authority epoch offline scenario: {case.scenario}")

    def test_versioned_synthetic_negative_corpus_blocks_old_authority_epoch_chain(self) -> None:
        cases = self._load_cases()
        self.assertEqual(len(cases), 7)
        self.assertEqual(len({case.case_id for case in cases}), len(cases))
        evaluator = _Evaluator()
        results: list[_Result] = []
        for case in cases:
            with self.subTest(case_id=case.case_id):
                observation = self._observation_for(case)
                result = evaluator.evaluate(case=case, observation=observation)
                self.assertTrue(result.passed, result.value_free_summary())
                results.append(result)
        report = evaluator.report(results)
        self.assertTrue(report["passed"], report)
        rendered = json.dumps(report, ensure_ascii=False, sort_keys=True)
        for case in cases:
            for marker in case.private_markers:
                self.assertNotIn(marker, rendered)

    def test_evaluator_rejects_forbidden_metrics_even_when_disposition_matches(self) -> None:
        case = self._load_cases()[0]
        metrics = _empty_metrics()
        metrics["asyncCallbackBlockedCount"] = 1
        metrics["privateInputLeakageCount"] = 1
        result = _Evaluator().evaluate(
            case=case,
            observation=_Observation(
                disposition="blocked",
                reason_codes=("authorityEpochChanged",),
                metrics=metrics,
            ),
        )
        self.assertFalse(result.passed)
        self.assertIn("nonzeroForbiddenMetric:privateInputLeakageCount", result.violation_codes)

    def test_fixture_and_metrics_remain_synthetic_and_non_engagement(self) -> None:
        self.assertEqual(METRIC_ALLOWLIST & FORBIDDEN_ENGAGEMENT_METRICS, frozenset())
        for case in self._load_cases():
            self.assertTrue(case.case_id)
            self.assertTrue(case.scenario)
            self.assertTrue(case.private_markers)
            self.assertTrue(case.expected_reason_codes)
            self.assertTrue(set(case.expected_nonzero_metrics).issubset(METRIC_ALLOWLIST))


if __name__ == "__main__":
    unittest.main()
