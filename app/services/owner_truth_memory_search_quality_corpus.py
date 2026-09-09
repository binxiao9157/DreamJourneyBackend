"""Auditable synthetic quality corpus and real-embedding evaluation helpers.

This module intentionally keeps the quality corpus outside a user vault.  The
corpus contains synthetic Chinese facts only and separates the deterministic
retrieval regressions from a separately approved real-model evaluation.  A
passing fake-provider unit test therefore never becomes evidence that an
embedding provider, pgvector, or a private fact succeeded in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from pathlib import Path
import json
import re
from typing import Mapping, Protocol, Sequence

from app.services.owner_truth_memory_search_hybrid import (
    OwnerTruthEmbeddingModel,
    OwnerTruthMemorySearchHybridUnavailable,
    OwnerTruthQueryEmbedding,
)


OWNER_TRUTH_MEMORY_SEARCH_QUALITY_CORPUS_SCHEMA_VERSION = (
    "owner-truth-memory-search-quality-corpus-v1"
)
OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_SCHEMA_VERSION = (
    "owner-truth-memory-search-quality-evaluation-v1"
)
OWNER_TRUTH_MEMORY_SEARCH_QUALITY_MIN_TOP10_RECALL = 0.95
OWNER_TRUTH_MEMORY_SEARCH_QUALITY_REQUIRED_CATEGORIES = frozenset(
    {
        "school",
        "major",
        "graduationYear",
        "foodPreference",
        "dailyPreference",
        "professionalKnowledge",
        "professionalExperience",
        "historicalEmotion",
        "currentEmotion",
        "familyReport",
        "sameNamedPerson",
        "timeOverlap",
        "timeDisjoint",
        "unknownTime",
        "negationAndDegree",
        "questionAndHypothesis",
        "multiSourceEvidence",
        "correctionAndRevocation",
        "asrVariation",
        "followUpDisambiguation",
    }
)
_CASE_ID = re.compile(r"^Q[0-9]{3}$")
_CHINESE = re.compile(r"[\u3400-\u9fff]")
_PUNCTUATION = re.compile(r"[\s\W_]+", re.UNICODE)


class OwnerTruthMemorySearchQualityCorpusError(ValueError):
    """The checked-in synthetic evaluation corpus is malformed."""


@dataclass(frozen=True)
class OwnerTruthMemorySearchQualityCase:
    """One independent synthetic retrieval scenario.

    ``evaluation_mode`` documents which locally executable boundary provides
    evidence today.  ``semantic`` cases are deliberately not counted as a
    model pass until ``evaluate_embedding_quality_corpus`` runs against an
    explicitly approved real embedding provider.
    """

    case_id: str
    split: str
    category: str
    evaluation_mode: str
    fact_text: str
    query: str
    anchor: str
    distractor_text: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "OwnerTruthMemorySearchQualityCase":
        if not isinstance(value, Mapping):
            raise OwnerTruthMemorySearchQualityCorpusError("quality case must be an object")
        fields = {
            "case_id": value.get("caseId"),
            "split": value.get("split"),
            "category": value.get("category"),
            "evaluation_mode": value.get("evaluationMode"),
            "fact_text": value.get("factText"),
            "query": value.get("query"),
            "anchor": value.get("anchor"),
            "distractor_text": value.get("distractorText"),
        }
        normalized = {
            name: _required_text(raw, field=name) for name, raw in fields.items()
        }
        if _CASE_ID.fullmatch(normalized["case_id"]) is None:
            raise OwnerTruthMemorySearchQualityCorpusError("quality case_id is invalid")
        if normalized["split"] not in {"dev", "holdout"}:
            raise OwnerTruthMemorySearchQualityCorpusError("quality split is unsupported")
        if normalized["category"] not in OWNER_TRUTH_MEMORY_SEARCH_QUALITY_REQUIRED_CATEGORIES:
            raise OwnerTruthMemorySearchQualityCorpusError("quality category is unsupported")
        if normalized["evaluation_mode"] not in {"lexical", "semantic"}:
            raise OwnerTruthMemorySearchQualityCorpusError("quality evaluation_mode is unsupported")
        for field in ("fact_text", "query", "distractor_text"):
            if _CHINESE.search(normalized[field]) is None:
                raise OwnerTruthMemorySearchQualityCorpusError(
                    f"quality {field} must contain Chinese text"
                )
        if normalized["anchor"] not in normalized["fact_text"]:
            raise OwnerTruthMemorySearchQualityCorpusError(
                "quality anchor must occur in the supported fact"
            )
        if normalized["anchor"] in normalized["distractor_text"]:
            raise OwnerTruthMemorySearchQualityCorpusError(
                "quality distractor must not contain the gold anchor"
            )
        if _semantic_key(normalized["fact_text"]) == _semantic_key(normalized["query"]):
            raise OwnerTruthMemorySearchQualityCorpusError(
                "quality query must not copy the supported fact verbatim"
            )
        if normalized["evaluation_mode"] == "lexical" and normalized["anchor"] not in normalized["query"]:
            raise OwnerTruthMemorySearchQualityCorpusError(
                "lexical quality query must contain its exact anchor"
            )
        return cls(**normalized)


@dataclass(frozen=True)
class OwnerTruthMemorySearchEmbeddingQualityResult:
    """Value-free result of a real-provider synthetic corpus run."""

    model: OwnerTruthEmbeddingModel
    case_count: int
    overall_metrics: Mapping[str, int | float]
    split_metrics: Mapping[str, Mapping[str, int | float]]
    category_metrics: Mapping[str, Mapping[str, int | float]]
    failed_top10_case_ids: tuple[str, ...]

    @property
    def acceptance_passed(self) -> bool:
        required = (
            self.overall_metrics,
            self.split_metrics.get("dev", {}),
            self.split_metrics.get("holdout", {}),
        )
        return all(
            float(metrics.get("top10Recall", 0.0))
            >= OWNER_TRUTH_MEMORY_SEARCH_QUALITY_MIN_TOP10_RECALL
            for metrics in required
        )

    def value_free_summary(self) -> dict[str, object]:
        return {
            "schemaVersion": OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_SCHEMA_VERSION,
            "model": {
                "modelId": self.model.model_id,
                "modelVersion": self.model.model_version,
                "dimensions": self.model.dimensions,
            },
            "caseCount": self.case_count,
            "overallMetrics": dict(self.overall_metrics),
            "splitMetrics": {
                split: dict(metrics) for split, metrics in sorted(self.split_metrics.items())
            },
            "categoryMetrics": {
                category: dict(metrics)
                for category, metrics in sorted(self.category_metrics.items())
            },
            "acceptance": {
                "passed": self.acceptance_passed,
                "metric": "top10Recall",
                "minimum": OWNER_TRUTH_MEMORY_SEARCH_QUALITY_MIN_TOP10_RECALL,
                "requiredScopes": ["overall", "dev", "holdout"],
            },
            "failedTop10CaseCount": len(self.failed_top10_case_ids),
            "failedTop10CaseIds": list(self.failed_top10_case_ids),
        }


class OwnerTruthMemorySearchEmbeddingQualityProvider(Protocol):
    """Minimal real-provider contract used only for synthetic evaluation."""

    @property
    def model(self) -> OwnerTruthEmbeddingModel:
        ...

    def embed_documents(self, *, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        ...

    def embed_query(self, *, query: str) -> OwnerTruthQueryEmbedding:
        ...


def load_owner_truth_memory_search_quality_corpus(
    path: str | Path,
) -> tuple[OwnerTruthMemorySearchQualityCase, ...]:
    """Load and validate the versioned checked-in synthetic corpus."""

    corpus_path = Path(path)
    try:
        payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OwnerTruthMemorySearchQualityCorpusError(
            "quality corpus is unreadable"
        ) from error
    if not isinstance(payload, Mapping):
        raise OwnerTruthMemorySearchQualityCorpusError("quality corpus root must be an object")
    if payload.get("schemaVersion") != OWNER_TRUTH_MEMORY_SEARCH_QUALITY_CORPUS_SCHEMA_VERSION:
        raise OwnerTruthMemorySearchQualityCorpusError("quality corpus schema version is unsupported")
    if payload.get("syntheticOnly") is not True:
        raise OwnerTruthMemorySearchQualityCorpusError("quality corpus must be synthetic-only")
    rows = payload.get("cases")
    if not isinstance(rows, list):
        raise OwnerTruthMemorySearchQualityCorpusError("quality corpus cases must be an array")
    cases = tuple(OwnerTruthMemorySearchQualityCase.from_mapping(row) for row in rows)
    _validate_quality_corpus(cases)
    return cases


def evaluate_embedding_quality_corpus(
    *,
    provider: OwnerTruthMemorySearchEmbeddingQualityProvider,
    cases: Sequence[OwnerTruthMemorySearchQualityCase],
    batch_size: int = 16,
) -> OwnerTruthMemorySearchEmbeddingQualityResult:
    """Evaluate only synthetic facts with an approved embedding provider.

    The caller owns privacy approval and cost admission.  This function neither
    reads a Vault nor writes an index, so it cannot be misrepresented as the
    PostgreSQL/pgvector acceptance test required elsewhere.
    """

    normalized_cases = tuple(cases)
    _validate_quality_corpus(normalized_cases)
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
        raise OwnerTruthMemorySearchQualityCorpusError("quality batch_size is invalid")
    model = provider.model
    if not isinstance(model, OwnerTruthEmbeddingModel):
        raise OwnerTruthMemorySearchHybridUnavailable("quality provider has no valid model")
    document_vectors: list[tuple[float, ...]] = []
    for start in range(0, len(normalized_cases), batch_size):
        batch = normalized_cases[start : start + batch_size]
        values = provider.embed_documents(texts=tuple(case.fact_text for case in batch))
        if len(values) != len(batch):
            raise OwnerTruthMemorySearchHybridUnavailable(
                "quality provider document response count does not match request"
            )
        document_vectors.extend(
            OwnerTruthQueryEmbedding(model=model, values=tuple(vector)).values
            for vector in values
        )

    query_embeddings = _embed_quality_queries(
        provider=provider,
        cases=normalized_cases,
        batch_size=batch_size,
        model=model,
    )
    ranks: list[tuple[OwnerTruthMemorySearchQualityCase, int]] = []
    for expected_index, (case, query) in enumerate(
        zip(normalized_cases, query_embeddings)
    ):
        if query.model != model:
            raise OwnerTruthMemorySearchHybridUnavailable(
                "quality provider returned a different query model"
            )
        scores = [
            _cosine_similarity(query.values, document_vector)
            for document_vector in document_vectors
        ]
        order = sorted(
            range(len(normalized_cases)),
            key=lambda index: (-scores[index], normalized_cases[index].case_id),
        )
        ranks.append((case, order.index(expected_index) + 1))
    overall_metrics = _quality_metrics_by(ranks, key=lambda _case: "overall")[
        "overall"
    ]
    return OwnerTruthMemorySearchEmbeddingQualityResult(
        model=model,
        case_count=len(normalized_cases),
        overall_metrics=overall_metrics,
        split_metrics=_quality_metrics_by(ranks, key=lambda case: case.split),
        category_metrics=_quality_metrics_by(ranks, key=lambda case: case.category),
        failed_top10_case_ids=tuple(
            case.case_id for case, rank in ranks if rank > 10
        ),
    )


def _embed_quality_queries(
    *,
    provider: OwnerTruthMemorySearchEmbeddingQualityProvider,
    cases: Sequence[OwnerTruthMemorySearchQualityCase],
    batch_size: int,
    model: OwnerTruthEmbeddingModel,
) -> tuple[OwnerTruthQueryEmbedding, ...]:
    """Prefer a provider batch API while retaining the original protocol."""

    batch_method = getattr(provider, "embed_queries", None)
    if not callable(batch_method):
        return tuple(provider.embed_query(query=case.query) for case in cases)

    values: list[OwnerTruthQueryEmbedding] = []
    for start in range(0, len(cases), batch_size):
        batch = cases[start : start + batch_size]
        embedded = tuple(batch_method(queries=tuple(case.query for case in batch)))
        if len(embedded) != len(batch):
            raise OwnerTruthMemorySearchHybridUnavailable(
                "quality provider query response count does not match request"
            )
        for query in embedded:
            if not isinstance(query, OwnerTruthQueryEmbedding) or query.model != model:
                raise OwnerTruthMemorySearchHybridUnavailable(
                    "quality provider returned an invalid query model"
                )
        values.extend(embedded)
    return tuple(values)


def _quality_metrics_by(
    ranks: Sequence[tuple[OwnerTruthMemorySearchQualityCase, int]],
    *,
    key,
) -> dict[str, dict[str, int | float]]:
    groups: dict[str, list[int]] = {}
    for case, rank in ranks:
        groups.setdefault(str(key(case)), []).append(rank)
    return {
        group: {
            "caseCount": len(values),
            "top1Recall": _ratio(sum(rank <= 1 for rank in values), len(values)),
            "top3Recall": _ratio(sum(rank <= 3 for rank in values), len(values)),
            "top10Recall": _ratio(sum(rank <= 10 for rank in values), len(values)),
            "meanReciprocalRank": round(
                sum(1.0 / rank for rank in values) / len(values), 6
            ),
        }
        for group, values in sorted(groups.items())
    }


def _validate_quality_corpus(cases: Sequence[OwnerTruthMemorySearchQualityCase]) -> None:
    if len(cases) != 200:
        raise OwnerTruthMemorySearchQualityCorpusError(
            "quality corpus must contain exactly 200 independent cases"
        )
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise OwnerTruthMemorySearchQualityCorpusError("quality corpus has duplicate case IDs")
    fact_keys = [_semantic_key(case.fact_text) for case in cases]
    query_keys = [_semantic_key(case.query) for case in cases]
    if len(set(fact_keys)) != len(fact_keys):
        raise OwnerTruthMemorySearchQualityCorpusError("quality corpus has duplicate facts")
    if len(set(query_keys)) != len(query_keys):
        raise OwnerTruthMemorySearchQualityCorpusError("quality corpus has duplicate queries")
    categories = {case.category for case in cases}
    if categories != OWNER_TRUTH_MEMORY_SEARCH_QUALITY_REQUIRED_CATEGORIES:
        raise OwnerTruthMemorySearchQualityCorpusError("quality corpus category coverage is incomplete")
    category_counts = {category: sum(case.category == category for case in cases) for category in categories}
    if any(count != 10 for count in category_counts.values()):
        raise OwnerTruthMemorySearchQualityCorpusError(
            "each quality category must contain exactly 10 independent cases"
        )
    split_counts = {split: sum(case.split == split for case in cases) for split in ("dev", "holdout")}
    if split_counts != {"dev": 140, "holdout": 60}:
        raise OwnerTruthMemorySearchQualityCorpusError(
            "quality corpus must contain 140 dev and 60 holdout cases"
        )
    for category in categories:
        category_splits = {case.split for case in cases if case.category == category}
        if category_splits != {"dev", "holdout"}:
            raise OwnerTruthMemorySearchQualityCorpusError(
                "each quality category must be represented in dev and holdout"
            )
    if not any(case.evaluation_mode == "lexical" for case in cases):
        raise OwnerTruthMemorySearchQualityCorpusError("quality corpus needs lexical regressions")
    if not any(case.evaluation_mode == "semantic" for case in cases):
        raise OwnerTruthMemorySearchQualityCorpusError("quality corpus needs semantic evaluations")


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OwnerTruthMemorySearchQualityCorpusError(f"quality {field} is required")
    return text


def _semantic_key(value: str) -> str:
    return _PUNCTUATION.sub("", value).casefold()


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise OwnerTruthMemorySearchHybridUnavailable("quality embedding dimensions do not match")
    numerator = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = sqrt(sum(float(value) ** 2 for value in left))
    right_norm = sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0 or right_norm == 0:
        raise OwnerTruthMemorySearchHybridUnavailable("quality embedding must not be zero")
    score = numerator / (left_norm * right_norm)
    if not isfinite(score):
        raise OwnerTruthMemorySearchHybridUnavailable("quality embedding score is invalid")
    return score


def _ratio(numerator: int, denominator: int) -> float:
    return round(float(numerator) / float(denominator), 6)


__all__ = [
    "OWNER_TRUTH_MEMORY_SEARCH_QUALITY_CORPUS_SCHEMA_VERSION",
    "OWNER_TRUTH_MEMORY_SEARCH_QUALITY_EVALUATION_SCHEMA_VERSION",
    "OWNER_TRUTH_MEMORY_SEARCH_QUALITY_MIN_TOP10_RECALL",
    "OWNER_TRUTH_MEMORY_SEARCH_QUALITY_REQUIRED_CATEGORIES",
    "OwnerTruthMemorySearchEmbeddingQualityResult",
    "OwnerTruthMemorySearchQualityCase",
    "OwnerTruthMemorySearchQualityCorpusError",
    "evaluate_embedding_quality_corpus",
    "load_owner_truth_memory_search_quality_corpus",
]
