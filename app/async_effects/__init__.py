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
from .scheduler_repository import (
    AsyncEffectSchedulerLease,
    AsyncEffectSchedulerLeaseError,
    AsyncEffectSchedulerLeaseLost,
    AsyncEffectSchedulerLeaseRegistration,
    AsyncEffectSchedulerPreview,
    InMemoryAsyncEffectSchedulerLeaseRepository,
    PostgresAsyncEffectSchedulerLeaseRepository,
)
from .consumer_repository import (
    AsyncEffectConsumerAdmissionDenied,
    AsyncEffectConsumerError,
    AsyncEffectConsumerIncomplete,
    AsyncEffectConsumerReceipt,
    AsyncEffectSyntheticConsumerCommand,
    InMemoryAsyncEffectConsumerRepository,
    PostgresAsyncEffectConsumerRepository,
)
from .target_admission import (
    AsyncEffectTargetAdmission,
    AsyncEffectTargetAdmissionError,
    InMemoryOwnerTruthSourceTargetAdmissionRepository,
    PostgresOwnerTruthSourceTargetAdmissionRepository,
)

__all__ = [
    "ASYNC_EFFECT_SCHEMA_VERSION",
    "AsyncEffectContractError",
    "AsyncEffectCancelResult",
    "AsyncEffectConsumerAdmissionDenied",
    "AsyncEffectConsumerError",
    "AsyncEffectConsumerIncomplete",
    "AsyncEffectConsumerReceipt",
    "AsyncEffectConflict",
    "AsyncEffectIntent",
    "AsyncEffectJobLease",
    "AsyncEffectJobPreview",
    "AsyncEffectLeaseCancelled",
    "AsyncEffectLeaseError",
    "AsyncEffectLeaseLost",
    "AsyncEffectRuntimeStatus",
    "AsyncEffectSchedulerLease",
    "AsyncEffectSchedulerLeaseError",
    "AsyncEffectSchedulerLeaseLost",
    "AsyncEffectSchedulerLeaseRegistration",
    "AsyncEffectSchedulerPreview",
    "AsyncEffectTarget",
    "AsyncEffectSyntheticConsumerCommand",
    "AsyncEffectTargetAdmission",
    "AsyncEffectTargetAdmissionError",
    "EffectReceiptSummary",
    "InMemoryEffectKernelRepository",
    "InMemoryAsyncEffectLeaseRepository",
    "InMemoryAsyncEffectConsumerRepository",
    "InMemoryOwnerTruthSourceTargetAdmissionRepository",
    "InMemoryAsyncEffectSchedulerLeaseRepository",
    "PostgresEffectKernelRepository",
    "PostgresAsyncEffectLeaseRepository",
    "PostgresAsyncEffectConsumerRepository",
    "PostgresOwnerTruthSourceTargetAdmissionRepository",
    "PostgresAsyncEffectSchedulerLeaseRepository",
    "resolve_async_effect_runtime_status",
]
