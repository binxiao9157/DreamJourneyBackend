"""Deterministic Chinese quality corpus for formal-memory fact comparison.

The checked-in fixture defines twenty comparison families and ten synthetic
variants.  Expanding their Cartesian product produces 200 auditable cases
without copying private Vault content into test evidence.  This gate measures
the write-time authority itself; it is separate from model extraction and
retrieval quality so a pass in one layer cannot conceal a failure in another.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid5

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
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    enrich_memory_payload_v5,
)


OWNER_TRUTH_MEMORY_CHANGESET_QUALITY_CORPUS_SCHEMA_VERSION = (
    "owner-truth-memory-changeset-quality-corpus-v1"
)
OWNER_TRUTH_MEMORY_CHANGESET_QUALITY_RESULT_SCHEMA_VERSION = (
    "owner-truth-memory-changeset-quality-result-v1"
)
OWNER_TRUTH_MEMORY_CHANGESET_MIN_ACCURACY = 0.95
_QUALITY_NAMESPACE = UUID("b8057ca0-a191-4933-bf4f-a6e34eab1090")
_MERGING_OPERATIONS = frozenset(
    {
        OwnerTruthMemoryChangeOperationKind.ADD_EVIDENCE.value,
        OwnerTruthMemoryChangeOperationKind.REFINE.value,
        OwnerTruthMemoryChangeOperationKind.CORRECT.value,
        OwnerTruthMemoryChangeOperationKind.DUPLICATE.value,
    }
)
_NON_MERGING_EXPECTATIONS = frozenset(
    {
        OwnerTruthMemoryChangeOperationKind.ADD.value,
        OwnerTruthMemoryChangeOperationKind.TEMPORAL_CHANGE.value,
        OwnerTruthMemoryChangeOperationKind.DISPUTE.value,
        OwnerTruthMemoryChangeOperationKind.NO_PERSONAL_FACT.value,
    }
)
_TEMPORAL_CATEGORIES = frozenset(
    {
        "disjointHistoricalPeriods",
        "historicalToCurrentOpposite",
        "overlappingOpposite",
        "sameScopeOpposite",
        "unknownTimeOpposite",
        "uncertainTimeOpposite",
        "differentScenario",
        "differentPlace",
    }
)


class OwnerTruthMemoryChangeSetQualityError(ValueError):
    """The synthetic comparison corpus is malformed or incomplete."""


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetQualityCase:
    case_id: str
    split: str
    category: str
    expected_operation: str
    candidate: OwnerTruthCandidateSnapshot
    current_memories: tuple[OwnerTruthCurrentFormalMemory, ...]


@dataclass(frozen=True)
class OwnerTruthMemoryChangeSetQualityObservation:
    case_id: str
    split: str
    category: str
    expected_operation: str
    actual_operation: str

    @property
    def passed(self) -> bool:
        return self.expected_operation == self.actual_operation


def load_owner_truth_memory_changeset_quality_corpus(
    path: str | Path,
) -> tuple[OwnerTruthMemoryChangeSetQualityCase, ...]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OwnerTruthMemoryChangeSetQualityError(
            "changeset quality corpus is unreadable"
        ) from error
    if not isinstance(payload, Mapping):
        raise OwnerTruthMemoryChangeSetQualityError(
            "changeset quality corpus root must be an object"
        )
    if (
        payload.get("schemaVersion")
        != OWNER_TRUTH_MEMORY_CHANGESET_QUALITY_CORPUS_SCHEMA_VERSION
        or payload.get("syntheticOnly") is not True
    ):
        raise OwnerTruthMemoryChangeSetQualityError(
            "changeset quality corpus metadata is invalid"
        )
    variants = payload.get("variants")
    families = payload.get("families")
    if not isinstance(variants, list) or len(variants) != 10:
        raise OwnerTruthMemoryChangeSetQualityError(
            "changeset quality corpus requires ten variants"
        )
    if not isinstance(families, list) or len(families) != 20:
        raise OwnerTruthMemoryChangeSetQualityError(
            "changeset quality corpus requires twenty families"
        )
    categories = [
        str(family.get("category") or "")
        for family in families
        if isinstance(family, Mapping)
    ]
    if len(categories) != len(families) or len(set(categories)) != len(families):
        raise OwnerTruthMemoryChangeSetQualityError(
            "changeset quality categories must be unique"
        )

    cases: list[OwnerTruthMemoryChangeSetQualityCase] = []
    for family_index, family in enumerate(families):
        if not isinstance(family, Mapping):
            raise OwnerTruthMemoryChangeSetQualityError(
                "changeset quality family must be an object"
            )
        category = _required_text(family.get("category"), field="category")
        expected = _required_text(
            family.get("expectedOperation"),
            field="expectedOperation",
        )
        try:
            OwnerTruthMemoryChangeOperationKind(expected)
        except ValueError as error:
            raise OwnerTruthMemoryChangeSetQualityError(
                "changeset quality expectation is unsupported"
            ) from error
        for variant_index, raw_variant in enumerate(variants):
            if not isinstance(raw_variant, Mapping):
                raise OwnerTruthMemoryChangeSetQualityError(
                    "changeset quality variant must be an object"
                )
            case_number = family_index * len(variants) + variant_index + 1
            cases.append(
                _build_case(
                    case_id=f"C{case_number:03d}",
                    split="dev" if variant_index < 7 else "holdout",
                    category=category,
                    expected_operation=expected,
                    variant=raw_variant,
                )
            )
    if len(cases) != 200 or len({case.case_id for case in cases}) != 200:
        raise OwnerTruthMemoryChangeSetQualityError(
            "changeset quality corpus must expand to 200 unique cases"
        )
    return tuple(cases)


def evaluate_owner_truth_memory_changeset_quality(
    cases: Sequence[OwnerTruthMemoryChangeSetQualityCase],
) -> dict[str, object]:
    observations = tuple(_observe(case) for case in cases)
    failed = tuple(item.case_id for item in observations if not item.passed)
    false_merges = tuple(
        item.case_id
        for item in observations
        if item.expected_operation in _NON_MERGING_EXPECTATIONS
        and item.actual_operation in _MERGING_OPERATIONS
    )
    false_conflicts = tuple(
        item.case_id
        for item in observations
        if item.expected_operation != OwnerTruthMemoryChangeOperationKind.DISPUTE.value
        and item.actual_operation == OwnerTruthMemoryChangeOperationKind.DISPUTE.value
    )
    temporal = tuple(
        item for item in observations if item.category in _TEMPORAL_CATEGORIES
    )
    split_metrics = {
        split: _metrics(tuple(item for item in observations if item.split == split))
        for split in ("dev", "holdout")
    }
    category_metrics = {
        category: _metrics(
            tuple(item for item in observations if item.category == category)
        )
        for category in sorted({item.category for item in observations})
    }
    overall = _metrics(observations)
    temporal_metrics = _metrics(temporal)
    passed = (
        len(observations) == 200
        and overall["accuracy"] >= OWNER_TRUTH_MEMORY_CHANGESET_MIN_ACCURACY
        and all(
            metrics["accuracy"] >= OWNER_TRUTH_MEMORY_CHANGESET_MIN_ACCURACY
            for metrics in split_metrics.values()
        )
        and temporal_metrics["accuracy"] >= OWNER_TRUTH_MEMORY_CHANGESET_MIN_ACCURACY
        and not false_merges
    )
    return {
        "schemaVersion": OWNER_TRUTH_MEMORY_CHANGESET_QUALITY_RESULT_SCHEMA_VERSION,
        "executionMode": "deterministicSyntheticComparison",
        "caseCount": len(observations),
        "overallMetrics": overall,
        "splitMetrics": split_metrics,
        "categoryMetrics": category_metrics,
        "temporalMetrics": temporal_metrics,
        "failedCaseCount": len(failed),
        "failedCaseIds": list(failed),
        "falseMergeCount": len(false_merges),
        "falseMergeCaseIds": list(false_merges),
        "falseConflictCount": len(false_conflicts),
        "falseConflictCaseIds": list(false_conflicts),
        "acceptance": {
            "minimumAccuracy": OWNER_TRUTH_MEMORY_CHANGESET_MIN_ACCURACY,
            "requiredScopes": ["overall", "dev", "holdout", "temporal"],
            "falseMergeMaximum": 0,
            "passed": passed,
        },
        "syntheticOnly": True,
        "privateVaultRead": False,
        "status": "passed" if passed else "failed",
    }


def _observe(
    case: OwnerTruthMemoryChangeSetQualityCase,
) -> OwnerTruthMemoryChangeSetQualityObservation:
    actual = build_memory_changeset(
        candidate=case.candidate,
        current_memories=case.current_memories,
        base_memory_revision=7,
    ).operation.kind.value
    return OwnerTruthMemoryChangeSetQualityObservation(
        case_id=case.case_id,
        split=case.split,
        category=case.category,
        expected_operation=case.expected_operation,
        actual_operation=actual,
    )


def _metrics(
    values: Sequence[OwnerTruthMemoryChangeSetQualityObservation],
) -> dict[str, int | float]:
    passed = sum(item.passed for item in values)
    return {
        "caseCount": len(values),
        "passedCount": passed,
        "accuracy": float(passed) / float(len(values)) if values else 0.0,
    }


def _build_case(
    *,
    case_id: str,
    split: str,
    category: str,
    expected_operation: str,
    variant: Mapping[str, Any],
) -> OwnerTruthMemoryChangeSetQualityCase:
    dish = _required_text(variant.get("dish"), field="dish")
    place_a = _required_text(variant.get("placeA"), field="placeA")
    place_b = _required_text(variant.get("placeB"), field="placeB")
    subject_a = _required_text(variant.get("subjectA"), field="subjectA")
    subject_b = _required_text(variant.get("subjectB"), field="subjectB")
    year_a = _required_year(variant.get("yearA"), field="yearA")
    year_b = _required_year(variant.get("yearB"), field="yearB")
    current_source_id = _stable_uuid(f"{case_id}:source:current")
    candidate_source_id = (
        current_source_id
        if category in {"exactDuplicate", "weakerRestatementSameEvidence"}
        else _stable_uuid(f"{case_id}:source:candidate")
    )
    memory_id = _stable_uuid(f"{case_id}:memory")
    memory_version_id = _stable_uuid(f"{case_id}:memory-version")
    current_content = _preference_content(
        dish=dish,
        place=place_a,
        subject=subject_a,
        statement=f"我在{place_a}生活时喜欢吃{dish}。",
        start=str(year_a),
        end=str(year_a),
        expression=f"{year_a}年",
    )
    candidate_options: dict[str, Any] = {
        "dish": dish,
        "place": place_a,
        "subject": subject_a,
        "statement": f"我在{place_a}生活时喜欢吃{dish}。",
        "start": str(year_a),
        "end": str(year_a),
        "expression": f"{year_a}年",
    }
    review_mode = "single"
    correction_target: str | None = None

    if category == "disjointHistoricalPeriods":
        candidate_options.update(
            start=str(year_b), end=str(year_b), expression=f"{year_b}年"
        )
    elif category == "historicalToCurrentOpposite":
        candidate_options.update(
            statement=f"现在我不喜欢吃{dish}。",
            polarity="negative",
            current_applicability="current",
            start=None,
            end=None,
            expression=None,
        )
    elif category == "overlappingOpposite":
        current_content = _preference_content(
            dish=dish,
            place=place_a,
            subject=subject_a,
            statement=f"我在{year_a}年至{year_a + 2}年喜欢吃{dish}。",
            start=str(year_a),
            end=str(year_a + 2),
            expression=f"{year_a}年至{year_a + 2}年",
        )
        candidate_options.update(
            statement=f"我在{year_a + 1}年至{year_a + 3}年不喜欢吃{dish}。",
            polarity="negative",
            start=str(year_a + 1),
            end=str(year_a + 3),
            expression=f"{year_a + 1}年至{year_a + 3}年",
        )
    elif category == "sameScopeOpposite":
        candidate_options.update(
            statement=f"我在{place_a}生活时不喜欢吃{dish}。",
            polarity="negative",
        )
    elif category == "unknownTimeOpposite":
        current_content = _preference_content(
            dish=dish,
            place=place_a,
            subject=subject_a,
            statement=f"我喜欢吃{dish}。",
            current_applicability="unknown",
            start=None,
            end=None,
            expression=None,
        )
        candidate_options.update(
            statement=f"我不喜欢吃{dish}。",
            polarity="negative",
            current_applicability="unknown",
            start=None,
            end=None,
            expression=None,
        )
    elif category == "differentSubject":
        candidate_options.update(subject=subject_b, statement=f"家人喜欢吃{dish}。")
    elif category == "differentObject":
        candidate_options.update(dish=f"{dish}配菜", statement=f"我喜欢吃{dish}配菜。")
    elif category == "personalQuestion":
        candidate_options.update(statement=f"我是不是喜欢吃{dish}？")
    elif category == "explicitCorrection":
        candidate_options.update(statement=f"更正：我不喜欢吃{dish}。", polarity="negative")
        review_mode = "correction"
        correction_target = memory_version_id
    elif category == "additiveRefinement":
        candidate_options.update(domains=["饮食", "家庭饮食"])
    elif category in {"weakerRestatementSameEvidence", "weakerRestatementNewEvidence"}:
        current_content = _preference_content(
            dish=dish,
            place=place_a,
            subject=subject_a,
            statement=f"我最喜欢吃{dish}。",
            start=str(year_a),
            end=str(year_a),
            expression=f"{year_a}年",
            superlative=True,
        )
        candidate_options.update(statement=f"我喜欢吃{dish}。", superlative=False)
    elif category == "destructiveReplacement":
        current_content = _preference_content(
            dish=dish,
            place=place_a,
            subject=subject_a,
            statement=f"我在{place_a}生活时喜欢吃{dish}。",
            start=str(year_a),
            end=str(year_a),
            expression=f"{year_a}年",
            domains=["饮食", "家庭饮食"],
        )
        candidate_options.update(domains=["旅行"])
    elif category == "uncertainTimeOpposite":
        current_content = _preference_content(
            dish=dish,
            place=place_a,
            subject=subject_a,
            statement=f"我小学阶段喜欢吃{dish}。",
            start=None,
            end=None,
            expression="小学阶段",
            precision="approximate",
        )
        candidate_options.update(
            statement=f"我工作初期不喜欢吃{dish}。",
            polarity="negative",
            start=None,
            end=None,
            expression="工作初期",
            precision="approximate",
        )
    elif category == "differentScenario":
        candidate_options.update(scenario="晚餐")
    elif category == "differentPlace":
        candidate_options.update(place=place_b)
    elif category == "differentFactType":
        candidate_options.update(
            fact_type="habit",
            predicate="practices",
            statement=f"我习惯在早餐吃{dish}。",
        )
    elif category == "explicitCorrectionDifferentObject":
        candidate_options.update(
            dish=f"{dish}配菜",
            statement=f"更正：我喜欢的是{dish}配菜。",
        )
        review_mode = "correction"
        correction_target = memory_version_id

    candidate_content = _preference_content(**candidate_options)
    candidate = _candidate(
        case_id=case_id,
        source_id=candidate_source_id,
        content=candidate_content,
        review_mode=review_mode,
        correction_target=correction_target,
    )
    current = OwnerTruthCurrentFormalMemory(
        memory_id=memory_id,
        memory_version_id=memory_version_id,
        vault_id="vault-memory-changeset-quality",
        owner_subject_id="subject-memory-changeset-quality",
        version_number=1,
        memory_kind=MemoryKind.KNOWLEDGE,
        content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        content=current_content,
        evidence_refs=(
            {"sourceId": current_source_id, "sourceVersion": 1, "span": {"start": 0, "end": 12}},
        ),
    )
    return OwnerTruthMemoryChangeSetQualityCase(
        case_id=case_id,
        split=split,
        category=category,
        expected_operation=expected_operation,
        candidate=candidate,
        current_memories=(current,),
    )


def _preference_content(
    *,
    dish: str,
    place: str,
    subject: str,
    statement: str,
    polarity: str = "positive",
    current_applicability: str = "historical",
    start: str | None,
    end: str | None,
    expression: str | None,
    precision: str = "year",
    scenario: str = "用餐",
    domains: Sequence[str] = ("饮食",),
    superlative: bool = False,
    fact_type: str = "preference",
    predicate: str = "prefers",
) -> dict[str, Any]:
    return enrich_memory_payload_v5(
        kind=MemoryKind.KNOWLEDGE,
        payload={
            "statement": statement,
            "knowledgeType": "personal_experience",
            "domains": list(domains),
            "applicability": None,
            "exceptions": [],
            "learnedFrom": None,
            "factType": fact_type,
            "predicate": predicate,
            "object": {"label": dish, "category": "dish"},
            "qualifiers": {
                "polarity": polarity,
                "strengthExpression": "最喜欢" if superlative else None,
                "superlativeAsserted": superlative,
                "currentApplicability": current_applicability,
                "validTime": {
                    "start": start,
                    "end": end,
                    "precision": precision if expression else "unknown",
                    "expression": expression,
                },
                "place": {"label": place, "category": "city"},
                "scenario": scenario,
            },
        },
        provenance={"mode": "selfReport"},
        memory_subject_id=subject,
        claim_subject_id=subject,
    )


def _candidate(
    *,
    case_id: str,
    source_id: str,
    content: Mapping[str, Any],
    review_mode: str,
    correction_target: str | None,
) -> OwnerTruthCandidateSnapshot:
    payload = {
        "content": dict(content),
        "contentSchemaVersion": OWNER_TRUTH_SCHEMA_VERSION_V5,
        "evidenceRefs": [
            {"sourceId": source_id, "sourceVersion": 1, "span": {"start": 0, "end": 12}}
        ],
        "reviewMode": review_mode,
        "schemaVersion": "owner-truth-candidate-proposal-v1",
    }
    if correction_target is not None:
        payload["correctionOfMemoryVersionId"] = correction_target
    return OwnerTruthCandidateSnapshot(
        candidate_id=_stable_uuid(f"{case_id}:candidate"),
        vault_id="vault-memory-changeset-quality",
        owner_subject_id="subject-memory-changeset-quality",
        source_id=source_id,
        memory_kind=MemoryKind.KNOWLEDGE,
        perspective_type=PerspectiveType.FIRST_PERSON,
        epistemic_status=EpistemicStatus.RECALLED,
        sensitivity=SensitivityLevel.STANDARD,
        decision=CandidateDecision.PENDING,
        policy_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        authority_epoch=0,
        row_version=1,
        content_hash=_digest(content),
        content_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        payload=payload,
    )


def _required_text(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthMemoryChangeSetQualityError(
            f"changeset quality {field} is required"
        )
    return normalized


def _required_year(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise OwnerTruthMemoryChangeSetQualityError(
            f"changeset quality {field} is invalid"
        )
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise OwnerTruthMemoryChangeSetQualityError(
            f"changeset quality {field} is invalid"
        ) from error
    if normalized < 1900 or normalized > 2100:
        raise OwnerTruthMemoryChangeSetQualityError(
            f"changeset quality {field} is outside the synthetic range"
        )
    return normalized


def _stable_uuid(value: str) -> str:
    return str(uuid5(_QUALITY_NAMESPACE, value))


def _digest(value: Mapping[str, Any]) -> str:
    return sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "OWNER_TRUTH_MEMORY_CHANGESET_QUALITY_CORPUS_SCHEMA_VERSION",
    "OWNER_TRUTH_MEMORY_CHANGESET_QUALITY_RESULT_SCHEMA_VERSION",
    "OWNER_TRUTH_MEMORY_CHANGESET_MIN_ACCURACY",
    "OwnerTruthMemoryChangeSetQualityCase",
    "OwnerTruthMemoryChangeSetQualityError",
    "evaluate_owner_truth_memory_changeset_quality",
    "load_owner_truth_memory_changeset_quality_corpus",
]
