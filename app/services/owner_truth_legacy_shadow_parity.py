"""Value-free legacy/Projection parity observation for Owner Truth.

This service deliberately stops before legacy backfill or authority cutover.
It first proves the caller owns an active V4 Vault through the Projection
reader, then inventories that caller's legacy rows and emits only hashes,
counts and explicit no-cutover reasons.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.domain.owner_truth.legacy_migration import (
    LegacyShadowParityReport,
    OwnerTruthLegacyMigrationError,
    build_legacy_shadow_parity_report,
)
from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionAccessDenied,
    OwnerTruthMemoryProjectionError,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_cutover_admission_shadow import (
    OwnerTruthCutoverAdmissionContext,
    OwnerTruthCutoverAdmissionShadow,
    observe_owner_truth_cutover_admission,
)
from app.services.owner_truth_legacy_migration import (
    OwnerTruthLegacyMigrationAccessDenied,
    OwnerTruthLegacyMigrationConflict,
    OwnerTruthLegacyMigrationInventoryService,
    OwnerTruthLegacyMigrationUnavailable,
)
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


class OwnerTruthLegacyShadowParityStore(Protocol):
    def owner_truth_legacy_migration_repository(self) -> Any:
        ...

    def owner_truth_memory_projection_repository(self) -> Any:
        ...


@dataclass(frozen=True)
class OwnerTruthLegacyShadowParityObservation:
    inventory_outcome: str
    inventory_run_id: str
    report: LegacyShadowParityReport
    cutover_admission: OwnerTruthCutoverAdmissionShadow

    def public_summary(self) -> dict[str, object]:
        summary = self.report.summary()
        summary["cutoverAdmission"] = self.cutover_admission.value_free_summary()
        summary["inventoryOutcome"] = self.inventory_outcome
        return summary


class OwnerTruthLegacyShadowParityService:
    """Default-off observer; it never creates a V4 migration target."""

    def __init__(self, store: OwnerTruthLegacyShadowParityStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def observe(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthLegacyShadowParityObservation:
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthLegacyMigrationAccessDenied(
                "only the Vault Owner may observe legacy shadow parity"
            )
        if not self._enabled:
            raise OwnerTruthLegacyMigrationUnavailable(
                "legacy shadow parity is disabled"
            )

        # Validate the V4 Vault before the legacy collector can write an
        # inventory checkpoint for it. A foreign/unknown Vault never gets a
        # legacy run associated with its identifier.
        try:
            projection_snapshot = OwnerTruthMemoryProjectionService(self._store).read(
                context=context
            )
        except OwnerTruthMemoryProjectionAccessDenied as error:
            raise OwnerTruthLegacyMigrationAccessDenied(
                "Vault is not active for this Owner"
            ) from error
        except OwnerTruthMemoryProjectionError as error:
            raise OwnerTruthLegacyMigrationConflict(
                "projection state cannot be observed for legacy parity"
            ) from error

        run = OwnerTruthLegacyMigrationInventoryService(
            self._store,
            enabled=True,
        ).inventory(context=context)
        report = build_legacy_shadow_parity_report(
            inventory_run_id=run.run_id,
            inventory=run.inventory,
            owner_subject_id=context.owner_subject_id,
            projection_snapshot=projection_snapshot,
        )
        cutover_admission = observe_owner_truth_cutover_admission(
            report,
            context=OwnerTruthCutoverAdmissionContext(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=report.projection_authority_epoch,
            ),
            enabled=True,
        )
        return OwnerTruthLegacyShadowParityObservation(
            inventory_outcome=run.outcome,
            inventory_run_id=run.run_id,
            report=report,
            cutover_admission=cutover_admission,
        )


def legacy_shadow_parity_summary(
    observation: OwnerTruthLegacyShadowParityObservation,
) -> dict[str, object]:
    if not isinstance(observation, OwnerTruthLegacyShadowParityObservation):
        raise OwnerTruthLegacyMigrationError("legacy shadow parity observation is required")
    return observation.public_summary()


__all__ = [
    "OwnerTruthLegacyShadowParityObservation",
    "OwnerTruthLegacyShadowParityService",
    "legacy_shadow_parity_summary",
]
