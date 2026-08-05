"""Default-off ShareGrant and Visitor admission for the M2 closed beta.

This service operates only on the independently stored publication projection
created by the Owner-authority lane.  It does not provide a public URL, a
Visitor reader, an answer endpoint, or an automatic Family authorization.
Raw grant and session credentials are accepted only at the HTTP boundary,
hashed immediately, and never persisted or returned after their first issue.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import secrets
from threading import RLock
from typing import Any, Callable, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.publication.share_grant_session import (
    PublicationAdultVerificationState,
    PublicationVisitorRelationshipOrigin,
)


PUBLICATION_VISITOR_ACCESS_SCHEMA_VERSION = "publication-visitor-access-v1"
_NAMESPACE = UUID("7c37bf2a-1d16-4f20-a7dd-44a667330a0e")
_MAX_GRANT_TTL = timedelta(days=7)
_CREDENTIAL_MINIMUM_LENGTH = 24
_CREDENTIAL_MAXIMUM_LENGTH = 256


class PublicationVisitorAccessError(ValueError):
    """A closed-beta ShareGrant or Visitor admission cannot proceed safely."""


class PublicationVisitorAccessDisabled(PublicationVisitorAccessError):
    """The internal M2 visitor-access lane is still default-off."""


class PublicationVisitorAccessDenied(PublicationVisitorAccessError):
    """The current principal does not own or hold the requested scope."""


class PublicationVisitorAccessUnavailable(PublicationVisitorAccessError):
    """The publication, projection, grant, or session is not usable now."""


class PublicationVisitorAccessConflict(PublicationVisitorAccessError):
    """A replay command conflicts with its immutable original command."""


class PublicationVisitorAdultVerificationRequired(PublicationVisitorAccessError):
    """The server has no verified adult, direct-relationship admission result."""


def _utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PublicationVisitorAccessError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _uuid(value: object, *, field_name: str) -> str:
    try:
        return str(UUID(str(value or "").strip()))
    except (TypeError, ValueError) as exc:
        raise PublicationVisitorAccessError(f"{field_name} must be a UUID") from exc


def _identifier(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256:
        raise PublicationVisitorAccessError(f"{field_name} must be a non-empty opaque identifier")
    return normalized


def _positive_int(value: object, *, field_name: str, maximum: int = 100) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise PublicationVisitorAccessError(f"{field_name} must be between 1 and {maximum}")
    return value


def _credential(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not (_CREDENTIAL_MINIMUM_LENGTH <= len(normalized) <= _CREDENTIAL_MAXIMUM_LENGTH):
        raise PublicationVisitorAccessError(
            f"{field_name} must be an opaque credential of { _CREDENTIAL_MINIMUM_LENGTH }-{ _CREDENTIAL_MAXIMUM_LENGTH } characters"
        )
    return normalized


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _hash_payload(value: Mapping[str, Any]) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _grant_id(*, vault_id: str, command_id_hash: str) -> str:
    return str(uuid5(_NAMESPACE, f"grant:{vault_id}:{command_id_hash}"))


def _session_id(*, grant_id: str, command_id_hash: str) -> str:
    return str(uuid5(_NAMESPACE, f"session:{grant_id}:{command_id_hash}"))


def _receipt_id(*, grant_id: str, action: str, command_id_hash: str) -> str:
    return str(uuid5(_NAMESPACE, f"receipt:{grant_id}:{action}:{command_id_hash}"))


def _grant_policy_hash(
    *,
    vault_id: str,
    publication_id: str,
    publication_version_id: str,
    grantee_subject_hash: str,
    expires_at: datetime,
    use_limit: int,
) -> str:
    return _hash_payload(
        {
            "expiresAt": expires_at.isoformat(),
            "grantScope": "publicationVersionRead",
            "granteeSubjectHash": grantee_subject_hash,
            "publicationId": publication_id,
            "publicationVersionId": publication_version_id,
            "schemaVersion": PUBLICATION_VISITOR_ACCESS_SCHEMA_VERSION,
            "useLimit": use_limit,
            "vaultId": vault_id,
        }
    )


def _session_policy_hash(
    *,
    grant_id: str,
    publication_id: str,
    publication_version_id: str,
    visitor_subject_hash: str,
    expires_at: datetime,
) -> str:
    return _hash_payload(
        {
            "expiresAt": expires_at.isoformat(),
            "grantId": grant_id,
            "publicationId": publication_id,
            "publicationVersionId": publication_version_id,
            "schemaVersion": PUBLICATION_VISITOR_ACCESS_SCHEMA_VERSION,
            "visitorSubjectHash": visitor_subject_hash,
        }
    )


def _owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise PublicationVisitorAccessDenied("Owner context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise PublicationVisitorAccessDenied("only the Vault Owner may manage ShareGrants")


@dataclass(frozen=True)
class PublicationGrantIssueCommand:
    command_id: str
    publication_id: str
    publication_version_id: str
    grantee_subject_id: str = field(repr=False)
    expires_at: datetime
    use_limit: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _uuid(self.command_id, field_name="command_id"))
        object.__setattr__(self, "publication_id", _uuid(self.publication_id, field_name="publication_id"))
        object.__setattr__(
            self,
            "publication_version_id",
            _uuid(self.publication_version_id, field_name="publication_version_id"),
        )
        object.__setattr__(
            self,
            "grantee_subject_id",
            _identifier(self.grantee_subject_id, field_name="grantee_subject_id"),
        )
        object.__setattr__(self, "expires_at", _utc(self.expires_at, field_name="expires_at"))
        object.__setattr__(self, "use_limit", _positive_int(self.use_limit, field_name="use_limit"))

    @property
    def command_id_hash(self) -> str:
        return _hash_text(self.command_id)


@dataclass(frozen=True)
class PublicationGrantRevokeCommand:
    command_id: str
    grant_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _uuid(self.command_id, field_name="command_id"))
        object.__setattr__(self, "grant_id", _uuid(self.grant_id, field_name="grant_id"))

    @property
    def command_id_hash(self) -> str:
        return _hash_text(self.command_id)


@dataclass(frozen=True)
class PublicationVisitorSessionCommand:
    command_id: str
    grant_credential: str = field(repr=False)
    session_credential: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _uuid(self.command_id, field_name="command_id"))
        object.__setattr__(
            self,
            "grant_credential",
            _credential(self.grant_credential, field_name="grant_credential"),
        )
        object.__setattr__(
            self,
            "session_credential",
            _credential(self.session_credential, field_name="session_credential"),
        )

    @property
    def command_id_hash(self) -> str:
        return _hash_text(self.command_id)


@dataclass(frozen=True)
class PublicationVisitorEligibility:
    adult_verification: PublicationAdultVerificationState
    relationship_origin: PublicationVisitorRelationshipOrigin

    def __post_init__(self) -> None:
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

    @property
    def admitted(self) -> bool:
        return (
            self.adult_verification is PublicationAdultVerificationState.VERIFIED
            and self.relationship_origin is PublicationVisitorRelationshipOrigin.DIRECT
        )


class PublicationVisitorEligibilityResolver(Protocol):
    def resolve(self, *, visitor_subject_id: str) -> PublicationVisitorEligibility:
        """Resolve server-trusted eligibility without client-supplied claims."""


class DenyPublicationVisitorEligibilityResolver:
    """Production-safe default until an adult-verification provider is configured."""

    def resolve(self, *, visitor_subject_id: str) -> PublicationVisitorEligibility:
        del visitor_subject_id
        return PublicationVisitorEligibility(
            adult_verification=PublicationAdultVerificationState.UNKNOWN,
            relationship_origin=PublicationVisitorRelationshipOrigin.DIRECT,
        )


class StaticPublicationVisitorEligibilityResolver:
    """Synthetic resolver for unit/API smoke only; never configured by default."""

    def __init__(self, values: Mapping[str, PublicationVisitorEligibility]) -> None:
        self._values = dict(values)

    def resolve(self, *, visitor_subject_id: str) -> PublicationVisitorEligibility:
        value = self._values.get(visitor_subject_id)
        if value is None:
            return PublicationVisitorEligibility(
                adult_verification=PublicationAdultVerificationState.UNKNOWN,
                relationship_origin=PublicationVisitorRelationshipOrigin.DIRECT,
            )
        return value


@dataclass(frozen=True)
class PublicationGrantScope:
    vault_id: str
    owner_subject_id: str = field(repr=False)
    authority_epoch: int
    publication_id: str
    publication_version_id: str
    projection_state: str
    vault_state: str = "active"
    publication_state: str = "confirmed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field_name="vault_id"))
        object.__setattr__(self, "owner_subject_id", _identifier(self.owner_subject_id, field_name="owner_subject_id"))
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int) or self.authority_epoch < 0:
            raise PublicationVisitorAccessError("authority_epoch must be non-negative")
        object.__setattr__(self, "publication_id", _uuid(self.publication_id, field_name="publication_id"))
        object.__setattr__(
            self,
            "publication_version_id",
            _uuid(self.publication_version_id, field_name="publication_version_id"),
        )
        object.__setattr__(self, "projection_state", _identifier(self.projection_state, field_name="projection_state"))
        object.__setattr__(self, "vault_state", _identifier(self.vault_state, field_name="vault_state"))
        object.__setattr__(
            self,
            "publication_state",
            _identifier(self.publication_state, field_name="publication_state"),
        )

    @property
    def active(self) -> bool:
        return (
            self.vault_state == "active"
            and self.publication_state == "confirmed"
            and self.projection_state == "active"
        )


@dataclass(frozen=True)
class PublicationGrantIssueResult:
    outcome: str
    grant_id: str
    publication_id: str
    publication_version_id: str
    expires_at: datetime
    use_remaining: int
    grant_credential: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise PublicationVisitorAccessError("grant issue outcome is invalid")
        for field_name in ("grant_id", "publication_id", "publication_version_id"):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "expires_at", _utc(self.expires_at, field_name="expires_at"))
        if isinstance(self.use_remaining, bool) or not isinstance(self.use_remaining, int) or self.use_remaining < 0:
            raise PublicationVisitorAccessError("use_remaining must be non-negative")
        if self.grant_credential is not None:
            object.__setattr__(
                self,
                "grant_credential",
                _credential(self.grant_credential, field_name="grant_credential"),
            )


@dataclass(frozen=True)
class PublicationGrantRevokeResult:
    outcome: str
    grant_id: str
    revoked_session_count: int

    def __post_init__(self) -> None:
        if self.outcome not in {"revoked", "deduplicated", "alreadyRevoked"}:
            raise PublicationVisitorAccessError("grant revoke outcome is invalid")
        object.__setattr__(self, "grant_id", _uuid(self.grant_id, field_name="grant_id"))
        if (
            isinstance(self.revoked_session_count, bool)
            or not isinstance(self.revoked_session_count, int)
            or self.revoked_session_count < 0
        ):
            raise PublicationVisitorAccessError("revoked_session_count must be non-negative")


@dataclass(frozen=True)
class PublicationVisitorAdmissionResult:
    outcome: str
    grant_id: str
    session_id: str
    publication_id: str
    publication_version_id: str
    expires_at: datetime
    use_remaining: int

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise PublicationVisitorAccessError("visitor admission outcome is invalid")
        for field_name in ("grant_id", "session_id", "publication_id", "publication_version_id"):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field_name=field_name))
        object.__setattr__(self, "expires_at", _utc(self.expires_at, field_name="expires_at"))
        if isinstance(self.use_remaining, bool) or not isinstance(self.use_remaining, int) or self.use_remaining < 0:
            raise PublicationVisitorAccessError("use_remaining must be non-negative")


class PublicationVisitorAccessRepository(Protocol):
    def issue_grant(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationGrantIssueCommand,
        grantee_subject_hash: str,
        grant_credential_hash: str,
        policy_hash: str,
        now: datetime,
    ) -> PublicationGrantIssueResult:
        ...

    def revoke_grant(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationGrantRevokeCommand,
        now: datetime,
    ) -> PublicationGrantRevokeResult:
        ...

    def admit_visitor(
        self,
        *,
        visitor_subject_hash: str,
        eligibility: PublicationVisitorEligibility,
        grant_id: str,
        command: PublicationVisitorSessionCommand,
        grant_credential_hash: str,
        session_credential_hash: str,
        now: datetime,
    ) -> PublicationVisitorAdmissionResult:
        ...


class PublicationVisitorAccessService:
    """Coordinates the server-only M2 ShareGrant and Visitor session boundary."""

    def __init__(
        self,
        repository: PublicationVisitorAccessRepository,
        *,
        eligibility_resolver: PublicationVisitorEligibilityResolver | None = None,
        enabled: bool = False,
    ) -> None:
        self._repository = repository
        self._eligibility_resolver = eligibility_resolver or DenyPublicationVisitorEligibilityResolver()
        self._enabled = enabled

    def issue_grant(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationGrantIssueCommand,
        now: datetime | None = None,
    ) -> PublicationGrantIssueResult:
        _owner_context(context)
        if not self._enabled:
            raise PublicationVisitorAccessDisabled("publication visitor access is default-off")
        instant = _utc(now or datetime.now(timezone.utc), field_name="now")
        if command.expires_at <= instant or command.expires_at > instant + _MAX_GRANT_TTL:
            raise PublicationVisitorAccessUnavailable("ShareGrant expiry must be within seven days")
        grantee_subject_hash = _hash_text(command.grantee_subject_id)
        grant_credential = secrets.token_urlsafe(32)
        result = self._repository.issue_grant(
            context=context,
            command=command,
            grantee_subject_hash=grantee_subject_hash,
            grant_credential_hash=_hash_text(grant_credential),
            policy_hash=_grant_policy_hash(
                vault_id=context.vault_id,
                publication_id=command.publication_id,
                publication_version_id=command.publication_version_id,
                grantee_subject_hash=grantee_subject_hash,
                expires_at=command.expires_at,
                use_limit=command.use_limit,
            ),
            now=instant,
        )
        if result.outcome == "created":
            return replace(result, grant_credential=grant_credential)
        return result

    def revoke_grant(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationGrantRevokeCommand,
        now: datetime | None = None,
    ) -> PublicationGrantRevokeResult:
        _owner_context(context)
        if not self._enabled:
            raise PublicationVisitorAccessDisabled("publication visitor access is default-off")
        return self._repository.revoke_grant(
            context=context,
            command=command,
            now=_utc(now or datetime.now(timezone.utc), field_name="now"),
        )

    def admit_visitor(
        self,
        *,
        visitor_subject_id: str,
        grant_id: str,
        command: PublicationVisitorSessionCommand,
        now: datetime | None = None,
    ) -> PublicationVisitorAdmissionResult:
        if not self._enabled:
            raise PublicationVisitorAccessDisabled("publication visitor access is default-off")
        visitor_subject_id = _identifier(visitor_subject_id, field_name="visitor_subject_id")
        eligibility = self._eligibility_resolver.resolve(visitor_subject_id=visitor_subject_id)
        if not isinstance(eligibility, PublicationVisitorEligibility) or not eligibility.admitted:
            raise PublicationVisitorAdultVerificationRequired(
                "Visitor requires a server-verified adult, direct relationship admission"
            )
        instant = _utc(now or datetime.now(timezone.utc), field_name="now")
        return self._repository.admit_visitor(
            visitor_subject_hash=_hash_text(visitor_subject_id),
            eligibility=eligibility,
            grant_id=_uuid(grant_id, field_name="grant_id"),
            command=command,
            grant_credential_hash=_hash_text(command.grant_credential),
            session_credential_hash=_hash_text(command.session_credential),
            now=instant,
        )


class InMemoryPublicationVisitorAccessRepository:
    """Semantic double for default-off API and contract tests."""

    def __init__(
        self,
        *,
        projection_scope_reader: Callable[[str, str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self._lock = RLock()
        self._projection_scope_reader = projection_scope_reader
        self._scopes: dict[tuple[str, str], PublicationGrantScope] = {}
        self._grants: dict[str, dict[str, Any]] = {}
        self._sessions: dict[str, dict[str, Any]] = {}

    def seed_scope(self, scope: PublicationGrantScope) -> None:
        if not isinstance(scope, PublicationGrantScope):
            raise TypeError("PublicationGrantScope is required")
        with self._lock:
            self._scopes[(scope.publication_id, scope.publication_version_id)] = scope

    def grant_snapshot(self, grant_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            value = self._grants.get(grant_id)
            return deepcopy(value) if value is not None else None

    def session_snapshot(self, session_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            value = self._sessions.get(session_id)
            return deepcopy(value) if value is not None else None

    def _scope(self, *, publication_id: str, publication_version_id: str) -> PublicationGrantScope:
        value: Mapping[str, Any] | PublicationGrantScope | None = None
        if self._projection_scope_reader is not None:
            value = self._projection_scope_reader(publication_id, publication_version_id)
        if value is None:
            value = self._scopes.get((publication_id, publication_version_id))
        if value is None:
            raise PublicationVisitorAccessUnavailable("publication projection is unavailable")
        if isinstance(value, PublicationGrantScope):
            scope = value
        else:
            scope = PublicationGrantScope(
                vault_id=value.get("vaultId") or value.get("vault_id"),
                owner_subject_id=value.get("ownerSubjectId") or value.get("owner_subject_id"),
                authority_epoch=int(value.get("authorityEpoch", value.get("authority_epoch", -1))),
                publication_id=value.get("publicationId") or value.get("publication_id"),
                publication_version_id=(
                    value.get("publicationVersionId") or value.get("publication_version_id")
                ),
                projection_state=value.get("projectionState", value.get("projection_state", "blocked")),
                vault_state=value.get("vaultState", value.get("vault_state", "active")),
                publication_state=value.get("publicationState", value.get("publication_state", "confirmed")),
            )
        if not scope.active:
            raise PublicationVisitorAccessUnavailable("publication projection is not active")
        return scope

    @staticmethod
    def _require_owner_scope(scope: PublicationGrantScope, context: OwnerTruthCommandContext) -> None:
        if (
            scope.vault_id != context.vault_id
            or scope.owner_subject_id != context.owner_subject_id
        ):
            raise PublicationVisitorAccessDenied("publication scope is not owned by this Owner")

    def issue_grant(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationGrantIssueCommand,
        grantee_subject_hash: str,
        grant_credential_hash: str,
        policy_hash: str,
        now: datetime,
    ) -> PublicationGrantIssueResult:
        with self._lock:
            scope = self._scope(
                publication_id=command.publication_id,
                publication_version_id=command.publication_version_id,
            )
            self._require_owner_scope(scope, context)
            grant_id = _grant_id(vault_id=context.vault_id, command_id_hash=command.command_id_hash)
            existing = self._grants.get(grant_id)
            if existing is not None:
                if (
                    existing["issuanceCommandHash"] != command.command_id_hash
                    or existing["policyHash"] != policy_hash
                    or existing["granteeSubjectHash"] != grantee_subject_hash
                ):
                    raise PublicationVisitorAccessConflict("ShareGrant command replay conflicts with original")
                return PublicationGrantIssueResult(
                    outcome="deduplicated",
                    grant_id=grant_id,
                    publication_id=str(existing["publicationId"]),
                    publication_version_id=str(existing["publicationVersionId"]),
                    expires_at=existing["expiresAt"],
                    use_remaining=max(0, int(existing["useLimit"]) - int(existing["useCount"])),
                )
            self._grants[grant_id] = {
                "grantId": grant_id,
                "vaultId": context.vault_id,
                "ownerSubjectId": context.owner_subject_id,
                "authorityEpoch": scope.authority_epoch,
                "publicationId": command.publication_id,
                "publicationVersionId": command.publication_version_id,
                "granteeSubjectHash": grantee_subject_hash,
                "grantCredentialHash": grant_credential_hash,
                "policyHash": policy_hash,
                "state": "active",
                "expiresAt": command.expires_at,
                "useLimit": command.use_limit,
                "useCount": 0,
                "issuanceCommandHash": command.command_id_hash,
                "revocationCommandHash": None,
                "createdAt": now,
            }
            return PublicationGrantIssueResult(
                outcome="created",
                grant_id=grant_id,
                publication_id=command.publication_id,
                publication_version_id=command.publication_version_id,
                expires_at=command.expires_at,
                use_remaining=command.use_limit,
            )

    def revoke_grant(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationGrantRevokeCommand,
        now: datetime,
    ) -> PublicationGrantRevokeResult:
        with self._lock:
            grant = self._grants.get(command.grant_id)
            if grant is None or grant["vaultId"] != context.vault_id:
                raise PublicationVisitorAccessDenied("ShareGrant is not available in this Owner Vault")
            if grant["ownerSubjectId"] != context.owner_subject_id:
                raise PublicationVisitorAccessDenied("ShareGrant is not owned by this Owner")
            if grant["revocationCommandHash"] == command.command_id_hash:
                return PublicationGrantRevokeResult(
                    outcome="deduplicated",
                    grant_id=command.grant_id,
                    revoked_session_count=0,
                )
            if grant["state"] == "revoked":
                return PublicationGrantRevokeResult(
                    outcome="alreadyRevoked",
                    grant_id=command.grant_id,
                    revoked_session_count=0,
                )
            grant["state"] = "revoked"
            grant["revokedAt"] = now
            grant["revocationCommandHash"] = command.command_id_hash
            revoked = 0
            for session in self._sessions.values():
                if session["grantId"] == command.grant_id and session["state"] == "active":
                    session["state"] = "revoked"
                    revoked += 1
            return PublicationGrantRevokeResult(
                outcome="revoked",
                grant_id=command.grant_id,
                revoked_session_count=revoked,
            )

    def admit_visitor(
        self,
        *,
        visitor_subject_hash: str,
        eligibility: PublicationVisitorEligibility,
        grant_id: str,
        command: PublicationVisitorSessionCommand,
        grant_credential_hash: str,
        session_credential_hash: str,
        now: datetime,
    ) -> PublicationVisitorAdmissionResult:
        del eligibility
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                raise PublicationVisitorAccessDenied("ShareGrant credential is not accepted")
            if grant["grantCredentialHash"] != grant_credential_hash:
                raise PublicationVisitorAccessDenied("ShareGrant credential is not accepted")
            if grant["granteeSubjectHash"] != visitor_subject_hash:
                raise PublicationVisitorAccessDenied("ShareGrant credential is not accepted")
            scope = self._scope(
                publication_id=str(grant["publicationId"]),
                publication_version_id=str(grant["publicationVersionId"]),
            )
            if (
                scope.vault_id != grant["vaultId"]
                or scope.owner_subject_id != grant["ownerSubjectId"]
                or scope.authority_epoch != grant["authorityEpoch"]
            ):
                raise PublicationVisitorAccessUnavailable("publication authority changed")
            if grant["state"] != "active":
                raise PublicationVisitorAccessUnavailable("ShareGrant is not active")
            if grant["expiresAt"] <= now:
                grant["state"] = "expired"
                raise PublicationVisitorAccessUnavailable("ShareGrant has expired")
            session_id = _session_id(grant_id=grant_id, command_id_hash=command.command_id_hash)
            existing = self._sessions.get(session_id)
            if existing is not None:
                if (
                    existing["grantId"] != grant_id
                    or existing["visitorSubjectHash"] != visitor_subject_hash
                    or existing["sessionCredentialHash"] != session_credential_hash
                ):
                    raise PublicationVisitorAccessConflict("Visitor session replay conflicts with original")
                return PublicationVisitorAdmissionResult(
                    outcome="deduplicated",
                    grant_id=grant_id,
                    session_id=session_id,
                    publication_id=str(existing["publicationId"]),
                    publication_version_id=str(existing["publicationVersionId"]),
                    expires_at=existing["expiresAt"],
                    use_remaining=max(0, int(grant["useLimit"]) - int(grant["useCount"])),
                )
            if int(grant["useCount"]) >= int(grant["useLimit"]):
                raise PublicationVisitorAccessUnavailable("ShareGrant use limit is reached")
            expected_use_count = int(grant["useCount"]) + 1
            grant["useCount"] = expected_use_count
            self._sessions[session_id] = {
                "sessionId": session_id,
                "grantId": grant_id,
                "vaultId": grant["vaultId"],
                "publicationId": grant["publicationId"],
                "publicationVersionId": grant["publicationVersionId"],
                "visitorSubjectHash": visitor_subject_hash,
                "sessionCredentialHash": session_credential_hash,
                "expectedGrantUseCount": expected_use_count,
                "expiresAt": grant["expiresAt"],
                "state": "active",
            }
            return PublicationVisitorAdmissionResult(
                outcome="created",
                grant_id=grant_id,
                session_id=session_id,
                publication_id=str(grant["publicationId"]),
                publication_version_id=str(grant["publicationVersionId"]),
                expires_at=grant["expiresAt"],
                use_remaining=int(grant["useLimit"]) - int(grant["useCount"]),
            )


class PostgresPublicationVisitorAccessRepository:
    """Postgres writer/reader bound to one active request unit of work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def issue_grant(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationGrantIssueCommand,
        grantee_subject_hash: str,
        grant_credential_hash: str,
        policy_hash: str,
        now: datetime,
    ) -> PublicationGrantIssueResult:
        with self._cursor() as cursor:
            scope = self._active_owner_scope(
                cursor,
                context=context,
                publication_id=command.publication_id,
                publication_version_id=command.publication_version_id,
                lock=True,
            )
            grant_id = _grant_id(vault_id=context.vault_id, command_id_hash=command.command_id_hash)
            cursor.execute(
                """
                SELECT publication_id, publication_version_id, grantee_subject_hash,
                    expires_at, use_limit, use_count, issuance_command_hash, grant_policy_hash
                FROM publication.share_grants
                WHERE id = %s
                FOR UPDATE
                """,
                (grant_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    str(existing["issuance_command_hash"] or "") != command.command_id_hash
                    or str(existing["grant_policy_hash"] or "") != policy_hash
                    or str(existing["grantee_subject_hash"]) != grantee_subject_hash
                ):
                    raise PublicationVisitorAccessConflict("ShareGrant command replay conflicts with original")
                return PublicationGrantIssueResult(
                    outcome="deduplicated",
                    grant_id=grant_id,
                    publication_id=str(existing["publication_id"]),
                    publication_version_id=str(existing["publication_version_id"]),
                    expires_at=_utc(existing["expires_at"], field_name="expires_at"),
                    use_remaining=max(0, int(existing["use_limit"]) - int(existing["use_count"])),
                )
            cursor.execute(
                """
                INSERT INTO publication.share_grants (
                    id, vault_id, publication_id, publication_version_id, owner_subject_id,
                    authority_epoch, grantee_subject_hash, token_hash, purpose, state,
                    use_limit, use_count, expires_at, issuance_command_hash, grant_policy_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'read', 'active', %s, 0, %s, %s, %s)
                """,
                (
                    grant_id,
                    scope.vault_id,
                    scope.publication_id,
                    scope.publication_version_id,
                    scope.owner_subject_id,
                    scope.authority_epoch,
                    grantee_subject_hash,
                    grant_credential_hash,
                    command.use_limit,
                    command.expires_at,
                    command.command_id_hash,
                    policy_hash,
                ),
            )
            self._insert_receipt(
                cursor,
                grant_id=grant_id,
                scope=scope,
                action="issue",
                command_id_hash=command.command_id_hash,
                actor_subject_hash=_hash_text(context.owner_subject_id),
                visitor_subject_hash=grantee_subject_hash,
                policy_hash=policy_hash,
            )
            return PublicationGrantIssueResult(
                outcome="created",
                grant_id=grant_id,
                publication_id=scope.publication_id,
                publication_version_id=scope.publication_version_id,
                expires_at=command.expires_at,
                use_remaining=command.use_limit,
            )

    def revoke_grant(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationGrantRevokeCommand,
        now: datetime,
    ) -> PublicationGrantRevokeResult:
        with self._cursor() as cursor:
            grant = self._owner_grant(cursor, context=context, grant_id=command.grant_id)
            if str(grant.get("revocation_command_hash") or "") == command.command_id_hash:
                return PublicationGrantRevokeResult(
                    outcome="deduplicated",
                    grant_id=command.grant_id,
                    revoked_session_count=0,
                )
            if str(grant["state"]) == "revoked":
                return PublicationGrantRevokeResult(
                    outcome="alreadyRevoked",
                    grant_id=command.grant_id,
                    revoked_session_count=0,
                )
            cursor.execute(
                """
                UPDATE publication.share_grants
                SET state = 'revoked', revoked_at = %s, revocation_command_hash = %s, updated_at = %s
                WHERE id = %s AND state <> 'revoked'
                """,
                (now, command.command_id_hash, now, command.grant_id),
            )
            cursor.execute(
                """
                UPDATE publication.visitor_sessions
                SET state = 'revoked', updated_at = %s
                WHERE share_grant_id = %s AND state = 'active'
                """,
                (now, command.grant_id),
            )
            revoked_session_count = int(cursor.rowcount)
            scope = PublicationGrantScope(
                vault_id=str(grant["vault_id"]),
                owner_subject_id=str(grant["owner_subject_id"]),
                authority_epoch=int(grant["authority_epoch"]),
                publication_id=str(grant["publication_id"]),
                publication_version_id=str(grant["publication_version_id"]),
                projection_state="active",
            )
            self._insert_receipt(
                cursor,
                grant_id=command.grant_id,
                scope=scope,
                action="revoke",
                command_id_hash=command.command_id_hash,
                actor_subject_hash=_hash_text(context.owner_subject_id),
                visitor_subject_hash=str(grant["grantee_subject_hash"]),
                policy_hash=str(grant.get("grant_policy_hash") or _hash_text("legacy-grant-policy")),
            )
            return PublicationGrantRevokeResult(
                outcome="revoked",
                grant_id=command.grant_id,
                revoked_session_count=revoked_session_count,
            )

    def admit_visitor(
        self,
        *,
        visitor_subject_hash: str,
        eligibility: PublicationVisitorEligibility,
        grant_id: str,
        command: PublicationVisitorSessionCommand,
        grant_credential_hash: str,
        session_credential_hash: str,
        now: datetime,
    ) -> PublicationVisitorAdmissionResult:
        with self._cursor() as cursor:
            grant = self._grant_with_active_projection(cursor, grant_id=grant_id)
            if str(grant["token_hash"]) != grant_credential_hash or str(
                grant["grantee_subject_hash"]
            ) != visitor_subject_hash:
                raise PublicationVisitorAccessDenied("ShareGrant credential is not accepted")
            if str(grant["state"]) != "active":
                raise PublicationVisitorAccessUnavailable("ShareGrant is not active")
            expires_at = _utc(grant["expires_at"], field_name="expires_at")
            if expires_at <= now:
                cursor.execute(
                    "UPDATE publication.share_grants SET state = 'expired', updated_at = %s WHERE id = %s AND state = 'active'",
                    (now, grant_id),
                )
                raise PublicationVisitorAccessUnavailable("ShareGrant has expired")
            if not eligibility.admitted:
                raise PublicationVisitorAdultVerificationRequired(
                    "Visitor requires a server-verified adult, direct relationship admission"
                )
            session_id = _session_id(grant_id=grant_id, command_id_hash=command.command_id_hash)
            cursor.execute(
                """
                SELECT share_grant_id, visitor_subject_hash, session_token_hash,
                    publication_id, publication_version_id, expires_at
                FROM publication.visitor_sessions
                WHERE id = %s
                FOR UPDATE
                """,
                (session_id,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    str(existing["share_grant_id"]) != grant_id
                    or str(existing["visitor_subject_hash"]) != visitor_subject_hash
                    or str(existing["session_token_hash"]) != session_credential_hash
                ):
                    raise PublicationVisitorAccessConflict("Visitor session replay conflicts with original")
                return PublicationVisitorAdmissionResult(
                    outcome="deduplicated",
                    grant_id=grant_id,
                    session_id=session_id,
                    publication_id=str(existing["publication_id"]),
                    publication_version_id=str(existing["publication_version_id"]),
                    expires_at=_utc(existing["expires_at"], field_name="expires_at"),
                    use_remaining=max(0, int(grant["use_limit"]) - int(grant["use_count"])),
                )
            use_count = int(grant["use_count"])
            use_limit = int(grant["use_limit"])
            if use_count >= use_limit:
                raise PublicationVisitorAccessUnavailable("ShareGrant use limit is reached")
            cursor.execute(
                """
                UPDATE publication.share_grants
                SET use_count = use_count + 1, updated_at = %s
                WHERE id = %s
                  AND state = 'active'
                  AND expires_at > %s
                  AND use_count = %s
                """,
                (now, grant_id, now, use_count),
            )
            if cursor.rowcount != 1:
                raise PublicationVisitorAccessConflict("ShareGrant use count changed before Visitor admission")
            session_policy_hash = _session_policy_hash(
                grant_id=grant_id,
                publication_id=str(grant["publication_id"]),
                publication_version_id=str(grant["publication_version_id"]),
                visitor_subject_hash=visitor_subject_hash,
                expires_at=expires_at,
            )
            cursor.execute(
                """
                INSERT INTO publication.visitor_sessions (
                    id, vault_id, publication_id, publication_version_id, share_grant_id,
                    visitor_subject_hash, session_token_hash, state, expires_at,
                    adult_verification_state, relationship_origin, expected_grant_use_count,
                    session_policy_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, 'verified', 'direct', %s, %s)
                """,
                (
                    session_id,
                    str(grant["vault_id"]),
                    str(grant["publication_id"]),
                    str(grant["publication_version_id"]),
                    grant_id,
                    visitor_subject_hash,
                    session_credential_hash,
                    expires_at,
                    use_count + 1,
                    session_policy_hash,
                ),
            )
            scope = PublicationGrantScope(
                vault_id=str(grant["vault_id"]),
                owner_subject_id=str(grant["owner_subject_id"]),
                authority_epoch=int(grant["authority_epoch"]),
                publication_id=str(grant["publication_id"]),
                publication_version_id=str(grant["publication_version_id"]),
                projection_state="active",
            )
            self._insert_receipt(
                cursor,
                grant_id=grant_id,
                scope=scope,
                action="access",
                command_id_hash=command.command_id_hash,
                actor_subject_hash=visitor_subject_hash,
                visitor_subject_hash=visitor_subject_hash,
                policy_hash=session_policy_hash,
            )
            return PublicationVisitorAdmissionResult(
                outcome="created",
                grant_id=grant_id,
                session_id=session_id,
                publication_id=scope.publication_id,
                publication_version_id=scope.publication_version_id,
                expires_at=expires_at,
                use_remaining=use_limit - (use_count + 1),
            )

    def _active_owner_scope(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        publication_id: str,
        publication_version_id: str,
        lock: bool,
    ) -> PublicationGrantScope:
        lock_clause = " FOR UPDATE OF vault, publication, version, projection" if lock else ""
        cursor.execute(
            """
            SELECT vault.owner_subject_id AS vault_owner_subject_id,
                vault.authority_epoch AS vault_authority_epoch,
                vault.status AS vault_state,
                publication.owner_subject_id AS publication_owner_subject_id,
                publication.authority_epoch AS publication_authority_epoch,
                publication.state AS publication_state,
                projection.state AS projection_state
            FROM owner_truth.vaults AS vault
            JOIN publication.publications AS publication
              ON publication.vault_id = vault.vault_id
            JOIN publication.publication_versions AS version
              ON version.publication_id = publication.id
             AND version.vault_id = publication.vault_id
            JOIN publication.public_projections AS projection
              ON projection.publication_version_id = version.id
             AND projection.publication_id = publication.id
             AND projection.vault_id = publication.vault_id
            WHERE vault.vault_id = %s
              AND publication.id = %s
              AND version.id = %s
            """ + lock_clause,
            (context.vault_id, publication_id, publication_version_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise PublicationVisitorAccessUnavailable("publication projection is unavailable")
        if (
            str(row["vault_owner_subject_id"]) != context.owner_subject_id
            or str(row["publication_owner_subject_id"]) != context.owner_subject_id
        ):
            raise PublicationVisitorAccessDenied("publication scope is not owned by this Owner")
        scope = PublicationGrantScope(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=int(row["vault_authority_epoch"]),
            publication_id=publication_id,
            publication_version_id=publication_version_id,
            projection_state=str(row["projection_state"]),
            vault_state=str(row["vault_state"]),
            publication_state=str(row["publication_state"]),
        )
        if int(row["publication_authority_epoch"]) != scope.authority_epoch or not scope.active:
            raise PublicationVisitorAccessUnavailable("publication projection is not active")
        return scope

    def _owner_grant(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        grant_id: str,
    ) -> Mapping[str, Any]:
        cursor.execute(
            """
            SELECT share_grant.id, share_grant.vault_id, share_grant.publication_id, share_grant.publication_version_id,
                share_grant.owner_subject_id, share_grant.grantee_subject_hash, share_grant.state,
                share_grant.authority_epoch, share_grant.grant_policy_hash, share_grant.revocation_command_hash,
                vault.owner_subject_id AS vault_owner_subject_id
            FROM publication.share_grants AS share_grant
            JOIN owner_truth.vaults AS vault ON vault.vault_id = share_grant.vault_id
            WHERE share_grant.id = %s AND share_grant.vault_id = %s
            FOR UPDATE OF share_grant, vault
            """,
            (grant_id, context.vault_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise PublicationVisitorAccessDenied("ShareGrant is not available in this Owner Vault")
        if (
            str(row["owner_subject_id"]) != context.owner_subject_id
            or str(row["vault_owner_subject_id"]) != context.owner_subject_id
        ):
            raise PublicationVisitorAccessDenied("ShareGrant is not owned by this Owner")
        return row

    def _grant_with_active_projection(self, cursor: Any, *, grant_id: str) -> Mapping[str, Any]:
        cursor.execute(
            """
            SELECT share_grant.id, share_grant.vault_id, share_grant.publication_id, share_grant.publication_version_id,
                share_grant.owner_subject_id, share_grant.grantee_subject_hash, share_grant.token_hash, share_grant.state,
                share_grant.expires_at, share_grant.use_limit, share_grant.use_count, share_grant.grant_policy_hash,
                share_grant.authority_epoch,
                vault.owner_subject_id AS vault_owner_subject_id,
                vault.authority_epoch AS vault_authority_epoch,
                vault.status AS vault_state,
                publication.state AS publication_state,
                publication.authority_epoch AS publication_authority_epoch,
                projection.state AS projection_state
            FROM publication.share_grants AS share_grant
            JOIN owner_truth.vaults AS vault ON vault.vault_id = share_grant.vault_id
            JOIN publication.publications AS publication
              ON publication.id = share_grant.publication_id AND publication.vault_id = share_grant.vault_id
            JOIN publication.publication_versions AS version
              ON version.id = share_grant.publication_version_id
             AND version.publication_id = share_grant.publication_id
             AND version.vault_id = share_grant.vault_id
            JOIN publication.public_projections AS projection
              ON projection.publication_version_id = version.id
             AND projection.publication_id = version.publication_id
             AND projection.vault_id = version.vault_id
            WHERE share_grant.id = %s
            FOR UPDATE OF share_grant, vault, publication, version, projection
            """,
            (grant_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise PublicationVisitorAccessDenied("ShareGrant credential is not accepted")
        if (
            str(row["vault_state"]) != "active"
            or str(row["publication_state"]) != "confirmed"
            or str(row["projection_state"]) != "active"
            or int(row["vault_authority_epoch"]) != int(row["authority_epoch"])
            or int(row["publication_authority_epoch"]) != int(row["authority_epoch"])
            or str(row["vault_owner_subject_id"]) != str(row["owner_subject_id"])
        ):
            raise PublicationVisitorAccessUnavailable("publication projection is not active")
        return row

    @staticmethod
    def _insert_receipt(
        cursor: Any,
        *,
        grant_id: str,
        scope: PublicationGrantScope,
        action: str,
        command_id_hash: str,
        actor_subject_hash: str,
        visitor_subject_hash: str,
        policy_hash: str,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO publication.share_grant_authorization_receipts (
                id, vault_id, publication_id, publication_version_id, share_grant_id,
                action, command_hash, actor_subject_hash, visitor_subject_hash, outcome, policy_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'accepted', %s)
            """,
            (
                _receipt_id(grant_id=grant_id, action=action, command_id_hash=command_id_hash),
                scope.vault_id,
                scope.publication_id,
                scope.publication_version_id,
                grant_id,
                action,
                command_id_hash,
                actor_subject_hash,
                visitor_subject_hash,
                policy_hash,
            ),
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - deployment dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "DenyPublicationVisitorEligibilityResolver",
    "InMemoryPublicationVisitorAccessRepository",
    "PostgresPublicationVisitorAccessRepository",
    "PublicationGrantIssueCommand",
    "PublicationGrantIssueResult",
    "PublicationGrantRevokeCommand",
    "PublicationGrantRevokeResult",
    "PublicationGrantScope",
    "PublicationVisitorAccessConflict",
    "PublicationVisitorAccessDenied",
    "PublicationVisitorAccessDisabled",
    "PublicationVisitorAccessError",
    "PublicationVisitorAccessRepository",
    "PublicationVisitorAccessService",
    "PublicationVisitorAccessUnavailable",
    "PublicationVisitorAdultVerificationRequired",
    "PublicationVisitorAdmissionResult",
    "PublicationVisitorEligibility",
    "PublicationVisitorEligibilityResolver",
    "PUBLICATION_VISITOR_ACCESS_SCHEMA_VERSION",
    "StaticPublicationVisitorEligibilityResolver",
]
