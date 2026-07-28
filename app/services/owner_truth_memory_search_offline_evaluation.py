"""Synthetic retrieval evaluation baseline for the private SearchDocument fallback.

The Phase 4C reader deliberately remains deterministic text retrieval until a
separately approved semantic provider exists.  This evaluator gives that
fallback a repeatable, value-free gold-corpus gate without treating it as a
production efficacy claim or accepting real Owner content.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Iterable, Mapping, Tuple

from app.domain.owner_truth.contracts import OwnerTruthContractError, require_nonblank
from app.domain.owner_truth.search_documents import (
    OWNER_TRUTH_MEMORY_SEARCH_RETRIEVAL_MODE,
    OwnerTruthMemorySearchHit,
    OwnerTruthSearchDocumentProjection,
    build_owner_truth_memory_search_query_plan,
    search_owner_truth_documents,
)


OWNER_TRUTH_MEMORY_SEARCH_OFFLINE_EVALUATION_SCHEMA_VERSION = (
    "owner-truth-memory-search-offline-evaluation-v1"
)

# The corpus is for correctness and privacy regression only. These metrics
# intentionally cannot become a proxy for engagement, session growth, or
# Persona dependence.
OWNER_TRUTH_MEMORY_SEARCH_EVALUATION_METRIC_ALLOWLIST = frozenset(
    {
        "queryCount",
        "expectedCitationCount",
        "matchedExpectedCitationCount",
        "rankOneExpectedCitationCount",
        "missingExpectedCitationCount",
        "unexpectedCitationCount",
        "policyViolationCount",
        "privateInputLeakageCount",
    }
)
OWNER_TRUTH_MEMORY_SEARCH_FORBIDDEN_ENGAGEMENT_METRICS = frozenset(
    {
        "conversationDuration",
        "messageCount",
        "clickThroughRate",
        "activeDays",
        "personaDependency",
    }
)


class OwnerTruthMemorySearchOfflineEvaluationError(OwnerTruthContractError):
    """A synthetic memory-search evaluation case is malformed."""


@dataclass(frozen=True)
class OwnerTruthMemorySearchEvaluationQuery:
    """One synthetic query and its ordered citation-only gold result."""

    query_id: str
    query: str
    limit: int
    expected_memory_version_ids: Tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "query_id", require_nonblank(self.query_id, field="query_id"))
        object.__setattr__(self, "query", require_nonblank(self.query, field="query"))
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) or self.limit < 1:
            raise OwnerTruthMemorySearchOfflineEvaluationError("query limit must be positive")
        expected = tuple(
            require_nonblank(str(item or ""), field="expected_memory_version_id")
            for item in self.expected_memory_version_ids
        )
        if len(expected) != len(set(expected)):
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "expected memory version identifiers must be unique"
            )
        if len(expected) > self.limit:
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "expected result count cannot exceed the query limit"
            )
        object.__setattr__(self, "expected_memory_version_ids", expected)


@dataclass(frozen=True)
class OwnerTruthMemorySearchEvaluationCase:
    """A synthetic, one-owner SearchDocument projection gold case."""

    case_id: str
    projection: OwnerTruthSearchDocumentProjection
    queries: Tuple[OwnerTruthMemorySearchEvaluationQuery, ...]
    private_markers: Tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", require_nonblank(self.case_id, field="case_id"))
        if not isinstance(self.projection, OwnerTruthSearchDocumentProjection):
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "evaluation case requires a typed SearchDocument projection"
            )
        queries = tuple(self.queries)
        if not queries or not all(isinstance(item, OwnerTruthMemorySearchEvaluationQuery) for item in queries):
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "evaluation case requires typed queries"
            )
        query_ids = tuple(item.query_id for item in queries)
        if len(query_ids) != len(set(query_ids)):
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "evaluation query identifiers must be unique"
            )
        known_memory_versions = {
            document.memory_version_id for document in self.projection.documents
        }
        if any(
            not set(query.expected_memory_version_ids).issubset(known_memory_versions)
            for query in queries
        ):
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "gold citations must belong to the evaluation projection"
            )
        markers = tuple(
            require_nonblank(str(item or ""), field="private_marker")
            for item in self.private_markers
        )
        if len(markers) != len(set(markers)):
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "private markers must be unique"
            )
        object.__setattr__(self, "queries", queries)
        object.__setattr__(self, "private_markers", markers)


@dataclass(frozen=True)
class OwnerTruthMemorySearchEvaluationResult:
    """Value-free verdict for one synthetic retrieval case."""

    case_id: str
    metrics: Mapping[str, int]
    violation_codes: Tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_id", require_nonblank(self.case_id, field="case_id"))
        metric_keys = frozenset(self.metrics)
        if metric_keys != OWNER_TRUTH_MEMORY_SEARCH_EVALUATION_METRIC_ALLOWLIST:
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "memory-search evaluation metrics must use the allowlist"
            )
        if metric_keys.intersection(OWNER_TRUTH_MEMORY_SEARCH_FORBIDDEN_ENGAGEMENT_METRICS):
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "memory-search evaluation cannot emit engagement metrics"
            )
        normalized_metrics: dict[str, int] = {}
        for key, value in self.metrics.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise OwnerTruthMemorySearchOfflineEvaluationError(
                    "memory-search evaluation metrics must be non-negative integers"
                )
            normalized_metrics[key] = value
        violations = tuple(require_nonblank(item, field="violation_code") for item in self.violation_codes)
        if len(violations) != len(set(violations)):
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "memory-search evaluation violation codes must be unique"
            )
        object.__setattr__(self, "metrics", normalized_metrics)
        object.__setattr__(self, "violation_codes", violations)

    @property
    def passed(self) -> bool:
        return not self.violation_codes

    def value_free_summary(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "metrics": dict(self.metrics),
            "passed": self.passed,
            "schemaVersion": OWNER_TRUTH_MEMORY_SEARCH_OFFLINE_EVALUATION_SCHEMA_VERSION,
            "violationCodes": list(self.violation_codes),
        }


@dataclass(frozen=True)
class OwnerTruthMemorySearchEvaluationReport:
    """Aggregate synthetic retrieval evaluation report."""

    results: Tuple[OwnerTruthMemorySearchEvaluationResult, ...]

    def __post_init__(self) -> None:
        results = tuple(self.results)
        if not results or not all(
            isinstance(item, OwnerTruthMemorySearchEvaluationResult) for item in results
        ):
            raise OwnerTruthMemorySearchOfflineEvaluationError(
                "evaluation report requires typed results"
            )
        case_ids = tuple(item.case_id for item in results)
        if len(case_ids) != len(set(case_ids)):
            raise OwnerTruthMemorySearchOfflineEvaluationError(
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
            "retrievalMode": OWNER_TRUTH_MEMORY_SEARCH_RETRIEVAL_MODE,
            "schemaVersion": OWNER_TRUTH_MEMORY_SEARCH_OFFLINE_EVALUATION_SCHEMA_VERSION,
            "syntheticOnly": True,
        }


class OwnerTruthMemorySearchOfflineEvaluator:
    """Evaluate the current deterministic fallback without runtime effects."""

    def evaluate_case(
        self,
        case: OwnerTruthMemorySearchEvaluationCase,
    ) -> OwnerTruthMemorySearchEvaluationResult:
        if not isinstance(case, OwnerTruthMemorySearchEvaluationCase):
            raise TypeError("case must be an OwnerTruthMemorySearchEvaluationCase")
        metrics = {key: 0 for key in OWNER_TRUTH_MEMORY_SEARCH_EVALUATION_METRIC_ALLOWLIST}
        violations: list[str] = []
        for query in case.queries:
            query_plan = build_owner_truth_memory_search_query_plan(
                projection=case.projection,
                query=query.query,
                limit=query.limit,
            )
            hits = search_owner_truth_documents(
                projection=case.projection,
                query_plan=query_plan,
            )
            actual_ids = tuple(item.document.memory_version_id for item in hits)
            expected_ids = query.expected_memory_version_ids
            expected_set = set(expected_ids)
            actual_set = set(actual_ids)
            missing = expected_set.difference(actual_set)
            unexpected = actual_set.difference(expected_set)
            metrics["queryCount"] += 1
            metrics["expectedCitationCount"] += len(expected_ids)
            metrics["matchedExpectedCitationCount"] += len(expected_set.intersection(actual_set))
            metrics["rankOneExpectedCitationCount"] += int(
                bool(expected_ids) and bool(actual_ids) and actual_ids[0] == expected_ids[0]
            )
            metrics["missingExpectedCitationCount"] += len(missing)
            metrics["unexpectedCitationCount"] += len(unexpected)
            if missing or unexpected:
                violations.append(f"expectedCitationMismatch:{query.query_id}")
            elif actual_ids != expected_ids:
                violations.append(f"rankOrderMismatch:{query.query_id}")

            rendered = json.dumps(
                self._value_free_read_summary(
                    projection=case.projection,
                    query_plan=query_plan,
                    hits=hits,
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
            leaked_marker_count = sum(marker in rendered for marker in case.private_markers)
            metrics["privateInputLeakageCount"] += leaked_marker_count
            if leaked_marker_count:
                violations.append(f"privateInputLeakage:{query.query_id}")

        unique_violations = tuple(dict.fromkeys(violations))
        metrics["policyViolationCount"] = len(unique_violations)
        return OwnerTruthMemorySearchEvaluationResult(
            case_id=case.case_id,
            metrics=metrics,
            violation_codes=unique_violations,
        )

    def evaluate(
        self,
        cases: Iterable[OwnerTruthMemorySearchEvaluationCase],
    ) -> OwnerTruthMemorySearchEvaluationReport:
        return OwnerTruthMemorySearchEvaluationReport(
            results=tuple(self.evaluate_case(case) for case in cases)
        )

    @staticmethod
    def _value_free_read_summary(
        *,
        projection: OwnerTruthSearchDocumentProjection,
        query_plan: object,
        hits: Tuple[OwnerTruthMemorySearchHit, ...],
    ) -> dict[str, object]:
        return {
            "state": "ready",
            "projection": projection.value_free_summary(),
            "queryPlan": query_plan.value_free_summary(),
            "hits": [item.value_free_summary() for item in hits],
        }


__all__ = [
    "OWNER_TRUTH_MEMORY_SEARCH_EVALUATION_METRIC_ALLOWLIST",
    "OWNER_TRUTH_MEMORY_SEARCH_FORBIDDEN_ENGAGEMENT_METRICS",
    "OWNER_TRUTH_MEMORY_SEARCH_OFFLINE_EVALUATION_SCHEMA_VERSION",
    "OwnerTruthMemorySearchEvaluationCase",
    "OwnerTruthMemorySearchEvaluationQuery",
    "OwnerTruthMemorySearchEvaluationReport",
    "OwnerTruthMemorySearchEvaluationResult",
    "OwnerTruthMemorySearchOfflineEvaluationError",
    "OwnerTruthMemorySearchOfflineEvaluator",
]
