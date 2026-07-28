"""Synthetic offline evaluation baseline for M0-B recommendations.

This module is deliberately a test/evaluation harness rather than a runtime
ranker. It evaluates typed, value-free RecommendationCandidate fixtures using
the production selector, then independently checks that the selected output
still respects the Owner Truth safety boundary. It never accepts real Owner
text, records product engagement, writes authority data, or changes Echo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable, Mapping, Optional, Tuple

from app.domain.owner_truth.contracts import OwnerTruthContractError, require_nonblank
from app.domain.owner_truth.knowledge_recommendations import (
    DimensionProjection,
    KnowledgeDimension,
    RecommendationCandidate,
    RecommendationDecision,
    RecommendationFilteredCandidate,
    RecommendationSelection,
    RecommendationSelector,
    RecommendationSlot,
)


OWNER_TRUTH_RECOMMENDATION_OFFLINE_EVALUATION_SCHEMA_VERSION = (
    "owner-truth-recommendation-offline-evaluation-v1"
)

# Phase 5A explicitly rejects optimizing conversation duration, message volume,
# clicks, retention/streaks, or Persona dependence. Keep the report's metric
# vocabulary small and policy-oriented so a future caller cannot mistake this
# synthetic baseline for an engagement optimizer.
OWNER_TRUTH_RECOMMENDATION_EVALUATION_METRIC_ALLOWLIST = frozenset(
    {
        "selectedCount",
        "filteredCount",
        "continuitySelectionCount",
        "breadthSelectionCount",
        "policyViolationCount",
        "duplicateQuestionCount",
        "expectedSelectionMismatchCount",
        "expectedFilterMismatchCount",
    }
)
OWNER_TRUTH_RECOMMENDATION_FORBIDDEN_ENGAGEMENT_METRICS = frozenset(
    {
        "conversationDuration",
        "messageCount",
        "clickThroughRate",
        "activeDays",
        "personaDependency",
    }
)


class OwnerTruthRecommendationOfflineEvaluationError(OwnerTruthContractError):
    """An offline recommendation evaluation case is malformed."""


class RecommendationEvaluationCategory(str, Enum):
    QUALITY = "quality"
    SAFETY_RED_TEAM = "safetyRedTeam"
    REPETITION_BASELINE = "repetitionBaseline"


@dataclass(frozen=True)
class OwnerTruthRecommendationEvaluationCase:
    """One synthetic, value-free Phase 5A evaluation scenario."""

    case_id: str
    category: RecommendationEvaluationCategory | str
    owner_subject_id: str
    vault_id: str
    coverage: DimensionProjection
    candidates: Tuple[RecommendationCandidate, ...]
    now: datetime
    expected_selected_candidate_ids: Tuple[str, ...]
    expected_filtered_reason_codes: Mapping[str, str]
    crisis_active: bool = False
    accepted_candidate_ids: Tuple[str, ...] = ()
    excluded_candidate_ids: Tuple[str, ...] = ()
    feedback_dimension_penalty_counts: Mapping[KnowledgeDimension | str, int] | None = None
    feedback_question_template_penalty_counts: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", require_nonblank(self.case_id, field="case_id"))
        try:
            object.__setattr__(self, "category", RecommendationEvaluationCategory(self.category))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "evaluation category is not supported"
            ) from exc
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        if not isinstance(self.coverage, DimensionProjection):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "coverage must be a DimensionProjection"
            )
        if (
            self.coverage.owner_subject_id != self.owner_subject_id
            or self.coverage.vault_id != self.vault_id
        ):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "coverage scope does not match evaluation case"
            )
        if not isinstance(self.now, datetime) or self.now.tzinfo is None:
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "now must be timezone-aware"
            )
        if not isinstance(self.crisis_active, bool):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "crisis_active must be a boolean"
            )
        candidates = tuple(self.candidates)
        if not candidates:
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "evaluation cases require at least one candidate"
            )
        if not all(isinstance(item, RecommendationCandidate) for item in candidates):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "candidates must contain RecommendationCandidate values"
            )
        candidate_ids = tuple(item.candidate_id for item in candidates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "evaluation candidate identifiers must be unique"
            )
        object.__setattr__(self, "candidates", candidates)
        expected = tuple(str(item or "").strip() for item in self.expected_selected_candidate_ids)
        if len(expected) != len(set(expected)):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "expected selected candidate identifiers must be unique"
            )
        if any(not candidate_id for candidate_id in expected):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "expected selected candidate identifiers must be non-empty"
            )
        if not set(expected).issubset(candidate_ids):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "expected selected candidate identifiers must be in candidates"
            )
        object.__setattr__(self, "expected_selected_candidate_ids", expected)
        expected_filtered = {
            str(candidate_id or "").strip(): str(reason or "").strip()
            for candidate_id, reason in self.expected_filtered_reason_codes.items()
        }
        if not set(expected_filtered).issubset(candidate_ids):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "expected filtered candidate identifiers must be in candidates"
            )
        if any(not candidate_id or not reason for candidate_id, reason in expected_filtered.items()):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "expected filtered reasons must be non-empty"
            )
        object.__setattr__(self, "expected_filtered_reason_codes", expected_filtered)


@dataclass(frozen=True)
class OwnerTruthRecommendationEvaluationResult:
    """Value-free result for one synthetic corpus case."""

    case_id: str
    category: RecommendationEvaluationCategory | str
    selected_candidate_ids: Tuple[str, ...]
    filtered_reason_codes: Tuple[Tuple[str, str], ...]
    violation_codes: Tuple[str, ...]
    metrics: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", require_nonblank(self.case_id, field="case_id"))
        try:
            object.__setattr__(self, "category", RecommendationEvaluationCategory(self.category))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "evaluation result category is not supported"
            ) from exc
        selected = tuple(str(item or "").strip() for item in self.selected_candidate_ids)
        if len(selected) != len(set(selected)):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "selected candidate identifiers must be unique"
            )
        object.__setattr__(self, "selected_candidate_ids", selected)
        filtered = tuple(
            (str(candidate_id or "").strip(), str(reason or "").strip())
            for candidate_id, reason in self.filtered_reason_codes
        )
        if len({candidate_id for candidate_id, _ in filtered}) != len(filtered):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "filtered candidate identifiers must be unique"
            )
        if any(not candidate_id or not reason for candidate_id, reason in filtered):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "filtered candidate reasons must be non-empty"
            )
        object.__setattr__(self, "filtered_reason_codes", filtered)
        violations = tuple(str(item or "").strip() for item in self.violation_codes)
        if len(violations) != len(set(violations)) or any(not item for item in violations):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "violation codes must be unique non-empty strings"
            )
        object.__setattr__(self, "violation_codes", violations)
        metric_keys = frozenset(self.metrics)
        if metric_keys != OWNER_TRUTH_RECOMMENDATION_EVALUATION_METRIC_ALLOWLIST:
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "evaluation metrics must use the policy metric allowlist"
            )
        if metric_keys.intersection(OWNER_TRUTH_RECOMMENDATION_FORBIDDEN_ENGAGEMENT_METRICS):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "evaluation metrics must not contain engagement metrics"
            )
        normalized_metrics: dict[str, int] = {}
        for key, value in self.metrics.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise OwnerTruthRecommendationOfflineEvaluationError(
                    "evaluation metrics must be non-negative integers"
                )
            normalized_metrics[key] = value
        object.__setattr__(self, "metrics", normalized_metrics)

    @property
    def passed(self) -> bool:
        return not self.violation_codes

    def value_free_summary(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "category": self.category.value,
            "metrics": dict(self.metrics),
            "passed": self.passed,
            "schemaVersion": OWNER_TRUTH_RECOMMENDATION_OFFLINE_EVALUATION_SCHEMA_VERSION,
            "selectedCandidateIds": list(self.selected_candidate_ids),
            "violationCodes": list(self.violation_codes),
        }


@dataclass(frozen=True)
class OwnerTruthRecommendationEvaluationReport:
    """Aggregate synthetic corpus result; never a production efficacy claim."""

    results: Tuple[OwnerTruthRecommendationEvaluationResult, ...]

    def __post_init__(self) -> None:
        rows = tuple(self.results)
        if not rows:
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "evaluation report requires at least one result"
            )
        if not all(isinstance(item, OwnerTruthRecommendationEvaluationResult) for item in rows):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "evaluation report results are invalid"
            )
        case_ids = tuple(item.case_id for item in rows)
        if len(case_ids) != len(set(case_ids)):
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "evaluation report case identifiers must be unique"
            )
        object.__setattr__(self, "results", rows)

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.results)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "caseCount": len(self.results),
            "failedCaseIds": [item.case_id for item in self.results if not item.passed],
            "passed": self.passed,
            "schemaVersion": OWNER_TRUTH_RECOMMENDATION_OFFLINE_EVALUATION_SCHEMA_VERSION,
            "syntheticOnly": True,
        }


class OwnerTruthRecommendationOfflineEvaluator:
    """Run deterministic policy and red-team baselines without runtime effects."""

    def __init__(self, *, selector: Optional[RecommendationSelector] = None) -> None:
        self._selector = selector or RecommendationSelector()

    def evaluate_case(
        self,
        case: OwnerTruthRecommendationEvaluationCase,
    ) -> OwnerTruthRecommendationEvaluationResult:
        if not isinstance(case, OwnerTruthRecommendationEvaluationCase):
            raise TypeError("case must be an OwnerTruthRecommendationEvaluationCase")
        selection = self._selector.select(
            owner_subject_id=case.owner_subject_id,
            vault_id=case.vault_id,
            coverage=case.coverage,
            candidates=case.candidates,
            now=case.now,
            crisis_active=case.crisis_active,
            accepted_candidate_ids=case.accepted_candidate_ids,
            excluded_candidate_ids=case.excluded_candidate_ids,
            feedback_dimension_penalty_counts=case.feedback_dimension_penalty_counts,
            feedback_question_template_penalty_counts=(
                case.feedback_question_template_penalty_counts
            ),
        )
        return self.evaluate_selection(case=case, selection=selection)

    def evaluate_selection(
        self,
        *,
        case: OwnerTruthRecommendationEvaluationCase,
        selection: RecommendationSelection,
    ) -> OwnerTruthRecommendationEvaluationResult:
        """Verify a selection independently of how the selector was invoked."""

        if not isinstance(case, OwnerTruthRecommendationEvaluationCase):
            raise TypeError("case must be an OwnerTruthRecommendationEvaluationCase")
        if not isinstance(selection, RecommendationSelection):
            raise TypeError("selection must be a RecommendationSelection")

        candidate_by_id = {item.candidate_id: item for item in case.candidates}
        selected_ids = tuple(item.candidate_id for item in selection.selected)
        filtered_reasons = tuple(
            sorted((item.candidate_id, item.reason_code) for item in selection.filtered)
        )
        expected_filtered = tuple(sorted(case.expected_filtered_reason_codes.items()))
        violations: list[str] = []

        if selection.owner_subject_id != case.owner_subject_id or selection.vault_id != case.vault_id:
            violations.append("selectionScopeMismatch")
        if selection.policy_version != case.coverage.policy_version:
            violations.append("selectionPolicyVersionMismatch")
        if selected_ids != case.expected_selected_candidate_ids:
            violations.append("selectedCandidateMismatch")
        if filtered_reasons != expected_filtered:
            violations.append("filteredReasonMismatch")
        if len(selection.selected) > 2:
            violations.append("selectedCountExceeded")
        if len({item.slot for item in selection.selected}) != len(selection.selected):
            violations.append("duplicateRecommendationSlot")
        if len({(item.target_dimension, item.missing_facet) for item in selection.selected}) != len(
            selection.selected
        ):
            violations.append("duplicateKnowledgeGap")

        for decision in selection.selected:
            candidate = candidate_by_id.get(decision.candidate_id)
            if candidate is None:
                violations.append("selectedCandidateUnknown")
                continue
            if not self._decision_matches_candidate(decision, candidate):
                violations.append("selectedDecisionCandidateMismatch")
            if decision.policy_version != selection.policy_version:
                violations.append("selectedDecisionPolicyVersionMismatch")
            reason = self._blocked_reason(case=case, candidate=candidate)
            if reason is not None:
                violations.append("selectedBlockedCandidate")
            allowed_refs = set(
                case.coverage.for_dimension(decision.target_dimension).memory_version_ids
            )
            if not set(decision.evidence_refs).issubset(allowed_refs):
                violations.append("selectedEvidenceOutsideConfirmedCoverage")

        metrics = {
            "selectedCount": len(selection.selected),
            "filteredCount": len(selection.filtered),
            "continuitySelectionCount": sum(
                1 for item in selection.selected if item.slot is RecommendationSlot.CONTINUITY
            ),
            "breadthSelectionCount": sum(
                1 for item in selection.selected if item.slot is RecommendationSlot.BREADTH
            ),
            "policyViolationCount": len(
                {
                    item
                    for item in violations
                    if item
                    not in {
                        "selectedCandidateMismatch",
                        "filteredReasonMismatch",
                    }
                }
            ),
            "duplicateQuestionCount": max(
                0,
                len(selection.selected)
                - len({(item.target_dimension, item.missing_facet) for item in selection.selected}),
            ),
            "expectedSelectionMismatchCount": int(
                selected_ids != case.expected_selected_candidate_ids
            ),
            "expectedFilterMismatchCount": int(filtered_reasons != expected_filtered),
        }
        return OwnerTruthRecommendationEvaluationResult(
            case_id=case.case_id,
            category=case.category,
            selected_candidate_ids=selected_ids,
            filtered_reason_codes=filtered_reasons,
            violation_codes=tuple(sorted(set(violations))),
            metrics=metrics,
        )

    def evaluate_corpus(
        self,
        cases: Iterable[OwnerTruthRecommendationEvaluationCase],
    ) -> OwnerTruthRecommendationEvaluationReport:
        rows = tuple(cases)
        if not rows:
            raise OwnerTruthRecommendationOfflineEvaluationError(
                "evaluation corpus requires at least one case"
            )
        return OwnerTruthRecommendationEvaluationReport(
            results=tuple(self.evaluate_case(case) for case in rows)
        )

    @staticmethod
    def _decision_matches_candidate(
        decision: RecommendationDecision,
        candidate: RecommendationCandidate,
    ) -> bool:
        return (
            decision.slot is candidate.slot
            and decision.thread_id == candidate.thread_id
            and decision.target_dimension is candidate.target_dimension
            and decision.missing_facet == candidate.missing_facet
            and decision.question_template_id == candidate.question_template_id
            and decision.evidence_refs == candidate.evidence_refs
            and decision.reason_code == candidate.reason_code
        )

    @staticmethod
    def _blocked_reason(
        *,
        case: OwnerTruthRecommendationEvaluationCase,
        candidate: RecommendationCandidate,
    ) -> Optional[str]:
        """Small independent oracle for selected-result safety, not ranking."""

        if candidate.candidate_id in set(case.accepted_candidate_ids):
            return "acceptedAlready"
        if candidate.candidate_id in set(case.excluded_candidate_ids):
            return "userRequestedReplacement"
        if case.crisis_active:
            return "crisisSafetyOverride"
        if (
            candidate.owner_subject_id != case.owner_subject_id
            or candidate.vault_id != case.vault_id
        ):
            return "candidateScopeMismatch"
        if not candidate.is_accessible:
            return "evidenceNotAccessible"
        if candidate.is_deleted:
            return "evidenceDeleted"
        if candidate.is_revoked:
            return "evidenceRevoked"
        if candidate.is_disputed:
            return "evidenceDisputed"
        if candidate.is_ai_inference_only:
            return "aiInferenceOnly"
        if candidate.is_minor_risk:
            return "minorRisk"
        if candidate.requires_persona:
            return "personaRuntimeNotAllowed"
        if candidate.is_do_not_ask:
            return "doNotAsk"
        if candidate.is_in_cooldown:
            return "userCooldown"
        if candidate.consecutive_skip_count >= 2 and not candidate.was_reopened_by_user:
            return "repeatedSkipWithoutReopen"
        if candidate.is_sensitive and not candidate.has_recent_user_consent:
            return "sensitiveWithoutRecentConsent"
        if candidate.expires_at is not None and candidate.expires_at <= case.now:
            return "candidateExpired"
        if (
            candidate.slot is RecommendationSlot.BREADTH
            and candidate.missing_facet
            not in case.coverage.for_dimension(candidate.target_dimension).missing_facets
        ):
            return "facetAlreadyCovered"
        return None


__all__ = [
    "OWNER_TRUTH_RECOMMENDATION_EVALUATION_METRIC_ALLOWLIST",
    "OWNER_TRUTH_RECOMMENDATION_FORBIDDEN_ENGAGEMENT_METRICS",
    "OWNER_TRUTH_RECOMMENDATION_OFFLINE_EVALUATION_SCHEMA_VERSION",
    "OwnerTruthRecommendationEvaluationCase",
    "OwnerTruthRecommendationEvaluationReport",
    "OwnerTruthRecommendationEvaluationResult",
    "OwnerTruthRecommendationOfflineEvaluationError",
    "OwnerTruthRecommendationOfflineEvaluator",
    "RecommendationEvaluationCategory",
]
