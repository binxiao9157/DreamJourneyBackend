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
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


MEMORY_PROJECTION_REBUILD_OPERATION_TYPE = "ownerTruth.memoryVersion.activated"
MEMORY_PROJECTION_REBUILD_EVENT_TYPE = "ownerTruth.memoryProjection.rebuildRequested"
MEMORY_PROJECTION_REBUILD_JOB_TYPE = "ownerTruth.memoryProjection.rebuild"


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

    return AsyncEffectIntent(
        operation_type=MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
        target=AsyncEffectTarget(
            owner_subject_id=context.owner_subject_id,
            vault_id=context.vault_id,
            resource_type="memoryVersion",
            resource_id=activation.memory_version_id,
            resource_version=activation.memory_version,
            purpose="compatibilityProjection",
            authority_epoch=activation.authority_epoch,
        ),
        payload_hash=activation.content_hash,
        event_type=MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
        job_type=MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    )


__all__ = [
    "MEMORY_PROJECTION_REBUILD_EVENT_TYPE",
    "MEMORY_PROJECTION_REBUILD_JOB_TYPE",
    "MEMORY_PROJECTION_REBUILD_OPERATION_TYPE",
    "build_memory_projection_rebuild_effect_intent",
]
