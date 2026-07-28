"""Default-deny Publication lifecycle and revoke-propagation contract.

This G0 module models the mandatory access-deny and cleanup boundaries for a
future published version. It accepts opaque IDs, hashes, enum state and counts
only. It never mutates a publication, revokes a grant/session, clears a cache,
contacts an index/object/CDN provider, writes a receipt, or exposes a route.
"""

from __future__ import annotations

from dataclasses import dataclass
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


PUBLICATION_LIFECYCLE_PROPAGATION_G0_SCHEMA_VERSION = "publication-lifecycle-propagation-g0-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class PublicationLifecyclePropagationError(ValueError):
    """Raised when a future lifecycle/propagation envelope is malformed."""


class PublicationLifecycleAction(str, Enum):
    UPDATE = "update"
    SUSPEND = "suspend"
    WITHDRAW = "withdraw"


class PublicationLifecycleTrigger(str, Enum):
    OWNER_ACTION = "ownerAction"
    MEMORY_CORRECTION = "memoryCorrection"
    MEMORY_DELETED = "memoryDeleted"
    CONSENT_REVOKED = "consentRevoked"
    THIRD_PARTY_OBJECTION = "thirdPartyObjection"
    RIGHTS_REQUEST = "rightsRequest"


class PublicationLifecycleState(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SUSPENDED = "suspended"
    WITHDRAWN = "withdrawn"


class PublicationPropagationLayer(str, Enum):
    PUBLIC_GATEWAY = "publicGateway"
    SHARE_GRANT = "shareGrant"
    VISITOR_SESSION = "visitorSession"
    PUBLIC_INDEX = "publicIndex"
    CACHE = "cache"
    EXTERNAL_INDEX = "externalIndex"
    OBJECT_STORE = "objectStore"
    CDN = "cdn"


class PublicationLifecycleDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    OWNER_SCOPE_DENIED = "owner_scope_denied"
    DUPLICATE_OR_OUT_OF_ORDER = "duplicate_or_out_of_order"
    WITHDRAWN_REPUBLISH_DENIED = "withdrawn_republish_denied"
    PRIVATE_TRIGGER_SUSPEND_REQUIRED = "private_trigger_suspend_required"
    UPDATE_REQUIRES_NEW_VERSION = "update_requires_new_version"
    UPDATE_CONFIRMATION_REQUIRED = "update_confirmation_required"
    ACCESS_DENY_PLAN_REQUIRED = "access_deny_plan_required"
    EXTERNAL_CLEANUP_GATES_REQUIRED = "external_cleanup_gates_required"
    POLICY_DISABLED = "policy_disabled"


_REQUIRED_DENY_LAYERS = frozenset(
    {
        PublicationPropagationLayer.PUBLIC_GATEWAY,
        PublicationPropagationLayer.SHARE_GRANT,
        PublicationPropagationLayer.VISITOR_SESSION,
        PublicationPropagationLayer.PUBLIC_INDEX,
        PublicationPropagationLayer.CACHE,
    }
)
_EXTERNAL_LAYERS = frozenset(
    {
        PublicationPropagationLayer.EXTERNAL_INDEX,
        PublicationPropagationLayer.OBJECT_STORE,
        PublicationPropagationLayer.CDN,
    }
)
_PRIVATE_TRIGGERS = frozenset(
    {
        PublicationLifecycleTrigger.MEMORY_CORRECTION,
        PublicationLifecycleTrigger.MEMORY_DELETED,
        PublicationLifecycleTrigger.CONSENT_REVOKED,
        PublicationLifecycleTrigger.THIRD_PARTY_OBJECTION,
        PublicationLifecycleTrigger.RIGHTS_REQUEST,
    }
)


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise PublicationLifecyclePropagationError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise PublicationLifecyclePropagationError(f"{field} must be a UUID") from exc


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise PublicationLifecyclePropagationError(f"{field} must be a SHA-256 digest")
    return normalized


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicationLifecyclePropagationError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PublicationLifecyclePropagationError(f"{field} must be a positive integer")
    return value


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PublicationLifecycleCommand:
    """Hash-only command envelope for a future publication state transition."""

    command_id: str
    publication_id: str
    publication_version_id: str
    vault_id: str
    owner_subject_hash: str
    authority_epoch: int
    action: PublicationLifecycleAction
    trigger: PublicationLifecycleTrigger
    current_state: PublicationLifecycleState
    transition_sequence: int
    previous_transition_sequence: int
    request_hash: str
    policy_hash: str
    propagation_layers: tuple[PublicationPropagationLayer, ...]
    active_access_observation_count: int = 0
    new_publication_version_id: str | None = None
    new_pinned_memory_version_hash: str | None = None
    second_confirmation_hash: str | None = None
    external_copy_observed: bool = False

    def __post_init__(self) -> None:
        for field_name in ("command_id", "publication_id", "publication_version_id"):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field=field_name))
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(self, "owner_subject_hash", _digest(self.owner_subject_hash, field="owner_subject_hash"))
        object.__setattr__(self, "authority_epoch", _nonnegative_int(self.authority_epoch, field="authority_epoch"))
        object.__setattr__(self, "action", PublicationLifecycleAction(self.action))
        object.__setattr__(self, "trigger", PublicationLifecycleTrigger(self.trigger))
        object.__setattr__(self, "current_state", PublicationLifecycleState(self.current_state))
        object.__setattr__(
            self,
            "transition_sequence",
            _positive_int(self.transition_sequence, field="transition_sequence"),
        )
        object.__setattr__(
            self,
            "previous_transition_sequence",
            _nonnegative_int(self.previous_transition_sequence, field="previous_transition_sequence"),
        )
        for field_name in ("request_hash", "policy_hash"):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field=field_name))
        try:
            layers = tuple(PublicationPropagationLayer(layer) for layer in self.propagation_layers)
        except (TypeError, ValueError) as exc:
            raise PublicationLifecyclePropagationError("propagation_layers are invalid") from exc
        if len(layers) != len(set(layers)):
            raise PublicationLifecyclePropagationError("propagation_layers must be unique")
        object.__setattr__(self, "propagation_layers", tuple(sorted(layers, key=lambda layer: layer.value)))
        object.__setattr__(
            self,
            "active_access_observation_count",
            _nonnegative_int(
                self.active_access_observation_count,
                field="active_access_observation_count",
            ),
        )
        if self.new_publication_version_id is not None:
            object.__setattr__(
                self,
                "new_publication_version_id",
                _uuid(self.new_publication_version_id, field="new_publication_version_id"),
            )
        if self.new_pinned_memory_version_hash is not None:
            object.__setattr__(
                self,
                "new_pinned_memory_version_hash",
                _digest(self.new_pinned_memory_version_hash, field="new_pinned_memory_version_hash"),
            )
        if self.second_confirmation_hash is not None:
            object.__setattr__(
                self,
                "second_confirmation_hash",
                _digest(self.second_confirmation_hash, field="second_confirmation_hash"),
            )
        object.__setattr__(self, "external_copy_observed", bool(self.external_copy_observed))


