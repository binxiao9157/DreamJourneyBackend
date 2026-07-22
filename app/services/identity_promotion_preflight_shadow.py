"""Fail-closed G0 preflight for a future legacy identity promotion.

This module models the minimum evidence needed before any legacy Owner data
could be associated with a verified V4 subject.  It is deliberately a pure,
default-off observer: it has no persistence, API, provider, or side-effect
dependency and cannot create a subject, claim an alias, issue a session, or
authorize a route.

The preflight accepts only opaque identifiers and already-redacted hashes.
Its public summary returns only state, reason codes, and derived hashes so a
future migration receipt cannot expose a phone number, token, or content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re


IDENTITY_PROMOTION_PREFLIGHT_SHADOW_SCHEMA_VERSION = (
    "identity-promotion-preflight-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class IdentityPromotionPreflightContractError(ValueError):
    """Raised when a synthetic identity-promotion envelope is malformed."""


class LegacyAliasClaimState(str, Enum):
    """Read-only state of a legacy alias before an explicit migration claim."""

    EXPLICIT_OWNER_CLAIM = "explicit_owner_claim"
    CLAIM_PENDING = "claim_pending"
    SHARED = "shared"
    UNKNOWN = "unknown"
    QUARANTINED = "quarantined"


class IdentityPromotionSessionState(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    REVOKED = "revoked"
    MISSING = "missing"


class IdentityPromotionPrincipalSource(str, Enum):
    SERVER_DERIVED = "server_derived"
    PAYLOAD = "payload"
    ANONYMOUS = "anonymous"
    SYSTEM = "system"
    SHADOW = "shadow"


class IdentityPromotionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class IdentityPromotionPreflightDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    CLAIM_PENDING = "claim_pending"
    QUARANTINED = "quarantined"
    DENIED = "denied"
    SHADOW_ELIGIBLE = "shadow_eligible"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise IdentityPromotionPreflightContractError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _sha256(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise IdentityPromotionPreflightContractError(
            f"{field} must be a SHA-256 digest"
        )
    return normalized


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise IdentityPromotionPreflightContractError(
            "identity promotion preflight values must be JSON serializable"
        ) from exc


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LegacyAliasClaim:
    """A value-minimized alias claim input; no phone or provider token is allowed."""

    legacy_alias_hash: str
    state: LegacyAliasClaimState
    explicit_claim_subject_id: str | None = None
    explicit_claim_evidence_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "legacy_alias_hash",
            _sha256(self.legacy_alias_hash, field="legacy_alias_hash"),
        )
        object.__setattr__(self, "state", LegacyAliasClaimState(self.state))
        if self.state is LegacyAliasClaimState.EXPLICIT_OWNER_CLAIM:
            object.__setattr__(
                self,
                "explicit_claim_subject_id",
                _identifier(
                    self.explicit_claim_subject_id,
                    field="explicit_claim_subject_id",
                ),
            )
            object.__setattr__(
                self,
                "explicit_claim_evidence_hash",
                _sha256(
                    self.explicit_claim_evidence_hash,
                    field="explicit_claim_evidence_hash",
                ),
            )
            return
        if (
            self.explicit_claim_subject_id is not None
            or self.explicit_claim_evidence_hash is not None
        ):
            raise IdentityPromotionPreflightContractError(
                "only an explicit owner claim may carry claim evidence"
            )


@dataclass(frozen=True)
class IdentityPromotionPreflightContext:
    """Server-resolved identity, lease, route, and resource evidence only."""

    vault_id: str
    server_subject_id: str
    session_subject_id: str
    resource_owner_subject_id: str
    payload_owner_subject_id: str
    session_vault_id: str
    resource_vault_id: str
    payload_vault_id: str
    account_generation: str
    session_account_generation: str
    session_state: IdentityPromotionSessionState
    principal_source: IdentityPromotionPrincipalSource
    route_decision: IdentityPromotionDecision
    resource_decision: IdentityPromotionDecision
    route_policy_version: str
    route_evidence_hash: str
    resource_evidence_hash: str

    def __post_init__(self) -> None:
        for field in (
            "vault_id",
            "server_subject_id",
            "session_subject_id",
            "resource_owner_subject_id",
            "payload_owner_subject_id",
            "session_vault_id",
            "resource_vault_id",
            "payload_vault_id",
            "account_generation",
            "session_account_generation",
            "route_policy_version",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field=field))
        object.__setattr__(
            self,
            "session_state",
            IdentityPromotionSessionState(self.session_state),
        )
        object.__setattr__(
            self,
            "principal_source",
            IdentityPromotionPrincipalSource(self.principal_source),
        )
        object.__setattr__(
            self,
            "route_decision",
            IdentityPromotionDecision(self.route_decision),
        )
        object.__setattr__(
            self,
            "resource_decision",
            IdentityPromotionDecision(self.resource_decision),
        )
        for field in ("route_evidence_hash", "resource_evidence_hash"):
            object.__setattr__(self, field, _sha256(getattr(self, field), field=field))


@dataclass(frozen=True)
class IdentityPromotionPreflightShadow:
    """A non-mutating identity-promotion assessment for G0 evidence only."""

    enabled: bool
    disposition: IdentityPromotionPreflightDisposition
    reason_codes: tuple[str, ...]
    alias_hash: str | None = None
    scope_hash: str | None = None
    evidence_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise IdentityPromotionPreflightContractError("shadow enabled must be a boolean")
        if not isinstance(self.disposition, IdentityPromotionPreflightDisposition):
            raise IdentityPromotionPreflightContractError("shadow disposition is required")
        normalized_reasons = tuple(
            sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes})
        )
        if not normalized_reasons:
            raise IdentityPromotionPreflightContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized_reasons)
        for field in ("alias_hash", "scope_hash", "evidence_hash"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _sha256(value, field=field))

    @property
    def promotion_written(self) -> bool:
        return False

    @property
    def alias_claim_committed(self) -> bool:
        return False

    @property
    def session_issued(self) -> bool:
        return False

    @property
    def route_policy_changed(self) -> bool:
        return False

    @property
    def visitor_enumeration_allowed(self) -> bool:
        return False

    @property
    def cutover_allowed(self) -> bool:
        return False

    @property
    def would_be_eligible_for_future_promotion(self) -> bool:
        return self.disposition is IdentityPromotionPreflightDisposition.SHADOW_ELIGIBLE

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "aliasClaimCommitted": self.alias_claim_committed,
            "cutoverAllowed": self.cutover_allowed,
            "enabled": self.enabled,
            "promotionWritten": self.promotion_written,
            "reasonCodes": list(self.reason_codes),
            "requiredExternalGates": ["G1", "G2", "G4"],
            "routePolicyChanged": self.route_policy_changed,
            "schemaVersion": IDENTITY_PROMOTION_PREFLIGHT_SHADOW_SCHEMA_VERSION,
            "sessionIssued": self.session_issued,
            "shadowOnly": True,
            "status": self.disposition.value,
            "visitorEnumerationAllowed": self.visitor_enumeration_allowed,
            "wouldBeEligibleForFuturePromotion": self.would_be_eligible_for_future_promotion,
        }
        if self.alias_hash is not None:
            summary["aliasHash"] = self.alias_hash
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.evidence_hash is not None:
            summary["evidenceHash"] = self.evidence_hash
        return summary


def _scope_hash(
    claim: LegacyAliasClaim,
    context: IdentityPromotionPreflightContext,
) -> str:
    return _digest(
        {
            "aliasHash": claim.legacy_alias_hash,
            "payloadOwnerSubjectId": context.payload_owner_subject_id,
            "payloadVaultId": context.payload_vault_id,
            "resourceOwnerSubjectId": context.resource_owner_subject_id,
            "resourceVaultId": context.resource_vault_id,
            "serverSubjectId": context.server_subject_id,
            "sessionAccountGeneration": context.session_account_generation,
            "sessionSubjectId": context.session_subject_id,
            "sessionVaultId": context.session_vault_id,
            "vaultId": context.vault_id,
        }
    )


def _evidence_hash(
    claim: LegacyAliasClaim,
    context: IdentityPromotionPreflightContext,
) -> str:
    return _digest(
        {
            "accountGeneration": context.account_generation,
            "aliasHash": claim.legacy_alias_hash,
            "claimEvidenceHash": claim.explicit_claim_evidence_hash,
            "claimState": claim.state.value,
            "principalSource": context.principal_source.value,
            "resourceDecision": context.resource_decision.value,
            "resourceEvidenceHash": context.resource_evidence_hash,
            "routeDecision": context.route_decision.value,
            "routeEvidenceHash": context.route_evidence_hash,
            "routePolicyVersion": context.route_policy_version,
            "sessionAccountGeneration": context.session_account_generation,
            "sessionState": context.session_state.value,
        }
    )


def _claim_preflight_result(
    claim: LegacyAliasClaim,
) -> tuple[IdentityPromotionPreflightDisposition, tuple[str, ...]] | None:
    if claim.state is LegacyAliasClaimState.QUARANTINED:
        return (
            IdentityPromotionPreflightDisposition.QUARANTINED,
            ("legacyAliasQuarantined", "noAutomaticAliasPromotion"),
        )
    if claim.state is LegacyAliasClaimState.SHARED:
        return (
            IdentityPromotionPreflightDisposition.QUARANTINED,
            ("legacyAliasShared", "explicitOwnerClaimRequired"),
        )
    if claim.state is LegacyAliasClaimState.UNKNOWN:
        return (
            IdentityPromotionPreflightDisposition.CLAIM_PENDING,
            ("legacyAliasUnknown", "explicitOwnerClaimRequired"),
        )
    if claim.state is LegacyAliasClaimState.CLAIM_PENDING:
        return (
            IdentityPromotionPreflightDisposition.CLAIM_PENDING,
            ("legacyAliasClaimPending", "explicitOwnerClaimRequired"),
        )
    return None


def _denial_reasons(
    claim: LegacyAliasClaim,
    context: IdentityPromotionPreflightContext,
) -> tuple[str, ...]:
    reasons: set[str] = set()
    if context.principal_source is not IdentityPromotionPrincipalSource.SERVER_DERIVED:
        reasons.add("principalNotServerDerived")
    if context.session_state is not IdentityPromotionSessionState.ACTIVE:
        reasons.add("sessionNotActive")
    if context.session_subject_id != context.server_subject_id:
        reasons.add("sessionSubjectMismatch")
    if context.session_vault_id != context.vault_id:
        reasons.add("sessionVaultMismatch")
    if context.resource_owner_subject_id != context.server_subject_id:
        reasons.add("resourceOwnerSubjectMismatch")
    if context.payload_owner_subject_id != context.server_subject_id:
        reasons.add("payloadOwnerSubjectMismatch")
    if context.resource_vault_id != context.vault_id:
        reasons.add("resourceVaultMismatch")
    if context.payload_vault_id != context.vault_id:
        reasons.add("payloadVaultMismatch")
    if context.account_generation != context.session_account_generation:
        reasons.add("accountGenerationMismatch")
    if context.route_decision is not IdentityPromotionDecision.ALLOW:
        reasons.add("routeDecisionDenied")
    if context.resource_decision is not IdentityPromotionDecision.ALLOW:
        reasons.add("resourceDecisionDenied")
    if claim.explicit_claim_subject_id != context.server_subject_id:
        reasons.add("explicitClaimSubjectMismatch")
    return tuple(sorted(reasons))


def observe_identity_promotion_preflight(
    claim: LegacyAliasClaim | object,
    *,
    context: IdentityPromotionPreflightContext | object,
    enabled: bool = False,
) -> IdentityPromotionPreflightShadow:
    """Assess a future identity promotion without writing or authorizing it.

    `SHADOW_ELIGIBLE` is explicitly not a promotion decision.  A future G1,
    G2, and G4-approved command must repeat every check transactionally before
    any alias, subject, session, route policy, or Owner data is changed.
    """

    if not enabled:
        return IdentityPromotionPreflightShadow(
            enabled=False,
            disposition=IdentityPromotionPreflightDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(claim, LegacyAliasClaim) or not isinstance(
        context,
        IdentityPromotionPreflightContext,
    ):
        return IdentityPromotionPreflightShadow(
            enabled=True,
            disposition=IdentityPromotionPreflightDisposition.INVALID_ENVELOPE,
            reason_codes=("invalidIdentityPromotionEnvelope",),
        )

    scope_hash = _scope_hash(claim, context)
    evidence_hash = _evidence_hash(claim, context)
    claim_result = _claim_preflight_result(claim)
    if claim_result is not None:
        disposition, reason_codes = claim_result
        return IdentityPromotionPreflightShadow(
            enabled=True,
            disposition=disposition,
            reason_codes=reason_codes,
            alias_hash=claim.legacy_alias_hash,
            scope_hash=scope_hash,
            evidence_hash=evidence_hash,
        )

    denial_reasons = _denial_reasons(claim, context)
    if denial_reasons:
        return IdentityPromotionPreflightShadow(
            enabled=True,
            disposition=IdentityPromotionPreflightDisposition.DENIED,
            reason_codes=denial_reasons,
            alias_hash=claim.legacy_alias_hash,
            scope_hash=scope_hash,
            evidence_hash=evidence_hash,
        )
    return IdentityPromotionPreflightShadow(
        enabled=True,
        disposition=IdentityPromotionPreflightDisposition.SHADOW_ELIGIBLE,
        reason_codes=(
            "futurePromotionStillRequiresIndependentCommand",
            "separateG1G2G4ApprovalRequired",
        ),
        alias_hash=claim.legacy_alias_hash,
        scope_hash=scope_hash,
        evidence_hash=evidence_hash,
    )


__all__ = [
    "IDENTITY_PROMOTION_PREFLIGHT_SHADOW_SCHEMA_VERSION",
    "IdentityPromotionDecision",
    "IdentityPromotionPreflightContext",
    "IdentityPromotionPreflightContractError",
    "IdentityPromotionPreflightDisposition",
    "IdentityPromotionPreflightShadow",
    "IdentityPromotionPrincipalSource",
    "IdentityPromotionSessionState",
    "LegacyAliasClaim",
    "LegacyAliasClaimState",
    "observe_identity_promotion_preflight",
]
