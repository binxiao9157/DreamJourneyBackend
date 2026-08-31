"""Fail-closed activation preflight for every long-running worker.

Diagnostics are deliberately bounded and value-free. Raw exception messages,
database connection strings, Provider configuration and job payloads are never
included in stdout or stderr.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import json
import secrets
import sys
from typing import Callable

from app.async_effects.contracts import (
    is_async_effect_store_ready,
    resolve_async_effect_runtime_status,
)
from app.async_effects.worker_deployment_registry import (
    LONG_RUNNING_WORKERS,
    deployment_spec_for,
)
from app.core.config import Settings
from app.services.provider_runtime import ProviderRuntimeInventory
from app.services.store_factory import close_store, make_store, open_store


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
    blocking_dependency: str | None = None

    CONTRACT_VERSION = 1

    def public_descriptor(self) -> dict[str, object]:
        return {
            "contractVersion": self.CONTRACT_VERSION,
            "worker": self.worker.value,
            "ready": self.ready,
            "reason": self.reason,
            "blockingDependency": self.blocking_dependency,
        }


_OWNER_WORKER_ENABLE_FLAGS = {
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


def _owner_blocked(
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
    if settings.store_backend != "postgres":
        return _owner_blocked(worker, "ownerTruthWorkerPostgresRequired", "store")

    runtime = resolve_async_effect_runtime_status(
        async_effect_v1_enabled=settings.async_effect_v1_enabled,
        worker_enabled=settings.async_effect_worker_enabled,
        schema_ready=bool(schema_ready),
    )
    if not runtime.allowed:
        return _owner_blocked(worker, runtime.reason, "asyncEffectRuntime")

    flag_name, disabled_reason = _OWNER_WORKER_ENABLE_FLAGS[worker]
    if not bool(getattr(settings, flag_name)):
        return _owner_blocked(worker, disabled_reason, "workerKillSwitch")

    if (
        worker is OwnerTruthWorkerKind.CANDIDATE_EXTRACTION
        and settings.owner_truth_live_memory_organization_enabled
        and not settings.deepseek_api_key
    ):
        return _owner_blocked(
            worker,
            "ownerTruthLiveMemoryOrganizerNotConfigured",
            "deepSeek",
        )

    if (
        worker is OwnerTruthWorkerKind.MEMORY_PROJECTION
        and not settings.owner_truth_candidate_extraction_worker_enabled
    ):
        return _owner_blocked(
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
            return _owner_blocked(worker, storage.reason, "ownerTruthMediaStorage")

    return OwnerTruthWorkerActivationDecision(
        worker=worker,
        ready=True,
        reason="ownerTruthWorkerActivationReady",
    )


@dataclass(frozen=True)
class WorkerActivationDecision:
    worker: str
    ready: bool
    reason: str
    blocking_dependency: str | None = None
    failure_stage: str | None = None
    failure_code: str | None = None
    retryable: bool | None = None
    correlation_id: str | None = None

    CONTRACT_VERSION = 2

    def public_descriptor(self) -> dict[str, object]:
        return {
            "contractVersion": self.CONTRACT_VERSION,
            "worker": self.worker,
            "ready": self.ready,
            "reason": self.reason,
            "blockingDependency": self.blocking_dependency,
            "failureStage": self.failure_stage,
            "failureCode": self.failure_code,
            "retryable": self.retryable,
            "correlationId": self.correlation_id,
        }


class WorkerReadinessProbeError(RuntimeError):
    def __init__(self, *, stage: str, code: str, retryable: bool) -> None:
        super().__init__(code)
        self.stage = stage
        self.code = code
        self.retryable = retryable


_OWNER_TRUTH_WORKERS = {
    kind.value: kind
    for kind in OwnerTruthWorkerKind
}

_DISABLED_REASONS = {
    "narrativeGeneration": "narrativeGenerationWorkerDisabled",
    "businessMessageProjection": "businessMessageProjectionWorkerDisabled",
    "publicationExternalCleanupMaterializer": (
        "publicationExternalCleanupMaterializerDisabled"
    ),
}

_READY_REASONS = {
    "narrativeGeneration": "narrativeGenerationWorkerActivationReady",
    "businessMessageProjection": "businessMessageProjectionWorkerActivationReady",
    "publicationExternalCleanupMaterializer": (
        "publicationExternalCleanupMaterializerActivationReady"
    ),
}


def _blocked(
    worker: str,
    reason: str,
    dependency: str,
) -> WorkerActivationDecision:
    return WorkerActivationDecision(
        worker=worker,
        ready=False,
        reason=reason,
        blocking_dependency=dependency,
    )


def _diagnostic_failure(
    *,
    worker: str,
    stage: str,
    code: str,
    retryable: bool,
    correlation_id: str | None = None,
) -> WorkerActivationDecision:
    reason = (
        "ownerTruthWorkerReadinessProbeFailed"
        if worker in _OWNER_TRUTH_WORKERS
        else "workerReadinessProbeFailed"
    )
    return WorkerActivationDecision(
        worker=worker,
        ready=False,
        reason=reason,
        blocking_dependency="runtimeReadiness",
        failure_stage=stage,
        failure_code=code,
        retryable=retryable,
        correlation_id=correlation_id or secrets.token_hex(8),
    )


def evaluate_worker_activation(
    *,
    worker: str,
    settings: Settings,
    schema_ready: bool,
    provider_inventory: ProviderRuntimeInventory | None = None,
) -> WorkerActivationDecision:
    spec = deployment_spec_for(worker)
    owner_kind = _OWNER_TRUTH_WORKERS.get(worker)
    if owner_kind is not None:
        owner_decision = evaluate_owner_truth_worker_activation(
            worker=owner_kind,
            settings=settings,
            schema_ready=schema_ready,
            provider_inventory=provider_inventory,
        )
        return WorkerActivationDecision(
            worker=worker,
            ready=owner_decision.ready,
            reason=owner_decision.reason,
            blocking_dependency=owner_decision.blocking_dependency,
        )

    if settings.store_backend != "postgres":
        return _blocked(worker, "longRunningWorkerPostgresRequired", "store")

    runtime = resolve_async_effect_runtime_status(
        async_effect_v1_enabled=settings.async_effect_v1_enabled,
        worker_enabled=settings.async_effect_worker_enabled,
        schema_ready=bool(schema_ready),
    )
    if not runtime.allowed:
        return _blocked(worker, runtime.reason, "asyncEffectRuntime")

    if not spec.enabled(settings):
        return _blocked(worker, _DISABLED_REASONS[worker], "workerKillSwitch")

    if (
        worker == "narrativeGeneration"
        and settings.narrative_generation_provider != "deepseek"
    ):
        return _blocked(
            worker,
            "narrativeGenerationProviderNotConfigured",
            "narrativeProvider",
        )
    if worker == "narrativeGeneration" and not settings.deepseek_api_key:
        return _blocked(
            worker,
            "narrativeGenerationProviderCredentialNotConfigured",
            "deepSeek",
        )
    if (
        worker == "narrativeGeneration"
        and settings.narrative_generation_model in {"", "disabled"}
    ):
        return _blocked(
            worker,
            "narrativeGenerationModelNotConfigured",
            "narrativeModel",
        )

    return WorkerActivationDecision(
        worker=worker,
        ready=True,
        reason=_READY_REASONS[worker],
    )


def live_schema_ready(settings: Settings) -> bool:
    try:
        store = make_store(settings)
    except Exception as exc:
        raise WorkerReadinessProbeError(
            stage="makeStore",
            code="storeFactoryFailed",
            retryable=False,
        ) from exc

    primary_error: WorkerReadinessProbeError | None = None
    try:
        try:
            open_store(store, wait=True)
        except Exception as exc:
            raise WorkerReadinessProbeError(
                stage="openStore",
                code="storeOpenFailed",
                retryable=True,
            ) from exc

        readiness_probe = getattr(store, "readiness_probe", None)
        if not callable(readiness_probe):
            raise WorkerReadinessProbeError(
                stage="readinessProbe",
                code="readinessProbeUnavailable",
                retryable=False,
            )
        try:
            readiness = readiness_probe()
            return is_async_effect_store_ready(readiness)
        except Exception as exc:
            raise WorkerReadinessProbeError(
                stage="readinessProbe",
                code="readinessProbeFailed",
                retryable=True,
            ) from exc
    except WorkerReadinessProbeError as exc:
        primary_error = exc
        raise
    finally:
        try:
            close_store(store)
        except Exception as exc:
            if primary_error is None:
                raise WorkerReadinessProbeError(
                    stage="closeStore",
                    code="storeCloseFailed",
                    retryable=True,
                ) from exc


def run_worker_activation_preflight(
    *,
    worker: str,
    settings: Settings,
    schema_probe: Callable[[Settings], bool] | None = None,
) -> WorkerActivationDecision:
    probe = schema_probe or live_schema_ready
    try:
        schema_ready = probe(settings)
    except WorkerReadinessProbeError as exc:
        return _diagnostic_failure(
            worker=worker,
            stage=exc.stage,
            code=exc.code,
            retryable=exc.retryable,
        )
    except Exception:
        return _diagnostic_failure(
            worker=worker,
            stage="runtimeReadiness",
            code="unexpectedReadinessFailure",
            retryable=True,
        )

    try:
        return evaluate_worker_activation(
            worker=worker,
            settings=settings,
            schema_ready=schema_ready,
        )
    except Exception:
        return _diagnostic_failure(
            worker=worker,
            stage="activationEvaluation",
            code="unexpectedActivationFailure",
            retryable=False,
        )


def _emit_safe_diagnostic(decision: WorkerActivationDecision) -> None:
    if decision.failure_code is None:
        return
    print(
        json.dumps(
            {
                "event": "workerActivationBlocked",
                "worker": decision.worker,
                "failureStage": decision.failure_stage,
                "failureCode": decision.failure_code,
                "retryable": decision.retryable,
                "correlationId": decision.correlation_id,
            },
            sort_keys=True,
        ),
        file=sys.stderr,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate one long-running worker before process activation."
    )
    parser.add_argument(
        "--worker",
        required=True,
        choices=[spec.worker for spec in LONG_RUNNING_WORKERS],
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env()
    except Exception:
        decision = _diagnostic_failure(
            worker=args.worker,
            stage="configuration",
            code="settingsLoadFailed",
            retryable=False,
        )
    else:
        decision = run_worker_activation_preflight(
            worker=args.worker,
            settings=settings,
        )

    _emit_safe_diagnostic(decision)
    print(json.dumps(decision.public_descriptor(), sort_keys=True))
    return 0 if decision.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "OwnerTruthWorkerActivationDecision",
    "OwnerTruthWorkerKind",
    "WorkerActivationDecision",
    "WorkerReadinessProbeError",
    "evaluate_owner_truth_worker_activation",
    "evaluate_worker_activation",
    "live_schema_ready",
    "main",
    "run_worker_activation_preflight",
]
