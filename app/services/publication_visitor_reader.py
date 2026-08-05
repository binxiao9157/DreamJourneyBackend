"""Default-off Visitor reads and deterministic public-projection answers.

This is deliberately a closed-beta service. It accepts an authenticated
Visitor session credential only to re-check a previously admitted scope, reads
only the immutable public projection, and never queries Owner Truth, KBLite,
private Echo context, a voice profile, or an external model provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Protocol
from uuid import UUID

from app.services.publication_visitor_access import (
    DenyPublicationVisitorEligibilityResolver,
    PublicationVisitorAccessDisabled,
    PublicationVisitorAccessError,
    PublicationVisitorAdultVerificationRequired,
    PublicationVisitorEligibility,
    PublicationVisitorEligibilityResolver,
)


PUBLICATION_VISITOR_READER_SCHEMA_VERSION = "publication-visitor-reader-v1"
_CREDENTIAL_MINIMUM_LENGTH = 24
_CREDENTIAL_MAXIMUM_LENGTH = 256
_QUESTION_MAXIMUM_LENGTH = 800
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_PROMPT_INJECTION_MARKERS = (
    "ignore previous",
    "ignore all previous",
    "system prompt",
    "prompt injection",
    "private record",
    "private archive",
    "private memory",
    "owner truth",
    "提示词",
    "忽略之前",
    "忽略上述",
    "私有档案",
    "私人档案",
    "kblite",
    "candidate",
)
_HIGH_STAKES_MARKERS = (
    "diagnosis",
    "treatment",
    "prescription",
    "medication",
    "investment",
    "stock",
    "finance",
    "payment",
    "transfer",
    "loan",
    "诊断",
    "治疗",
    "处方",
    "用药",
    "投资",
    "股票",
    "理财",
    "支付",
    "转账",
    "贷款",
)


def _uuid(value: object, *, field_name: str) -> str:
    try:
        return str(UUID(str(value or "").strip()))
    except (TypeError, ValueError) as exc:
        raise PublicationVisitorAccessError(f"{field_name} must be a UUID") from exc


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _credential(value: object) -> str:
    normalized = str(value or "").strip()
    if not (_CREDENTIAL_MINIMUM_LENGTH <= len(normalized) <= _CREDENTIAL_MAXIMUM_LENGTH):
        raise PublicationVisitorAccessError("sessionCredential must be an opaque credential")
    return normalized


def _visitor_subject_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256:
        raise PublicationVisitorAccessError("visitor_subject_id must be an opaque identifier")
    return normalized


@dataclass(frozen=True)
class PublicationVisitorProjectionReadResult:
    """The only readable fields from a Visitor-bound public projection."""

    session_id: str
    publication_id: str
    publication_version_id: str
    expires_at: datetime
    display_title: str
    display_body: str
    ai_disclosure: str
    projection_hash: str
    public_citation_hash: str

    def __post_init__(self) -> None:
        for field_name in ("session_id", "publication_id", "publication_version_id"):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field_name=field_name))
        if not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None:
            raise PublicationVisitorAccessError("expires_at must be timezone-aware")
        object.__setattr__(self, "expires_at", self.expires_at.astimezone(timezone.utc))
        for field_name, maximum in (("display_title", 120), ("display_body", 12000), ("ai_disclosure", 300)):
            value = str(getattr(self, field_name) or "").strip()
            if not value or len(value) > maximum:
                raise PublicationVisitorAccessError(f"{field_name} is not a valid public projection field")
            object.__setattr__(self, field_name, value)
        for field_name in ("projection_hash", "public_citation_hash"):
            value = str(getattr(self, field_name) or "").strip().lower()
            if not _SHA256_PATTERN.fullmatch(value):
                raise PublicationVisitorAccessError(f"{field_name} must be a SHA-256 digest")
            object.__setattr__(self, field_name, value)

    def payload(self) -> dict[str, object]:
        return {
            "schemaVersion": PUBLICATION_VISITOR_READER_SCHEMA_VERSION,
            "visitorSessionId": self.session_id,
            "publicationId": self.publication_id,
            "publicationVersionId": self.publication_version_id,
            "expiresAt": self.expires_at.isoformat(),
            "title": self.display_title,
            "body": self.display_body,
            "aiDisclosure": self.ai_disclosure,
            "source": {
                "kind": "publicationVersion",
                "projectionHash": self.projection_hash,
                "publicCitationHash": self.public_citation_hash,
            },
            "answerBoundary": {
                "identityDisclosureRequired": True,
                "privateContextAllowed": False,
                "providerCallAllowed": False,
                "unknownFallbackRequired": True,
            },
        }


@dataclass(frozen=True)
class PublicationVisitorAnswerResult:
    projection: PublicationVisitorProjectionReadResult
    answer_kind: str
    answer_text: str
    uncertainty_code: str
    reason_code: str

    def __post_init__(self) -> None:
        if self.answer_kind not in {"excerpt", "unknown"}:
            raise PublicationVisitorAccessError("answer_kind is invalid")
        for field_name, maximum in (("answer_text", 12400), ("uncertainty_code", 128), ("reason_code", 128)):
            value = str(getattr(self, field_name) or "").strip()
            if not value or len(value) > maximum:
                raise PublicationVisitorAccessError(f"{field_name} is invalid")
            object.__setattr__(self, field_name, value)

    def payload(self) -> dict[str, object]:
        payload = self.projection.payload()
        payload["answer"] = {
            "kind": self.answer_kind,
            "text": self.answer_text,
            "identityDisclosure": "This response is generated by AI from the approved public projection only.",
            "source": "publicationVersion",
            "publicCitationHash": self.projection.public_citation_hash,
            "uncertainty": self.uncertainty_code,
            "reasonCode": self.reason_code,
        }
        return payload


class PublicationVisitorReaderRepository(Protocol):
    def read_public_projection(
        self,
        *,
        visitor_subject_hash: str,
        eligibility: PublicationVisitorEligibility,
        session_id: str,
        session_credential_hash: str,
        now: datetime,
    ) -> PublicationVisitorProjectionReadResult:
        ...


class PublicationVisitorReaderService:
    """Revalidates Visitor scope before each public-projection read or answer."""

    def __init__(
        self,
        repository: PublicationVisitorReaderRepository,
        *,
        eligibility_resolver: PublicationVisitorEligibilityResolver | None = None,
        enabled: bool = False,
    ) -> None:
        self._repository = repository
        self._eligibility_resolver = eligibility_resolver or DenyPublicationVisitorEligibilityResolver()
        self._enabled = enabled

    def read_projection(
        self,
        *,
        visitor_subject_id: str,
        session_id: str,
        session_credential: str,
        now: datetime | None = None,
    ) -> PublicationVisitorProjectionReadResult:
        if not self._enabled:
            raise PublicationVisitorAccessDisabled("publication visitor reader is default-off")
        normalized_subject = _visitor_subject_id(visitor_subject_id)
        eligibility = self._eligibility_resolver.resolve(visitor_subject_id=normalized_subject)
        if not isinstance(eligibility, PublicationVisitorEligibility) or not eligibility.admitted:
            raise PublicationVisitorAdultVerificationRequired(
                "Visitor requires a server-verified adult, direct-relationship admission"
            )
        return self._repository.read_public_projection(
            visitor_subject_hash=_hash(normalized_subject),
            eligibility=eligibility,
            session_id=_uuid(session_id, field_name="session_id"),
            session_credential_hash=_hash(_credential(session_credential)),
            now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc),
        )

    def answer(
        self,
        *,
        visitor_subject_id: str,
        session_id: str,
        session_credential: str,
        question: str,
        now: datetime | None = None,
    ) -> PublicationVisitorAnswerResult:
        normalized_question = str(question or "").strip()
        if not normalized_question or len(normalized_question) > _QUESTION_MAXIMUM_LENGTH:
            raise PublicationVisitorAccessError("question must be between 1 and 800 characters")
        projection = self.read_projection(
            visitor_subject_id=visitor_subject_id,
            session_id=session_id,
            session_credential=session_credential,
            now=now,
        )
        lowered_question = normalized_question.lower()
        if any(marker in lowered_question for marker in _PROMPT_INJECTION_MARKERS):
            return PublicationVisitorAnswerResult(
                projection=projection,
                answer_kind="unknown",
                answer_text="I do not know. I can only use the approved public projection and cannot access private records.",
                uncertainty_code="privateContextUnavailable",
                reason_code="promptOrPrivateContextRequestDenied",
            )
        if any(marker in lowered_question for marker in _HIGH_STAKES_MARKERS):
            return PublicationVisitorAnswerResult(
                projection=projection,
                answer_kind="unknown",
                answer_text="I do not know. I cannot provide medical, financial, payment, or other high-stakes guidance.",
                uncertainty_code="highStakesGuidanceUnavailable",
                reason_code="highStakesQuestionDenied",
            )
        return PublicationVisitorAnswerResult(
            projection=projection,
            answer_kind="excerpt",
            answer_text=projection.display_body,
            uncertainty_code="excerptOnlyNoInference",
            reason_code="approvedPublicProjectionExcerpt",
        )


__all__ = [
    "PUBLICATION_VISITOR_READER_SCHEMA_VERSION",
    "PublicationVisitorAnswerResult",
    "PublicationVisitorProjectionReadResult",
    "PublicationVisitorReaderRepository",
    "PublicationVisitorReaderService",
]
