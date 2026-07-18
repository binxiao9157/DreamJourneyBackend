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

__all__ = [
    "ASYNC_EFFECT_SCHEMA_VERSION",
    "AsyncEffectContractError",
    "AsyncEffectConflict",
    "AsyncEffectIntent",
    "AsyncEffectRuntimeStatus",
    "AsyncEffectTarget",
    "EffectReceiptSummary",
    "InMemoryEffectKernelRepository",
    "PostgresEffectKernelRepository",
    "resolve_async_effect_runtime_status",
]
