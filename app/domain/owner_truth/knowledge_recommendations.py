"""Deterministic M0-B knowledge coverage and recommendation policy.

This module is intentionally a read-only projection.  It derives stable
knowledge-dimension coverage from admissible confirmed memory evidence and
selects at most one continuity and one breadth recommendation.  It neither
generates natural-language questions nor mutates Source, Candidate,
DecisionReceipt, MemoryVersion, ConversationThread, or provider state.

Keeping the selector content-free is deliberate: a later phrasing layer may
turn an approved ``question_template_id`` plus authorized evidence into a
specific question, but it must not use this projection to smuggle private text
or AI-only inference into an active recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Iterable, Mapping, Optional, Tuple
from uuid import UUID

from .contracts import OwnerTruthContractError, require_nonblank
from .conversation import OwnerTruthConversationThreadAuthoritySnapshot


KNOWLEDGE_DIMENSION_PROJECTION_SCHEMA_VERSION = "owner-truth-dimension-projection-v1"
RECOMMENDATION_SELECTION_SCHEMA_VERSION = "owner-truth-recommendation-selection-v1"
KNOWLEDGE_DIMENSION_POLICY_VERSION = "m0-knowledge-dimension-v1"

_OPAQUE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class KnowledgeRecommendationError(OwnerTruthContractError):
    """A knowledge projection or recommendation request is unsafe or malformed."""


class KnowledgeDimension(str, Enum):
    LIFE_STAGE = "lifeStage"
    IMPORTANT_PEOPLE = "importantPeople"
    KEY_DECISIONS = "keyDecisions"
    PROFESSIONAL_EXPERIENCE = "professionalExperience"
    VALUES = "values"
    ASPIRATIONS_AND_BOUNDARIES = "aspirationsAndBoundaries"


class RecommendationSlot(str, Enum):
    CONTINUITY = "continuity"
    BREADTH = "breadth"


class RecommendationEvidenceKind(str, Enum):
    CONFIRMED_MEMORY = "confirmedMemory"
    SAVED_CONTINUATION = "savedContinuation"
    COLD_START_BLUEPRINT = "coldStartBlueprint"


_DIMENSION_FACETS: Mapping[KnowledgeDimension, Tuple[str, ...]] = {
    KnowledgeDimension.LIFE_STAGE: ("timeContext", "experience"),
    KnowledgeDimension.IMPORTANT_PEOPLE: ("person", "relationshipChange"),
    KnowledgeDimension.KEY_DECISIONS: ("choice", "reason", "outcome"),
    KnowledgeDimension.PROFESSIONAL_EXPERIENCE: ("practice", "judgment"),
    KnowledgeDimension.VALUES: ("priority", "reflection"),
    KnowledgeDimension.ASPIRATIONS_AND_BOUNDARIES: ("aspiration", "boundary"),
}


def knowledge_dimension_facets(dimension: KnowledgeDimension | str) -> Tuple[str, ...]:
    """Return the stable, policy-owned facet order for one knowledge dimension.

    Confirmation writers use this helper to normalize an Owner's explicit
    selection.  Keeping the order in this module prevents an independent
    receipt contract from quietly drifting away from the recommendation policy.
    """

    try:
        normalized = KnowledgeDimension(dimension)
    except (TypeError, ValueError) as exc:
        raise KnowledgeRecommendationError("dimension is not supported") from exc
    return _DIMENSION_FACETS[normalized]


def _opaque_identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if _OPAQUE_IDENTIFIER.fullmatch(normalized):
        return normalized
    # Owner Truth records use UUID primary keys.  The original synthetic-only
    # matcher rejected valid production MemoryVersion and Source identifiers
    # that begin with a numeral, preventing a read-only projection from ever
    # consume real authority records.
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise KnowledgeRecommendationError(f"{field} must be an opaque identifier") from exc


def _identifier_tuple(values: Iterable[object], *, field: str) -> Tuple[str, ...]:
    normalized = tuple(_opaque_identifier(value, field=field) for value in values)
    if not normalized:
        raise KnowledgeRecommendationError(f"{field} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise KnowledgeRecommendationError(f"{field} must not contain duplicates")
    return normalized


def _optional_identifier_tuple(values: Iterable[object], *, field: str) -> Tuple[str, ...]:
    normalized = tuple(_opaque_identifier(value, field=field) for value in values)
    if len(normalized) != len(set(normalized)):
        raise KnowledgeRecommendationError(f"{field} must not contain duplicates")
    return normalized


def _normalize_datetime(value: Optional[datetime], *, field: str) -> Optional[datetime]:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise KnowledgeRecommendationError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ConfirmedMemoryDimensionEvidence:
    """A value-free pointer to memory evidence that may increase coverage.

    The upstream Authority layer must mark a record current and confirmed.  A
    candidate, AI-only inference, inaccessible record, revoked record, deleted
    record, or disputed record cannot increase a coverage projection.
    """

    memory_version_id: str
    source_id: str
    vault_id: str
    owner_subject_id: str
    dimension: KnowledgeDimension
    covered_facets: Tuple[str, ...]
    is_current_confirmed: bool = True
    is_accessible: bool = True
    is_deleted: bool = False
    is_revoked: bool = False
    is_disputed: bool = False
    is_ai_inference_only: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_version_id", _opaque_identifier(self.memory_version_id, field="memory_version_id"))
        object.__setattr__(self, "source_id", _opaque_identifier(self.source_id, field="source_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "owner_subject_id", require_nonblank(self.owner_subject_id, field="owner_subject_id"))
        try:
            dimension = KnowledgeDimension(self.dimension)
        except (TypeError, ValueError) as exc:
            raise KnowledgeRecommendationError("dimension is not supported") from exc
        object.__setattr__(self, "dimension", dimension)
        facets = tuple(str(facet or "").strip() for facet in self.covered_facets)
        if not facets:
            raise KnowledgeRecommendationError("covered_facets must not be empty")
        if len(facets) != len(set(facets)):
            raise KnowledgeRecommendationError("covered_facets must not contain duplicates")
        unsupported = sorted(set(facets).difference(_DIMENSION_FACETS[dimension]))
        if unsupported:
            raise KnowledgeRecommendationError("covered_facets contain unsupported values")
        object.__setattr__(self, "covered_facets", facets)
        for field in (
            "is_current_confirmed",
            "is_accessible",
            "is_deleted",
            "is_revoked",
            "is_disputed",
            "is_ai_inference_only",
        ):
            if not isinstance(getattr(self, field), bool):
                raise KnowledgeRecommendationError(f"{field} must be a boolean")

    @property
    def is_admissible(self) -> bool:
        return (
            self.is_current_confirmed
            and self.is_accessible
            and not self.is_deleted
            and not self.is_revoked
            and not self.is_disputed
            and not self.is_ai_inference_only
        )


@dataclass(frozen=True)
class DimensionCoverage:
    """Rebuildable, non-authoritative coverage for one stable dimension."""

    dimension: KnowledgeDimension
    memory_version_ids: Tuple[str, ...]
    source_ids: Tuple[str, ...]
    covered_facets: Tuple[str, ...]
    missing_facets: Tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            dimension = KnowledgeDimension(self.dimension)
        except (TypeError, ValueError) as exc:
            raise KnowledgeRecommendationError("dimension is not supported") from exc
        object.__setattr__(self, "dimension", dimension)
        object.__setattr__(
            self,
            "memory_version_ids",
            _optional_identifier_tuple(self.memory_version_ids, field="memory_version_ids"),
        )
        object.__setattr__(
            self,
            "source_ids",
            _optional_identifier_tuple(self.source_ids, field="source_ids"),
        )
        object.__setattr__(self, "covered_facets", tuple(str(item or "").strip() for item in self.covered_facets))
        object.__setattr__(self, "missing_facets", tuple(str(item or "").strip() for item in self.missing_facets))
        known = set(_DIMENSION_FACETS[dimension])
        if not self.memory_version_ids and self.source_ids:
            raise KnowledgeRecommendationError("source_ids require memory_version_ids")
        if self.memory_version_ids and not self.source_ids:
            raise KnowledgeRecommendationError("memory_version_ids require source_ids")
        if len(self.covered_facets) != len(set(self.covered_facets)):
            raise KnowledgeRecommendationError("covered_facets must not contain duplicates")
        if len(self.missing_facets) != len(set(self.missing_facets)):
            raise KnowledgeRecommendationError("missing_facets must not contain duplicates")
        if set(self.covered_facets).intersection(self.missing_facets):
            raise KnowledgeRecommendationError("coverage facets must not overlap")
        if set(self.covered_facets).union(self.missing_facets) != known:
            raise KnowledgeRecommendationError("coverage facets must match the stable dimension definition")
        if self.covered_facets != tuple(
            facet for facet in _DIMENSION_FACETS[dimension] if facet in self.covered_facets
        ):
            raise KnowledgeRecommendationError("covered_facets must use stable dimension order")
        if self.missing_facets != tuple(
            facet for facet in _DIMENSION_FACETS[dimension] if facet in self.missing_facets
        ):
            raise KnowledgeRecommendationError("missing_facets must use stable dimension order")

    @property
    def missing_facet_count(self) -> int:
        return len(self.missing_facets)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "dimension": self.dimension.value,
            "evidenceCount": len(self.memory_version_ids),
            "coveredFacetCount": len(self.covered_facets),
            "missingFacetCount": len(self.missing_facets),
            "schemaVersion": KNOWLEDGE_DIMENSION_PROJECTION_SCHEMA_VERSION,
        }


@dataclass(frozen=True)
class DimensionProjection:
    """A complete six-dimension projection for one owner and Vault scope."""

    owner_subject_id: str
    vault_id: str
    policy_version: str
    coverage: Tuple[DimensionCoverage, ...]
    excluded_evidence_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_subject_id", require_nonblank(self.owner_subject_id, field="owner_subject_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        if tuple(item.dimension for item in self.coverage) != tuple(KnowledgeDimension):
            raise KnowledgeRecommendationError("coverage must contain each stable dimension exactly once")
        if not isinstance(self.excluded_evidence_count, int) or self.excluded_evidence_count < 0:
            raise KnowledgeRecommendationError("excluded_evidence_count must be a non-negative integer")

    def for_dimension(self, dimension: KnowledgeDimension) -> DimensionCoverage:
        normalized = KnowledgeDimension(dimension)
        return next(item for item in self.coverage if item.dimension is normalized)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "dimensions": [item.value_free_summary() for item in self.coverage],
            "excludedEvidenceCount": self.excluded_evidence_count,
            "policyVersion": self.policy_version,
            "schemaVersion": KNOWLEDGE_DIMENSION_PROJECTION_SCHEMA_VERSION,
        }


class KnowledgeDimensionProjector:
    """Rebuild coverage only from current, accessible confirmed memory evidence."""

    def project(
        self,
        *,
        owner_subject_id: str,
        vault_id: str,
        evidence: Iterable[ConfirmedMemoryDimensionEvidence],
        policy_version: str = KNOWLEDGE_DIMENSION_POLICY_VERSION,
    ) -> DimensionProjection:
        owner = require_nonblank(owner_subject_id, field="owner_subject_id")
        vault = require_nonblank(vault_id, field="vault_id")
        policy = require_nonblank(policy_version, field="policy_version")
        included: dict[KnowledgeDimension, list[ConfirmedMemoryDimensionEvidence]] = {
            dimension: [] for dimension in KnowledgeDimension
        }
        excluded = 0
        for item in evidence:
            if not isinstance(item, ConfirmedMemoryDimensionEvidence):
                raise TypeError("evidence must contain ConfirmedMemoryDimensionEvidence")
            if item.owner_subject_id != owner or item.vault_id != vault or not item.is_admissible:
                excluded += 1
                continue
            included[item.dimension].append(item)

        coverage = []
        for dimension in KnowledgeDimension:
            records = included[dimension]
            memory_version_ids = tuple(sorted({record.memory_version_id for record in records}))
            source_ids = tuple(sorted({record.source_id for record in records}))
            covered_facets = tuple(
                facet
                for facet in _DIMENSION_FACETS[dimension]
                if any(facet in record.covered_facets for record in records)
            )
            missing_facets = tuple(
                facet for facet in _DIMENSION_FACETS[dimension] if facet not in covered_facets
            )
            coverage.append(
                DimensionCoverage(
                    dimension=dimension,
                    memory_version_ids=memory_version_ids,
                    source_ids=source_ids,
                    covered_facets=covered_facets,
                    missing_facets=missing_facets,
                )
            )
        return DimensionProjection(
            owner_subject_id=owner,
            vault_id=vault,
            policy_version=policy,
            coverage=tuple(coverage),
            excluded_evidence_count=excluded,
        )


@dataclass(frozen=True)
class RecommendationCandidate:
    """A content-free candidate supplied by a future thread/gap projector."""

    candidate_id: str
    owner_subject_id: str
    vault_id: str
    slot: RecommendationSlot
    thread_id: str
    target_dimension: KnowledgeDimension
    missing_facet: str
    question_template_id: str
    evidence_kind: RecommendationEvidenceKind
    evidence_refs: Tuple[str, ...]
    reason_code: str
    explicit_intent_priority: int = 0
    continuity_score: int = 0
    importance_score: int = 0
    is_accessible: bool = True
    is_do_not_ask: bool = False
    is_in_cooldown: bool = False
    consecutive_skip_count: int = 0
    was_reopened_by_user: bool = False
    is_sensitive: bool = False
    has_recent_user_consent: bool = False
    is_ai_inference_only: bool = False
    is_deleted: bool = False
    is_revoked: bool = False
    is_disputed: bool = False
    is_minor_risk: bool = False
    requires_persona: bool = False
    expires_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        for field in ("candidate_id", "thread_id", "missing_facet", "question_template_id", "reason_code"):
            object.__setattr__(self, field, _opaque_identifier(getattr(self, field), field=field))
        object.__setattr__(self, "owner_subject_id", require_nonblank(self.owner_subject_id, field="owner_subject_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        try:
            object.__setattr__(self, "slot", RecommendationSlot(self.slot))
            object.__setattr__(self, "target_dimension", KnowledgeDimension(self.target_dimension))
            object.__setattr__(self, "evidence_kind", RecommendationEvidenceKind(self.evidence_kind))
        except (TypeError, ValueError) as exc:
            raise KnowledgeRecommendationError("recommendation candidate contains an unsupported enum value") from exc
        if self.missing_facet not in _DIMENSION_FACETS[self.target_dimension]:
            raise KnowledgeRecommendationError("missing_facet is not valid for target_dimension")
        object.__setattr__(self, "evidence_refs", _identifier_tuple(self.evidence_refs, field="evidence_refs"))
        for field in ("explicit_intent_priority", "continuity_score", "importance_score", "consecutive_skip_count"):
            value = getattr(self, field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise KnowledgeRecommendationError(f"{field} must be a non-negative integer")
        for field in (
            "is_accessible",
            "is_do_not_ask",
            "is_in_cooldown",
            "was_reopened_by_user",
            "is_sensitive",
            "has_recent_user_consent",
            "is_ai_inference_only",
            "is_deleted",
            "is_revoked",
            "is_disputed",
            "is_minor_risk",
            "requires_persona",
        ):
            if not isinstance(getattr(self, field), bool):
                raise KnowledgeRecommendationError(f"{field} must be a boolean")
        object.__setattr__(self, "expires_at", _normalize_datetime(self.expires_at, field="expires_at"))

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return (self.thread_id, self.missing_facet)


@dataclass(frozen=True)
class RecommendationDecision:
    slot: RecommendationSlot
    candidate_id: str
    thread_id: str
    target_dimension: KnowledgeDimension
    missing_facet: str
    question_template_id: str
    evidence_refs: Tuple[str, ...]
    reason_code: str
    policy_version: str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "slot", RecommendationSlot(self.slot))
            object.__setattr__(self, "target_dimension", KnowledgeDimension(self.target_dimension))
        except (TypeError, ValueError) as exc:
            raise KnowledgeRecommendationError("recommendation decision contains an unsupported enum value") from exc
        for field in ("candidate_id", "thread_id", "missing_facet", "question_template_id", "reason_code"):
            object.__setattr__(self, field, _opaque_identifier(getattr(self, field), field=field))
        if self.missing_facet not in _DIMENSION_FACETS[self.target_dimension]:
            raise KnowledgeRecommendationError("missing_facet is not valid for target_dimension")
        object.__setattr__(self, "evidence_refs", _identifier_tuple(self.evidence_refs, field="evidence_refs"))
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))

    def value_free_summary(self) -> dict[str, object]:
        return {
            "candidateId": self.candidate_id,
            "evidenceRefCount": len(self.evidence_refs),
            "missingFacet": self.missing_facet,
            "policyVersion": self.policy_version,
            "questionTemplateId": self.question_template_id,
            "reasonCode": self.reason_code,
            "slot": self.slot.value,
            "targetDimension": self.target_dimension.value,
        }


@dataclass(frozen=True)
class RecommendationFilteredCandidate:
    candidate_id: str
    slot: RecommendationSlot
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _opaque_identifier(self.candidate_id, field="candidate_id"))
        object.__setattr__(self, "reason_code", _opaque_identifier(self.reason_code, field="reason_code"))
        try:
            object.__setattr__(self, "slot", RecommendationSlot(self.slot))
        except (TypeError, ValueError) as exc:
            raise KnowledgeRecommendationError("filtered candidate contains an unsupported slot") from exc

    def value_free_summary(self) -> dict[str, str]:
        return {
            "candidateId": self.candidate_id,
            "reasonCode": self.reason_code,
            "slot": self.slot.value,
        }


@dataclass(frozen=True)
class RecommendationSelection:
    owner_subject_id: str
    vault_id: str
    policy_version: str
    selected: Tuple[RecommendationDecision, ...]
    filtered: Tuple[RecommendationFilteredCandidate, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_subject_id", require_nonblank(self.owner_subject_id, field="owner_subject_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        if len(self.selected) > 2:
            raise KnowledgeRecommendationError("recommendation selection must not contain more than two decisions")
        if len({decision.slot for decision in self.selected}) != len(self.selected):
            raise KnowledgeRecommendationError("recommendation selection must not duplicate a slot")
        if len({(decision.thread_id, decision.missing_facet) for decision in self.selected}) != len(self.selected):
            raise KnowledgeRecommendationError("recommendation selection must not duplicate a thread facet")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "filtered": [item.value_free_summary() for item in self.filtered],
            "policyVersion": self.policy_version,
            "schemaVersion": RECOMMENDATION_SELECTION_SCHEMA_VERSION,
            "selected": [item.value_free_summary() for item in self.selected],
        }


@dataclass(frozen=True)
class ServerPlannedContinuationCue:
    """One explicit Owner continuation pointer eligible for server planning.

    This is not a topic classifier or a transcript summary. A cue is created
    only by an explicit Owner action and points at one current, confirmed
    MemoryVersion plus a still-missing facet in the same private interview
    session. It carries no question text or message content.
    """

    cue_id: str
    owner_subject_id: str
    vault_id: str
    authority_epoch: int
    thread_id: str
    session_id: str
    expected_session_version: int
    memory_version_id: str
    target_dimension: KnowledgeDimension
    missing_facet: str

    def __post_init__(self) -> None:
        for field in ("cue_id", "thread_id", "session_id", "memory_version_id"):
            object.__setattr__(self, field, _opaque_identifier(getattr(self, field), field=field))
        object.__setattr__(self, "owner_subject_id", require_nonblank(self.owner_subject_id, field="owner_subject_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        try:
            object.__setattr__(self, "target_dimension", KnowledgeDimension(self.target_dimension))
        except (TypeError, ValueError) as exc:
            raise KnowledgeRecommendationError("saved continuation cue contains an unsupported dimension") from exc
        object.__setattr__(self, "missing_facet", _opaque_identifier(self.missing_facet, field="missing_facet"))
        if self.missing_facet not in _DIMENSION_FACETS[self.target_dimension]:
            raise KnowledgeRecommendationError("saved continuation cue missing_facet is not valid")
        if (
            not isinstance(self.expected_session_version, int)
            or isinstance(self.expected_session_version, bool)
            or self.expected_session_version < 1
        ):
            raise KnowledgeRecommendationError(
                "saved continuation cue expected_session_version must be a positive integer"
            )
        if (
            not isinstance(self.authority_epoch, int)
            or isinstance(self.authority_epoch, bool)
            or self.authority_epoch < 0
        ):
            raise KnowledgeRecommendationError("saved continuation cue authority_epoch must be a non-negative integer")


class RecommendationSelector:
    """Choose at most one continuity and one breadth recommendation.

    This is a policy selector, not a ranking-only recommender.  Safety and
    user-control exclusions are evaluated before score ordering, and no slot is
    backfilled when no candidate is safe.
    """

    def select(
        self,
        *,
        owner_subject_id: str,
        vault_id: str,
        coverage: DimensionProjection,
        candidates: Iterable[RecommendationCandidate],
        now: datetime,
        crisis_active: bool = False,
        policy_version: str = KNOWLEDGE_DIMENSION_POLICY_VERSION,
    ) -> RecommendationSelection:
        owner = require_nonblank(owner_subject_id, field="owner_subject_id")
        vault = require_nonblank(vault_id, field="vault_id")
        policy = require_nonblank(policy_version, field="policy_version")
        current_time = _normalize_datetime(now, field="now")
        if current_time is None:
            raise KnowledgeRecommendationError("now is required")
        if not isinstance(coverage, DimensionProjection):
            raise TypeError("coverage must be a DimensionProjection")
        if coverage.owner_subject_id != owner or coverage.vault_id != vault:
            raise KnowledgeRecommendationError("coverage scope does not match recommendation scope")
        if not isinstance(crisis_active, bool):
            raise KnowledgeRecommendationError("crisis_active must be a boolean")

        eligible: dict[RecommendationSlot, list[RecommendationCandidate]] = {
            RecommendationSlot.CONTINUITY: [],
            RecommendationSlot.BREADTH: [],
        }
        filtered: list[RecommendationFilteredCandidate] = []
        for candidate in candidates:
            if not isinstance(candidate, RecommendationCandidate):
                raise TypeError("candidates must contain RecommendationCandidate")
            reason = self._filter_reason(
                candidate,
                owner_subject_id=owner,
                vault_id=vault,
                coverage=coverage,
                now=current_time,
                crisis_active=crisis_active,
            )
            if reason is not None:
                filtered.append(
                    RecommendationFilteredCandidate(
                        candidate_id=candidate.candidate_id,
                        slot=candidate.slot,
                        reason_code=reason,
                    )
                )
                continue
            eligible[candidate.slot].append(candidate)

        selected: list[RecommendationDecision] = []
        continuity = self._best_continuity(eligible[RecommendationSlot.CONTINUITY])
        if continuity is not None:
            selected.append(self._decision(continuity, policy_version=policy))

        continuity_key = continuity.dedupe_key if continuity is not None else None
        breadth_candidates = []
        for candidate in eligible[RecommendationSlot.BREADTH]:
            if candidate.dedupe_key == continuity_key:
                filtered.append(
                    RecommendationFilteredCandidate(
                        candidate_id=candidate.candidate_id,
                        slot=candidate.slot,
                        reason_code="duplicateThreadFacet",
                    )
                )
                continue
            breadth_candidates.append(candidate)
        breadth = self._best_breadth(breadth_candidates, coverage=coverage)
        if breadth is not None:
            selected.append(self._decision(breadth, policy_version=policy))

        return RecommendationSelection(
            owner_subject_id=owner,
            vault_id=vault,
            policy_version=policy,
            selected=tuple(selected),
            filtered=tuple(filtered),
        )

    @staticmethod
    def _filter_reason(
        candidate: RecommendationCandidate,
        *,
        owner_subject_id: str,
        vault_id: str,
        coverage: DimensionProjection,
        now: datetime,
        crisis_active: bool,
    ) -> Optional[str]:
        if crisis_active:
            return "crisisSafetyOverride"
        if candidate.owner_subject_id != owner_subject_id or candidate.vault_id != vault_id:
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
        if candidate.expires_at is not None and candidate.expires_at <= now:
            return "candidateExpired"
        if (
            candidate.slot is RecommendationSlot.BREADTH
            and candidate.missing_facet not in coverage.for_dimension(candidate.target_dimension).missing_facets
        ):
            return "facetAlreadyCovered"
        return None

    @staticmethod
    def _best_continuity(candidates: Iterable[RecommendationCandidate]) -> Optional[RecommendationCandidate]:
        rows = tuple(candidates)
        if not rows:
            return None
        return max(
            rows,
            key=lambda candidate: (
                candidate.explicit_intent_priority,
                candidate.continuity_score,
                candidate.importance_score,
                candidate.candidate_id,
            ),
        )

    @staticmethod
    def _best_breadth(
        candidates: Iterable[RecommendationCandidate],
        *,
        coverage: DimensionProjection,
    ) -> Optional[RecommendationCandidate]:
        rows = tuple(candidates)
        if not rows:
            return None
        return max(
            rows,
            key=lambda candidate: (
                coverage.for_dimension(candidate.target_dimension).missing_facet_count,
                candidate.importance_score,
                candidate.explicit_intent_priority,
                candidate.candidate_id,
            ),
        )

    @staticmethod
    def _decision(candidate: RecommendationCandidate, *, policy_version: str) -> RecommendationDecision:
        return RecommendationDecision(
            slot=candidate.slot,
            candidate_id=candidate.candidate_id,
            thread_id=candidate.thread_id,
            target_dimension=candidate.target_dimension,
            missing_facet=candidate.missing_facet,
            question_template_id=candidate.question_template_id,
            evidence_refs=candidate.evidence_refs,
            reason_code=candidate.reason_code,
            policy_version=policy_version,
        )


class ServerPlannedRecommendationCandidateProjector:
    """Derive value-free M0-B candidates from current private authority.

    This is deliberately narrower than a conversational recommendation engine:
    it has no natural-language input, provider output, message text, or write
    path.  It can use an active/open private interview thread, or one paused
    cooldown thread that a caller has already proven elapsed from a
    server-owned ThreadPreference clock.  It never evaluates client time or
    mutates the session.  When current authority or confirmed coverage is
    unavailable, it returns no candidates rather than guessing a topic.
    """

    def project(
        self,
        *,
        owner_subject_id: str,
        vault_id: str,
        authority_epoch: int,
        checkpoint: str,
        coverage: DimensionProjection,
        thread_authorities: Iterable[OwnerTruthConversationThreadAuthoritySnapshot],
        continuity_cues: Iterable[ServerPlannedContinuationCue] = (),
        elapsed_cooldown_thread_ids: Iterable[str] = (),
    ) -> Tuple[RecommendationCandidate, ...]:
        owner = require_nonblank(owner_subject_id, field="owner_subject_id")
        vault = require_nonblank(vault_id, field="vault_id")
        if (
            not isinstance(authority_epoch, int)
            or isinstance(authority_epoch, bool)
            or authority_epoch < 0
        ):
            raise KnowledgeRecommendationError("authority_epoch must be a non-negative integer")
        normalized_checkpoint = require_nonblank(checkpoint, field="checkpoint")
        if not isinstance(coverage, DimensionProjection):
            raise TypeError("coverage must be a DimensionProjection")
        if coverage.owner_subject_id != owner or coverage.vault_id != vault:
            raise KnowledgeRecommendationError("coverage scope does not match recommendation scope")
        try:
            elapsed_cooldown_ids = frozenset(
                require_nonblank(str(thread_id), field="elapsed_cooldown_thread_id")
                for thread_id in elapsed_cooldown_thread_ids
            )
        except TypeError as exc:
            raise KnowledgeRecommendationError("elapsed_cooldown_thread_ids must be iterable") from exc

        eligible = self._eligible_thread(
            thread_authorities=thread_authorities,
            owner_subject_id=owner,
            vault_id=vault,
            authority_epoch=authority_epoch,
            elapsed_cooldown_thread_ids=elapsed_cooldown_ids,
        )
        if eligible is None:
            return ()
        thread_authority, is_elapsed_cooldown = eligible

        candidates: list[RecommendationCandidate] = []
        breadth_coverage = tuple(
            item
            for item in coverage.coverage
            if item.memory_version_ids and item.missing_facets
        )
        continuity_cue = self._eligible_continuity_cue(
            continuity_cues=continuity_cues,
            thread_authority=thread_authority,
            owner_subject_id=owner,
            vault_id=vault,
            authority_epoch=authority_epoch,
        )
        if is_elapsed_cooldown:
            # The owner explicitly chose "later".  After the server-owned
            # cooldown expires, a still-current explicit cue is the highest
            # priority continuity source. It may span exactly the direct
            # ``open/N -> paused-cooldown/N+1`` transition after the separate
            # server-clock check; anything older is filtered before projection.
            if continuity_cue is not None:
                continuity_coverage = coverage.for_dimension(continuity_cue.target_dimension)
                return (
                    self._candidate(
                        slot=RecommendationSlot.CONTINUITY,
                        thread_authority=thread_authority,
                        authority_epoch=authority_epoch,
                        checkpoint=normalized_checkpoint,
                        coverage=continuity_coverage,
                        missing_facet=continuity_cue.missing_facet,
                        question_template_id="continueSavedOwnerCue",
                        reason_code="elapsedCooldownSavedContinuation",
                        explicit_intent_priority=3,
                        continuity_score=2,
                        evidence_kind=RecommendationEvidenceKind.SAVED_CONTINUATION,
                        evidence_refs=(continuity_cue.memory_version_id,),
                        cue_id=continuity_cue.cue_id,
                    ),
                )
            if not breadth_coverage:
                return ()
            selected_coverage = max(
                breadth_coverage,
                key=lambda item: (
                    len(item.missing_facets),
                    len(item.memory_version_ids),
                    item.dimension.value,
                ),
            )
            return (
                self._candidate(
                    slot=RecommendationSlot.CONTINUITY,
                    thread_authority=thread_authority,
                    authority_epoch=authority_epoch,
                    checkpoint=normalized_checkpoint,
                    coverage=selected_coverage,
                    missing_facet=selected_coverage.missing_facets[0],
                    question_template_id="continueElapsedCooldown",
                    reason_code="elapsedCooldownContinuation",
                    explicit_intent_priority=2,
                    continuity_score=2,
                    importance_score=len(selected_coverage.missing_facets),
                ),
            )

        if continuity_cue is not None:
            continuity_coverage = coverage.for_dimension(continuity_cue.target_dimension)
            candidates.append(
                self._candidate(
                    slot=RecommendationSlot.CONTINUITY,
                    thread_authority=thread_authority,
                    authority_epoch=authority_epoch,
                    checkpoint=normalized_checkpoint,
                    coverage=continuity_coverage,
                    missing_facet=continuity_cue.missing_facet,
                    question_template_id="continueSavedOwnerCue",
                    reason_code="explicitOwnerSavedContinuation",
                    explicit_intent_priority=1,
                    continuity_score=1,
                    evidence_kind=RecommendationEvidenceKind.SAVED_CONTINUATION,
                    evidence_refs=(continuity_cue.memory_version_id,),
                    cue_id=continuity_cue.cue_id,
                )
            )

        if not breadth_coverage:
            return tuple(candidates)
        selected_coverage = max(
            breadth_coverage,
            key=lambda item: (
                len(item.missing_facets),
                len(item.memory_version_ids),
                item.dimension.value,
            ),
        )
        breadth = self._candidate(
            slot=RecommendationSlot.BREADTH,
            thread_authority=thread_authority,
            authority_epoch=authority_epoch,
            checkpoint=normalized_checkpoint,
            coverage=selected_coverage,
            missing_facet=selected_coverage.missing_facets[0],
            question_template_id="broadenConfirmedGap",
            reason_code="confirmedDimensionGap",
            importance_score=len(selected_coverage.missing_facets),
        )
        candidates.append(breadth)
        return tuple(candidates)

    @staticmethod
    def _eligible_continuity_cue(
        *,
        continuity_cues: Iterable[ServerPlannedContinuationCue],
        thread_authority: OwnerTruthConversationThreadAuthoritySnapshot,
        owner_subject_id: str,
        vault_id: str,
        authority_epoch: int,
    ) -> Optional[ServerPlannedContinuationCue]:
        try:
            rows = tuple(continuity_cues)
        except TypeError as exc:
            raise KnowledgeRecommendationError("continuity_cues must be iterable") from exc
        matches: list[ServerPlannedContinuationCue] = []
        for cue in rows:
            if not isinstance(cue, ServerPlannedContinuationCue):
                raise KnowledgeRecommendationError(
                    "continuity_cues must contain ServerPlannedContinuationCue"
                )
            # A persisted cue becomes ineligible rather than being revived when
            # its authority/session binding is no longer current. This keeps
            # historical receipts intact while making the plan fail closed.
            if (
                cue.owner_subject_id != owner_subject_id
                or cue.vault_id != vault_id
                or cue.authority_epoch != authority_epoch
                or cue.thread_id != thread_authority.thread_id
                or cue.session_id != thread_authority.session_id
            ):
                continue
            matches.append(cue)
        if len(matches) > 1:
            raise KnowledgeRecommendationError(
                "multiple current saved continuation cues cannot plan one continuity recommendation"
            )
        return matches[0] if matches else None

    @staticmethod
    def _eligible_thread(
        *,
        thread_authorities: Iterable[OwnerTruthConversationThreadAuthoritySnapshot],
        owner_subject_id: str,
        vault_id: str,
        authority_epoch: int,
        elapsed_cooldown_thread_ids: frozenset[str],
    ) -> Optional[tuple[OwnerTruthConversationThreadAuthoritySnapshot, bool]]:
        try:
            rows = tuple(thread_authorities)
        except TypeError as exc:
            raise KnowledgeRecommendationError("thread_authorities must be iterable") from exc
        eligible: list[tuple[OwnerTruthConversationThreadAuthoritySnapshot, bool]] = []
        seen_thread_ids: set[str] = set()
        for item in rows:
            if not isinstance(item, OwnerTruthConversationThreadAuthoritySnapshot):
                raise KnowledgeRecommendationError(
                    "thread_authorities must contain OwnerTruthConversationThreadAuthoritySnapshot"
                )
            if (
                item.owner_subject_id != owner_subject_id
                or item.vault_id != vault_id
                or item.authority_epoch != authority_epoch
            ):
                raise KnowledgeRecommendationError(
                    "thread authority scope does not match recommendation scope"
                )
            if item.thread_id in seen_thread_ids:
                raise KnowledgeRecommendationError("thread_authorities must not duplicate a thread")
            seen_thread_ids.add(item.thread_id)
            if item.is_recommendation_eligible:
                eligible.append((item, False))
                continue
            if (
                item.thread_id in elapsed_cooldown_thread_ids
                and item.is_elapsed_cooldown_candidate
            ):
                eligible.append((item, True))
                continue
            if item.is_elapsed_cooldown_candidate:
                raise KnowledgeRecommendationError(
                    "elapsed cooldown thread requires a server-verified eligibility marker"
                )
            raise KnowledgeRecommendationError(
                "thread_authorities must contain only active open interview sessions"
            )
        if len(eligible) > 1:
            raise KnowledgeRecommendationError("multiple active open interview threads cannot plan recommendations")
        return eligible[0] if eligible else None

    @staticmethod
    def _candidate(
        *,
        slot: RecommendationSlot,
        thread_authority: OwnerTruthConversationThreadAuthoritySnapshot,
        authority_epoch: int,
        checkpoint: str,
        coverage: DimensionCoverage,
        missing_facet: str,
        question_template_id: str,
        reason_code: str,
        explicit_intent_priority: int = 0,
        continuity_score: int = 0,
        importance_score: int = 0,
        evidence_kind: RecommendationEvidenceKind = RecommendationEvidenceKind.CONFIRMED_MEMORY,
        evidence_refs: Optional[Tuple[str, ...]] = None,
        cue_id: Optional[str] = None,
    ) -> RecommendationCandidate:
        resolved_evidence_refs = coverage.memory_version_ids if evidence_refs is None else evidence_refs
        candidate_id = ServerPlannedRecommendationCandidateProjector._candidate_id(
            slot=slot,
            thread_id=thread_authority.thread_id,
            session_id=thread_authority.session_id,
            authority_epoch=authority_epoch,
            checkpoint=checkpoint,
            dimension=coverage.dimension,
            missing_facet=missing_facet,
            evidence_refs=resolved_evidence_refs,
            cue_id=cue_id,
        )
        return RecommendationCandidate(
            candidate_id=candidate_id,
            owner_subject_id=thread_authority.owner_subject_id,
            vault_id=thread_authority.vault_id,
            slot=slot,
            thread_id=thread_authority.thread_id,
            target_dimension=coverage.dimension,
            missing_facet=missing_facet,
            question_template_id=question_template_id,
            evidence_kind=evidence_kind,
            evidence_refs=resolved_evidence_refs,
            reason_code=reason_code,
            explicit_intent_priority=explicit_intent_priority,
            continuity_score=continuity_score,
            importance_score=importance_score,
        )

    @staticmethod
    def _candidate_id(
        *,
        slot: RecommendationSlot,
        thread_id: str,
        session_id: str,
        authority_epoch: int,
        checkpoint: str,
        dimension: KnowledgeDimension,
        missing_facet: str,
        evidence_refs: Tuple[str, ...],
        cue_id: Optional[str] = None,
    ) -> str:
        payload = {
            "dimension": dimension.value,
            "evidenceRefs": list(evidence_refs),
            "authorityEpoch": authority_epoch,
            "checkpoint": checkpoint,
            "missingFacet": missing_facet,
            "sessionId": session_id,
            "slot": slot.value,
            "threadId": thread_id,
        }
        if cue_id is not None:
            payload["cueId"] = _opaque_identifier(cue_id, field="cue_id")
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
        return f"server-plan-{slot.value}-{digest}"


__all__ = [
    "ConfirmedMemoryDimensionEvidence",
    "DimensionCoverage",
    "DimensionProjection",
    "KnowledgeDimension",
    "knowledge_dimension_facets",
    "KnowledgeDimensionProjector",
    "KnowledgeRecommendationError",
    "KNOWLEDGE_DIMENSION_POLICY_VERSION",
    "KNOWLEDGE_DIMENSION_PROJECTION_SCHEMA_VERSION",
    "RecommendationCandidate",
    "RecommendationDecision",
    "RecommendationEvidenceKind",
    "RecommendationFilteredCandidate",
    "RecommendationSelection",
    "RecommendationSelector",
    "RecommendationSlot",
    "RECOMMENDATION_SELECTION_SCHEMA_VERSION",
    "ServerPlannedContinuationCue",
    "ServerPlannedRecommendationCandidateProjector",
]
