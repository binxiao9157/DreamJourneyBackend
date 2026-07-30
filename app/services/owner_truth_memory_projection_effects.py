"""Value-free async effect intents for Owner Truth compatibility projections.

An Owner-approved ``MemoryVersion`` is authoritative data.  The async effect
kernel only records an opaque request to rebuild a derived compatibility
projection; it must never receive the MemoryVersion payload, DecisionReceipt,
or review rationale.  The worker remains disabled until a later Work Item
admits a read-only consumer.
"""

from __future__ import annotations

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectTarget
from app.domain.owner_truth.memory_activation import (
    OwnerTruthMemoryActivationError,
    OwnerTruthMemoryActivationResult,
)
from app.domain.owner_truth.memory_correction import (
    OwnerTruthMemoryCorrectionActivationResult,
)
from app.domain.owner_truth.projection_rights import OwnerTruthProjectionRightsSnapshot
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


MEMORY_PROJECTION_REBUILD_OPERATION_TYPE = "ownerTruth.memoryVersion.activated"
MEMORY_PROJECTION_REBUILD_EVENT_TYPE = "ownerTruth.memoryProjection.rebuildRequested"
MEMORY_PROJECTION_REBUILD_JOB_TYPE = "ownerTruth.memoryProjection.rebuild"
MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE = "ownerTruth.projectionRights.recorded"
MEMORY_PROJECTION_RIGHTS_REBUILD_EVENT_TYPE = "ownerTruth.memoryProjection.rightsRebuildRequested"


def build_memory_projection_rebuild_effect_intent_for_rights_revision(
    *,
    context: OwnerTruthCommandContext,
    rights: OwnerTruthProjectionRightsSnapshot,
) -> AsyncEffectIntent:
    """Return a value-free rebuild request for one recorded rights revision.

    A rights event changes the projection fence even when no MemoryVersion has
    changed.  The effect identifies only the immutable revision and event hash;
    it does not transport consent text, source values, or memory content.  The
    existing projection worker rechecks the live rights state before rebuilding.
    """

    if not isinstance(rights, OwnerTruthProjectionRightsSnapshot):
        raise TypeError("projection rights snapshot is required")
    if (
        rights.vault_id != context.vault_id
        or rights.owner_subject_id != context.owner_subject_id
        or rights.revision < 1
    ):
        raise ValueError("recorded projection rights metadata is required for a rebuild")
    return AsyncEffectIntent(
        operation_type=MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE,
        target=AsyncEffectTarget(
            owner_subject_id=context.owner_subject_id,
            vault_id=context.vault_id,
            resource_type="projectionRightsRevision",
            resource_id=f"projectionRightsRevision:{rights.revision}",
            resource_version=rights.revision,
            purpose="compatibilityProjectionRights",
            authority_epoch=rights.authority_epoch,
        ),
        payload_hash=rights.event_hash,
        event_type=MEMORY_PROJECTION_RIGHTS_REBUILD_EVENT_TYPE,
        job_type=MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    )


def build_memory_projection_rebuild_effect_intent(
    *,
    context: OwnerTruthCommandContext,
    activation: OwnerTruthMemoryActivationResult,
) -> AsyncEffectIntent:
    """Return the idempotent rebuild request for one active MemoryVersion.

    The target identity is the immutable MemoryVersion plus its authority
    epoch.  ``payload_hash`` intentionally reuses only the content hash: it is
    a tamper-detecting opaque reference, not a transport for memory content.
    """

    if activation.outcome not in {"created", "deduplicated"}:
        raise OwnerTruthMemoryActivationError(
            "only an activated MemoryVersion can request a compatibility projection rebuild"
        )
    if (
        not activation.memory_version_id
        or activation.memory_version is None
        or activation.authority_epoch is None
        or not activation.content_hash
    ):
        raise OwnerTruthMemoryActivationError(
            "activated MemoryVersion metadata is required for a compatibility projection rebuild"
        )

    return build_memory_projection_rebuild_effect_intent_for_version(
        context=context,
        memory_version_id=activation.memory_version_id,
        memory_version=activation.memory_version,
        authority_epoch=activation.authority_epoch,
        content_hash=activation.content_hash,
    )


def build_memory_projection_rebuild_effect_intent_for_correction(
    *,
    context: OwnerTruthCommandContext,
    activation: OwnerTruthMemoryCorrectionActivationResult,
) -> AsyncEffectIntent:
    """Build the same rebuild effect for a same-record correction successor."""

    if activation.outcome not in {"created", "deduplicated"}:
        raise OwnerTruthMemoryActivationError(
            "only a superseding MemoryVersion can request a compatibility projection rebuild"
        )
    return build_memory_projection_rebuild_effect_intent_for_version(
        context=context,
        memory_version_id=activation.replacement_memory_version_id,
        memory_version=activation.replacement_memory_version,
        authority_epoch=activation.authority_epoch,
        content_hash=activation.content_hash,
    )


def build_memory_projection_rebuild_effect_intent_for_version(
    *,
    context: OwnerTruthCommandContext,
    memory_version_id: str,
    memory_version: int,
    authority_epoch: int,
    content_hash: str,
) -> AsyncEffectIntent:
    """Build a value-free projection rebuild intent for any active version."""

    if (
        not memory_version_id
        or memory_version < 1
        or authority_epoch < 0
        or not content_hash
    ):
        raise OwnerTruthMemoryActivationError(
            "active MemoryVersion metadata is required for a compatibility projection rebuild"
        )
    return AsyncEffectIntent(
        operation_type=MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
        target=AsyncEffectTarget(
            owner_subject_id=context.owner_subject_id,
            vault_id=context.vault_id,
            resource_type="memoryVersion",
            resource_id=memory_version_id,
            resource_version=memory_version,
            purpose="compatibilityProjection",
            authority_epoch=authority_epoch,
        ),
        payload_hash=content_hash,
        event_type=MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
        job_type=MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    )


__all__ = [
    "MEMORY_PROJECTION_REBUILD_EVENT_TYPE",
    "MEMORY_PROJECTION_REBUILD_JOB_TYPE",
    "MEMORY_PROJECTION_REBUILD_OPERATION_TYPE",
    "MEMORY_PROJECTION_RIGHTS_REBUILD_EVENT_TYPE",
    "MEMORY_PROJECTION_RIGHTS_REBUILD_OPERATION_TYPE",
    "build_memory_projection_rebuild_effect_intent",
    "build_memory_projection_rebuild_effect_intent_for_correction",
    "build_memory_projection_rebuild_effect_intent_for_rights_revision",
    "build_memory_projection_rebuild_effect_intent_for_version",
]
