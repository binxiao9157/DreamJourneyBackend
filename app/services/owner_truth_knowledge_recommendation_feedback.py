"""Owner-controlled, append-only feedback for default-off M0-B recommendations.

This module keeps recommendation feedback deliberately narrower than a new
conversation or topic authority.  ``replace`` suppresses only one short-lived
server-planned candidate.  ``notInterested`` records a value-free preference
against a knowledge dimension or policy question template so later selection
can lower its ranking.  The one explicit ``defer`` + ``timing`` combination
reuses the existing ThreadPreference and saved-continuation commands.  It is
available only from the opaque guided recommendation set, so feedback can
never silently become ``cooldown`` or ``doNotAsk``.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
import re
from threading import RLock
from types import MappingProxyType
from typing import Any, ContextManager, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.contracts import OwnerTruthContractError, require_nonblank, require_uuid
from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
    SetInterviewBoundaryCommand,
)
from app.domain.owner_truth.knowledge_dimension_read import OwnerTruthKnowledgeDimensionReadState
from app.domain.owner_truth.knowledge_recommendations import (
    KnowledgeDimension,
    RecommendationDecision,
    RecommendationSlot,
    knowledge_dimension_facets,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_saved_continuation import (
    OwnerTruthSavedContinuationCueCommand,
    OwnerTruthSavedContinuationCueError,
    OwnerTruthSavedContinuationCueService,
)
from app.services.owner_truth_thread_preferences import (
    OwnerTruthThreadPreferenceError,
    OwnerTruthThreadPreferenceService,
)


OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_SCHEMA_VERSION = (
    "owner-truth-knowledge-recommendation-feedback-v1"
)
OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_UI_SCHEMA_VERSION = (
    "knowledge-recommendation-feedback-v1"
)
OWNER_TRUTH_GUIDED_RECOMMENDATION_FEEDBACK_SCHEMA_VERSION = (
    "owner-truth-guided-recommendation-feedback-v1"
)

_FEEDBACK_NAMESPACE = UUID("961f5cf8-6ee9-4b9c-bcd4-cde6ebf702a2")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_OPAQUE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class OwnerTruthKnowledgeRecommendationFeedbackError(OwnerTruthContractError):
    """A recommendation feedback command cannot be safely interpreted."""


class OwnerTruthKnowledgeRecommendationFeedbackAccessDenied(
    OwnerTruthKnowledgeRecommendationFeedbackError
):
    """Only the active Vault Owner may submit recommendation feedback."""


class OwnerTruthKnowledgeRecommendationFeedbackConflict(
    OwnerTruthKnowledgeRecommendationFeedbackError
):
    """A command or current candidate was reused incompatibly."""


class OwnerTruthKnowledgeRecommendationFeedbackStale(
    OwnerTruthKnowledgeRecommendationFeedbackConflict
):
    """The selected recommendation or current session changed before feedback."""


class OwnerTruthKnowledgeRecommendationFeedbackUnavailable(
    OwnerTruthKnowledgeRecommendationFeedbackError
):
    """The default-off QA feedback lane is disabled or cannot safely plan."""


class RecommendationFeedbackAction(str, Enum):
    REPLACE = "replace"
    NOT_INTERESTED = "notInterested"
    DEFER = "defer"


class RecommendationFeedbackReason(str, Enum):
    QUESTION_WORDING = "questionWording"
    TOPIC_PREFERENCE = "topicPreference"
    RECOMMENDATION_TYPE = "recommendationType"
    TIMING = "timing"


class RecommendationFeedbackScope(str, Enum):
    CANDIDATE = "candidate"
    DIMENSION = "dimension"
    QUESTION_TEMPLATE = "questionTemplate"


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthKnowledgeRecommendationFeedbackError(
            "recommendation feedback payload must be JSON serializable"
        ) from exc


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _digest(value: Mapping[str, Any]) -> str:
    return _sha256(_canonical_json(value))


def _hash(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if _HASH_PATTERN.fullmatch(normalized) is None:
        raise OwnerTruthKnowledgeRecommendationFeedbackError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


def _opaque_identifier(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if _OPAQUE_IDENTIFIER.fullmatch(normalized) is None:
        raise OwnerTruthKnowledgeRecommendationFeedbackError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _positive_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OwnerTruthKnowledgeRecommendationFeedbackError(
            f"{field} must be a positive integer"
        )
    return value


def _nonnegative_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OwnerTruthKnowledgeRecommendationFeedbackError(
            f"{field} must be a non-negative integer"
        )
    return value


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthKnowledgeRecommendationFeedbackAccessDenied(
            "owner truth command context is required"
        )
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthKnowledgeRecommendationFeedbackAccessDenied(
            "only the Vault Owner may submit recommendation feedback"
        )


@dataclass(frozen=True)
class OwnerTruthKnowledgeRecommendationFeedbackPolicy:
    """Value-free policy inputs derived from immutable feedback receipts."""

    replaced_candidate_ids: frozenset[str] = frozenset()
    dimension_penalty_counts: Mapping[KnowledgeDimension | str, int] = field(
        default_factory=dict
    )
    question_template_penalty_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            replaced = frozenset(
                _opaque_identifier(candidate_id, field="replaced_candidate_id")
                for candidate_id in self.replaced_candidate_ids
            )
        except TypeError as exc:
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "replaced_candidate_ids must be iterable"
            ) from exc
        if not isinstance(self.dimension_penalty_counts, Mapping):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "dimension_penalty_counts must be a mapping"
            )
        dimensions: dict[KnowledgeDimension, int] = {}
        for raw_dimension, raw_count in self.dimension_penalty_counts.items():
            try:
                dimension = KnowledgeDimension(raw_dimension)
            except (TypeError, ValueError) as exc:
                raise OwnerTruthKnowledgeRecommendationFeedbackError(
                    "dimension penalty contains an unsupported dimension"
                ) from exc
            dimensions[dimension] = _nonnegative_int(
                raw_count,
                field="dimension_penalty_count",
            )
        if not isinstance(self.question_template_penalty_counts, Mapping):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "question_template_penalty_counts must be a mapping"
            )
        templates: dict[str, int] = {}
        for raw_template, raw_count in self.question_template_penalty_counts.items():
            template = _opaque_identifier(
                raw_template,
                field="question_template_penalty",
            )
            templates[template] = _nonnegative_int(
                raw_count,
                field="question_template_penalty_count",
            )
        object.__setattr__(self, "replaced_candidate_ids", replaced)
        object.__setattr__(
            self,
            "dimension_penalty_counts",
            MappingProxyType(dict(sorted(dimensions.items(), key=lambda item: item[0].value))),
        )
        object.__setattr__(
            self,
            "question_template_penalty_counts",
            MappingProxyType(dict(sorted(templates.items()))),
        )

    @classmethod
    def empty(cls) -> "OwnerTruthKnowledgeRecommendationFeedbackPolicy":
        return cls()


@dataclass(frozen=True)
class OwnerTruthKnowledgeRecommendationFeedbackCommand:
    """A value-free feedback command bound to one selected server plan row."""

    command_id: str
    expected_candidate_id: str
    feedback_action: RecommendationFeedbackAction | str
    feedback_reason: RecommendationFeedbackReason | str
    expected_session_version: int
    idempotency_payload_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(
            self,
            "expected_candidate_id",
            _opaque_identifier(self.expected_candidate_id, field="expected_candidate_id"),
        )
        try:
            object.__setattr__(
                self,
                "feedback_action",
                RecommendationFeedbackAction(self.feedback_action),
            )
            object.__setattr__(
                self,
                "feedback_reason",
                RecommendationFeedbackReason(self.feedback_reason),
            )
        except (TypeError, ValueError) as exc:
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "recommendation feedback contains an unsupported action or reason"
            ) from exc
        _positive_int(self.expected_session_version, field="expected_session_version")
        if self.idempotency_payload_hash is not None:
            object.__setattr__(
                self,
                "idempotency_payload_hash",
                _hash(
                    self.idempotency_payload_hash,
                    field="idempotency_payload_hash",
                ),
            )
        if (
            self.feedback_action is RecommendationFeedbackAction.REPLACE
            and self.feedback_reason is not RecommendationFeedbackReason.QUESTION_WORDING
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "replace feedback only supports questionWording"
            )
        if (
            self.feedback_action is RecommendationFeedbackAction.NOT_INTERESTED
            and self.feedback_reason
            not in {
                RecommendationFeedbackReason.TOPIC_PREFERENCE,
                RecommendationFeedbackReason.RECOMMENDATION_TYPE,
            }
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "notInterested feedback requires topicPreference or recommendationType"
            )
        if (
            self.feedback_action is RecommendationFeedbackAction.DEFER
            and self.feedback_reason is not RecommendationFeedbackReason.TIMING
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "defer feedback only supports timing"
            )

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)

    @property
    def payload_hash(self) -> str:
        if self.idempotency_payload_hash is not None:
            return self.idempotency_payload_hash
        return _digest(
            {
                "schemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_SCHEMA_VERSION,
                "expectedCandidateId": self.expected_candidate_id,
                "feedbackAction": self.feedback_action.value,
                "feedbackReason": self.feedback_reason.value,
                "expectedSessionVersion": self.expected_session_version,
                "uiSchemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_UI_SCHEMA_VERSION,
            }
        )

    @property
    def feedback_scope(self) -> RecommendationFeedbackScope:
        if self.feedback_action in {
            RecommendationFeedbackAction.REPLACE,
            RecommendationFeedbackAction.DEFER,
        }:
            return RecommendationFeedbackScope.CANDIDATE
        if self.feedback_reason is RecommendationFeedbackReason.TOPIC_PREFERENCE:
            return RecommendationFeedbackScope.DIMENSION
        return RecommendationFeedbackScope.QUESTION_TEMPLATE


@dataclass(frozen=True)
class OwnerTruthGuidedRecommendationFeedbackCommand:
    """Public-safe feedback bound to one opaque guided recommendation set."""

    command_id: str
    recommendation_set_id: str
    slot: RecommendationSlot | str
    feedback_action: RecommendationFeedbackAction | str
    feedback_reason: RecommendationFeedbackReason | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(
            self,
            "recommendation_set_id",
            _hash(self.recommendation_set_id, field="recommendation_set_id"),
        )
        try:
            object.__setattr__(self, "slot", RecommendationSlot(self.slot))
            object.__setattr__(
                self,
                "feedback_action",
                RecommendationFeedbackAction(self.feedback_action),
            )
            object.__setattr__(
                self,
                "feedback_reason",
                RecommendationFeedbackReason(self.feedback_reason),
            )
        except (TypeError, ValueError) as exc:
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "guided recommendation feedback contains an unsupported action, reason or slot"
            ) from exc
        if (
            self.feedback_action is RecommendationFeedbackAction.REPLACE
            and self.feedback_reason is not RecommendationFeedbackReason.QUESTION_WORDING
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "replace feedback only supports questionWording"
            )
        if (
            self.feedback_action is RecommendationFeedbackAction.NOT_INTERESTED
            and self.feedback_reason
            not in {
                RecommendationFeedbackReason.TOPIC_PREFERENCE,
                RecommendationFeedbackReason.RECOMMENDATION_TYPE,
            }
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "notInterested feedback requires topicPreference or recommendationType"
            )
        if (
            self.feedback_action is RecommendationFeedbackAction.DEFER
            and self.feedback_reason is not RecommendationFeedbackReason.TIMING
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "defer feedback only supports timing"
            )

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "schemaVersion": OWNER_TRUTH_GUIDED_RECOMMENDATION_FEEDBACK_SCHEMA_VERSION,
                "recommendationSetId": self.recommendation_set_id,
                "slot": self.slot.value,
                "feedbackAction": self.feedback_action.value,
                "feedbackReason": self.feedback_reason.value,
            }
        )

    def to_feedback_command(
        self,
        *,
        decision: RecommendationDecision,
        expected_session_version: int,
    ) -> "OwnerTruthKnowledgeRecommendationFeedbackCommand":
        if decision.slot is not self.slot:
            raise OwnerTruthKnowledgeRecommendationFeedbackStale(
                "guided recommendation slot is no longer selected"
            )
        return OwnerTruthKnowledgeRecommendationFeedbackCommand(
            command_id=self.command_id,
            expected_candidate_id=decision.candidate_id,
            feedback_action=self.feedback_action,
            feedback_reason=self.feedback_reason,
            expected_session_version=expected_session_version,
            idempotency_payload_hash=self.payload_hash,
        )


@dataclass(frozen=True)
class OwnerTruthKnowledgeRecommendationFeedbackResult:
    """Minimal feedback receipt result with no private text or evidence IDs."""

    outcome: str
    feedback_id: str
    candidate_id: str
    feedback_action: RecommendationFeedbackAction | str
    feedback_reason: RecommendationFeedbackReason | str
    feedback_scope: RecommendationFeedbackScope | str
    slot: RecommendationSlot | str
    thread_id: str
    session_id: str
    expected_session_version: int
    target_dimension: KnowledgeDimension | str
    missing_facet: str
    question_template_id: str
    authority_epoch: int
    evidence_ref_count: int
    reason_code: str

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "recommendation feedback outcome is not supported"
            )
        for field_name in ("feedback_id", "thread_id", "session_id"):
            object.__setattr__(
                self,
                field_name,
                require_uuid(getattr(self, field_name), field=field_name),
            )
        object.__setattr__(
            self,
            "candidate_id",
            _opaque_identifier(self.candidate_id, field="candidate_id"),
        )
        object.__setattr__(
            self,
            "question_template_id",
            _opaque_identifier(self.question_template_id, field="question_template_id"),
        )
        object.__setattr__(
            self,
            "missing_facet",
            require_nonblank(self.missing_facet, field="missing_facet"),
        )
        object.__setattr__(
            self,
            "reason_code",
            _opaque_identifier(self.reason_code, field="reason_code"),
        )
        try:
            object.__setattr__(
                self,
                "feedback_action",
                RecommendationFeedbackAction(self.feedback_action),
            )
            object.__setattr__(
                self,
                "feedback_reason",
                RecommendationFeedbackReason(self.feedback_reason),
            )
            object.__setattr__(
                self,
                "feedback_scope",
                RecommendationFeedbackScope(self.feedback_scope),
            )
            object.__setattr__(self, "slot", RecommendationSlot(self.slot))
            object.__setattr__(
                self,
                "target_dimension",
                KnowledgeDimension(self.target_dimension),
            )
        except (TypeError, ValueError) as exc:
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "recommendation feedback contains an unsupported enum value"
            ) from exc
        if self.missing_facet not in knowledge_dimension_facets(self.target_dimension):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "missing_facet is not valid for target_dimension"
            )
        _positive_int(self.expected_session_version, field="expected_session_version")
        _nonnegative_int(self.authority_epoch, field="authority_epoch")
        _nonnegative_int(self.evidence_ref_count, field="evidence_ref_count")
        expected_scope = (
            RecommendationFeedbackScope.CANDIDATE
            if self.feedback_action
            in {
                RecommendationFeedbackAction.REPLACE,
                RecommendationFeedbackAction.DEFER,
            }
            else (
                RecommendationFeedbackScope.DIMENSION
                if self.feedback_reason is RecommendationFeedbackReason.TOPIC_PREFERENCE
                else RecommendationFeedbackScope.QUESTION_TEMPLATE
            )
        )
        if self.feedback_scope is not expected_scope:
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "feedback action and reason do not bind the expected scope"
            )


class OwnerTruthKnowledgeRecommendationFeedbackRepository(Protocol):
    def replay(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthKnowledgeRecommendationFeedbackCommand,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult | None:
        ...

    def replay_idempotency(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id: str,
        payload_hash: str,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult | None:
        ...

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult:
        ...

    def current_policy(
        self,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackPolicy:
        ...


class OwnerTruthKnowledgeRecommendationFeedbackStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def owner_truth_knowledge_recommendation_feedback_repository(
        self,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackRepository:
        ...

    def owner_truth_conversation_repository(self) -> Any:
        ...

    def owner_truth_memory_projection_repository(self) -> Any:
        ...

    def owner_truth_knowledge_dimension_confirmation_repository(self) -> Any:
        ...

    def owner_truth_saved_continuation_cue_repository(self) -> Any:
        ...

    def owner_truth_thread_preference_repository(self) -> Any:
        ...

    def owner_truth_knowledge_recommendation_activation_repository(self) -> Any:
        ...


def _result_from_record(
    record: Mapping[str, Any],
    *,
    outcome: str,
) -> OwnerTruthKnowledgeRecommendationFeedbackResult:
    return OwnerTruthKnowledgeRecommendationFeedbackResult(
        outcome=outcome,
        feedback_id=str(record.get("feedbackId") or ""),
        candidate_id=str(record.get("candidateId") or ""),
        feedback_action=record.get("feedbackAction"),
        feedback_reason=record.get("feedbackReason"),
        feedback_scope=record.get("feedbackScope"),
        slot=record.get("slot"),
        thread_id=str(record.get("threadId") or ""),
        session_id=str(record.get("sessionId") or ""),
        expected_session_version=record.get("expectedSessionVersion"),
        target_dimension=record.get("targetDimension"),
        missing_facet=str(record.get("missingFacet") or ""),
        question_template_id=str(record.get("questionTemplateId") or ""),
        authority_epoch=record.get("authorityEpoch"),
        evidence_ref_count=record.get("evidenceRefCount"),
        reason_code=str(record.get("reasonCode") or ""),
    )


def guided_recommendation_set_id(
    *,
    context: OwnerTruthCommandContext,
    authority_epoch: int,
    decisions: tuple[RecommendationDecision, ...],
) -> str:
    """Bind a display-only recommendation set to current Owner authority."""

    _assert_owner_context(context)
    _nonnegative_int(authority_epoch, field="authority_epoch")
    if len(decisions) > 2:
        raise OwnerTruthKnowledgeRecommendationFeedbackError(
            "guided recommendation set cannot contain more than two decisions"
        )
    if any(not isinstance(decision, RecommendationDecision) for decision in decisions):
        raise OwnerTruthKnowledgeRecommendationFeedbackError(
            "guided recommendation set requires recommendation decisions"
        )
    if len({decision.slot for decision in decisions}) != len(decisions):
        raise OwnerTruthKnowledgeRecommendationFeedbackError(
            "guided recommendation set cannot duplicate a slot"
        )
    return _digest(
        {
            "schemaVersion": "owner-truth-guided-recommendation-set-v1",
            "vaultId": context.vault_id,
            "ownerSubjectId": context.owner_subject_id,
            "authorityEpoch": authority_epoch,
            "selected": [
                {
                    "slot": decision.slot.value,
                    "candidateId": decision.candidate_id,
                    "threadId": decision.thread_id,
                    "targetDimension": decision.target_dimension.value,
                    "missingFacet": decision.missing_facet,
                    "questionTemplateId": decision.question_template_id,
                    "policyVersion": decision.policy_version,
                    "expiresAt": (
                        None if decision.expires_at is None else decision.expires_at.isoformat()
                    ),
                }
                for decision in sorted(decisions, key=lambda item: item.slot.value)
            ],
        }
    )


class InMemoryOwnerTruthKnowledgeRecommendationFeedbackRepository:
    """Thread-safe semantic double for feedback receipts and derived policy."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records_by_command: dict[tuple[str, str], dict[str, Any]] = {}
        self._records_by_candidate: dict[tuple[str, str], dict[str, Any]] = {}

    def replay(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthKnowledgeRecommendationFeedbackCommand,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult | None:
        _assert_owner_context(context)
        command_key = (context.vault_id, command.command_id_hash)
        with self._lock:
            existing = self._records_by_command.get(command_key)
            if existing is None:
                return None
            if str(existing.get("payloadHash") or "") != command.payload_hash:
                raise OwnerTruthKnowledgeRecommendationFeedbackConflict(
                    "commandId cannot be reused with different recommendation feedback"
                )
            return _result_from_record(existing, outcome="deduplicated")

    def replay_idempotency(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id: str,
        payload_hash: str,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult | None:
        _assert_owner_context(context)
        command_hash = _sha256(require_nonblank(command_id, field="command_id"))
        normalized_payload_hash = _hash(payload_hash, field="payload_hash")
        command_key = (context.vault_id, command_hash)
        with self._lock:
            existing = self._records_by_command.get(command_key)
            if existing is None:
                return None
            if (
                str(existing.get("ownerSubjectId") or "") != context.owner_subject_id
                or str(existing.get("actorSubjectId") or "") != context.owner_subject_id
            ):
                raise OwnerTruthKnowledgeRecommendationFeedbackAccessDenied(
                    "recommendation feedback replay does not match Owner context"
                )
            if str(existing.get("payloadHash") or "") != normalized_payload_hash:
                raise OwnerTruthKnowledgeRecommendationFeedbackConflict(
                    "commandId cannot be reused with different recommendation feedback"
                )
            return _result_from_record(existing, outcome="deduplicated")

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        _result_from_record(normalized, outcome="created")
        if (
            str(normalized.get("vaultId") or "") != context.vault_id
            or str(normalized.get("ownerSubjectId") or "") != context.owner_subject_id
            or str(normalized.get("actorSubjectId") or "") != context.owner_subject_id
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackAccessDenied(
                "recommendation feedback does not match Owner context"
            )
        command_hash = _hash(normalized.get("commandIdHash"), field="commandIdHash")
        payload_hash = _hash(normalized.get("payloadHash"), field="payloadHash")
        candidate_id = _opaque_identifier(normalized.get("candidateId"), field="candidateId")
        command_key = (context.vault_id, command_hash)
        candidate_key = (context.vault_id, candidate_id)
        with self._lock:
            existing = self._records_by_command.get(command_key)
            if existing is not None:
                if str(existing.get("payloadHash") or "") != payload_hash:
                    raise OwnerTruthKnowledgeRecommendationFeedbackConflict(
                        "commandId cannot be reused with different recommendation feedback"
                    )
                return _result_from_record(existing, outcome="deduplicated")
            if candidate_key in self._records_by_candidate:
                raise OwnerTruthKnowledgeRecommendationFeedbackConflict(
                    "server-planned recommendation already has feedback"
                )
            self._records_by_command[command_key] = normalized
            self._records_by_candidate[candidate_key] = normalized
            return _result_from_record(normalized, outcome="created")

    def current_policy(
        self,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackPolicy:
        _assert_owner_context(context)
        _nonnegative_int(authority_epoch, field="authority_epoch")
        with self._lock:
            rows = tuple(
                record
                for record in self._records_by_command.values()
                if str(record.get("vaultId") or "") == context.vault_id
                and str(record.get("ownerSubjectId") or "") == context.owner_subject_id
                and str(record.get("actorSubjectId") or "") == context.owner_subject_id
                and int(
                    record["authorityEpoch"]
                    if record.get("authorityEpoch") is not None
                    else -1
                )
                == authority_epoch
            )
        return self._policy_from_records(rows)

    @staticmethod
    def _policy_from_records(
        records: tuple[Mapping[str, Any], ...],
    ) -> OwnerTruthKnowledgeRecommendationFeedbackPolicy:
        replaced: set[str] = set()
        dimensions: dict[KnowledgeDimension, int] = {}
        templates: dict[str, int] = {}
        for record in records:
            action = RecommendationFeedbackAction(record.get("feedbackAction"))
            scope = RecommendationFeedbackScope(record.get("feedbackScope"))
            if action is RecommendationFeedbackAction.REPLACE:
                replaced.add(_opaque_identifier(record.get("candidateId"), field="candidateId"))
            elif action is RecommendationFeedbackAction.DEFER:
                # Timing is enforced by the explicit ThreadPreference cooldown,
                # not by permanently suppressing a candidate or topic.
                continue
            elif scope is RecommendationFeedbackScope.DIMENSION:
                dimension = KnowledgeDimension(record.get("targetDimension"))
                dimensions[dimension] = dimensions.get(dimension, 0) + 1
            elif scope is RecommendationFeedbackScope.QUESTION_TEMPLATE:
                template = _opaque_identifier(
                    record.get("questionTemplateId"),
                    field="questionTemplateId",
                )
                templates[template] = templates.get(template, 0) + 1
        return OwnerTruthKnowledgeRecommendationFeedbackPolicy(
            replaced_candidate_ids=frozenset(replaced),
            dimension_penalty_counts=dimensions,
            question_template_penalty_counts=templates,
        )


class PostgresOwnerTruthKnowledgeRecommendationFeedbackRepository:
    """Postgres persistence for append-only, owner-scoped feedback receipts."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def replay(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthKnowledgeRecommendationFeedbackCommand,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult | None:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-recommendation-feedback-command:"
                    f"{context.vault_id}:{command.command_id_hash}",
                ),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    candidate_id, feedback_action, feedback_reason, feedback_scope,
                    slot, thread_id, session_id, expected_session_version,
                    target_dimension, missing_facet, question_template_id,
                    evidence_ref_count, reason_code, command_id_hash, command_payload_hash
                FROM owner_truth.knowledge_recommendation_feedback_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command.command_id_hash),
            )
            existing = cursor.fetchone()
        if existing is None:
            return None
        if str(existing["command_payload_hash"]) != command.payload_hash:
            raise OwnerTruthKnowledgeRecommendationFeedbackConflict(
                "commandId cannot be reused with different recommendation feedback"
            )
        return _result_from_record(self._row_to_record(existing), outcome="deduplicated")

    def replay_idempotency(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id: str,
        payload_hash: str,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult | None:
        _assert_owner_context(context)
        command_hash = _sha256(require_nonblank(command_id, field="command_id"))
        normalized_payload_hash = _hash(payload_hash, field="payload_hash")
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-recommendation-feedback-command:"
                    f"{context.vault_id}:{command_hash}",
                ),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    candidate_id, feedback_action, feedback_reason, feedback_scope,
                    slot, thread_id, session_id, expected_session_version,
                    target_dimension, missing_facet, question_template_id,
                    evidence_ref_count, reason_code, command_id_hash, command_payload_hash
                FROM owner_truth.knowledge_recommendation_feedback_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command_hash),
            )
            existing = cursor.fetchone()
        if existing is None:
            return None
        if (
            str(existing["owner_subject_id"]) != context.owner_subject_id
            or str(existing["actor_subject_id"]) != context.owner_subject_id
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackAccessDenied(
                "recommendation feedback replay does not match Owner context"
            )
        if str(existing["command_payload_hash"]) != normalized_payload_hash:
            raise OwnerTruthKnowledgeRecommendationFeedbackConflict(
                "commandId cannot be reused with different recommendation feedback"
            )
        return _result_from_record(self._row_to_record(existing), outcome="deduplicated")

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        _result_from_record(normalized, outcome="created")
        command_hash = _hash(normalized.get("commandIdHash"), field="commandIdHash")
        payload_hash = _hash(normalized.get("payloadHash"), field="payloadHash")
        candidate_id = _opaque_identifier(normalized.get("candidateId"), field="candidateId")
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-recommendation-feedback-command:"
                    f"{context.vault_id}:{command_hash}",
                ),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    candidate_id, feedback_action, feedback_reason, feedback_scope,
                    slot, thread_id, session_id, expected_session_version,
                    target_dimension, missing_facet, question_template_id,
                    evidence_ref_count, reason_code, command_id_hash, command_payload_hash
                FROM owner_truth.knowledge_recommendation_feedback_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing["command_payload_hash"]) != payload_hash:
                    raise OwnerTruthKnowledgeRecommendationFeedbackConflict(
                        "commandId cannot be reused with different recommendation feedback"
                    )
                return _result_from_record(self._row_to_record(existing), outcome="deduplicated")

            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-recommendation-feedback-candidate:"
                    f"{context.vault_id}:{candidate_id}",
                ),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id
                FROM owner_truth.knowledge_recommendation_feedback_receipts
                WHERE vault_id = %s AND candidate_id = %s
                FOR UPDATE
                """,
                (context.vault_id, candidate_id),
            )
            if cursor.fetchone() is not None:
                raise OwnerTruthKnowledgeRecommendationFeedbackConflict(
                    "server-planned recommendation already has feedback"
                )
            self._assert_current_target(cursor, context=context, record=normalized)
            cursor.execute(
                """
                INSERT INTO owner_truth.knowledge_recommendation_feedback_receipts (
                    id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    candidate_id, feedback_action, feedback_reason, feedback_scope,
                    slot, thread_id, session_id, expected_session_version,
                    target_dimension, missing_facet, question_template_id,
                    selection_policy_version, evidence_ref_count, reason_code,
                    command_id_hash, command_payload_hash, schema_version, ui_schema_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    normalized["feedbackId"],
                    normalized["vaultId"],
                    normalized["ownerSubjectId"],
                    normalized["actorSubjectId"],
                    normalized["authorityEpoch"],
                    candidate_id,
                    normalized["feedbackAction"],
                    normalized["feedbackReason"],
                    normalized["feedbackScope"],
                    normalized["slot"],
                    normalized["threadId"],
                    normalized["sessionId"],
                    normalized["expectedSessionVersion"],
                    normalized["targetDimension"],
                    normalized["missingFacet"],
                    normalized["questionTemplateId"],
                    normalized["selectionPolicyVersion"],
                    normalized["evidenceRefCount"],
                    normalized["reasonCode"],
                    command_hash,
                    payload_hash,
                    normalized["schemaVersion"],
                    normalized["uiSchemaVersion"],
                ),
            )
        return _result_from_record(normalized, outcome="created")

    def current_policy(
        self,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackPolicy:
        _assert_owner_context(context)
        _nonnegative_int(authority_epoch, field="authority_epoch")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT receipt.candidate_id, receipt.feedback_action, receipt.feedback_scope,
                    receipt.target_dimension, receipt.question_template_id
                FROM owner_truth.knowledge_recommendation_feedback_receipts AS receipt
                JOIN owner_truth.vaults AS vault ON vault.vault_id = receipt.vault_id
                WHERE receipt.vault_id = %s
                  AND receipt.owner_subject_id = %s
                  AND receipt.actor_subject_id = %s
                  AND receipt.authority_epoch = %s
                  AND vault.owner_subject_id = %s
                  AND vault.status = 'active'
                  AND vault.authority_epoch = %s
                ORDER BY receipt.created_at ASC, receipt.id ASC
                """,
                (
                    context.vault_id,
                    context.owner_subject_id,
                    context.owner_subject_id,
                    authority_epoch,
                    context.owner_subject_id,
                    authority_epoch,
                ),
            )
            rows = tuple(self._row_to_policy_record(row) for row in cursor.fetchall())
        return InMemoryOwnerTruthKnowledgeRecommendationFeedbackRepository._policy_from_records(rows)

    @staticmethod
    def _assert_current_target(
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> None:
        cursor.execute(
            """
            SELECT vault.owner_subject_id, vault.authority_epoch, vault.status,
                thread.owner_subject_id AS thread_owner_subject_id,
                thread.authority_epoch AS thread_authority_epoch, thread.state AS thread_state,
                session.owner_subject_id AS session_owner_subject_id,
                session.authority_epoch AS session_authority_epoch,
                session.current_thread_id, session.state AS session_state,
                session.boundary AS session_boundary, session.row_version AS session_row_version
            FROM owner_truth.vaults AS vault
            JOIN owner_truth.conversation_threads AS thread
              ON thread.vault_id = vault.vault_id AND thread.id = %s
            JOIN owner_truth.interview_sessions AS session
              ON session.vault_id = vault.vault_id AND session.id = %s
            WHERE vault.vault_id = %s
            FOR SHARE OF vault, thread, session
            """,
            (record["threadId"], record["sessionId"], context.vault_id),
        )
        snapshot = cursor.fetchone()
        if (
            snapshot is None
            or str(snapshot["owner_subject_id"]) != context.owner_subject_id
            or str(snapshot["status"]) != "active"
            or int(snapshot["authority_epoch"]) != int(record["authorityEpoch"])
            or str(snapshot["thread_owner_subject_id"]) != context.owner_subject_id
            or int(snapshot["thread_authority_epoch"]) != int(record["authorityEpoch"])
            or str(snapshot["thread_state"]) != "active"
            or str(snapshot["session_owner_subject_id"]) != context.owner_subject_id
            or int(snapshot["session_authority_epoch"]) != int(record["authorityEpoch"])
            or str(snapshot["current_thread_id"]) != str(record["threadId"])
            or str(snapshot["session_state"]) != "active"
            or str(snapshot["session_boundary"]) != "open"
            or int(snapshot["session_row_version"]) != int(record["expectedSessionVersion"])
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackStale(
                "recommendation feedback must bind the current active open interview session"
            )

    @staticmethod
    def _row_to_record(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "feedbackId": str(row["id"]),
            "vaultId": str(row["vault_id"]),
            "ownerSubjectId": str(row["owner_subject_id"]),
            "actorSubjectId": str(row["actor_subject_id"]),
            "authorityEpoch": int(row["authority_epoch"]),
            "candidateId": str(row["candidate_id"]),
            "feedbackAction": str(row["feedback_action"]),
            "feedbackReason": str(row["feedback_reason"]),
            "feedbackScope": str(row["feedback_scope"]),
            "slot": str(row["slot"]),
            "threadId": str(row["thread_id"]),
            "sessionId": str(row["session_id"]),
            "expectedSessionVersion": int(row["expected_session_version"]),
            "targetDimension": str(row["target_dimension"]),
            "missingFacet": str(row["missing_facet"]),
            "questionTemplateId": str(row["question_template_id"]),
            "evidenceRefCount": int(row["evidence_ref_count"]),
            "reasonCode": str(row["reason_code"]),
            "commandIdHash": str(row["command_id_hash"]),
            "payloadHash": str(row["command_payload_hash"]),
        }

    @staticmethod
    def _row_to_policy_record(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "candidateId": str(row["candidate_id"]),
            "feedbackAction": str(row["feedback_action"]),
            "feedbackScope": str(row["feedback_scope"]),
            "targetDimension": str(row["target_dimension"]),
            "questionTemplateId": str(row["question_template_id"]),
        }

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthKnowledgeRecommendationFeedbackService:
    """Persist only server-revalidated M0-B feedback from the current QA plan."""

    def __init__(
        self,
        store: OwnerTruthKnowledgeRecommendationFeedbackStore,
        *,
        enabled: bool = False,
        thread_cooldown_seconds: int = 7 * 24 * 60 * 60,
    ) -> None:
        _positive_int(thread_cooldown_seconds, field="thread_cooldown_seconds")
        self._store = store
        self._enabled = bool(enabled)
        self._thread_cooldown_seconds = thread_cooldown_seconds

    def submit(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthKnowledgeRecommendationFeedbackCommand,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult:
        _assert_owner_context(context)
        if not isinstance(command, OwnerTruthKnowledgeRecommendationFeedbackCommand):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "recommendation feedback command is required"
            )
        if not self._enabled:
            raise OwnerTruthKnowledgeRecommendationFeedbackUnavailable(
                "recommendation feedback QA contract is disabled"
            )
        if command.feedback_action is RecommendationFeedbackAction.DEFER:
            raise OwnerTruthKnowledgeRecommendationFeedbackUnavailable(
                "timing feedback must use the guided recommendation presentation contract"
            )
        with self._request_unit_of_work(
            correlation_id=(
                "owner-truth-knowledge-recommendation-feedback-"
                f"{context.vault_id}:{command.expected_candidate_id}"
            ),
            command_id=command.command_id_hash,
        ):
            repository = self._store.owner_truth_knowledge_recommendation_feedback_repository()
            replayed = repository.replay(context=context, command=command)
            if replayed is not None:
                return replayed
            return self._record_current_plan(
                repository=repository,
                context=context,
                command=command,
                plan=self._current_plan(context=context),
            )

    def submit_guided_presentation(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthGuidedRecommendationFeedbackCommand,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult:
        """Map a display-only prompt action to the current private plan server-side."""

        _assert_owner_context(context)
        if not isinstance(command, OwnerTruthGuidedRecommendationFeedbackCommand):
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "guided recommendation feedback command is required"
            )
        if not self._enabled:
            raise OwnerTruthKnowledgeRecommendationFeedbackUnavailable(
                "guided recommendation feedback contract is disabled"
            )
        with self._request_unit_of_work(
            correlation_id=(
                "owner-truth-guided-recommendation-feedback-"
                f"{context.vault_id}:{command.recommendation_set_id}"
            ),
            command_id=_sha256(command.command_id),
        ):
            repository = self._store.owner_truth_knowledge_recommendation_feedback_repository()
            replayed = repository.replay_idempotency(
                context=context,
                command_id=command.command_id,
                payload_hash=command.payload_hash,
            )
            if replayed is not None:
                return replayed
            plan = self._current_plan(context=context)
            if plan.selection is None:
                raise OwnerTruthKnowledgeRecommendationFeedbackUnavailable(
                    "current Owner-confirmed recommendation plan is unavailable"
                )
            current_set_id = guided_recommendation_set_id(
                context=context,
                authority_epoch=plan.dimension_read.authority_epoch,
                decisions=plan.selection.selected,
            )
            if current_set_id != command.recommendation_set_id:
                raise OwnerTruthKnowledgeRecommendationFeedbackStale(
                    "guided recommendation set is no longer current"
                )
            decision = self._selected_slot(
                plan.selection.selected,
                slot=command.slot,
            )
            session = self._current_session_for_decision(
                context=context,
                decision=decision,
            )
            feedback_command = command.to_feedback_command(
                decision=decision,
                expected_session_version=session.row_version,
            )
            if command.feedback_action is RecommendationFeedbackAction.DEFER:
                return self._record_guided_timing_feedback(
                    repository=repository,
                    context=context,
                    command=feedback_command,
                    decision=decision,
                    session=session,
                    plan=plan,
                )
            return self._record_current_plan(
                repository=repository,
                context=context,
                command=feedback_command,
                plan=plan,
            )

    def _record_guided_timing_feedback(
        self,
        *,
        repository: OwnerTruthKnowledgeRecommendationFeedbackRepository,
        context: OwnerTruthCommandContext,
        command: OwnerTruthKnowledgeRecommendationFeedbackCommand,
        decision: RecommendationDecision,
        session: Any,
        plan: Any,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult:
        """Persist an explicit defer receipt, then enter the existing cooldown flow.

        The feedback receipt is intentionally written while the session remains
        active/open because its database trigger verifies that exact authority
        boundary.  The surrounding request unit of work makes the receipt,
        continuation cue and cooldown transition atomic in Postgres.
        """

        if command.feedback_action is not RecommendationFeedbackAction.DEFER:
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "guided timing feedback requires defer action"
            )
        if command.feedback_reason is not RecommendationFeedbackReason.TIMING:
            raise OwnerTruthKnowledgeRecommendationFeedbackError(
                "guided timing feedback requires timing reason"
            )
        memory_version_id = self._continuation_memory_version_id(decision=decision)
        feedback = self._record_current_plan(
            repository=repository,
            context=context,
            command=command,
            plan=plan,
        )
        cue_command = OwnerTruthSavedContinuationCueCommand(
            command_id=f"{command.command_id}:guided-defer-continuation",
            thread_id=decision.thread_id,
            session_id=session.session_id,
            expected_session_version=command.expected_session_version,
            memory_version_id=memory_version_id,
            target_dimension=decision.target_dimension,
            missing_facet=decision.missing_facet,
        )
        boundary_command = SetInterviewBoundaryCommand(
            command_id=f"{command.command_id}:guided-defer-cooldown",
            thread_id=decision.thread_id,
            session_id=session.session_id,
            expected_session_version=command.expected_session_version,
            boundary=InterviewBoundary.COOLDOWN,
        )
        try:
            OwnerTruthSavedContinuationCueService(
                self._store,
                enabled=True,
            ).create(context=context, command=cue_command)
            OwnerTruthThreadPreferenceService(
                self._store,
                enabled=True,
                cooldown_seconds=self._thread_cooldown_seconds,
            ).set_boundary(context=context, command=boundary_command)
        except (
            OwnerTruthSavedContinuationCueError,
            OwnerTruthThreadPreferenceError,
        ) as error:
            # The client never supplied these authority fields.  A failure here
            # means the server-planned active/open state changed or no longer
            # safely supports the complete defer transition.
            raise OwnerTruthKnowledgeRecommendationFeedbackStale(
                "guided timing feedback no longer binds the current continuation state"
            ) from error
        return feedback

    @staticmethod
    def _continuation_memory_version_id(*, decision: RecommendationDecision) -> str:
        if not decision.evidence_refs:
            raise OwnerTruthKnowledgeRecommendationFeedbackUnavailable(
                "guided timing feedback requires current confirmed evidence"
            )
        try:
            return require_uuid(
                min(decision.evidence_refs),
                field="guided_timing_memory_version_id",
            )
        except OwnerTruthContractError as error:
            raise OwnerTruthKnowledgeRecommendationFeedbackUnavailable(
                "guided timing feedback requires current confirmed evidence"
            ) from error

    def _current_plan(self, *, context: OwnerTruthCommandContext) -> Any:
        try:
            # Delayed to avoid a module import cycle: the read adapter consumes
            # feedback policy while this writer revalidates its current output.
            from app.services.owner_truth_knowledge_recommendation_read import (
                OwnerTruthKnowledgeRecommendationReadService,
            )

            plan = OwnerTruthKnowledgeRecommendationReadService(self._store).plan(context=context)
        except OwnerTruthContractError as error:
            raise OwnerTruthKnowledgeRecommendationFeedbackUnavailable(
                "current Owner-confirmed recommendation plan is unavailable"
            ) from error
        if (
            plan.state is not OwnerTruthKnowledgeDimensionReadState.READY
            or plan.selection is None
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackUnavailable(
                "current Owner-confirmed recommendation plan is unavailable"
            )
        return plan

    def _record_current_plan(
        self,
        *,
        repository: OwnerTruthKnowledgeRecommendationFeedbackRepository,
        context: OwnerTruthCommandContext,
        command: OwnerTruthKnowledgeRecommendationFeedbackCommand,
        plan: Any,
    ) -> OwnerTruthKnowledgeRecommendationFeedbackResult:
        decision = self._selected_decision(
            plan.selection.selected,
            command=command,
        )
        session = self._current_session_for_decision(
            context=context,
            decision=decision,
        )
        if (
            session.thread_id != decision.thread_id
            or session.row_version != command.expected_session_version
            or session.state is not InterviewSessionState.ACTIVE
            or session.boundary is not InterviewBoundary.OPEN
            or session.authority_epoch != plan.dimension_read.authority_epoch
        ):
            raise OwnerTruthKnowledgeRecommendationFeedbackStale(
                "server-planned recommendation no longer binds the current active open interview session"
            )
        record = {
            "feedbackId": str(
                uuid5(_FEEDBACK_NAMESPACE, f"{context.vault_id}:{command.command_id_hash}")
            ),
            "vaultId": context.vault_id,
            "ownerSubjectId": context.owner_subject_id,
            "actorSubjectId": context.actor_subject_id,
            "authorityEpoch": plan.dimension_read.authority_epoch,
            "candidateId": decision.candidate_id,
            "feedbackAction": command.feedback_action.value,
            "feedbackReason": command.feedback_reason.value,
            "feedbackScope": command.feedback_scope.value,
            "slot": decision.slot.value,
            "threadId": decision.thread_id,
            "sessionId": session.session_id,
            "expectedSessionVersion": command.expected_session_version,
            "targetDimension": decision.target_dimension.value,
            "missingFacet": decision.missing_facet,
            "questionTemplateId": decision.question_template_id,
            "selectionPolicyVersion": decision.policy_version,
            "evidenceRefCount": len(decision.evidence_refs),
            "reasonCode": self._reason_code(command),
            "commandIdHash": command.command_id_hash,
            "payloadHash": command.payload_hash,
            "schemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_SCHEMA_VERSION,
            "uiSchemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_UI_SCHEMA_VERSION,
        }
        return repository.record(context=context, record=record)

    @staticmethod
    def _selected_decision(
        decisions: tuple[RecommendationDecision, ...],
        *,
        command: OwnerTruthKnowledgeRecommendationFeedbackCommand,
    ) -> RecommendationDecision:
        matches = tuple(
            decision
            for decision in decisions
            if decision.candidate_id == command.expected_candidate_id
        )
        if len(matches) != 1:
            raise OwnerTruthKnowledgeRecommendationFeedbackStale(
                "server-planned recommendation is no longer selected"
            )
        return matches[0]

    @staticmethod
    def _selected_slot(
        decisions: tuple[RecommendationDecision, ...],
        *,
        slot: RecommendationSlot,
    ) -> RecommendationDecision:
        matches = tuple(decision for decision in decisions if decision.slot is slot)
        if len(matches) != 1:
            raise OwnerTruthKnowledgeRecommendationFeedbackStale(
                "guided recommendation slot is no longer selected"
            )
        return matches[0]

    def _current_session_for_decision(
        self,
        *,
        context: OwnerTruthCommandContext,
        decision: RecommendationDecision,
    ) -> Any:
        conversation = self._store.owner_truth_conversation_repository()
        try:
            session_id = self._session_id_for_decision(
                conversation=conversation,
                context=context,
                decision=decision,
            )
            return conversation.get_interview_session(
                session_id=session_id,
                context=context,
            )
        except OwnerTruthConversationAccessDenied as error:
            raise OwnerTruthKnowledgeRecommendationFeedbackAccessDenied(str(error)) from error
        except OwnerTruthContractError as error:
            raise OwnerTruthKnowledgeRecommendationFeedbackStale(
                "server-planned recommendation no longer has current conversation authority"
            ) from error

    @staticmethod
    def _session_id_for_decision(
        *,
        conversation: Any,
        context: OwnerTruthCommandContext,
        decision: RecommendationDecision,
    ) -> str:
        thread = conversation.get_interview_thread_authority(
            thread_id=decision.thread_id,
            context=context,
        )
        return thread.session_id

    @staticmethod
    def _reason_code(
        command: OwnerTruthKnowledgeRecommendationFeedbackCommand,
    ) -> str:
        if command.feedback_action is RecommendationFeedbackAction.DEFER:
            return "userDeferredTiming"
        if command.feedback_action is RecommendationFeedbackAction.REPLACE:
            return "userRequestedReplacement"
        if command.feedback_reason is RecommendationFeedbackReason.TOPIC_PREFERENCE:
            return "topicNotInterested"
        return "recommendationTypeNotInterested"

    def _request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


def knowledge_recommendation_feedback_summary(
    result: OwnerTruthKnowledgeRecommendationFeedbackResult,
) -> dict[str, Any]:
    if not isinstance(result, OwnerTruthKnowledgeRecommendationFeedbackResult):
        raise OwnerTruthKnowledgeRecommendationFeedbackError(
            "recommendation feedback result is required"
        )
    return {
        "schemaVersion": OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_SCHEMA_VERSION,
        "status": result.outcome,
        "feedbackId": result.feedback_id,
        "candidateId": result.candidate_id,
        "feedbackAction": result.feedback_action.value,
        "feedbackReason": result.feedback_reason.value,
        "feedbackScope": result.feedback_scope.value,
        "slot": result.slot.value,
        "threadId": result.thread_id,
        "sessionId": result.session_id,
        "expectedSessionVersion": result.expected_session_version,
        "targetDimension": result.target_dimension.value,
        "missingFacet": result.missing_facet,
        "questionTemplateId": result.question_template_id,
        "authorityEpoch": result.authority_epoch,
        "evidenceRefCount": result.evidence_ref_count,
        "reasonCode": result.reason_code,
    }


__all__ = [
    "InMemoryOwnerTruthKnowledgeRecommendationFeedbackRepository",
    "OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_SCHEMA_VERSION",
    "OWNER_TRUTH_KNOWLEDGE_RECOMMENDATION_FEEDBACK_UI_SCHEMA_VERSION",
    "OwnerTruthKnowledgeRecommendationFeedbackAccessDenied",
    "OwnerTruthKnowledgeRecommendationFeedbackCommand",
    "OwnerTruthKnowledgeRecommendationFeedbackConflict",
    "OwnerTruthKnowledgeRecommendationFeedbackError",
    "OwnerTruthKnowledgeRecommendationFeedbackPolicy",
    "OwnerTruthKnowledgeRecommendationFeedbackRepository",
    "OwnerTruthKnowledgeRecommendationFeedbackResult",
    "OwnerTruthKnowledgeRecommendationFeedbackService",
    "OwnerTruthKnowledgeRecommendationFeedbackStale",
    "OwnerTruthKnowledgeRecommendationFeedbackUnavailable",
    "PostgresOwnerTruthKnowledgeRecommendationFeedbackRepository",
    "RecommendationFeedbackAction",
    "RecommendationFeedbackReason",
    "RecommendationFeedbackScope",
    "knowledge_recommendation_feedback_summary",
]
