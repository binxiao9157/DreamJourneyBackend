"""Fail-closed activation preflight for every long-running worker.

Diagnostics are deliberately bounded and value-free. Raw exception messages,
database connection strings, Provider configuration and job payloads are never
included in stdout or stderr.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import secrets
import sys
from typing import Callable

from app.async_effects.contracts import (
    is_async_effect_store_ready,
    resolve_async_effect_runtime_status,
)
from app.async_effects.owner_truth_worker_activation import (
    OwnerTruthWorkerKind,
    evaluate_owner_truth_worker_activation,
)
from app.async_effects.worker_deployment_registry import (
    LONG_RUNNING_WORKERS,
    deployment_spec_for,
)
from app.core.config import Settings
from app.services.provider_runtime import ProviderRuntimeInventory
from app.services.store_factory import close_store, make_store, open_store


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
    "businessMessageProjection": "businessMessageProjectionWorkerDisabled",
    "publicationExternalCleanupMaterializer": (
        "publicationExternalCleanupMaterializerDisabled"
    ),
}

_READY_REASONS = {
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
    "WorkerActivationDecision",
    "WorkerReadinessProbeError",
    "evaluate_worker_activation",
    "live_schema_ready",
    "main",
    "run_worker_activation_preflight",
]
