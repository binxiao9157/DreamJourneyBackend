"""Fail-closed G0 model for a future Owner Truth cohort cutover.

The legacy parity observer intentionally never promotes data, advances a Vault
authority epoch, or retires a legacy writer.  This companion model makes that
boundary explicit for future callers: parity output is useful evidence, but it
is not a production go record and cannot self-authorize either mutation.

This module has no store, route, effect, or provider dependency.  It is
default-off and emits only value-free hashes and reason codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re

from app.domain.owner_truth.legacy_migration import LegacyShadowParityReport


OWNER_TRUTH_CUTOVER_ADMISSION_SHADOW_SCHEMA_VERSION = "owner-truth-cutover-admission-shadow-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class OwnerTruthCutoverAdmissionContractError(ValueError):
    """Raised when a caller supplies an invalid synthetic cutover envelope."""


class OwnerTruthCutoverAdmissionDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    CONTEXT_MISMATCH = "context_mismatch"
    EXTERNAL_GO_REQUIRED = "external_go_required"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthCutoverAdmissionContractError(f"{field} must be an opaque identifier")
    return normalized


def _scope_hash(*, vault_id: str, owner_subject_id: str, authority_epoch: int) -> str:
    material = {
        "authorityEpoch": authority_epoch,
        "ownerSubjectId": owner_subject_id,
        "vaultId": vault_id,
    }
    return sha256(
        json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class OwnerTruthCutoverAdmissionContext:
    """The current, read-only Vault epoch used to bind a parity report."""

    vault_id: str
    owner_subject_id: str
    authority_epoch: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int):
            raise OwnerTruthCutoverAdmissionContractError("authority_epoch must be an integer")
        if self.authority_epoch < 0:
            raise OwnerTruthCutoverAdmissionContractError("authority_epoch must not be negative")


@dataclass(frozen=True)
class OwnerTruthCutoverAdmissionShadow:
    """A non-authorizing result that future cutover code must not treat as GO."""

    enabled: bool
    disposition: OwnerTruthCutoverAdmissionDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    parity_report_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise OwnerTruthCutoverAdmissionContractError("shadow enabled must be a boolean")
        if not isinstance(self.disposition, OwnerTruthCutoverAdmissionDisposition):
            raise OwnerTruthCutoverAdmissionContractError("shadow disposition is required")
        normalized_reasons = tuple(
            sorted({_identifier(reason, field="reason_code") for reason in self.reason_codes})
        )
        if not normalized_reasons:
            raise OwnerTruthCutoverAdmissionContractError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", normalized_reasons)
        for field in ("scope_hash", "parity_report_hash"):
            value = getattr(self, field)
            if value is not None and not re.fullmatch(r"[0-9a-f]{64}", value):
                raise OwnerTruthCutoverAdmissionContractError(f"{field} must be a SHA-256 digest")

    @property
    def cutover_allowed(self) -> bool:
        return False

    @property
    def authority_epoch_changed(self) -> bool:
        return False

    @property
    def legacy_writer_retired(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "authorityEpochChanged": self.authority_epoch_changed,
            "cutoverAllowed": self.cutover_allowed,
            "enabled": self.enabled,
            "legacyWriterRetired": self.legacy_writer_retired,
            "reasonCodes": list(self.reason_codes),
            "requiredExternalGates": ["G2", "G4"],
            "schemaVersion": OWNER_TRUTH_CUTOVER_ADMISSION_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "status": self.disposition.value,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        if self.parity_report_hash is not None:
            summary["parityReportHash"] = self.parity_report_hash
        return summary


def observe_owner_truth_cutover_admission(
    parity_report: LegacyShadowParityReport | object,
    *,
    context: OwnerTruthCutoverAdmissionContext | object,
    enabled: bool = False,
) -> OwnerTruthCutoverAdmissionShadow:
    """Model future cutover admission without performing or authorizing it.

    A genuine G2/G4 go record and a separate transactional command are both
    required before any production implementation can advance an epoch or
    retire a legacy writer.  This function deliberately has no such command.
    """

    if not enabled:
        return OwnerTruthCutoverAdmissionShadow(
            enabled=False,
            disposition=OwnerTruthCutoverAdmissionDisposition.SHADOW_DISABLED,
            reason_codes=("shadowDisabled",),
        )
    if not isinstance(parity_report, LegacyShadowParityReport) or not isinstance(
        context, OwnerTruthCutoverAdmissionContext
    ):
        return OwnerTruthCutoverAdmissionShadow(
            enabled=True,
            disposition=OwnerTruthCutoverAdmissionDisposition.INVALID_ENVELOPE,
            reason_codes=("invalidCutoverAdmissionEnvelope",),
        )

    scope_hash = _scope_hash(
        vault_id=context.vault_id,
        owner_subject_id=context.owner_subject_id,
        authority_epoch=context.authority_epoch,
    )
    if parity_report.vault_id != context.vault_id:
        return OwnerTruthCutoverAdmissionShadow(
            enabled=True,
            disposition=OwnerTruthCutoverAdmissionDisposition.CONTEXT_MISMATCH,
            reason_codes=("vaultMismatch", "separateProductionGoRecordRequired"),
            scope_hash=scope_hash,
            parity_report_hash=parity_report.report_hash,
        )
    if parity_report.projection_authority_epoch != context.authority_epoch:
        return OwnerTruthCutoverAdmissionShadow(
            enabled=True,
            disposition=OwnerTruthCutoverAdmissionDisposition.CONTEXT_MISMATCH,
            reason_codes=("authorityEpochMismatch", "separateProductionGoRecordRequired"),
            scope_hash=scope_hash,
            parity_report_hash=parity_report.report_hash,
        )

    reason_codes = {
        "authorityEpochCasRequiresIndependentCommand",
        "legacyWriterRetirementRequiresIndependentCommand",
        "separateProductionGoRecordRequired",
    }
    if parity_report.cutover_allowed:
        reason_codes.add("shadowParityFlagsCannotCommitCutover")
    else:
        reason_codes.add("legacyParityDoesNotAuthorizeCutover")
    if parity_report.authority_epoch_changed:
        reason_codes.add("shadowParityEpochMutationIgnored")
    if parity_report.legacy_writer_retired:
        reason_codes.add("shadowParityLegacyWriterMutationIgnored")
    return OwnerTruthCutoverAdmissionShadow(
        enabled=True,
        disposition=OwnerTruthCutoverAdmissionDisposition.EXTERNAL_GO_REQUIRED,
        reason_codes=tuple(reason_codes),
        scope_hash=scope_hash,
        parity_report_hash=parity_report.report_hash,
    )


__all__ = [
    "OWNER_TRUTH_CUTOVER_ADMISSION_SHADOW_SCHEMA_VERSION",
    "OwnerTruthCutoverAdmissionContext",
    "OwnerTruthCutoverAdmissionContractError",
    "OwnerTruthCutoverAdmissionDisposition",
    "OwnerTruthCutoverAdmissionShadow",
    "observe_owner_truth_cutover_admission",
]
