"""Synthetic Context/Citation negative-corpus evaluation for Owner Truth.

This is a QA-only evaluator, not a runtime selector or a retrieval quality
claim. Callers supply observations produced by the real Context V4 shadow
builder and Answer/Citation recorder. The evaluator emits only value-free case
identifiers, counters, and policy violation codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Iterable, Mapping

from app.domain.owner_truth.contracts import OwnerTruthContractError, require_nonblank
from app.services.owner_truth_answer_citation import (
    OwnerTruthAnswerCitationResult,
    answer_citation_summary,
)
from app.services.owner_truth_context_shadow_build import context_shadow_build_summary


OWNER_TRUTH_CONTEXT_CITATION_OFFLINE_EVALUATION_SCHEMA_VERSION = (
    "owner-truth-context-citation-offline-evaluation-v1"
)
OWNER_TRUTH_CONTEXT_CITATION_EVALUATION_METRIC_ALLOWLIST = frozenset(
    {
        "contextBuildCount",
        "selectedContextCount",
        "filteredContextCount",
        "citationCount",
        "fallbackCount",
        "accessDeniedCount",
        "rejectedWriteCount",
        "crossScopeCitationCount",
        "legacyReadObservedCount",
        "unresolvedCitationCount",
        "privateInputLeakageCount",
        "policyViolationCount",
    }
)
OWNER_TRUTH_CONTEXT_CITATION_FORBIDDEN_ENGAGEMENT_METRICS = frozenset(
    {
        "conversationDuration",
        "messageCount",
        "clickThroughRate",
        "activeDays",
        "personaDependency",
    }
)


class OwnerTruthContextCitationOfflineEvaluationError(OwnerTruthContractError):
    """A synthetic Context/Citation evaluation case is malformed."""


class ContextCitationWriteDisposition(str, Enum):
    RECORDED = "recorded"
    ACCESS_DENIED = "accessDenied"
    REJECTED = "rejected"


def _nonnegative_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OwnerTruthContextCitationOfflineEvaluationError(
            f"{field} must be a non-negative integer"
        )
    return value


def _nonblank_tuple(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise OwnerTruthContextCitationOfflineEvaluationError(f"{field} must be a list")
    normalized = tuple(require_nonblank(str(item or ""), field=field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise OwnerTruthContextCitationOfflineEvaluationError(f"{field} must be unique")
    return normalized


@dataclass(frozen=True)
class OwnerTruthContextCitationEvaluationCase:
    """One versioned, synthetic-only Context/Citation negative case."""

    case_id: str
    scenario: str
    expected_vault_id: str
    expected_disposition: ContextCitationWriteDisposition | str
    expected_context_state: str | None
    expected_selected_count: int
    expected_filtered_reason_codes: tuple[str, ...]
    expected_citation_count: int
    expected_fallbacks: tuple[str, ...]
    private_markers: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", require_nonblank(self.case_id, field="case_id"))
        object.__setattr__(self, "scenario", require_nonblank(self.scenario, field="scenario"))
        object.__setattr__(
            self,
            "expected_vault_id",
            require_nonblank(self.expected_vault_id, field="expected_vault_id"),
        )
        try:
            object.__setattr__(
                self,
                "expected_disposition",
                ContextCitationWriteDisposition(self.expected_disposition),
            )
        except (TypeError, ValueError) as exc:
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "expected_disposition is unsupported"
            ) from exc
        if self.expected_context_state is not None:
            object.__setattr__(
                self,
                "expected_context_state",
                require_nonblank(self.expected_context_state, field="expected_context_state"),
            )
        object.__setattr__(
            self,
            "expected_selected_count",
            _nonnegative_int(self.expected_selected_count, field="expected_selected_count"),
        )
        object.__setattr__(
            self,
            "expected_citation_count",
            _nonnegative_int(self.expected_citation_count, field="expected_citation_count"),
        )
        object.__setattr__(
            self,
            "expected_filtered_reason_codes",
            tuple(
                sorted(
                    _nonblank_tuple(
                        self.expected_filtered_reason_codes,
                        field="expected_filtered_reason_codes",
                    )
                )
            ),
        )
        object.__setattr__(
            self,
            "expected_fallbacks",
            _nonblank_tuple(self.expected_fallbacks, field="expected_fallbacks"),
        )
        object.__setattr__(
            self,
            "private_markers",
            _nonblank_tuple(self.private_markers, field="private_markers"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OwnerTruthContextCitationEvaluationCase":
        if not isinstance(value, Mapping):
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "evaluation case fixture must be an object"
            )
        expected = value.get("expected")
        if not isinstance(expected, Mapping):
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "evaluation case fixture expected must be an object"
            )
        return cls(
            case_id=str(value.get("caseId") or ""),
            scenario=str(value.get("scenario") or ""),
            expected_vault_id=str(value.get("expectedVaultId") or ""),
            expected_disposition=str(expected.get("disposition") or ""),
            expected_context_state=expected.get("contextState"),
            expected_selected_count=expected.get("selectedCount"),
            expected_filtered_reason_codes=tuple(expected.get("filteredReasonCodes") or ()),
            expected_citation_count=expected.get("citationCount"),
            expected_fallbacks=tuple(expected.get("fallbacks") or ()),
            private_markers=tuple(value.get("privateMarkers") or ()),
        )


@dataclass(frozen=True)
class OwnerTruthContextCitationEvaluationObservation:
    """Actual output from the Context builder and immutable receipt boundary."""

    disposition: ContextCitationWriteDisposition | str
    context_build: Mapping[str, Any] | None
    answer_result: OwnerTruthAnswerCitationResult | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self,
                "disposition",
                ContextCitationWriteDisposition(self.disposition),
            )
        except (TypeError, ValueError) as exc:
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "observation disposition is unsupported"
            ) from exc
        if self.context_build is not None and not isinstance(self.context_build, Mapping):
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "context_build must be an object or null"
            )
        if self.answer_result is not None and not isinstance(
            self.answer_result,
            OwnerTruthAnswerCitationResult,
        ):
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "answer_result must be an OwnerTruthAnswerCitationResult or null"
            )
        if self.disposition is ContextCitationWriteDisposition.RECORDED:
            if self.context_build is None or self.answer_result is None:
                raise OwnerTruthContextCitationOfflineEvaluationError(
                    "recorded observations require Context build and Answer/Citation result"
                )
        elif self.answer_result is not None:
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "non-recorded observations must not carry Answer/Citation output"
            )


@dataclass(frozen=True)
class OwnerTruthContextCitationEvaluationResult:
    """Value-free verdict for one synthetic Context/Citation case."""

    case_id: str
    metrics: Mapping[str, int]
    violation_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", require_nonblank(self.case_id, field="case_id"))
        metric_keys = frozenset(self.metrics)
        if metric_keys != OWNER_TRUTH_CONTEXT_CITATION_EVALUATION_METRIC_ALLOWLIST:
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "Context/Citation evaluation metrics must use the allowlist"
            )
        if metric_keys.intersection(OWNER_TRUTH_CONTEXT_CITATION_FORBIDDEN_ENGAGEMENT_METRICS):
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "Context/Citation evaluation must not emit engagement metrics"
            )
        object.__setattr__(
            self,
            "metrics",
            {
                key: _nonnegative_int(value, field=f"metrics.{key}")
                for key, value in self.metrics.items()
            },
        )
        object.__setattr__(
            self,
            "violation_codes",
            _nonblank_tuple(self.violation_codes, field="violation_codes"),
        )

    @property
    def passed(self) -> bool:
        return not self.violation_codes

    def value_free_summary(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "metrics": dict(self.metrics),
            "passed": self.passed,
            "schemaVersion": OWNER_TRUTH_CONTEXT_CITATION_OFFLINE_EVALUATION_SCHEMA_VERSION,
            "violationCodes": list(self.violation_codes),
        }


@dataclass(frozen=True)
class OwnerTruthContextCitationEvaluationReport:
    """Aggregate synthetic-only result; never a real retrieval quality claim."""

    results: tuple[OwnerTruthContextCitationEvaluationResult, ...]

    def __post_init__(self) -> None:
        results = tuple(self.results)
        if not results or not all(
            isinstance(item, OwnerTruthContextCitationEvaluationResult) for item in results
        ):
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "evaluation report requires typed results"
            )
        case_ids = tuple(item.case_id for item in results)
        if len(case_ids) != len(set(case_ids)):
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "evaluation report case identifiers must be unique"
            )
        object.__setattr__(self, "results", results)

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.results)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "caseCount": len(self.results),
            "failedCaseIds": [item.case_id for item in self.results if not item.passed],
            "passed": self.passed,
            "schemaVersion": OWNER_TRUTH_CONTEXT_CITATION_OFFLINE_EVALUATION_SCHEMA_VERSION,
            "syntheticOnly": True,
        }


class OwnerTruthContextCitationOfflineEvaluator:
    """Evaluate real shadow outputs against a synthetic privacy/safety corpus."""

    def evaluate_case(
        self,
        *,
        case: OwnerTruthContextCitationEvaluationCase,
        observation: OwnerTruthContextCitationEvaluationObservation,
    ) -> OwnerTruthContextCitationEvaluationResult:
        if not isinstance(case, OwnerTruthContextCitationEvaluationCase):
            raise TypeError("case must be an OwnerTruthContextCitationEvaluationCase")
        if not isinstance(observation, OwnerTruthContextCitationEvaluationObservation):
            raise TypeError("observation must be an OwnerTruthContextCitationEvaluationObservation")

        metrics = {key: 0 for key in OWNER_TRUTH_CONTEXT_CITATION_EVALUATION_METRIC_ALLOWLIST}
        violations: list[str] = []
        if observation.disposition is not case.expected_disposition:
            violations.append("writeDispositionMismatch")
        if observation.disposition is ContextCitationWriteDisposition.ACCESS_DENIED:
            metrics["accessDeniedCount"] = 1
        elif observation.disposition is ContextCitationWriteDisposition.REJECTED:
            metrics["rejectedWriteCount"] = 1

        summaries: list[Mapping[str, Any]] = []
        context_summary: Mapping[str, Any] | None = None
        if observation.context_build is None:
            if case.expected_context_state is not None:
                violations.append("contextBuildMissing")
        else:
            metrics["contextBuildCount"] = 1
            try:
                context_summary = context_shadow_build_summary(observation.context_build)
                summaries.append(context_summary)
            except Exception:
                violations.append("contextBuildSummaryInvalid")

        if context_summary is not None:
            self._evaluate_context_summary(
                case=case,
                context_summary=context_summary,
                metrics=metrics,
                violations=violations,
            )

        if observation.answer_result is not None:
            answer_summary = answer_citation_summary(observation.answer_result)
            summaries.append(answer_summary)
            metrics["citationCount"] = observation.answer_result.citation_count
            if observation.answer_result.citation_count != case.expected_citation_count:
                violations.append("citationCountMismatch")
            if tuple(observation.answer_result.fallbacks) != case.expected_fallbacks:
                violations.append("answerFallbackMismatch")
            for item in observation.answer_result.citations:
                self._check_answer_citation(
                    item=item,
                    expected_vault_id=case.expected_vault_id,
                    metrics=metrics,
                    violations=violations,
                )
        elif case.expected_disposition is ContextCitationWriteDisposition.RECORDED:
            violations.append("answerCitationMissing")
        elif case.expected_citation_count != 0:
            violations.append("unexpectedExpectedCitationCount")

        rendered = json.dumps(summaries, ensure_ascii=False, sort_keys=True)
        leaked_marker_count = sum(marker in rendered for marker in case.private_markers)
        metrics["privateInputLeakageCount"] = leaked_marker_count
        if leaked_marker_count:
            violations.append("privateInputLeakage")
        unique_violations = tuple(dict.fromkeys(violations))
        metrics["policyViolationCount"] = len(unique_violations)
        return OwnerTruthContextCitationEvaluationResult(
            case_id=case.case_id,
            metrics=metrics,
            violation_codes=unique_violations,
        )

    def evaluate(
        self,
        rows: Iterable[
            tuple[OwnerTruthContextCitationEvaluationCase, OwnerTruthContextCitationEvaluationObservation]
        ],
    ) -> OwnerTruthContextCitationEvaluationReport:
        pairs = tuple(rows)
        if not pairs:
            raise OwnerTruthContextCitationOfflineEvaluationError(
                "evaluation report requires at least one case"
            )
        return OwnerTruthContextCitationEvaluationReport(
            results=tuple(
                self.evaluate_case(case=case, observation=observation)
                for case, observation in pairs
            )
        )

    def _evaluate_context_summary(
        self,
        *,
        case: OwnerTruthContextCitationEvaluationCase,
        context_summary: Mapping[str, Any],
        metrics: dict[str, int],
        violations: list[str],
    ) -> None:
        selected = context_summary.get("selectedContext")
        filtered = context_summary.get("filteredContext")
        fallbacks = context_summary.get("fallbacks")
        authority = context_summary.get("authority")
        citation_proof = context_summary.get("citationProof")
        ranking_trace = context_summary.get("rankingTrace")
        if not isinstance(selected, list) or not isinstance(filtered, list):
            violations.append("contextListsInvalid")
            selected = []
            filtered = []
        if not isinstance(fallbacks, list):
            violations.append("contextFallbacksInvalid")
            fallbacks = []
        if not isinstance(authority, Mapping):
            violations.append("contextAuthorityInvalid")
            authority = {}
        if not isinstance(citation_proof, list) or not isinstance(ranking_trace, list):
            violations.append("contextTraceInvalid")
            citation_proof = []
            ranking_trace = []
        metrics["selectedContextCount"] = len(selected)
        metrics["filteredContextCount"] = len(filtered)
        metrics["fallbackCount"] = len(fallbacks)
        if str(authority.get("state") or "") != (case.expected_context_state or ""):
            violations.append("contextStateMismatch")
        if len(selected) != case.expected_selected_count:
            violations.append("selectedContextCountMismatch")
        actual_filtered_reasons = tuple(sorted(
            str(item.get("reason") or "") for item in filtered if isinstance(item, Mapping)
        ))
        if actual_filtered_reasons != case.expected_filtered_reason_codes:
            violations.append("filteredReasonMismatch")
        if tuple(str(item) for item in fallbacks) != case.expected_fallbacks:
            violations.append("fallbackMismatch")
        if str(authority.get("vaultId") or "") != case.expected_vault_id:
            violations.append("contextAuthorityScopeMismatch")
        if not bool(context_summary.get("shadowOnly")):
            violations.append("shadowOnlyRequired")
        if (
            not bool(context_summary.get("legacyContextUnchanged"))
            or bool(context_summary.get("legacyContextRead"))
        ):
            metrics["legacyReadObservedCount"] += 1
            violations.append("legacyReadObserved")
        if len(citation_proof) != len(selected) or len(ranking_trace) != len(selected):
            violations.append("contextTraceCountMismatch")
        for item in selected:
            self._check_context_item(
                item=item,
                expected_vault_id=case.expected_vault_id,
                metrics=metrics,
                violations=violations,
            )

    @staticmethod
    def _check_context_item(
        *,
        item: object,
        expected_vault_id: str,
        metrics: dict[str, int],
        violations: list[str],
    ) -> None:
        if not isinstance(item, Mapping):
            violations.append("selectedContextItemInvalid")
            return
        citation = item.get("citation")
        source_ref = item.get("sourceRef")
        if not isinstance(citation, Mapping) or not isinstance(source_ref, Mapping):
            violations.append("selectedCitationInvalid")
            return
        if (
            str(citation.get("vaultId") or "") != expected_vault_id
            or str(source_ref.get("vaultId") or "") != expected_vault_id
        ):
            metrics["crossScopeCitationCount"] += 1
            violations.append("crossScopeContextCitation")
        if "content" in item:
            violations.append("selectedContextContentLeak")

    @staticmethod
    def _check_answer_citation(
        *,
        item: object,
        expected_vault_id: str,
        metrics: dict[str, int],
        violations: list[str],
    ) -> None:
        if not isinstance(item, Mapping):
            violations.append("answerCitationInvalid")
            return
        citation = item.get("citation")
        if not isinstance(citation, Mapping):
            violations.append("answerCitationInvalid")
            return
        if str(citation.get("vaultId") or "") != expected_vault_id:
            metrics["crossScopeCitationCount"] += 1
            violations.append("crossScopeAnswerCitation")
        if not bool(item.get("resolved")):
            metrics["unresolvedCitationCount"] += 1
            violations.append("unresolvedAnswerCitation")


__all__ = [
    "ContextCitationWriteDisposition",
    "OWNER_TRUTH_CONTEXT_CITATION_EVALUATION_METRIC_ALLOWLIST",
    "OWNER_TRUTH_CONTEXT_CITATION_FORBIDDEN_ENGAGEMENT_METRICS",
    "OWNER_TRUTH_CONTEXT_CITATION_OFFLINE_EVALUATION_SCHEMA_VERSION",
    "OwnerTruthContextCitationEvaluationCase",
    "OwnerTruthContextCitationEvaluationObservation",
    "OwnerTruthContextCitationEvaluationReport",
    "OwnerTruthContextCitationEvaluationResult",
    "OwnerTruthContextCitationOfflineEvaluationError",
    "OwnerTruthContextCitationOfflineEvaluator",
]
