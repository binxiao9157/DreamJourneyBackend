"""Owner-inbox admission for completed business-message projections.

The caller must already hold the business Unit of Work that persisted the
consumer completion receipt.  This helper resolves an explicit active inbox
binding and atomically accepts the metadata-only projection job.  It never
infers an inbox from a family relationship or from a legacy user identifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.async_effects.business_message_projection_effects import (
    BusinessMessageProjectionRequest,
)
from app.async_effects.business_message_projection_enqueue import (
    BusinessMessageProjectionEnqueueCoordinator,
)
from app.async_effects.consumer_repository import AsyncEffectConsumerReceipt
from app.async_effects.contracts import AsyncEffectIntent
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
)


class OwnerBusinessMessageProjectionError(RuntimeError):
    """A completed result could not be bound to its active owner inbox."""


@dataclass(frozen=True)
class OwnerBusinessMessageProjectionResult:
    outcome: str
    input_outcome: str
    kind: InAppMessageKind

    def __post_init__(self) -> None:
        if self.outcome not in {"accepted", "deduplicated"}:
            raise OwnerBusinessMessageProjectionError("message effect outcome is invalid")
        if self.input_outcome not in {"recorded", "deduplicated"}:
            raise OwnerBusinessMessageProjectionError("message input outcome is invalid")
        if not isinstance(self.kind, InAppMessageKind):
            raise OwnerBusinessMessageProjectionError("message kind is invalid")


def enqueue_owner_business_message(
    store: Any,
    *,
    intent: AsyncEffectIntent,
    completion: AsyncEffectConsumerReceipt,
    kind: InAppMessageKind,
) -> OwnerBusinessMessageProjectionResult | None:
    """Accept one owner message when the store exposes the durable V4 seam.

    In-memory workers that predate the message seam may omit these repositories;
    they return ``None`` and remain useful as isolated domain test doubles.
    Production Postgres stores expose all three dependencies and fail the
    enclosing business transaction if inbox authority cannot be proven.
    """

    if getattr(store, "business_message_projection_enabled", True) is False:
        return None

    resolver_factory = getattr(store, "async_effect_legacy_inbox_account_resolver", None)
    effect_repository = getattr(store, "effect_kernel_repository", None)
    input_repository = getattr(
        store,
        "async_effect_business_message_projection_request_repository",
        None,
    )
    if not all(callable(candidate) for candidate in (
        resolver_factory,
        effect_repository,
        input_repository,
    )):
        return None

    target = intent.target
    resolved = resolver_factory().resolve_active(target.owner_subject_id)
    snapshot = resolved.snapshot
    if (
        snapshot.inbox_subject_id != target.owner_subject_id
        or snapshot.inbox_vault_id != target.vault_id
    ):
        raise OwnerBusinessMessageProjectionError(
            "active owner inbox does not match the completed business target"
        )

    source = BusinessCompletionMessageSource(
        intent=intent,
        completion=completion,
        message_kind=kind,
    )
    accepted = BusinessMessageProjectionEnqueueCoordinator(store).accept(
        BusinessMessageProjectionRequest(
            source=source,
            inbox_account=snapshot,
        )
    )
    return OwnerBusinessMessageProjectionResult(
        outcome=accepted.effect.outcome,
        input_outcome=accepted.input.outcome,
        kind=kind,
    )


__all__ = [
    "OwnerBusinessMessageProjectionError",
    "OwnerBusinessMessageProjectionResult",
    "enqueue_owner_business_message",
]
