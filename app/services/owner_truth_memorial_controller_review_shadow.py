"""Default-off G0 assessment for a future Memorial ``controller_review`` state.

The product specification requires a Memorial Vault to enter controller review
when its primary controller becomes unreachable, dies, is revoked or cannot
recover an account. This module only evaluates that future state boundary. It
does not change a Vault state, activate an appointment, grant access, run a
Provider effect or execute a preservation operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID


OWNER_TRUTH_MEMORIAL_CONTROLLER_REVIEW_SHADOW_SCHEMA_VERSION = (
    "owner-truth-memorial-controller-review-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_REVIEW_SCOPE_CATEGORIES = (
    "minimum_operations",
    "necessary_preservation",
    "rights_request",
)
_REVIEW_BLOCKED_CATEGORIES = ("provider_effect", "publication")


class MemorialControllerReviewContractError(ValueError):
    """Raised when a server-resolved controller-review envelope is malformed."""


class MemorialControllerReviewCondition(str, Enum):
    ACTIVE = "active"
    CONTROLLER_UNREACHABLE = "controller_unreachable"
    CONTROLLER_DECEASED = "controller_deceased"
    CONTROLLER_REVOKED = "controller_revoked"
    ACCOUNT_RECOVERY_FAILED = "account_recovery_failed"
    UNKNOWN = "unknown"


class MemorialControllerReviewDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN = "represented_login_principal_forbidden"
    REVIEW_NOT_REQUIRED = "review_not_required"
    CONTROLLER_REVIEW_REQUIRED = "controller_review_required"


_CONDITION_REASON_CODES = {
    MemorialControllerReviewCondition.CONTROLLER_UNREACHABLE: "primaryControllerUnreachable",
    MemorialControllerReviewCondition.CONTROLLER_DECEASED: "primaryControllerDeceased",
    MemorialControllerReviewCondition.CONTROLLER_REVOKED: "primaryControllerRevoked",
    MemorialControllerReviewCondition.ACCOUNT_RECOVERY_FAILED: (
        "primaryControllerAccountRecoveryFailed"
    ),
    MemorialControllerReviewCondition.UNKNOWN: "primaryControllerConditionUnknownFailsClosed",
}


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise MemorialControllerReviewContractError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise MemorialControllerReviewContractError(f"{field} must be a UUID") from exc


def _non_negative(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MemorialControllerReviewContractError(f"{field} must be a non-negative integer")
    return value


def _digest(value: object) -> str:
    try:
        canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MemorialControllerReviewContractError(
            "controller-review material must be JSON serializable"
        ) from exc
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemorialControllerReviewContext:
    """Read-only, server-resolved inputs for a future Vault state transition."""

    vault_id: str
    represented_persona_id: str
    primary_controller_condition: MemorialControllerReviewCondition
    authority_epoch: int
    represented_login_subject_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "represented_persona_id",
            _uuid(self.represented_persona_id, field="represented_persona_id"),
        )
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative(self.authority_epoch, field="authority_epoch"),
        )
        if self.represented_login_subject_id is not None:
            object.__setattr__(
                self,
                "represented_login_subject_id",
                _identifier(
                    self.represented_login_subject_id,
                    field="represented_login_subject_id",
                ),
            )
        try:
            condition = MemorialControllerReviewCondition(self.primary_controller_condition)
        except ValueError as exc:
            raise MemorialControllerReviewContractError(
                "primary_controller_condition is unsupported"
            ) from exc
        object.__setattr__(self, "primary_controller_condition", condition)

    def scope_hash(self) -> str:
        return _digest(
            {
                "personaId": self.represented_persona_id,
                "vaultId": self.vault_id,
            }
        )


@dataclass(frozen=True)
class MemorialControllerReviewShadow:
    """Value-free assessment. G0 cannot turn review scope into an effect."""

    enabled: bool
    disposition: MemorialControllerReviewDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    captured_authority_epoch: int | None = None
    controller_review_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise MemorialControllerReviewContractError("enabled must be a boolean")
        if not isinstance(self.disposition, MemorialControllerReviewDisposition):
            raise MemorialControllerReviewContractError("disposition is required")
        reasons = tuple(sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes}))
        if not reasons:
            raise MemorialControllerReviewContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.scope_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", self.scope_hash):
            raise MemorialControllerReviewContractError("scope_hash must be a SHA-256 digest")
        if self.captured_authority_epoch is not None:
            object.__setattr__(
                self,
                "captured_authority_epoch",
                _non_negative(self.captured_authority_epoch, field="captured_authority_epoch"),
            )
        if not isinstance(self.controller_review_required, bool):
            raise MemorialControllerReviewContractError(
                "controller_review_required must be a boolean"
            )
        if self.disposition is MemorialControllerReviewDisposition.CONTROLLER_REVIEW_REQUIRED:
            if not self.controller_review_required or self.scope_hash is None:
                raise MemorialControllerReviewContractError(
                    "controller review requirement needs a value-free scope hash"
                )
        elif self.controller_review_required:
            raise MemorialControllerReviewContractError(
                "only controller-review-required may require controller review"
            )

    @property
    def records_written(self) -> bool:
        return False

    @property
    def vault_state_written(self) -> bool:
        return False

    @property
    def authority_epoch_changed(self) -> bool:
        return False

    @property
    def controller_appointment_activated(self) -> bool:
        return False

    @property
    def publication_allowed(self) -> bool:
        return False

    @property
    def provider_effect_allowed(self) -> bool:
        return False

    @property
    def provider_or_runtime_mutated(self) -> bool:
        return False

    @property
    def review_scope_categories(self) -> tuple[str, ...]:
        return _REVIEW_SCOPE_CATEGORIES if self.controller_review_required else ()

    @property
    def blocked_categories(self) -> tuple[str, ...]:
        return _REVIEW_BLOCKED_CATEGORIES if self.controller_review_required else ()

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "allowedFutureReviewCategories": list(self.review_scope_categories),
            "authorityEpochChanged": self.authority_epoch_changed,
            "blockedFutureCategories": list(self.blocked_categories),
            "controllerAppointmentActivated": self.controller_appointment_activated,
            "controllerReviewRequired": self.controller_review_required,
            "enabled": self.enabled,
            "providerEffectAllowed": self.provider_effect_allowed,
            "providerOrRuntimeMutated": self.provider_or_runtime_mutated,
            "publicationAllowed": self.publication_allowed,
            "reasonCodes": list(self.reason_codes),
            "recordsWritten": self.records_written,
            "representedPersonaLoginPrincipal": False,
            "requiredExternalGates": ["G2", "G4"],
            "schemaVersion": OWNER_TRUTH_MEMORIAL_CONTROLLER_REVIEW_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
            "vaultStateWritten": self.vault_state_written,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.captured_authority_epoch is not None:
            summary["capturedAuthorityEpoch"] = self.captured_authority_epoch
        return summary


def observe_memorial_controller_review(
    context: MemorialControllerReviewContext | object,
    *,
    enabled: bool = False,
) -> MemorialControllerReviewShadow:
    """Assess a future controller-review transition without changing authority."""

    if not enabled:
        return MemorialControllerReviewShadow(
            enabled=False,
            disposition=MemorialControllerReviewDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(context, MemorialControllerReviewContext):
        return MemorialControllerReviewShadow(
            enabled=True,
            disposition=MemorialControllerReviewDisposition.INVALID_CONTEXT,
            reason_codes=("invalidMemorialControllerReviewContext",),
        )
    if context.represented_login_subject_id is not None:
        return MemorialControllerReviewShadow(
            enabled=True,
            disposition=(
                MemorialControllerReviewDisposition.REPRESENTED_LOGIN_PRINCIPAL_FORBIDDEN
            ),
            reason_codes=("representedPersonaCannotBeLoginPrincipal",),
        )
    if context.primary_controller_condition is MemorialControllerReviewCondition.ACTIVE:
        return MemorialControllerReviewShadow(
            enabled=True,
            disposition=MemorialControllerReviewDisposition.REVIEW_NOT_REQUIRED,
            reason_codes=("activePrimaryControllerNoReviewRequired",),
            scope_hash=context.scope_hash(),
            captured_authority_epoch=context.authority_epoch,
        )
    return MemorialControllerReviewShadow(
        enabled=True,
        disposition=MemorialControllerReviewDisposition.CONTROLLER_REVIEW_REQUIRED,
        reason_codes=(
            _CONDITION_REASON_CODES[context.primary_controller_condition],
            "controllerReviewAllowsOnlyRightsRequestNecessaryPreservationAndMinimumOperations",
            "controllerReviewBlocksPublicationAndProviderEffect",
            "newAppointmentMustBeEffectiveBeforeAuthorityResumes",
            "shadowControllerReviewDoesNotWriteVaultState",
        ),
        scope_hash=context.scope_hash(),
        captured_authority_epoch=context.authority_epoch,
        controller_review_required=True,
    )


__all__ = [
    "OWNER_TRUTH_MEMORIAL_CONTROLLER_REVIEW_SHADOW_SCHEMA_VERSION",
    "MemorialControllerReviewCondition",
    "MemorialControllerReviewContractError",
    "MemorialControllerReviewContext",
    "MemorialControllerReviewDisposition",
    "MemorialControllerReviewShadow",
    "observe_memorial_controller_review",
]