@dataclass(frozen=True)
class PublicationLifecycleResult:
    disposition: PublicationLifecycleDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    required_deny_layers: tuple[PublicationPropagationLayer, ...] = ()
    external_cleanup_required: bool = False
    active_access_observation_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", PublicationLifecycleDisposition(self.disposition))
        reasons = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise PublicationLifecyclePropagationError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.scope_hash is not None:
            object.__setattr__(self, "scope_hash", _digest(self.scope_hash, field="scope_hash"))
        layers = tuple(PublicationPropagationLayer(layer) for layer in self.required_deny_layers)
        object.__setattr__(self, "required_deny_layers", tuple(sorted(set(layers), key=lambda layer: layer.value)))
        object.__setattr__(self, "external_cleanup_required", bool(self.external_cleanup_required))
        if self.active_access_observation_count is not None:
            object.__setattr__(
                self,
                "active_access_observation_count",
                _nonnegative_int(
                    self.active_access_observation_count,
                    field="active_access_observation_count",
                ),
            )

    @property
    def publication_mutated(self) -> bool:
        return False

    @property
    def grant_revoked(self) -> bool:
        return False

    @property
    def visitor_session_closed(self) -> bool:
        return False

    @property
    def gateway_access_denied(self) -> bool:
        return False

    @property
    def index_or_cache_cleared(self) -> bool:
        return False

    @property
    def external_cleanup_performed(self) -> bool:
        return False

    @property
    def propagation_receipt_persisted(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "externalCleanupPerformed": self.external_cleanup_performed,
            "externalCleanupRequired": self.external_cleanup_required,
            "gatewayAccessDenied": self.gateway_access_denied,
            "grantRevoked": self.grant_revoked,
            "indexOrCacheCleared": self.index_or_cache_cleared,
            "propagationReceiptPersisted": self.propagation_receipt_persisted,
            "publicationMutated": self.publication_mutated,
            "reasonCodes": list(self.reason_codes),
            "releaseVisible": False,
            "requiredDenyLayers": [layer.value for layer in self.required_deny_layers],
            "schemaVersion": PUBLICATION_LIFECYCLE_PROPAGATION_G0_SCHEMA_VERSION,
            "status": self.disposition.value,
            "visitorSessionClosed": self.visitor_session_closed,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.active_access_observation_count is not None:
            summary["activeAccessObservationCount"] = self.active_access_observation_count
        return summary


def _scope_hash(
    *,
    context: PublicationAuthorizationContext,
    command: PublicationLifecycleCommand,
) -> str:
    return _hash(
        {
            "action": command.action.value,
            "authorityEpoch": command.authority_epoch,
            "commandId": command.command_id,
            "currentState": command.current_state.value,
            "publicationId": command.publication_id,
            "publicationVersionId": command.publication_version_id,
            "trigger": command.trigger.value,
            "vaultId": context.vault_id,
        }
    )


def _result(
    disposition: PublicationLifecycleDisposition,
    reason: str,
    *,
    scope_hash: str | None = None,
    required_deny_layers: tuple[PublicationPropagationLayer, ...] = (),
    external_cleanup_required: bool = False,
    active_access_observation_count: int | None = None,
) -> PublicationLifecycleResult:
    return PublicationLifecycleResult(
        disposition=disposition,
        reason_codes=(reason,),
        scope_hash=scope_hash,
        required_deny_layers=required_deny_layers,
        external_cleanup_required=external_cleanup_required,
        active_access_observation_count=active_access_observation_count,
    )


def evaluate_publication_lifecycle_propagation(
    *,
    context: PublicationAuthorizationContext | object,
    principal: PublicationAuthorizationPrincipal | object,
    command: PublicationLifecycleCommand | object,
    enabled: bool = False,
) -> PublicationLifecycleResult:
    """Evaluate a future lifecycle action without touching a live publication."""

    if enabled is not True:
        return _result(
            PublicationLifecycleDisposition.SHADOW_DISABLED,
            "publicationLifecyclePropagationShadowDisabled",
        )
    if not isinstance(context, PublicationAuthorizationContext) or not isinstance(
        principal, PublicationAuthorizationPrincipal
    ) or not isinstance(command, PublicationLifecycleCommand):
        return _result(
            PublicationLifecycleDisposition.INVALID_CONTEXT,
            "invalidPublicationLifecyclePropagationContext",
        )
    if (
        principal.kind is not PublicationPrincipalKind.OWNER
        or principal.vault_id != context.vault_id
        or principal.subject_hash != context.owner_subject_hash
        or command.vault_id != context.vault_id
        or command.owner_subject_hash != context.owner_subject_hash
        or command.authority_epoch != context.authority_epoch
    ):
        return _result(
            PublicationLifecycleDisposition.OWNER_SCOPE_DENIED,
            "publicationLifecycleOwnerScopeMismatch",
        )

    scope_hash = _scope_hash(context=context, command=command)
    if command.transition_sequence <= command.previous_transition_sequence:
        return _result(
            PublicationLifecycleDisposition.DUPLICATE_OR_OUT_OF_ORDER,
            "publicationLifecycleTransitionSequenceStale",
            scope_hash=scope_hash,
        )
    if command.current_state is PublicationLifecycleState.WITHDRAWN and command.action is PublicationLifecycleAction.UPDATE:
        return _result(
            PublicationLifecycleDisposition.WITHDRAWN_REPUBLISH_DENIED,
            "withdrawnPublicationRequiresNewLifecycle",
            scope_hash=scope_hash,
        )
    if command.trigger in _PRIVATE_TRIGGERS and command.action is PublicationLifecycleAction.UPDATE:
        return _result(
            PublicationLifecycleDisposition.PRIVATE_TRIGGER_SUSPEND_REQUIRED,
            "privateMemoryOrRightsTriggerRequiresSuspendOrWithdraw",
            scope_hash=scope_hash,
        )
    if command.action is PublicationLifecycleAction.UPDATE:
        if (
            command.new_publication_version_id is None
            or command.new_publication_version_id == command.publication_version_id
            or command.new_pinned_memory_version_hash is None
        ):
            return _result(
                PublicationLifecycleDisposition.UPDATE_REQUIRES_NEW_VERSION,
                "publicationUpdateRequiresNewVersionAndPinnedMemoryHash",
                scope_hash=scope_hash,
            )
        if command.second_confirmation_hash is None:
            return _result(
                PublicationLifecycleDisposition.UPDATE_CONFIRMATION_REQUIRED,
                "publicationUpdateRequiresSecondConfirmation",
                scope_hash=scope_hash,
            )
        return _result(
            PublicationLifecycleDisposition.POLICY_DISABLED,
            "publicationUpdateWriterAndVersionPublisherPolicyDisabled",
            scope_hash=scope_hash,
        )

    layers = frozenset(command.propagation_layers)
    if not _REQUIRED_DENY_LAYERS.issubset(layers):
        return _result(
            PublicationLifecycleDisposition.ACCESS_DENY_PLAN_REQUIRED,
            "publicationSuspendWithdrawRequiresGatewayGrantSessionIndexCacheDenyPlan",
            scope_hash=scope_hash,
            required_deny_layers=tuple(sorted(_REQUIRED_DENY_LAYERS, key=lambda layer: layer.value)),
            external_cleanup_required=command.external_copy_observed,
            active_access_observation_count=command.active_access_observation_count,
        )
    if command.external_copy_observed or layers.intersection(_EXTERNAL_LAYERS):
        return _result(
            PublicationLifecycleDisposition.EXTERNAL_CLEANUP_GATES_REQUIRED,
            "publicationExternalCleanupRequiresProviderAndReceiptGates",
            scope_hash=scope_hash,
            required_deny_layers=tuple(sorted(_REQUIRED_DENY_LAYERS, key=lambda layer: layer.value)),
            external_cleanup_required=True,
            active_access_observation_count=command.active_access_observation_count,
        )
    return _result(
        PublicationLifecycleDisposition.POLICY_DISABLED,
        "publicationSuspendWithdrawPropagationPolicyDisabled",
        scope_hash=scope_hash,
        required_deny_layers=tuple(sorted(_REQUIRED_DENY_LAYERS, key=lambda layer: layer.value)),
        active_access_observation_count=command.active_access_observation_count,
    )


__all__ = [
    "PUBLICATION_LIFECYCLE_PROPAGATION_G0_SCHEMA_VERSION",
    "PublicationLifecycleAction",
    "PublicationLifecycleCommand",
    "PublicationLifecycleDisposition",
    "PublicationLifecyclePropagationError",
    "PublicationLifecycleResult",
    "PublicationLifecycleState",
    "PublicationLifecycleTrigger",
    "PublicationPropagationLayer",
    "evaluate_publication_lifecycle_propagation",
]
