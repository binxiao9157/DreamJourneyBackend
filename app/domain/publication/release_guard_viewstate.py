"""Default-deny hidden Publication ViewState and release-guard contract.

This G0 module defines the value-minimized response boundary for a future
Owner publication-management surface and a future Visitor text surface. It
accepts only opaque identifiers, hashes, lifecycle state, policy booleans and
aggregate counts. It never reads a private Source/Memory/Persona record,
creates a route, issues a Visitor session, queries a public store, persists a
metric, or enables a release surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID

from .lifecycle_propagation import PublicationLifecycleState
from .schema_authz import (
    PublicationAuthorizationContext,
    PublicationAuthorizationPrincipal,
    PublicationPrincipalKind,
)
from .share_grant_session import (
    PublicationAdultVerificationState,
    PublicationVisitorIdentity,
    PublicationVisitorRelationshipOrigin,
)


PUBLICATION_RELEASE_GUARD_VIEWSTATE_G0_SCHEMA_VERSION = "publication-release-guard-viewstate-g0-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class PublicationReleaseGuardViewStateError(ValueError):
    """Raised when a future publication ViewState envelope is malformed."""


class PublicationViewAudience(str, Enum):
    OWNER = "owner"
    VISITOR = "visitor"


class PublicationReleaseGuardDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    OWNER_SCOPE_DENIED = "owner_scope_denied"
    VISITOR_SCOPE_DENIED = "visitor_scope_denied"
    VISITOR_SESSION_REQUIRED = "visitor_session_required"
    VISITOR_ADULT_VERIFICATION_REQUIRED = "visitor_adult_verification_required"
    FAMILY_AUTO_GRANT_DENIED = "family_auto_grant_denied"
    OFFLINE_DENIED = "offline_denied"
    RELEASE_GUARD_REQUIRED = "release_guard_required"
    PRIVACY_THRESHOLD_REQUIRED = "privacy_threshold_required"
    POLICY_DISABLED = "policy_disabled"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise PublicationReleaseGuardViewStateError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise PublicationReleaseGuardViewStateError(f"{field} must be a UUID") from exc


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise PublicationReleaseGuardViewStateError(f"{field} must be a SHA-256 digest")
    return normalized


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicationReleaseGuardViewStateError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PublicationReleaseGuardViewStateError(f"{field} must be a positive integer")
    return value


def _strict_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise PublicationReleaseGuardViewStateError(f"{field} must be a boolean")
    return value


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PublicationAggregateMetrics:
    """Aggregate-only metrics; there is no Visitor body or identity payload."""

    grant_count: int
    session_count: int
    feedback_count: int
    report_count: int
    receipt_count: int
    minimum_sample_size: int

    def __post_init__(self) -> None:
        for field_name in (
            "grant_count",
            "session_count",
            "feedback_count",
            "report_count",
            "receipt_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field=field_name),
            )
        object.__setattr__(
            self,
            "minimum_sample_size",
            _positive_int(self.minimum_sample_size, field="minimum_sample_size"),
        )

    @property
    def privacy_threshold_met(self) -> bool:
        return self.session_count >= self.minimum_sample_size

    def owner_summary(self) -> dict[str, object]:
        if not self.privacy_threshold_met:
            return {
                "metricsSuppressed": True,
                "minimumSampleSize": self.minimum_sample_size,
                "privacyThresholdMet": False,
            }
        return {
            "feedbackCount": self.feedback_count,
            "grantCount": self.grant_count,
            "metricsSuppressed": False,
            "minimumSampleSize": self.minimum_sample_size,
            "privacyThresholdMet": True,
            "receiptCount": self.receipt_count,
            "reportCount": self.report_count,
            "sessionCount": self.session_count,
        }


@dataclass(frozen=True)
class PublicationReleasePolicySnapshot:
    """Candidate server-policy inputs; no combination opens G0 release."""

    server_publication_switch_enabled: bool = False
    visitor_feature_switch_enabled: bool = False
    policy_ttl_valid: bool = False
    minimum_client_satisfied: bool = False
    cohort_approved: bool = False
    offline: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "server_publication_switch_enabled",
            "visitor_feature_switch_enabled",
            "policy_ttl_valid",
            "minimum_client_satisfied",
            "cohort_approved",
            "offline",
        ):
            object.__setattr__(
                self,
                field_name,
                _strict_bool(getattr(self, field_name), field=field_name),
            )

    @property
    def release_prerequisites_present(self) -> bool:
        return all(
            (
                self.server_publication_switch_enabled,
                self.visitor_feature_switch_enabled,
                self.policy_ttl_valid,
                self.minimum_client_satisfied,
                self.cohort_approved,
            )
        )


@dataclass(frozen=True)
class PublicationViewStateRequest:
    """Hash-only future ViewState request scoped to one published version."""

    publication_id: str
    publication_version_id: str
    vault_id: str
    owner_subject_hash: str
    authority_epoch: int
    policy_hash: str
    lifecycle_state: PublicationLifecycleState
    audience: PublicationViewAudience
    aggregate_metrics: PublicationAggregateMetrics
    release_policy: PublicationReleasePolicySnapshot

    def __post_init__(self) -> None:
        for field_name in ("publication_id", "publication_version_id"):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field=field_name))
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
        object.__setattr__(self, "policy_hash", _digest(self.policy_hash, field="policy_hash"))
        object.__setattr__(self, "lifecycle_state", PublicationLifecycleState(self.lifecycle_state))
        object.__setattr__(self, "audience", PublicationViewAudience(self.audience))
        if not isinstance(self.aggregate_metrics, PublicationAggregateMetrics):
            raise PublicationReleaseGuardViewStateError(
                "aggregate_metrics must be PublicationAggregateMetrics"
            )
        if not isinstance(self.release_policy, PublicationReleasePolicySnapshot):
            raise PublicationReleaseGuardViewStateError(
                "release_policy must be PublicationReleasePolicySnapshot"
            )


@dataclass(frozen=True)
class PublicationReleaseGuardViewStateResult:
    disposition: PublicationReleaseGuardDisposition
    reason_codes: tuple[str, ...]
    audience: PublicationViewAudience | None = None
    lifecycle_state: PublicationLifecycleState | None = None
    owner_aggregate_metrics: dict[str, object] | None = None
    scope_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disposition",
            PublicationReleaseGuardDisposition(self.disposition),
        )
        reasons = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise PublicationReleaseGuardViewStateError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.audience is not None:
            object.__setattr__(self, "audience", PublicationViewAudience(self.audience))
        if self.lifecycle_state is not None:
            object.__setattr__(
                self,
                "lifecycle_state",
                PublicationLifecycleState(self.lifecycle_state),
            )
        if self.scope_hash is not None:
            object.__setattr__(self, "scope_hash", _digest(self.scope_hash, field="scope_hash"))
        if self.owner_aggregate_metrics is not None:
            allowed = {
                "feedbackCount",
                "grantCount",
                "metricsSuppressed",
                "minimumSampleSize",
                "privacyThresholdMet",
                "receiptCount",
                "reportCount",
                "sessionCount",
            }
            observed = set(self.owner_aggregate_metrics)
            if not observed.issubset(allowed):
                raise PublicationReleaseGuardViewStateError(
                    "owner_aggregate_metrics contains a non-allowlisted field"
                )
            object.__setattr__(
                self,
                "owner_aggregate_metrics",
                {key: self.owner_aggregate_metrics[key] for key in sorted(observed)},
            )

    @property
    def owner_management_visible(self) -> bool:
        return False

    @property
    def visitor_feature_visible(self) -> bool:
        return False

    @property
    def visitor_session_accepted(self) -> bool:
        return False

    @property
    def aggregate_metrics_query_allowed(self) -> bool:
        return False

    @property
    def public_route_registered(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "aggregateMetricsQueryAllowed": self.aggregate_metrics_query_allowed,
            "audience": self.audience.value if self.audience is not None else None,
            "ownerManagementVisible": self.owner_management_visible,
            "publicRouteRegistered": self.public_route_registered,
            "reasonCodes": list(self.reason_codes),
            "releaseVisible": False,
            "schemaVersion": PUBLICATION_RELEASE_GUARD_VIEWSTATE_G0_SCHEMA_VERSION,
            "status": self.disposition.value,
            "visitorFeatureVisible": self.visitor_feature_visible,
            "visitorSessionAccepted": self.visitor_session_accepted,
        }
        if self.lifecycle_state is not None:
            summary["lifecycleState"] = self.lifecycle_state.value
        if self.owner_aggregate_metrics is not None:
            summary["ownerAggregateMetrics"] = self.owner_aggregate_metrics
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        return summary


def _scope_hash(
    *,
    context: PublicationAuthorizationContext,
    principal: PublicationAuthorizationPrincipal,
    request: PublicationViewStateRequest,
    visitor: PublicationVisitorIdentity | None,
) -> str:
    return _hash(
        {
            "audience": request.audience.value,
            "authorityEpoch": request.authority_epoch,
            "lifecycleState": request.lifecycle_state.value,
            "ownerSubjectHash": request.owner_subject_hash,
            "policyHash": request.policy_hash,
            "principalKind": principal.kind.value,
            "principalSubjectHash": principal.subject_hash,
            "publicationId": request.publication_id,
            "publicationVersionId": request.publication_version_id,
            "visitorSubjectHash": visitor.subject_hash if visitor is not None else None,
            "vaultId": request.vault_id,
        }
    )


def _result(
    disposition: PublicationReleaseGuardDisposition,
    reason: str,
    *,
    request: PublicationViewStateRequest | None = None,
    scope_hash: str | None = None,
    include_owner_metrics: bool = False,
) -> PublicationReleaseGuardViewStateResult:
    metrics = None
    if include_owner_metrics and request is not None:
        metrics = request.aggregate_metrics.owner_summary()
    return PublicationReleaseGuardViewStateResult(
        disposition=disposition,
        reason_codes=(reason,),
        audience=request.audience if request is not None else None,
        lifecycle_state=request.lifecycle_state if request is not None else None,
        owner_aggregate_metrics=metrics,
        scope_hash=scope_hash,
    )


def evaluate_publication_release_guard_viewstate(
    *,
    context: PublicationAuthorizationContext | object,
    principal: PublicationAuthorizationPrincipal | object,
    request: PublicationViewStateRequest | object,
    visitor: PublicationVisitorIdentity | object | None = None,
    enabled: bool = False,
) -> PublicationReleaseGuardViewStateResult:
    """Return a hidden/default-deny ViewState; no input combination opens release."""

    if enabled is not True:
        return _result(
            PublicationReleaseGuardDisposition.SHADOW_DISABLED,
            "publicationReleaseGuardViewStateShadowDisabled",
        )
    if not all(
        (
            isinstance(context, PublicationAuthorizationContext),
            isinstance(principal, PublicationAuthorizationPrincipal),
            isinstance(request, PublicationViewStateRequest),
        )
    ):
        return _result(
            PublicationReleaseGuardDisposition.INVALID_CONTEXT,
            "invalidPublicationReleaseGuardViewStateContext",
        )
    if visitor is not None and not isinstance(visitor, PublicationVisitorIdentity):
        return _result(
            PublicationReleaseGuardDisposition.INVALID_CONTEXT,
            "invalidPublicationReleaseGuardVisitorIdentity",
            request=request,
        )
    if (
        request.vault_id != context.vault_id
        or request.owner_subject_hash != context.owner_subject_hash
        or request.authority_epoch != context.authority_epoch
    ):
        return _result(
            PublicationReleaseGuardDisposition.OWNER_SCOPE_DENIED,
            "publicationViewStateAuthorityScopeMismatch",
            request=request,
        )

    scope_hash = _scope_hash(
        context=context,
        principal=principal,
        request=request,
        visitor=visitor if isinstance(visitor, PublicationVisitorIdentity) else None,
    )
    if request.audience is PublicationViewAudience.OWNER:
        if (
            principal.kind is not PublicationPrincipalKind.OWNER
            or principal.vault_id != context.vault_id
            or principal.subject_hash != context.owner_subject_hash
        ):
            return _result(
                PublicationReleaseGuardDisposition.OWNER_SCOPE_DENIED,
                "ownerPublicationManagementScopeMismatch",
                request=request,
                scope_hash=scope_hash,
            )
        if not request.aggregate_metrics.privacy_threshold_met:
            return _result(
                PublicationReleaseGuardDisposition.PRIVACY_THRESHOLD_REQUIRED,
                "ownerAggregateMetricsBelowPrivacyMinimum",
                request=request,
                scope_hash=scope_hash,
                include_owner_metrics=True,
            )
        return _result(
            PublicationReleaseGuardDisposition.POLICY_DISABLED,
            "ownerPublicationManagementReleasePolicyDisabled",
            request=request,
            scope_hash=scope_hash,
            include_owner_metrics=True,
        )

    if principal.kind is not PublicationPrincipalKind.VISITOR or principal.vault_id is not None:
        return _result(
            PublicationReleaseGuardDisposition.VISITOR_SCOPE_DENIED,
            "visitorPrincipalMustRemainOutsideOwnerVault",
            request=request,
            scope_hash=scope_hash,
        )
    if not isinstance(visitor, PublicationVisitorIdentity):
        return _result(
            PublicationReleaseGuardDisposition.VISITOR_SESSION_REQUIRED,
            "visitorIdentityAndGrantBoundSessionRequired",
            request=request,
            scope_hash=scope_hash,
        )
    if principal.subject_hash != visitor.subject_hash:
        return _result(
            PublicationReleaseGuardDisposition.VISITOR_SCOPE_DENIED,
            "visitorPrincipalIdentityMismatch",
            request=request,
            scope_hash=scope_hash,
        )
    if visitor.relationship_origin is PublicationVisitorRelationshipOrigin.FAMILY_DERIVED:
        return _result(
            PublicationReleaseGuardDisposition.FAMILY_AUTO_GRANT_DENIED,
            "familyRelationshipDoesNotOpenVisitorFeature",
            request=request,
            scope_hash=scope_hash,
        )
    if visitor.adult_verification is not PublicationAdultVerificationState.VERIFIED:
        return _result(
            PublicationReleaseGuardDisposition.VISITOR_ADULT_VERIFICATION_REQUIRED,
            "visitorAdultVerificationRequiredForPublicationFeature",
            request=request,
            scope_hash=scope_hash,
        )
    if request.release_policy.offline:
        return _result(
            PublicationReleaseGuardDisposition.OFFLINE_DENIED,
            "visitorPublicationFeatureDeniedOffline",
            request=request,
            scope_hash=scope_hash,
        )
    if not request.release_policy.release_prerequisites_present:
        return _result(
            PublicationReleaseGuardDisposition.RELEASE_GUARD_REQUIRED,
            "visitorPublicationReleaseGuardPrerequisitesMissing",
            request=request,
            scope_hash=scope_hash,
        )
    return _result(
        PublicationReleaseGuardDisposition.POLICY_DISABLED,
        "visitorPublicationFeatureRemainsDisabledAtG0",
        request=request,
        scope_hash=scope_hash,
    )


__all__ = [
    "PUBLICATION_RELEASE_GUARD_VIEWSTATE_G0_SCHEMA_VERSION",
    "PublicationAggregateMetrics",
    "PublicationReleaseGuardDisposition",
    "PublicationReleaseGuardViewStateError",
    "PublicationReleaseGuardViewStateResult",
    "PublicationReleasePolicySnapshot",
    "PublicationViewAudience",
    "PublicationViewStateRequest",
    "evaluate_publication_release_guard_viewstate",
]
