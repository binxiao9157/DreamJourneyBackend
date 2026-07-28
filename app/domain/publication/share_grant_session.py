"""Fail-closed ShareGrant and adult Visitor session contract.

This G0 model accepts only opaque identifiers, hashes, timestamps and state.
It never issues a share link, creates a session, consumes a use count, reads a
public store, exposes a visitor route, or accepts a raw bearer credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID

from .schema_authz import (
    PublicationAuthorizationContext,
    PublicationAuthorizationPrincipal,
    PublicationPrincipalKind,
)


PUBLICATION_SHARE_GRANT_SESSION_G0_SCHEMA_VERSION = "publication-share-grant-session-g0-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_MAX_TTL = timedelta(days=7)


class PublicationShareGrantSessionError(ValueError):
    """Raised when a synthetic grant/session envelope is malformed."""


class PublicationShareGrantAction(str, Enum):
    ISSUE = "issue"
    REVOKE = "revoke"
    ACCESS = "access"


class PublicationShareGrantState(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class PublicationAdultVerificationState(str, Enum):
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    MINOR = "minor"
    FAILED = "failed"


class PublicationVisitorRelationshipOrigin(str, Enum):
    DIRECT = "direct"
    FAMILY_DERIVED = "familyDerived"


class PublicationShareGrantSessionDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    OWNER_SCOPE_DENIED = "owner_scope_denied"
    FAMILY_AUTO_GRANT_DENIED = "family_auto_grant_denied"
    ADULT_VERIFICATION_DENIED = "adult_verification_denied"
    GRANT_VERSION_MISMATCH = "grant_version_mismatch"
    GRANT_INACTIVE = "grant_inactive"
    GRANT_EXPIRED = "grant_expired"
    SESSION_INVALID = "session_invalid"
    SESSION_EXPIRED = "session_expired"
    USE_CAS_REQUIRED = "use_cas_required"
    USE_LIMIT_REACHED = "use_limit_reached"
    POLICY_DISABLED = "policy_disabled"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise PublicationShareGrantSessionError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise PublicationShareGrantSessionError(f"{field} must be a UUID") from exc


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise PublicationShareGrantSessionError(f"{field} must be a SHA-256 digest")
    return normalized


def _positive_int(value: object, *, field: str, zero_allowed: bool = False) -> int:
    minimum = 0 if zero_allowed else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PublicationShareGrantSessionError(f"{field} must be at least {minimum}")
    return value


def _instant(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PublicationShareGrantSessionError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PublicationShareGrant:
    """Hash-only scoped authorization metadata for one publication version."""

    grant_id: str
    publication_id: str
    publication_version_id: str
    vault_id: str
    owner_subject_hash: str
    grantee_subject_hash: str
    grant_credential_hash: str
    state: PublicationShareGrantState
    issued_at: datetime
    expires_at: datetime
    use_limit: int
    use_count: int
    policy_hash: str

    def __post_init__(self) -> None:
        for field_name in ("grant_id", "publication_id", "publication_version_id"):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field=field_name))
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        for field_name in (
            "owner_subject_hash",
            "grantee_subject_hash",
            "grant_credential_hash",
            "policy_hash",
        ):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field=field_name))
        object.__setattr__(self, "state", PublicationShareGrantState(self.state))
        object.__setattr__(self, "issued_at", _instant(self.issued_at, field="issued_at"))
        object.__setattr__(self, "expires_at", _instant(self.expires_at, field="expires_at"))
        if self.expires_at <= self.issued_at or self.expires_at > self.issued_at + _MAX_TTL:
            raise PublicationShareGrantSessionError("grant expiry must be within seven days")
        object.__setattr__(self, "use_limit", _positive_int(self.use_limit, field="use_limit"))
        object.__setattr__(
            self,
            "use_count",
            _positive_int(self.use_count, field="use_count", zero_allowed=True),
        )
        if self.use_count > self.use_limit:
            raise PublicationShareGrantSessionError("use_count must not exceed use_limit")


@dataclass(frozen=True)
class PublicationVisitorIdentity:
    """Value-minimized Visitor identity result; raw identity proof stays external."""

    subject_hash: str
    adult_verification: PublicationAdultVerificationState
    relationship_origin: PublicationVisitorRelationshipOrigin
    emergency_contact_ref_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_hash", _digest(self.subject_hash, field="subject_hash"))
        object.__setattr__(
            self,
            "adult_verification",
            PublicationAdultVerificationState(self.adult_verification),
        )
        object.__setattr__(
            self,
            "relationship_origin",
            PublicationVisitorRelationshipOrigin(self.relationship_origin),
        )
        if self.emergency_contact_ref_hash is not None:
            object.__setattr__(
                self,
                "emergency_contact_ref_hash",
                _digest(self.emergency_contact_ref_hash, field="emergency_contact_ref_hash"),
            )


@dataclass(frozen=True)
class PublicationVisitorSessionProposal:
    """A future session envelope that contains only hashed credential material."""

    session_id: str
    grant_id: str
    publication_id: str
    publication_version_id: str
    visitor_subject_hash: str
    session_credential_hash: str
    issued_at: datetime
    expires_at: datetime
    expected_grant_use_count: int

    def __post_init__(self) -> None:
        for field_name in ("session_id", "grant_id", "publication_id", "publication_version_id"):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field=field_name))
        for field_name in ("visitor_subject_hash", "session_credential_hash"):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field=field_name))
        object.__setattr__(self, "issued_at", _instant(self.issued_at, field="issued_at"))
        object.__setattr__(self, "expires_at", _instant(self.expires_at, field="expires_at"))
        if self.expires_at <= self.issued_at or self.expires_at > self.issued_at + _MAX_TTL:
            raise PublicationShareGrantSessionError("session expiry must be within seven days")
        object.__setattr__(
            self,
            "expected_grant_use_count",
            _positive_int(
                self.expected_grant_use_count,
                field="expected_grant_use_count",
                zero_allowed=True,
            ),
        )


@dataclass(frozen=True)
class PublicationShareGrantCommand:
    command_id: str
    action: PublicationShareGrantAction

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _uuid(self.command_id, field="command_id"))
        object.__setattr__(self, "action", PublicationShareGrantAction(self.action))


@dataclass(frozen=True)
class PublicationShareGrantSessionResult:
    disposition: PublicationShareGrantSessionDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    use_remaining: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disposition",
            PublicationShareGrantSessionDisposition(self.disposition),
        )
        reasons = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise PublicationShareGrantSessionError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.scope_hash is not None:
            object.__setattr__(self, "scope_hash", _digest(self.scope_hash, field="scope_hash"))
        if self.use_remaining is not None:
            object.__setattr__(
                self,
                "use_remaining",
                _positive_int(self.use_remaining, field="use_remaining", zero_allowed=True),
            )

    @property
    def grant_issued(self) -> bool:
        return False

    @property
    def grant_revoked(self) -> bool:
        return False

    @property
    def visitor_session_issued(self) -> bool:
        return False

    @property
    def public_query_allowed(self) -> bool:
        return False

    @property
    def use_consumed(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "grantIssued": self.grant_issued,
            "grantRevoked": self.grant_revoked,
            "publicQueryAllowed": self.public_query_allowed,
            "reasonCodes": list(self.reason_codes),
            "releaseVisible": False,
            "schemaVersion": PUBLICATION_SHARE_GRANT_SESSION_G0_SCHEMA_VERSION,
            "status": self.disposition.value,
            "useConsumed": self.use_consumed,
            "visitorSessionIssued": self.visitor_session_issued,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.use_remaining is not None:
            summary["useRemaining"] = self.use_remaining
        return summary


def _scope_hash(
    *,
    owner_context: PublicationAuthorizationContext,
    grant: PublicationShareGrant,
    visitor: PublicationVisitorIdentity,
    command: PublicationShareGrantCommand,
) -> str:
    return _hash(
        {
            "action": command.action.value,
            "authorityEpoch": owner_context.authority_epoch,
            "grantId": grant.grant_id,
            "ownerSubjectHash": owner_context.owner_subject_hash,
            "publicationId": grant.publication_id,
            "publicationVersionId": grant.publication_version_id,
            "vaultId": owner_context.vault_id,
            "visitorSubjectHash": visitor.subject_hash,
        }
    )


def _result(
    disposition: PublicationShareGrantSessionDisposition,
    reason: str,
    *,
    scope_hash: str | None = None,
    use_remaining: int | None = None,
) -> PublicationShareGrantSessionResult:
    return PublicationShareGrantSessionResult(
        disposition=disposition,
        reason_codes=(reason,),
        scope_hash=scope_hash,
        use_remaining=use_remaining,
    )


def evaluate_publication_share_grant_session(
    *,
    owner_context: PublicationAuthorizationContext | object,
    owner_principal: PublicationAuthorizationPrincipal | object,
    grant: PublicationShareGrant | object,
    visitor: PublicationVisitorIdentity | object,
    command: PublicationShareGrantCommand | object,
    session: PublicationVisitorSessionProposal | object | None,
    now: datetime | None = None,
    enabled: bool = False,
) -> PublicationShareGrantSessionResult:
    """Evaluate a synthetic grant/session command without issuing credentials."""

    if enabled is not True:
        return _result(
            PublicationShareGrantSessionDisposition.SHADOW_DISABLED,
            "publicationShareGrantSessionShadowDisabled",
        )
    if not all(
        (
            isinstance(owner_context, PublicationAuthorizationContext),
            isinstance(owner_principal, PublicationAuthorizationPrincipal),
            isinstance(grant, PublicationShareGrant),
            isinstance(visitor, PublicationVisitorIdentity),
            isinstance(command, PublicationShareGrantCommand),
        )
    ):
        return _result(
            PublicationShareGrantSessionDisposition.INVALID_CONTEXT,
            "invalidPublicationShareGrantSessionContext",
        )
    if (
        owner_principal.kind is not PublicationPrincipalKind.OWNER
        or owner_principal.vault_id != owner_context.vault_id
        or owner_principal.subject_hash != owner_context.owner_subject_hash
        or grant.vault_id != owner_context.vault_id
        or grant.owner_subject_hash != owner_context.owner_subject_hash
    ):
        return _result(
            PublicationShareGrantSessionDisposition.OWNER_SCOPE_DENIED,
            "shareGrantOwnerScopeMismatch",
        )

    scope_hash = _scope_hash(
        owner_context=owner_context,
        grant=grant,
        visitor=visitor,
        command=command,
    )
    use_remaining = grant.use_limit - grant.use_count
    if visitor.relationship_origin is PublicationVisitorRelationshipOrigin.FAMILY_DERIVED:
        return _result(
            PublicationShareGrantSessionDisposition.FAMILY_AUTO_GRANT_DENIED,
            "familyRelationshipDoesNotImplyShareGrant",
            scope_hash=scope_hash,
            use_remaining=use_remaining,
        )
    if visitor.adult_verification is not PublicationAdultVerificationState.VERIFIED:
        return _result(
            PublicationShareGrantSessionDisposition.ADULT_VERIFICATION_DENIED,
            "visitorAdultVerificationRequired",
            scope_hash=scope_hash,
            use_remaining=use_remaining,
        )
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if grant.state is not PublicationShareGrantState.ACTIVE:
        return _result(
            PublicationShareGrantSessionDisposition.GRANT_INACTIVE,
            "shareGrantIsNotActive",
            scope_hash=scope_hash,
            use_remaining=use_remaining,
        )
    if grant.expires_at <= instant:
        return _result(
            PublicationShareGrantSessionDisposition.GRANT_EXPIRED,
            "shareGrantExpired",
            scope_hash=scope_hash,
            use_remaining=use_remaining,
        )
    if grant.use_count >= grant.use_limit:
        return _result(
            PublicationShareGrantSessionDisposition.USE_LIMIT_REACHED,
            "shareGrantUseLimitReached",
            scope_hash=scope_hash,
            use_remaining=0,
        )
    if command.action is PublicationShareGrantAction.ACCESS:
        if not isinstance(session, PublicationVisitorSessionProposal):
            return _result(
                PublicationShareGrantSessionDisposition.SESSION_INVALID,
                "visitorSessionProposalRequired",
                scope_hash=scope_hash,
                use_remaining=use_remaining,
            )
        if (
            session.grant_id != grant.grant_id
            or session.publication_id != grant.publication_id
            or session.publication_version_id != grant.publication_version_id
            or session.visitor_subject_hash != visitor.subject_hash
        ):
            return _result(
                PublicationShareGrantSessionDisposition.GRANT_VERSION_MISMATCH,
                "visitorSessionGrantOrVersionMismatch",
                scope_hash=scope_hash,
                use_remaining=use_remaining,
            )
        if session.expires_at <= instant or session.expires_at > grant.expires_at:
            return _result(
                PublicationShareGrantSessionDisposition.SESSION_EXPIRED,
                "visitorSessionExpiryInvalid",
                scope_hash=scope_hash,
                use_remaining=use_remaining,
            )
        if session.expected_grant_use_count != grant.use_count:
            return _result(
                PublicationShareGrantSessionDisposition.USE_CAS_REQUIRED,
                "visitorSessionGrantUseCountCompareAndSetRequired",
                scope_hash=scope_hash,
                use_remaining=use_remaining,
            )
    return _result(
        PublicationShareGrantSessionDisposition.POLICY_DISABLED,
        "publicationShareGrantAndVisitorGatewayPolicyDisabled",
        scope_hash=scope_hash,
        use_remaining=use_remaining,
    )


__all__ = [
    "PUBLICATION_SHARE_GRANT_SESSION_G0_SCHEMA_VERSION",
    "PublicationAdultVerificationState",
    "PublicationShareGrant",
    "PublicationShareGrantAction",
    "PublicationShareGrantSessionDisposition",
    "PublicationShareGrantSessionError",
    "PublicationShareGrantSessionResult",
    "PublicationShareGrantState",
    "PublicationVisitorIdentity",
    "PublicationVisitorRelationshipOrigin",
    "PublicationVisitorSessionProposal",
    "evaluate_publication_share_grant_session",
]
