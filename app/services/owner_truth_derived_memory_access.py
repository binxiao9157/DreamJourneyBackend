"""Shared read fence for data derived from Owner Truth formal memories.

Formal memories remain the private, review-approved source of truth. The
profile, autobiography and Live/read projections are derived views, so a
projection-rights revocation must make all of them unavailable immediately.
This helper deliberately returns no memory content and only evaluates the
value-free projection-rights fence supplied by the current projection read.
"""

from __future__ import annotations

from typing import Any

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionAccessDenied,
    OwnerTruthMemoryProjectionError,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_formal_memory import OwnerTruthFormalMemoryAccessDenied
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionService


class OwnerTruthDerivedMemoryAccessDenied(OwnerTruthFormalMemoryAccessDenied):
    """A derived view must not expose formal memory after its rights are revoked."""


def require_owner_truth_derived_memory_access(
    *,
    store: Any,
    context: OwnerTruthCommandContext,
) -> None:
    """Fail closed only when the current rights fence blocks derived reads.

    A rebuilding checkpoint with active rights is not itself a revocation: the
    caller may still derive a fresh private view from current formal memories.
    A revoked rights state, an inaccessible Vault, or an invalid projection
    contract never falls back to a stale derived artifact.
    """

    try:
        snapshot = OwnerTruthMemoryProjectionService(store).read(context=context)
    except (OwnerTruthMemoryProjectionAccessDenied, OwnerTruthMemoryProjectionError) as error:
        raise OwnerTruthDerivedMemoryAccessDenied(
            "derived formal-memory views are unavailable"
        ) from error
    if str(snapshot.get("rightsState") or "") != "active":
        raise OwnerTruthDerivedMemoryAccessDenied(
            "derived formal-memory views are unavailable"
        )


__all__ = [
    "OwnerTruthDerivedMemoryAccessDenied",
    "require_owner_truth_derived_memory_access",
]
