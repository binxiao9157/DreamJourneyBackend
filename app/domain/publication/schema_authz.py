"""Default-deny publication schema authorization contract.

This G0 module models the boundary between private Owner Truth authority and a
future separately stored public projection. It does not create publications,
versions, grants, sessions, feedback, routes, database records or public DTOs.
Every result remains denied while the captured publication policy is disabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re


PUBLICATION_SCHEMA_AUTHZ_G0_SCHEMA_VERSION = "publication-schema-authz-g0-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class PublicationSchemaAuthorizationError(ValueError):
    """Raised when a synthetic publication authorization envelope is invalid."""


class PublicationPrincipalKind(str, Enum):
    OWNER = "owner"
    VISITOR = "visitor"
    PUBLIC_GATEWAY = "publicGateway"
    SYSTEM_WORKER = "systemWorker"


class PublicationDataPlane(str, Enum):
    PRIVATE_AUTHORITY = "privateAuthority"
    PUBLIC_STORE = "publicStore"


class PublicationAccessAction(str, Enum):
    SCHEMA_CONTRACT_READ = "schemaContractRead"
    PUBLICATION_WRITE = "publicationWrite"
    SHARE_GRANT_WRITE = "shareGrantWrite"
    VISITOR_SESSION_WRITE = "visitorSessionWrite"
    PUBLIC_STORE_READ = "publicStoreRead"


class PublicationAuthorizationDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    PRIVATE_AUTHORITY_DENIED = "private_authority_denied"
    CROSS_VAULT_DENIED = "cross_vault_denied"
    POLICY_DISABLED = "policy_disabled"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise PublicationSchemaAuthorizationError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise PublicationSchemaAuthorizationError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicationSchemaAuthorizationError(f"{field} must be a non-negative integer")
    return value


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PublicationAuthorizationContext:
    """Owner Truth scope that a future publication writer must bind to."""

    vault_id: str
    owner_subject_hash: str
    authority_epoch: int
    policy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_hash",
            _digest(self.owner_subject_hash, field="owner_subject_hash"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _nonnegative_int(self.authority_epoch, field="authority_epoch"),
        )
        object.__setattr__(
            self,
            "policy_version",
            _identifier(self.policy_version, field="policy_version"),
        )


@dataclass(frozen=True)
class PublicationAuthorizationPrincipal:
    """Value-minimized principal binding for a future private/public boundary."""

    kind: PublicationPrincipalKind
    vault_id: str | None
    subject_hash: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", PublicationPrincipalKind(self.kind))
        if self.vault_id is not None:
            object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        if self.subject_hash is not None:
            object.__setattr__(
                self,
                "subject_hash",
                _digest(self.subject_hash, field="subject_hash"),
            )
        if self.kind is PublicationPrincipalKind.OWNER:
            if self.vault_id is None or self.subject_hash is None:
                raise PublicationSchemaAuthorizationError(
                    "owner principal requires vault_id and subject_hash"
                )
        elif self.subject_hash is not None and self.kind is PublicationPrincipalKind.PUBLIC_GATEWAY:
            raise PublicationSchemaAuthorizationError(
                "public gateway must not carry a subject hash"
            )


@dataclass(frozen=True)
class PublicationAuthorizationResult:
    disposition: PublicationAuthorizationDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disposition",
            PublicationAuthorizationDisposition(self.disposition),
        )
        reasons = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise PublicationSchemaAuthorizationError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.scope_hash is not None:
            object.__setattr__(self, "scope_hash", _digest(self.scope_hash, field="scope_hash"))

    @property
    def private_authority_read_allowed(self) -> bool:
        return False

    @property
    def publication_writer_allowed(self) -> bool:
        return False

    @property
    def public_store_read_allowed(self) -> bool:
        return False

    @property
    def share_grant_issued(self) -> bool:
        return False

    @property
    def visitor_session_issued(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "privateAuthorityReadAllowed": self.private_authority_read_allowed,
            "publicationWriterAllowed": self.publication_writer_allowed,
            "publicStoreReadAllowed": self.public_store_read_allowed,
            "reasonCodes": list(self.reason_codes),
            "releaseVisible": False,
            "schemaVersion": PUBLICATION_SCHEMA_AUTHZ_G0_SCHEMA_VERSION,
            "shareGrantIssued": self.share_grant_issued,
            "status": self.disposition.value,
            "visitorSessionIssued": self.visitor_session_issued,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        return summary


def _scope_hash(
    context: PublicationAuthorizationContext,
    principal: PublicationAuthorizationPrincipal,
    data_plane: PublicationDataPlane,
    action: PublicationAccessAction,
) -> str:
    return _hash(
        {
            "action": action.value,
            "authorityEpoch": context.authority_epoch,
            "dataPlane": data_plane.value,
            "ownerSubjectHash": context.owner_subject_hash,
            "policyVersion": context.policy_version,
            "principalKind": principal.kind.value,
            "principalSubjectHash": principal.subject_hash,
            "principalVaultId": principal.vault_id,
            "vaultId": context.vault_id,
        }
    )


def evaluate_publication_schema_authz(
    *,
    context: PublicationAuthorizationContext | object,
    principal: PublicationAuthorizationPrincipal | object,
    data_plane: PublicationDataPlane | object,
    action: PublicationAccessAction | object,
    enabled: bool = False,
) -> PublicationAuthorizationResult:
    """Fail closed before future publication persistence or public access exists."""

    if enabled is not True:
        return PublicationAuthorizationResult(
            disposition=PublicationAuthorizationDisposition.SHADOW_DISABLED,
            reason_codes=("publicationSchemaAuthzShadowDisabled",),
        )
    if not isinstance(context, PublicationAuthorizationContext) or not isinstance(
        principal, PublicationAuthorizationPrincipal
    ):
        return PublicationAuthorizationResult(
            disposition=PublicationAuthorizationDisposition.INVALID_CONTEXT,
            reason_codes=("invalidPublicationAuthorizationContext",),
        )
    try:
        typed_plane = PublicationDataPlane(data_plane)
        typed_action = PublicationAccessAction(action)
    except (TypeError, ValueError):
        return PublicationAuthorizationResult(
            disposition=PublicationAuthorizationDisposition.INVALID_CONTEXT,
            reason_codes=("invalidPublicationDataPlaneOrAction",),
        )

    scope_hash = _scope_hash(context, principal, typed_plane, typed_action)
    if typed_plane is PublicationDataPlane.PRIVATE_AUTHORITY and principal.kind in {
        PublicationPrincipalKind.VISITOR,
        PublicationPrincipalKind.PUBLIC_GATEWAY,
    }:
        return PublicationAuthorizationResult(
            disposition=PublicationAuthorizationDisposition.PRIVATE_AUTHORITY_DENIED,
            reason_codes=("publicPrincipalCannotReadPrivateAuthority",),
            scope_hash=scope_hash,
        )
    if principal.kind is PublicationPrincipalKind.OWNER and (
        principal.vault_id != context.vault_id
        or principal.subject_hash != context.owner_subject_hash
    ):
        return PublicationAuthorizationResult(
            disposition=PublicationAuthorizationDisposition.CROSS_VAULT_DENIED,
            reason_codes=("ownerVaultOrSubjectMismatch",),
            scope_hash=scope_hash,
        )
    return PublicationAuthorizationResult(
        disposition=PublicationAuthorizationDisposition.POLICY_DISABLED,
        reason_codes=(
            "publicationVisitorPolicyDefaultDeny",
            "publicGatewayAndWriterRemainDisabled",
        ),
        scope_hash=scope_hash,
    )


__all__ = [
    "PUBLICATION_SCHEMA_AUTHZ_G0_SCHEMA_VERSION",
    "PublicationAccessAction",
    "PublicationAuthorizationContext",
    "PublicationAuthorizationDisposition",
    "PublicationAuthorizationPrincipal",
    "PublicationAuthorizationResult",
    "PublicationDataPlane",
    "PublicationPrincipalKind",
    "PublicationSchemaAuthorizationError",
    "evaluate_publication_schema_authz",
]
