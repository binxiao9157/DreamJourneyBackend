"""Deterministic scoring for real-provider synthetic memory organization.

The evaluator never decides whether prose is attractive. It checks the safety
properties the production organizer promises: a supported anchor is retained,
candidate statements stay close to the source, owner-stated facets are present
in the source, and no new number is introduced. Only aggregate counters and
synthetic case identifiers belong in evidence files; provider output does not.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Mapping, Sequence

from app.services.owner_truth_memory_search_quality_corpus import (
    OwnerTruthMemorySearchQualityCase,
)


OWNER_TRUTH_ORGANIZATION_QUALITY_SCHEMA_VERSION = (
    "owner-truth-memory-organization-quality-v2"
)
OWNER_TRUTH_ORGANIZATION_MIN_CANDIDATE_PRECISION = 0.95
OWNER_TRUTH_ORGANIZATION_MIN_EVIDENCE_ACCURACY = 0.98
OWNER_TRUTH_ORGANIZATION_MAX_MISS_RATE = 0.05
OWNER_TRUTH_ORGANIZATION_MAX_FALSE_ADMISSION_RATE = 0.0
_PRIMARY_FIELDS = {
    "experience": ("event", "summary"),
    "knowledge": ("statement", "claim"),
    "emotion": ("expression", "label", "emotion"),
}
_FACET_NAMES = (
    "people",
    "time",
    "places",
    "relationships",
    "emotions",
    "values",
    "personality",
    "habits",
    "goals",
    "identity",
    "reflections",
)
_NORMALIZE = re.compile(r"[^0-9a-z\u3400-\u9fff]+", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class OwnerTruthOrganizationQualityCaseResult:
    case_id: str
    split: str
    category: str
    candidate_count: int
    supported_candidate_count: int
    evidence_claim_count: int
    supported_evidence_claim_count: int
    anchor_recalled: bool
    expected_candidate: bool = True
    unexpected_candidate_count: int = 0
    provider_error: bool = False
    provider_error_code: str = ""

    @property
    def expectation_met(self) -> bool:
        if self.expected_candidate:
            return self.anchor_recalled
        return self.unexpected_candidate_count == 0

    def value_free_descriptor(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "split": self.split,
            "category": self.category,
            "candidateCount": self.candidate_count,
            "supportedCandidateCount": self.supported_candidate_count,
            "evidenceClaimCount": self.evidence_claim_count,
            "supportedEvidenceClaimCount": self.supported_evidence_claim_count,
            "anchorRecalled": self.anchor_recalled,
            "expectedCandidate": self.expected_candidate,
            "unexpectedCandidateCount": self.unexpected_candidate_count,
            "expectationMet": self.expectation_met,
            "providerError": self.provider_error,
            "providerErrorCode": self.provider_error_code,
        }


def score_organization_quality_case(
    *,
    case: OwnerTruthMemorySearchQualityCase,
    organization: Mapping[str, object],
) -> OwnerTruthOrganizationQualityCaseResult:
    memories = organization.get("memories")
    if not isinstance(memories, list):
        memories = []
    source = _normalized(case.fact_text)
    source_numbers = set(_NUMBER.findall(case.fact_text))
    candidate_count = 0
    supported_candidate_count = 0
    evidence_claim_count = 0
    supported_evidence_claim_count = 0

    for memory in memories:
        if not isinstance(memory, Mapping):
            continue
        content = memory.get("content")
        if not isinstance(content, Mapping):
            continue
        kind = str(memory.get("memoryKind") or "")
        primary = next(
            (
                str(content.get(field) or "").strip()
                for field in _PRIMARY_FIELDS.get(kind, ())
                if str(content.get(field) or "").strip()
            ),
            "",
        )
        if not primary:
            continue
        candidate_count += 1
        evidence_claim_count += 1
        primary_supported = _statement_is_supported(
            source_text=source,
            source_numbers=source_numbers,
            statement=primary,
        )
        if primary_supported:
            supported_evidence_claim_count += 1

        facets = content.get("facets")
        facet_supported = True
        if isinstance(facets, Mapping):
            for facet_name in _FACET_NAMES:
                entries = facets.get(facet_name)
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        continue
                    if str(entry.get("evidenceMode") or "") != "ownerStated":
                        continue
                    value = str(entry.get("value") or "").strip()
                    if not value:
                        continue
                    evidence_claim_count += 1
                    supported = _normalized(value) in source
                    facet_supported = facet_supported and supported
                    if supported:
                        supported_evidence_claim_count += 1
        if primary_supported and facet_supported:
            supported_candidate_count += 1

    encoded = json.dumps(memories, ensure_ascii=False, sort_keys=True)
    expected_candidate = case.category != "questionAndHypothesis"
    return OwnerTruthOrganizationQualityCaseResult(
        case_id=case.case_id,
        split=case.split,
        category=case.category,
        candidate_count=candidate_count,
        supported_candidate_count=supported_candidate_count,
        evidence_claim_count=evidence_claim_count,
        supported_evidence_claim_count=supported_evidence_claim_count,
        anchor_recalled=case.anchor in encoded,
        expected_candidate=expected_candidate,
        unexpected_candidate_count=0 if expected_candidate else candidate_count,
    )


def organization_quality_summary(
    results: Sequence[OwnerTruthOrganizationQualityCaseResult],
    *,
    expected_case_count: int,
    model_id: str,
    prompt_version: str,
    required_splits: Sequence[str] = ("dev", "holdout"),
) -> dict[str, object]:
    normalized = tuple(results)
    overall_metrics = _organization_scope_metrics(normalized)
    split_metrics = {
        split: _organization_scope_metrics(
            tuple(item for item in normalized if item.split == split)
        )
        for split in sorted(set(required_splits).union(item.split for item in normalized))
    }
    category_metrics = {
        category: _organization_scope_metrics(
            tuple(item for item in normalized if item.category == category)
        )
        for category in sorted({item.category for item in normalized})
    }
    positive_results = tuple(item for item in normalized if item.expected_candidate)
    negative_results = tuple(item for item in normalized if not item.expected_candidate)
    missed = tuple(item.case_id for item in positive_results if not item.anchor_recalled)
    false_admissions = tuple(
        item.case_id for item in negative_results if item.unexpected_candidate_count > 0
    )
    provider_errors = tuple(item.case_id for item in normalized if item.provider_error)
    provider_error_code_counts: dict[str, int] = {}
    for item in normalized:
        if not item.provider_error:
            continue
        code = item.provider_error_code or "unclassified"
        provider_error_code_counts[code] = provider_error_code_counts.get(code, 0) + 1
    complete = len(normalized) == expected_case_count
    required_scope_metrics = (overall_metrics,) + tuple(
        split_metrics[split] for split in required_splits
    )
    passed = (
        complete
        and all(bool(metrics["passed"]) for metrics in required_scope_metrics)
    )
    return {
        "schemaVersion": OWNER_TRUTH_ORGANIZATION_QUALITY_SCHEMA_VERSION,
        "executionMode": "realProviderSyntheticInput",
        "modelId": model_id,
        "promptVersion": prompt_version,
        "expectedCaseCount": expected_case_count,
        "completedCaseCount": len(normalized),
        "expectedCandidateCaseCount": len(positive_results),
        "expectedNoCandidateCaseCount": len(negative_results),
        "candidateCount": overall_metrics["candidateCount"],
        "supportedCandidateCount": overall_metrics["supportedCandidateCount"],
        "evidenceClaimCount": overall_metrics["evidenceClaimCount"],
        "supportedEvidenceClaimCount": overall_metrics[
            "supportedEvidenceClaimCount"
        ],
        "metrics": {
            key: overall_metrics[key]
            for key in (
                "candidatePrecision",
                "evidenceSupportAccuracy",
                "missRate",
                "falseAdmissionRate",
            )
        },
        "splitMetrics": split_metrics,
        "categoryMetrics": category_metrics,
        "acceptance": {
            "candidatePrecisionMinimum": OWNER_TRUTH_ORGANIZATION_MIN_CANDIDATE_PRECISION,
            "evidenceSupportAccuracyMinimum": OWNER_TRUTH_ORGANIZATION_MIN_EVIDENCE_ACCURACY,
            "missRateMaximum": OWNER_TRUTH_ORGANIZATION_MAX_MISS_RATE,
            "falseAdmissionRateMaximum": (
                OWNER_TRUTH_ORGANIZATION_MAX_FALSE_ADMISSION_RATE
            ),
            "requiredScopes": ["overall", *required_splits],
            "passed": passed,
        },
        "failedAnchorCaseIds": list(missed),
        "falseAdmissionCaseIds": list(false_admissions),
        "providerErrorCaseIds": list(provider_errors),
        "providerErrorCodeCounts": provider_error_code_counts,
        "responseContentRetained": False,
        "privateVaultRead": False,
        "status": "passed" if passed else ("incomplete" if not complete else "failed"),
    }


def _organization_scope_metrics(
    results: Sequence[OwnerTruthOrganizationQualityCaseResult],
) -> dict[str, int | float | bool]:
    normalized = tuple(results)
    candidate_count = sum(item.candidate_count for item in normalized)
    supported_candidates = sum(item.supported_candidate_count for item in normalized)
    evidence_claim_count = sum(item.evidence_claim_count for item in normalized)
    supported_evidence = sum(
        item.supported_evidence_claim_count for item in normalized
    )
    positive_results = tuple(item for item in normalized if item.expected_candidate)
    negative_results = tuple(item for item in normalized if not item.expected_candidate)
    missed_count = sum(1 for item in positive_results if not item.anchor_recalled)
    false_admission_count = sum(
        1 for item in negative_results if item.unexpected_candidate_count > 0
    )
    provider_error_count = sum(1 for item in normalized if item.provider_error)
    candidate_precision = (
        _ratio(supported_candidates, candidate_count) if candidate_count else 1.0
    )
    evidence_accuracy = (
        _ratio(supported_evidence, evidence_claim_count)
        if evidence_claim_count
        else 1.0
    )
    miss_rate = _ratio(missed_count, len(positive_results)) if positive_results else 0.0
    false_admission_rate = (
        _ratio(false_admission_count, len(negative_results))
        if negative_results
        else 0.0
    )
    passed = (
        bool(normalized)
        and provider_error_count == 0
        and candidate_precision >= OWNER_TRUTH_ORGANIZATION_MIN_CANDIDATE_PRECISION
        and evidence_accuracy >= OWNER_TRUTH_ORGANIZATION_MIN_EVIDENCE_ACCURACY
        and miss_rate <= OWNER_TRUTH_ORGANIZATION_MAX_MISS_RATE
        and false_admission_rate
        <= OWNER_TRUTH_ORGANIZATION_MAX_FALSE_ADMISSION_RATE
    )
    return {
        "caseCount": len(normalized),
        "expectedCandidateCaseCount": len(positive_results),
        "expectedNoCandidateCaseCount": len(negative_results),
        "candidateCount": candidate_count,
        "supportedCandidateCount": supported_candidates,
        "evidenceClaimCount": evidence_claim_count,
        "supportedEvidenceClaimCount": supported_evidence,
        "providerErrorCount": provider_error_count,
        "missedCandidateCaseCount": missed_count,
        "falseAdmissionCaseCount": false_admission_count,
        "candidatePrecision": candidate_precision,
        "evidenceSupportAccuracy": evidence_accuracy,
        "missRate": miss_rate,
        "falseAdmissionRate": false_admission_rate,
        "passed": passed,
    }


def provider_error_case_result(
    case: OwnerTruthMemorySearchQualityCase,
    *,
    code: str = "unclassified",
) -> OwnerTruthOrganizationQualityCaseResult:
    return OwnerTruthOrganizationQualityCaseResult(
        case_id=case.case_id,
        split=case.split,
        category=case.category,
        candidate_count=0,
        supported_candidate_count=0,
        evidence_claim_count=0,
        supported_evidence_claim_count=0,
        anchor_recalled=False,
        expected_candidate=case.category != "questionAndHypothesis",
        unexpected_candidate_count=0,
        provider_error=True,
        provider_error_code=code,
    )


def _statement_is_supported(
    *,
    source_text: str,
    source_numbers: set[str],
    statement: str,
) -> bool:
    normalized = _normalized(statement)
    if not normalized:
        return False
    if not set(_NUMBER.findall(statement)).issubset(source_numbers):
        return False
    if normalized in source_text or source_text in normalized:
        return True
    statement_bigrams = _bigrams(normalized)
    if not statement_bigrams:
        return False
    overlap = len(statement_bigrams.intersection(_bigrams(source_text)))
    return overlap / len(statement_bigrams) >= 0.55


def _normalized(value: str) -> str:
    return _NORMALIZE.sub("", str(value or "").casefold())


def _bigrams(value: str) -> set[str]:
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


__all__ = [
    "OWNER_TRUTH_ORGANIZATION_QUALITY_SCHEMA_VERSION",
    "OWNER_TRUTH_ORGANIZATION_MAX_FALSE_ADMISSION_RATE",
    "OwnerTruthOrganizationQualityCaseResult",
    "organization_quality_summary",
    "provider_error_case_result",
    "score_organization_quality_case",
]
