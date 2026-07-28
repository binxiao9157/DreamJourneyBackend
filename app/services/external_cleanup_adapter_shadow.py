"""Default-off inventory for external data-rights cleanup adapters.

The application already records local account cleanup separately from external
object, Provider and retention boundaries. This G0 module standardizes the
external vocabulary without querying, deleting, closing, retaining, persisting
or dispatching anything. It is intentionally incapable of claiming that an
external resource has been cleaned up.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Iterable


EXTERNAL_CLEANUP_ADAPTER_SHADOW_SCHEMA_VERSION = "external-cleanup-adapter-shadow-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class ExternalCleanupAdapterShadowError(ValueError):
    """Raised when a value-minimized adapter inventory is malformed."""


class ExternalCleanupLayer(str, Enum):
    OBJECT_STORAGE = "objectStorage"
    PROVIDER_VOICE = "providerVoice"
    PROVIDER_DIGITAL_HUMAN = "providerDigitalHuman"
    BACKUP_RETENTION = "backupRetention"
    EVIDENCE_RETENTION = "evidenceRetention"


class ExternalCleanupAdapterMode(str, Enum):
    NO_EXTERNAL_TARGET = "noExternalTarget"
    UNSUPPORTED = "unsupported"
    QUERY_RECONCILE_ONLY = "queryReconcileOnly"
    RETENTION_AUDIT_ONLY = "retentionAuditOnly"
    EXTERNAL_RECEIPT_REQUIRED = "externalReceiptRequired"


class ExternalCleanupDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_INVENTORY = "invalid_inventory"
    EXTERNAL_GATES_REQUIRED = "external_gates_required"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ExternalCleanupAdapterShadowError(f"{field} must be an opaque identifier")
    return normalized


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ExternalCleanupAdapterShadowError(f"{field} must be a SHA-256 digest")
    return normalized


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_ALLOWED_MODES = {
    ExternalCleanupLayer.OBJECT_STORAGE: frozenset(
        {
            ExternalCleanupAdapterMode.NO_EXTERNAL_TARGET,
            ExternalCleanupAdapterMode.UNSUPPORTED,
            ExternalCleanupAdapterMode.QUERY_RECONCILE_ONLY,
            ExternalCleanupAdapterMode.EXTERNAL_RECEIPT_REQUIRED,
        }
    ),
    ExternalCleanupLayer.PROVIDER_VOICE: frozenset(
        {
            ExternalCleanupAdapterMode.UNSUPPORTED,
            ExternalCleanupAdapterMode.QUERY_RECONCILE_ONLY,
            ExternalCleanupAdapterMode.EXTERNAL_RECEIPT_REQUIRED,
        }
    ),
    ExternalCleanupLayer.PROVIDER_DIGITAL_HUMAN: frozenset(
        {
            ExternalCleanupAdapterMode.UNSUPPORTED,
            ExternalCleanupAdapterMode.QUERY_RECONCILE_ONLY,
            ExternalCleanupAdapterMode.EXTERNAL_RECEIPT_REQUIRED,
        }
    ),
    ExternalCleanupLayer.BACKUP_RETENTION: frozenset(
        {
            ExternalCleanupAdapterMode.QUERY_RECONCILE_ONLY,
            ExternalCleanupAdapterMode.RETENTION_AUDIT_ONLY,
            ExternalCleanupAdapterMode.EXTERNAL_RECEIPT_REQUIRED,
        }
    ),
    ExternalCleanupLayer.EVIDENCE_RETENTION: frozenset(
        {
            ExternalCleanupAdapterMode.QUERY_RECONCILE_ONLY,
            ExternalCleanupAdapterMode.RETENTION_AUDIT_ONLY,
            ExternalCleanupAdapterMode.EXTERNAL_RECEIPT_REQUIRED,
        }
    ),
}


@dataclass(frozen=True)
class ExternalCleanupAdapterDescriptor:
    """One configured capability boundary with no resource identifier or value."""

    layer: ExternalCleanupLayer
    mode: ExternalCleanupAdapterMode
    policy_version: str
    configuration_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "layer", ExternalCleanupLayer(self.layer))
        object.__setattr__(self, "mode", ExternalCleanupAdapterMode(self.mode))
        object.__setattr__(
            self,
            "policy_version",
            _identifier(self.policy_version, field="policy_version"),
        )
        object.__setattr__(
            self,
            "configuration_hash",
            _digest(self.configuration_hash, field="configuration_hash"),
        )
        if self.mode not in _ALLOWED_MODES[self.layer]:
            raise ExternalCleanupAdapterShadowError(
                f"{self.mode.value} is not valid for {self.layer.value}"
            )


@dataclass(frozen=True)
class ExternalCleanupLayerStatus:
    layer: ExternalCleanupLayer
    status: str
    reason_codes: tuple[str, ...]
    required_external_gates: tuple[str, ...]

    def value_free_summary(self) -> dict[str, object]:
        return {
            "layer": self.layer.value,
            "reasonCodes": list(self.reason_codes),
            "requiredExternalGates": list(self.required_external_gates),
            "status": self.status,
        }


@dataclass(frozen=True)
class ExternalCleanupAdapterShadowResult:
    enabled: bool
    disposition: ExternalCleanupDisposition
    reason_codes: tuple[str, ...]
    statuses: tuple[ExternalCleanupLayerStatus, ...] = ()
    inventory_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ExternalCleanupAdapterShadowError("enabled must be boolean")
        object.__setattr__(self, "disposition", ExternalCleanupDisposition(self.disposition))
        reasons = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise ExternalCleanupAdapterShadowError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(
            self,
            "statuses",
            tuple(sorted(self.statuses, key=lambda item: item.layer.value)),
        )
        if self.inventory_hash is not None:
            object.__setattr__(
                self,
                "inventory_hash",
                _digest(self.inventory_hash, field="inventory_hash"),
            )

    @property
    def external_cleanup_performed(self) -> bool:
        return False

    @property
    def provider_call_performed(self) -> bool:
        return False

    @property
    def receipt_persisted(self) -> bool:
        return False

    @property
    def retention_changed(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "externalCleanupPerformed": self.external_cleanup_performed,
            "providerCallPerformed": self.provider_call_performed,
            "receiptPersisted": self.receipt_persisted,
            "reasonCodes": list(self.reason_codes),
            "releaseVisible": False,
            "retentionChanged": self.retention_changed,
            "schemaVersion": EXTERNAL_CLEANUP_ADAPTER_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
            "surfaces": [item.value_free_summary() for item in self.statuses],
        }
        if self.inventory_hash is not None:
            summary["inventoryHash"] = self.inventory_hash
        return summary


def current_external_cleanup_adapter_inventory() -> tuple[ExternalCleanupAdapterDescriptor, ...]:
    """Return the current non-production capability inventory.

    The archive path is mock/local-only, while current Voice/DH providers have
    no approved exit adapter. Backup retention remains audit-only. These are
    explicit product disclosures, not successful external cleanup states.
    """

    entries = (
        (
            ExternalCleanupLayer.OBJECT_STORAGE,
            ExternalCleanupAdapterMode.NO_EXTERNAL_TARGET,
            "currentMockObjectStorageV1",
        ),
        (
            ExternalCleanupLayer.PROVIDER_VOICE,
            ExternalCleanupAdapterMode.UNSUPPORTED,
            "currentVoiceProviderExitV1",
        ),
        (
            ExternalCleanupLayer.PROVIDER_DIGITAL_HUMAN,
            ExternalCleanupAdapterMode.UNSUPPORTED,
            "currentDigitalHumanProviderExitV1",
        ),
        (
            ExternalCleanupLayer.BACKUP_RETENTION,
            ExternalCleanupAdapterMode.RETENTION_AUDIT_ONLY,
            "currentBackupRetentionAuditV1",
        ),
        (
            ExternalCleanupLayer.EVIDENCE_RETENTION,
            ExternalCleanupAdapterMode.QUERY_RECONCILE_ONLY,
            "currentEvidenceRetentionReconcileV1",
        ),
    )
    return tuple(
        ExternalCleanupAdapterDescriptor(
            layer=layer,
            mode=mode,
            policy_version=policy_version,
            configuration_hash=_hash(
                {
                    "layer": layer.value,
                    "mode": mode.value,
                    "policyVersion": policy_version,
                }
            ),
        )
        for layer, mode, policy_version in entries
    )


def _status_for(descriptor: ExternalCleanupAdapterDescriptor) -> ExternalCleanupLayerStatus:
    mode = descriptor.mode
    if mode is ExternalCleanupAdapterMode.NO_EXTERNAL_TARGET:
        return ExternalCleanupLayerStatus(
            layer=descriptor.layer,
            status="notApplicable",
            reason_codes=("noExternalTargetForCurrentMockOrLocalLane",),
            required_external_gates=("G2", "G3", "G4"),
        )
    if mode is ExternalCleanupAdapterMode.UNSUPPORTED:
        return ExternalCleanupLayerStatus(
            layer=descriptor.layer,
            status="unsupported",
            reason_codes=("externalCleanupAdapterNotConfigured",),
            required_external_gates=("G2", "G3", "G4"),
        )
    if mode is ExternalCleanupAdapterMode.QUERY_RECONCILE_ONLY:
        return ExternalCleanupLayerStatus(
            layer=descriptor.layer,
            status="queryRequired",
            reason_codes=("externalStateMustBeQueriedAndReconciled",),
            required_external_gates=("G2", "G3", "G4"),
        )
    if mode is ExternalCleanupAdapterMode.RETENTION_AUDIT_ONLY:
        return ExternalCleanupLayerStatus(
            layer=descriptor.layer,
            status="auditOnly",
            reason_codes=("retentionRequiresOperatorReceipt",),
            required_external_gates=("G2", "G3", "G4"),
        )
    return ExternalCleanupLayerStatus(
        layer=descriptor.layer,
        status="externalReceiptRequired",
        reason_codes=("externalReceiptRequiredBeforeCompletionClaim",),
        required_external_gates=("G2", "G3", "G4"),
    )


def plan_external_cleanup_adapter_shadow(
    inventory: Iterable[ExternalCleanupAdapterDescriptor] | object,
    *,
    enabled: bool = False,
) -> ExternalCleanupAdapterShadowResult:
    """Classify cleanup adapter boundaries without touching external systems."""

    if not enabled:
        return ExternalCleanupAdapterShadowResult(
            enabled=False,
            disposition=ExternalCleanupDisposition.SHADOW_DISABLED,
            reason_codes=("externalCleanupAdapterShadowDisabled",),
        )
    try:
        descriptors = tuple(inventory)  # type: ignore[arg-type]
    except TypeError:
        descriptors = ()
    if (
        len(descriptors) != len(ExternalCleanupLayer)
        or not all(isinstance(item, ExternalCleanupAdapterDescriptor) for item in descriptors)
        or {item.layer for item in descriptors if isinstance(item, ExternalCleanupAdapterDescriptor)}
        != set(ExternalCleanupLayer)
    ):
        return ExternalCleanupAdapterShadowResult(
            enabled=True,
            disposition=ExternalCleanupDisposition.INVALID_INVENTORY,
            reason_codes=("completeUniqueAdapterInventoryRequired",),
        )
    typed_descriptors = tuple(descriptors)
    inventory_hash = _hash(
        [
            {
                "configurationHash": item.configuration_hash,
                "layer": item.layer.value,
                "mode": item.mode.value,
                "policyVersion": item.policy_version,
            }
            for item in sorted(typed_descriptors, key=lambda item: item.layer.value)
        ]
    )
    return ExternalCleanupAdapterShadowResult(
        enabled=True,
        disposition=ExternalCleanupDisposition.EXTERNAL_GATES_REQUIRED,
        reason_codes=(
            "completionRequiresIndependentExternalReceipts",
            "currentInventoryDoesNotAuthorizeExternalCleanup",
            "releasePolicyDefaultOff",
        ),
        statuses=tuple(_status_for(item) for item in typed_descriptors),
        inventory_hash=inventory_hash,
    )


__all__ = [
    "EXTERNAL_CLEANUP_ADAPTER_SHADOW_SCHEMA_VERSION",
    "ExternalCleanupAdapterDescriptor",
    "ExternalCleanupAdapterMode",
    "ExternalCleanupAdapterShadowError",
    "ExternalCleanupAdapterShadowResult",
    "ExternalCleanupDisposition",
    "ExternalCleanupLayer",
    "ExternalCleanupLayerStatus",
    "current_external_cleanup_adapter_inventory",
    "plan_external_cleanup_adapter_shadow",
]
