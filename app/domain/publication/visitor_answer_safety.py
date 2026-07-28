"""Default-deny Visitor answer safety contract.

This G0 module defines the admission rules for a future text-only Visitor
experience. It carries opaque identifiers, hashes, timestamps, and enum state
only. It never accepts readable Visitor input, reads Owner Truth, queries a
public store, calls a model, writes feedback, closes a session, or exposes a
route. Every result remains non-admitting until later release gates supply
separate gateway, provider, retention, and operational evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID

from .share_grant_session import (
    PublicationAdultVerificationState,
    PublicationShareGrant,
    PublicationShareGrantState,
    PublicationVisitorIdentity,
    PublicationVisitorRelationshipOrigin,
    PublicationVisitorSessionProposal,
)


PUBLICATION_VISITOR_ANSWER_SAFETY_G0_SCHEMA_VERSION = "publication-visitor-answer-safety-g0-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_MAX_CONTINUOUS_USE = timedelta(hours=2)


class PublicationVisitorAnswerSafetyError(ValueError):
    """Raised when a value-minimized Visitor answer envelope is malformed."""


class PublicationVisitorInteractionKind(str, Enum):
    ANSWER = "answer"
    REPORT = "report"
    EXIT = "exit"


class PublicationVisitorPublicContextSource(str, Enum):
    PUBLICATION_VERSION = "publicationVersion"
    PRIVATE_OWNER_MEMORY = "privateOwnerMemory"
    PRIVATE_KBLITE = "privateKBLite"
    OWNER_PERSONA = "ownerPersona"
    VOICE_OR_DIGITAL_HUMAN = "voiceOrDigitalHuman"
    UNKNOWN = "unknown"


class PublicationVisitorPublicationState(str, Enum):
    PUBLISHED = "published"
    SUSPENDED = "suspended"
    WITHDRAWN = "withdrawn"
    UNKNOWN = "unknown"


class PublicationVisitorQuestionClass(str, Enum):
    GENERAL = "general"
    PAYMENT_OR_FINANCIAL_DECISION = "paymentOrFinancialDecision"
    HIGH_STAKES_DECISION = "highStakesDecision"


class PublicationVisitorPromptRisk(str, Enum):
    CLEAR = "clear"
    PROMPT_INJECTION = "promptInjection"
    PRIVATE_EXTRACTION = "privateExtraction"
    UNKNOWN = "unknown"


class PublicationVisitorRiskState(str, Enum):
    NONE = "none"
    CRISIS = "crisis"
    UNKNOWN = "unknown"


class PublicationVisitorRateLimitState(str, Enum):
    ALLOWED = "allowed"
    LIMIT_REACHED = "limitReached"
    UNKNOWN = "unknown"


class PublicationVisitorExitChannel(str, Enum):
    NONE = "none"
    UI = "ui"
    VOICE = "voice"
    KEYWORD = "keyword"


class PublicationVisitorReportKind(str, Enum):
    NONE = "none"
    SAFETY_REPORT = "safetyReport"
    ACCESS_ISSUE = "accessIssue"
    CONTENT_CONCERN = "contentConcern"


class PublicationVisitorAnswerSafetyDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    ADULT_VERIFICATION_DENIED = "adult_verification_denied"
    FAMILY_AUTO_GRANT_DENIED = "family_auto_grant_denied"
    GRANT_SCOPE_DENIED = "grant_scope_denied"
    GRANT_INACTIVE = "grant_inactive"
    GRANT_EXPIRED = "grant_expired"
    SESSION_SCOPE_DENIED = "session_scope_denied"
    SESSION_EXPIRED = "session_expired"
    EXIT_REQUESTED = "exit_requested"
    CRISIS_SAFETY_ASSISTANT_REQUIRED = "crisis_safety_assistant_required"
    PROMPT_INJECTION_BLOCKED = "prompt_injection_blocked"
    RISK_CLASSIFICATION_REQUIRED = "risk_classification_required"
    RATE_LIMIT_DENIED = "rate_limit_denied"
    PUBLICATION_INACCESSIBLE = "publication_inaccessible"
    PRIVATE_CONTEXT_REJECTED = "private_context_rejected"
    PUBLIC_EVIDENCE_REQUIRED = "public_evidence_required"
    PERSONA_DECISION_DENIED = "persona_decision_denied"
    CONTINUOUS_USE_REMINDER_REQUIRED = "continuous_use_reminder_required"
    REPORT_RECEIPT_REQUIRED = "report_receipt_required"
    POLICY_DISABLED = "policy_disabled"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise PublicationVisitorAnswerSafetyError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise PublicationVisitorAnswerSafetyError(f"{field} must be a UUID") from exc


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise PublicationVisitorAnswerSafetyError(f"{field} must be a SHA-256 digest")
    return normalized


def _instant(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PublicationVisitorAnswerSafetyError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PublicationVisitorAnswerRequest:
    """Hash-only metadata for a future Visitor text answer, report, or exit."""

    request_id: str
    vault_id: str
    publication_id: str
    publication_version_id: str
    visitor_session_id: str
    request_hash: str
    policy_hash: str
    publication_state: PublicationVisitorPublicationState
    current_public_version: bool
    context_source: PublicationVisitorPublicContextSource
    public_version_hash: str
    public_citation_hashes: tuple[str, ...]
    continuous_use_started_at: datetime
    interaction_kind: PublicationVisitorInteractionKind = PublicationVisitorInteractionKind.ANSWER
    question_class: PublicationVisitorQuestionClass = PublicationVisitorQuestionClass.GENERAL
    prompt_risk: PublicationVisitorPromptRisk = PublicationVisitorPromptRisk.UNKNOWN
    risk_state: PublicationVisitorRiskState = PublicationVisitorRiskState.UNKNOWN
    rate_limit_state: PublicationVisitorRateLimitState = PublicationVisitorRateLimitState.UNKNOWN
    exit_channel: PublicationVisitorExitChannel = PublicationVisitorExitChannel.NONE
    report_kind: PublicationVisitorReportKind = PublicationVisitorReportKind.NONE

    def __post_init__(self) -> None:
        for field_name in (
            "request_id",
            "publication_id",
            "publication_version_id",
            "visitor_session_id",
        ):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field=field_name))
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        for field_name in ("request_hash", "policy_hash", "public_version_hash"):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field=field_name))
        object.__setattr__(self, "publication_state", PublicationVisitorPublicationState(self.publication_state))
        object.__setattr__(self, "current_public_version", bool(self.current_public_version))
        object.__setattr__(self, "context_source", PublicationVisitorPublicContextSource(self.context_source))
        citations = tuple(_digest(item, field="public_citation_hash") for item in self.public_citation_hashes)
        if len(citations) != len(set(citations)):
            raise PublicationVisitorAnswerSafetyError("public_citation_hashes must be unique")
        object.__setattr__(self, "public_citation_hashes", tuple(sorted(citations)))
        object.__setattr__(
            self,
            "continuous_use_started_at",
            _instant(self.continuous_use_started_at, field="continuous_use_started_at"),
        )
        object.__setattr__(self, "interaction_kind", PublicationVisitorInteractionKind(self.interaction_kind))
        object.__setattr__(self, "question_class", PublicationVisitorQuestionClass(self.question_class))
        object.__setattr__(self, "prompt_risk", PublicationVisitorPromptRisk(self.prompt_risk))
        object.__setattr__(self, "risk_state", PublicationVisitorRiskState(self.risk_state))
        object.__setattr__(self, "rate_limit_state", PublicationVisitorRateLimitState(self.rate_limit_state))
        object.__setattr__(self, "exit_channel", PublicationVisitorExitChannel(self.exit_channel))
        object.__setattr__(self, "report_kind", PublicationVisitorReportKind(self.report_kind))


@dataclass(frozen=True)
class PublicationVisitorAnswerSafetyResult:
    disposition: PublicationVisitorAnswerSafetyDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    session_elapsed_seconds: int | None = None
    requires_continuous_use_reminder: bool = False
    requires_deterministic_exit: bool = False
    requires_neutral_crisis_assistant: bool = False
    report_receipt_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disposition",
            PublicationVisitorAnswerSafetyDisposition(self.disposition),
        )
        reasons = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise PublicationVisitorAnswerSafetyError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.scope_hash is not None:
            object.__setattr__(self, "scope_hash", _digest(self.scope_hash, field="scope_hash"))
        if self.session_elapsed_seconds is not None:
            if isinstance(self.session_elapsed_seconds, bool) or self.session_elapsed_seconds < 0:
                raise PublicationVisitorAnswerSafetyError("session_elapsed_seconds must be non-negative")
            object.__setattr__(self, "session_elapsed_seconds", int(self.session_elapsed_seconds))
        for field_name in (
            "requires_continuous_use_reminder",
            "requires_deterministic_exit",
            "requires_neutral_crisis_assistant",
            "report_receipt_required",
        ):
            object.__setattr__(self, field_name, bool(getattr(self, field_name)))

    @property
    def ai_disclosure_required(self) -> bool:
        return True

    @property
    def answer_allowed(self) -> bool:
        return False

    @property
    def public_query_allowed(self) -> bool:
        return False

    @property
    def provider_call_allowed(self) -> bool:
        return False

    @property
    def owner_memory_read_allowed(self) -> bool:
        return False

    @property
    def owner_persona_allowed(self) -> bool:
        return False

    @property
    def voice_or_digital_human_allowed(self) -> bool:
        return False

    @property
    def feedback_persisted(self) -> bool:
        return False

    @property
    def session_closed(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "aiDisclosureRequired": self.ai_disclosure_required,
            "answerAllowed": self.answer_allowed,
            "feedbackPersisted": self.feedback_persisted,
            "ownerMemoryReadAllowed": self.owner_memory_read_allowed,
            "ownerPersonaAllowed": self.owner_persona_allowed,
            "providerCallAllowed": self.provider_call_allowed,
            "publicQueryAllowed": self.public_query_allowed,
            "reasonCodes": list(self.reason_codes),
            "releaseVisible": False,
            "reportReceiptRequired": self.report_receipt_required,
            "requiresContinuousUseReminder": self.requires_continuous_use_reminder,
            "requiresDeterministicExit": self.requires_deterministic_exit,
            "requiresNeutralCrisisAssistant": self.requires_neutral_crisis_assistant,
            "schemaVersion": PUBLICATION_VISITOR_ANSWER_SAFETY_G0_SCHEMA_VERSION,
            "sessionClosed": self.session_closed,
            "status": self.disposition.value,
            "voiceOrDigitalHumanAllowed": self.voice_or_digital_human_allowed,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.session_elapsed_seconds is not None:
            summary["sessionElapsedSeconds"] = self.session_elapsed_seconds
        return summary


def _scope_hash(
    *,
    grant: PublicationShareGrant,
    visitor: PublicationVisitorIdentity,
    session: PublicationVisitorSessionProposal,
    request: PublicationVisitorAnswerRequest,
) -> str:
    return _hash(
        {
            "grantId": grant.grant_id,
            "publicationId": request.publication_id,
            "publicationVersionId": request.publication_version_id,
            "requestHash": request.request_hash,
            "sessionId": session.session_id,
            "visitorSubjectHash": visitor.subject_hash,
            "visitorSessionId": request.visitor_session_id,
            "vaultId": request.vault_id,
        }
    )


def _result(
    disposition: PublicationVisitorAnswerSafetyDisposition,
    reason: str,
    *,
    scope_hash: str | None = None,
    session_elapsed_seconds: int | None = None,
    requires_continuous_use_reminder: bool = False,
    requires_deterministic_exit: bool = False,
    requires_neutral_crisis_assistant: bool = False,
    report_receipt_required: bool = False,
) -> PublicationVisitorAnswerSafetyResult:
    return PublicationVisitorAnswerSafetyResult(
        disposition=disposition,
        reason_codes=(reason,),
        scope_hash=scope_hash,
        session_elapsed_seconds=session_elapsed_seconds,
        requires_continuous_use_reminder=requires_continuous_use_reminder,
        requires_deterministic_exit=requires_deterministic_exit,
        requires_neutral_crisis_assistant=requires_neutral_crisis_assistant,
        report_receipt_required=report_receipt_required,
    )


def evaluate_publication_visitor_answer_safety(
    *,
    grant: PublicationShareGrant | object,
    visitor: PublicationVisitorIdentity | object,
    session: PublicationVisitorSessionProposal | object,
    request: PublicationVisitorAnswerRequest | object,
    now: datetime | None = None,
    enabled: bool = False,
) -> PublicationVisitorAnswerSafetyResult:
    """Evaluate a future Visitor action without answering, querying, or writing."""

    if enabled is not True:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.SHADOW_DISABLED,
            "publicationVisitorAnswerSafetyShadowDisabled",
        )
    if not all(
        (
            isinstance(grant, PublicationShareGrant),
            isinstance(visitor, PublicationVisitorIdentity),
            isinstance(session, PublicationVisitorSessionProposal),
            isinstance(request, PublicationVisitorAnswerRequest),
        )
    ):
        return _result(
            PublicationVisitorAnswerSafetyDisposition.INVALID_CONTEXT,
            "invalidPublicationVisitorAnswerSafetyContext",
        )

    if visitor.relationship_origin is PublicationVisitorRelationshipOrigin.FAMILY_DERIVED:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.FAMILY_AUTO_GRANT_DENIED,
            "familyRelationshipDoesNotImplyVisitorAnswerAuthority",
        )
    if visitor.adult_verification is not PublicationAdultVerificationState.VERIFIED:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.ADULT_VERIFICATION_DENIED,
            "visitorAdultVerificationRequired",
        )
    if (
        grant.vault_id != request.vault_id
        or grant.publication_id != request.publication_id
        or grant.publication_version_id != request.publication_version_id
        or grant.grantee_subject_hash != visitor.subject_hash
    ):
        return _result(
            PublicationVisitorAnswerSafetyDisposition.GRANT_SCOPE_DENIED,
            "visitorAnswerGrantScopeMismatch",
        )
    if (
        session.session_id != request.visitor_session_id
        or session.grant_id != grant.grant_id
        or session.publication_id != grant.publication_id
        or session.publication_version_id != grant.publication_version_id
        or session.visitor_subject_hash != visitor.subject_hash
    ):
        return _result(
            PublicationVisitorAnswerSafetyDisposition.SESSION_SCOPE_DENIED,
            "visitorAnswerSessionScopeMismatch",
        )

    scope_hash = _scope_hash(grant=grant, visitor=visitor, session=session, request=request)
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if grant.state is not PublicationShareGrantState.ACTIVE:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.GRANT_INACTIVE,
            "visitorAnswerGrantNotActive",
            scope_hash=scope_hash,
        )
    if grant.expires_at <= instant:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.GRANT_EXPIRED,
            "visitorAnswerGrantExpired",
            scope_hash=scope_hash,
        )
    if (
        session.expires_at <= instant
        or session.expires_at > grant.expires_at
        or session.expected_grant_use_count != grant.use_count
        or request.continuous_use_started_at < session.issued_at
    ):
        return _result(
            PublicationVisitorAnswerSafetyDisposition.SESSION_EXPIRED,
            "visitorAnswerSessionExpiredOrUnbound",
            scope_hash=scope_hash,
        )

    session_elapsed_seconds = max(0, int((instant - request.continuous_use_started_at).total_seconds()))
    if request.interaction_kind is PublicationVisitorInteractionKind.EXIT or (
        request.exit_channel is not PublicationVisitorExitChannel.NONE
    ):
        return _result(
            PublicationVisitorAnswerSafetyDisposition.EXIT_REQUESTED,
            "visitorDeterministicExitRequested",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
            requires_deterministic_exit=True,
        )
    if request.interaction_kind is PublicationVisitorInteractionKind.REPORT:
        if request.report_kind is PublicationVisitorReportKind.NONE:
            return _result(
                PublicationVisitorAnswerSafetyDisposition.INVALID_CONTEXT,
                "visitorReportKindRequired",
                scope_hash=scope_hash,
                session_elapsed_seconds=session_elapsed_seconds,
            )
        return _result(
            PublicationVisitorAnswerSafetyDisposition.REPORT_RECEIPT_REQUIRED,
            "visitorReportRequiresFutureReceiptWriter",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
            report_receipt_required=True,
        )
    if request.risk_state is PublicationVisitorRiskState.CRISIS:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.CRISIS_SAFETY_ASSISTANT_REQUIRED,
            "visitorCrisisRequiresNeutralSafetyAssistant",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
            requires_neutral_crisis_assistant=True,
        )
    if request.risk_state is PublicationVisitorRiskState.UNKNOWN:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.RISK_CLASSIFICATION_REQUIRED,
            "visitorRiskClassificationRequired",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
        )
    if request.prompt_risk is not PublicationVisitorPromptRisk.CLEAR:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.PROMPT_INJECTION_BLOCKED,
            "visitorPromptRiskMustBeClear",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
        )
    if request.rate_limit_state is not PublicationVisitorRateLimitState.ALLOWED:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.RATE_LIMIT_DENIED,
            "visitorRateLimitNotAdmitted",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
        )
    if request.publication_state is not PublicationVisitorPublicationState.PUBLISHED or not request.current_public_version:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.PUBLICATION_INACCESSIBLE,
            "visitorAnswerRequiresCurrentPublishedVersion",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
        )
    if request.context_source in {
        PublicationVisitorPublicContextSource.PRIVATE_OWNER_MEMORY,
        PublicationVisitorPublicContextSource.PRIVATE_KBLITE,
        PublicationVisitorPublicContextSource.OWNER_PERSONA,
        PublicationVisitorPublicContextSource.VOICE_OR_DIGITAL_HUMAN,
    }:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.PRIVATE_CONTEXT_REJECTED,
            "visitorAnswerCannotUsePrivateOwnerContext",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
        )
    if (
        request.context_source is not PublicationVisitorPublicContextSource.PUBLICATION_VERSION
        or not request.public_citation_hashes
    ):
        return _result(
            PublicationVisitorAnswerSafetyDisposition.PUBLIC_EVIDENCE_REQUIRED,
            "visitorAnswerRequiresPublicCitationOnly",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
        )
    if request.question_class is not PublicationVisitorQuestionClass.GENERAL:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.PERSONA_DECISION_DENIED,
            "visitorPersonaCannotProvidePaymentOrHighStakesDecision",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
        )
    if timedelta(seconds=session_elapsed_seconds) >= _MAX_CONTINUOUS_USE:
        return _result(
            PublicationVisitorAnswerSafetyDisposition.CONTINUOUS_USE_REMINDER_REQUIRED,
            "visitorContinuousUseTwoHourReminderRequired",
            scope_hash=scope_hash,
            session_elapsed_seconds=session_elapsed_seconds,
            requires_continuous_use_reminder=True,
        )
    return _result(
        PublicationVisitorAnswerSafetyDisposition.POLICY_DISABLED,
        "publicationVisitorAnswerProviderAndGatewayPolicyDisabled",
        scope_hash=scope_hash,
        session_elapsed_seconds=session_elapsed_seconds,
    )


__all__ = [
    "PUBLICATION_VISITOR_ANSWER_SAFETY_G0_SCHEMA_VERSION",
    "PublicationVisitorAnswerRequest",
    "PublicationVisitorAnswerSafetyDisposition",
    "PublicationVisitorAnswerSafetyError",
    "PublicationVisitorAnswerSafetyResult",
    "PublicationVisitorExitChannel",
    "PublicationVisitorInteractionKind",
    "PublicationVisitorPromptRisk",
    "PublicationVisitorPublicationState",
    "PublicationVisitorPublicContextSource",
    "PublicationVisitorQuestionClass",
    "PublicationVisitorRateLimitState",
    "PublicationVisitorReportKind",
    "PublicationVisitorRiskState",
    "evaluate_publication_visitor_answer_safety",
]
