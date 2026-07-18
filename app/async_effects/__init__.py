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
    AsyncEffectJobCompletion,
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
    AsyncEffectConsumerCompletionCommand,
    AsyncEffectConsumerError,
    AsyncEffectConsumerIncomplete,
    AsyncEffectConsumerReceipt,
    AsyncEffectSyntheticConsumerCommand,
    InMemoryAsyncEffectConsumerRepository,
    OwnerTruthSourceBlockedConsumerCommand,
    OwnerTruthSourceCandidateExtractionConsumerCommand,
    OwnerTruthMemoryProjectionRebuildConsumerCommand,
    PostgresAsyncEffectConsumerRepository,
)
from .target_admission import (
    AsyncEffectTargetAdmission,
    AsyncEffectTargetAdmissionError,
    InMemoryOwnerTruthSourceTargetAdmissionRepository,
    InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository,
    PostgresOwnerTruthMemoryProjectionTargetAdmissionRepository,
    PostgresOwnerTruthSourceTargetAdmissionRepository,
)

__all__ = [
    "ASYNC_EFFECT_SCHEMA_VERSION",
    "AsyncEffectContractError",
    "AsyncEffectCancelResult",
    "AsyncEffectConsumerAdmissionDenied",
    "AsyncEffectConsumerCompletionCommand",
    "AsyncEffectConsumerError",
    "AsyncEffectConsumerIncomplete",
    "AsyncEffectConsumerReceipt",
    "AsyncEffectConflict",
    "AsyncEffectIntent",
    "AsyncEffectJobLease",
    "AsyncEffectJobCompletion",
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
    "OwnerTruthSourceBlockedConsumerCommand",
    "OwnerTruthSourceCandidateExtractionConsumerCommand",
    "OwnerTruthMemoryProjectionRebuildConsumerCommand",
    "InMemoryOwnerTruthSourceTargetAdmissionRepository",
    "InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository",
    "InMemoryAsyncEffectSchedulerLeaseRepository",
    "PostgresEffectKernelRepository",
    "PostgresAsyncEffectLeaseRepository",
    "PostgresAsyncEffectConsumerRepository",
    "PostgresOwnerTruthSourceTargetAdmissionRepository",
    "PostgresOwnerTruthMemoryProjectionTargetAdmissionRepository",
    "PostgresAsyncEffectSchedulerLeaseRepository",
    "resolve_async_effect_runtime_status",
]
