"""Fail-closed activation preflight for production Owner Truth workers.

The decision is intentionally value-free. It validates deployment switches,
the live async-effect schema and Provider readiness before a long-running
worker process can begin claiming jobs. It never prints credentials, bucket
names, object keys, owner identifiers or job payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.async_effects.contracts import (
    resolve_async_effect_runtime_status,
)
from app.core.config import Settings
from app.services.provider_runtime import ProviderRuntimeInventory


class OwnerTruthWorkerKind(str, Enum):
    CANDIDATE_EXTRACTION = "ownerTruthCandidateExtraction"
    MEMORY_PROJECTION = "ownerTruthMemoryProjection"
    MEDIA_PROCESSING = "ownerTruthMediaProcessing"
    MEDIA_DELETION = "ownerTruthMediaDeletion"


@dataclass(frozen=True)
class OwnerTruthWorkerActivationDecision:
    worker: OwnerTruthWorkerKind
    ready: bool
    reason: str
    blocking_dependency: Optional[str] = None

    CONTRACT_VERSION = 1

    def public_descriptor(self) -> dict[str, object]:
        return {
            "contractVersion": self.CONTRACT_VERSION,
            "worker": self.worker.value,
            "ready": self.ready,
            "reason": self.reason,
            "blockingDependency": self.blocking_dependency,
        }


_WORKER_ENABLE_FLAGS = {
    OwnerTruthWorkerKind.CANDIDATE_EXTRACTION: (
        "owner_truth_candidate_extraction_worker_enabled",
        "ownerTruthCandidateExtractionWorkerDisabled",
    ),
    OwnerTruthWorkerKind.MEMORY_PROJECTION: (
        "owner_truth_memory_projection_worker_enabled",
        "ownerTruthMemoryProjectionWorkerDisabled",
    ),
    OwnerTruthWorkerKind.MEDIA_PROCESSING: (
        "owner_truth_media_processing_worker_enabled",
        "ownerTruthMediaProcessingWorkerDisabled",
    ),
    OwnerTruthWorkerKind.MEDIA_DELETION: (
        "owner_truth_media_deletion_worker_enabled",
        "ownerTruthMediaDeletionWorkerDisabled",
    ),
}


def _blocked(
    worker: OwnerTruthWorkerKind,
    reason: str,
    dependency: str,
) -> OwnerTruthWorkerActivationDecision:
    return OwnerTruthWorkerActivationDecision(
        worker=worker,
        ready=False,
        reason=reason,
        blocking_dependency=dependency,
    )


def evaluate_owner_truth_worker_activation(
    *,
    worker: OwnerTruthWorkerKind,
    settings: Settings,
    schema_ready: bool,
    provider_inventory: ProviderRuntimeInventory | None = None,
) -> OwnerTruthWorkerActivationDecision:
    """Return the bounded startup decision for one typed worker process."""

    if settings.store_backend != "postgres":
        return _blocked(worker, "ownerTruthWorkerPostgresRequired", "store")

    runtime = resolve_async_effect_runtime_status(
        async_effect_v1_enabled=settings.async_effect_v1_enabled,
        worker_enabled=settings.async_effect_worker_enabled,
        schema_ready=bool(schema_ready),
    )
    if not runtime.allowed:
        return _blocked(worker, runtime.reason, "asyncEffectRuntime")

    flag_name, disabled_reason = _WORKER_ENABLE_FLAGS[worker]
    if not bool(getattr(settings, flag_name)):
        return _blocked(worker, disabled_reason, "workerKillSwitch")

    if (
        worker is OwnerTruthWorkerKind.CANDIDATE_EXTRACTION
        and settings.owner_truth_live_memory_organization_enabled
        and not settings.deepseek_api_key
    ):
        return _blocked(
            worker,
            "ownerTruthLiveMemoryOrganizerNotConfigured",
            "deepSeek",
        )

    if (
        worker is OwnerTruthWorkerKind.MEMORY_PROJECTION
        and not settings.owner_truth_candidate_extraction_worker_enabled
    ):
        return _blocked(
            worker,
            "ownerTruthCandidateExtractionWorkerDisabled",
            "candidateExtraction",
        )

    if worker in {
        OwnerTruthWorkerKind.MEDIA_PROCESSING,
        OwnerTruthWorkerKind.MEDIA_DELETION,
    }:
        inventory = provider_inventory or ProviderRuntimeInventory(
            settings,
            validated_at_startup=True,
        )
        storage = inventory.status_for("ownerTruthMediaStorage")
        if not storage.enabled or not storage.provider_ready:
            return _blocked(worker, storage.reason, "ownerTruthMediaStorage")

    return OwnerTruthWorkerActivationDecision(
        worker=worker,
        ready=True,
        reason="ownerTruthWorkerActivationReady",
    )


def main(argv: list[str] | None = None) -> int:
    # Preserve the historical module path while using the shared six-worker
    # registry and structured, value-free diagnostics.
    from app.async_effects.worker_activation import main as shared_main

    return shared_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "OwnerTruthWorkerActivationDecision",
    "OwnerTruthWorkerKind",
    "evaluate_owner_truth_worker_activation",
    "main",
]
