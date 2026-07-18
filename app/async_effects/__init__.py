"""Typed, disabled-by-default async effect kernel foundation.

The package owns only durable effect coordination records. It does not own
business aggregates, start workers, or invoke external providers.
"""

from .contracts import (
    ASYNC_EFFECT_SCHEMA_VERSION,
    AsyncEffectContractError,
    AsyncEffectConflict,
    AsyncEffectIntent,
    AsyncEffectRuntimeStatus,
    AsyncEffectTarget,
    EffectReceiptSummary,
    resolve_async_effect_runtime_status,
)
from .repository import (
    InMemoryEffectKernelRepository,
    PostgresEffectKernelRepository,
)
from .lease_repository import (
    AsyncEffectCancelResult,
    AsyncEffectJobLease,
    AsyncEffectJobPreview,
    AsyncEffectLeaseCancelled,
    AsyncEffectLeaseError,
    AsyncEffectLeaseLost,
    InMemoryAsyncEffectLeaseRepository,
    PostgresAsyncEffectLeaseRepository,
)

__all__ = [
    "ASYNC_EFFECT_SCHEMA_VERSION",
    "AsyncEffectContractError",
    "AsyncEffectCancelResult",
    "AsyncEffectConflict",
    "AsyncEffectIntent",
    "AsyncEffectJobLease",
    "AsyncEffectJobPreview",
    "AsyncEffectLeaseCancelled",
    "AsyncEffectLeaseError",
    "AsyncEffectLeaseLost",
    "AsyncEffectRuntimeStatus",
    "AsyncEffectTarget",
    "EffectReceiptSummary",
    "InMemoryEffectKernelRepository",
    "InMemoryAsyncEffectLeaseRepository",
    "PostgresEffectKernelRepository",
    "PostgresAsyncEffectLeaseRepository",
    "resolve_async_effect_runtime_status",
]
