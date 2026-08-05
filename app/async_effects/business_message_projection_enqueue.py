"""Atomic acceptance boundary for the default-off message projection worker.

The caller must already be inside its business Unit of Work. This coordinator
accepts a typed async-effect job and persists its immutable, value-free worker
input together, so a later worker never has to infer a message from a legacy
mailbox row. It does not start a worker, write ``mailbox_letters``, dispatch a
notification, or authorize a cross-account recipient.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.async_effects.business_message_projection_effects import (
    BusinessMessageProjectionRequest,
)
from app.async_effects.business_message_projection_request_repository import (
    BusinessMessageProjectionRequestPersistenceSummary,
)
from app.async_effects.contracts import EffectReceiptSummary


class BusinessMessageProjectionEnqueueError(RuntimeError):
    """The internal message-projection acceptance boundary was unavailable."""


@dataclass(frozen=True)
class BusinessMessageProjectionEnqueueResult:
    """Value-free evidence that one typed internal job was durably accepted."""

    effect: EffectReceiptSummary
    input: BusinessMessageProjectionRequestPersistenceSummary

    def __post_init__(self) -> None:
        if self.effect.outcome not in {"accepted", "deduplicated"}:
            raise BusinessMessageProjectionEnqueueError(
                "message projection effect acceptance outcome is invalid"
            )
        if self.input.outcome not in {"recorded", "deduplicated"}:
            raise BusinessMessageProjectionEnqueueError(
                "message projection input persistence outcome is invalid"
            )
        if self.effect.job_id != self.input.request.effect_intent.job_id:
            raise BusinessMessageProjectionEnqueueError(
                "message projection effect and input refer to different jobs"
            )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "effect": self.effect.public_contract(),
            "input": self.input.value_free_summary(),
        }


class BusinessMessageProjectionEnqueueCoordinator:
    """Accept one typed job and immutable input in the caller's active UoW."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def accept(
        self,
        request: BusinessMessageProjectionRequest,
    ) -> BusinessMessageProjectionEnqueueResult:
        if not isinstance(request, BusinessMessageProjectionRequest):
            raise TypeError("message projection request is required")
        effect_repository = getattr(self._store, "effect_kernel_repository", None)
        input_repository = getattr(
            self._store,
            "async_effect_business_message_projection_request_repository",
            None,
        )
        if not callable(effect_repository) or not callable(input_repository):
            raise BusinessMessageProjectionEnqueueError(
                "message projection acceptance requires effect and input repositories"
            )
        effect = effect_repository().accept(request.effect_intent)
        persisted_input = input_repository().record(request)
        return BusinessMessageProjectionEnqueueResult(effect=effect, input=persisted_input)


__all__ = [
    "BusinessMessageProjectionEnqueueCoordinator",
    "BusinessMessageProjectionEnqueueError",
    "BusinessMessageProjectionEnqueueResult",
]
