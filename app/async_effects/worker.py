"""Default-disabled async-effect worker entry support.

The first worker slice is intentionally observation-only. It can report
value-free eligible job coordinates, but it has no registered business handler
and therefore never claims or executes existing product jobs.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import socket
from typing import Any, Iterable, Optional

from app.async_effects.contracts import (
    is_async_effect_store_ready,
    resolve_async_effect_runtime_status,
)
from app.async_effects.readiness_evidence import build_async_effect_worker_readiness_evidence
from app.async_effects.provider_query_operations import (
    ProviderQueryBacklogEntry,
    build_provider_query_operations_evidence,
)
from app.async_effects.worker_loss_evidence import build_async_effect_worker_loss_evidence
from app.core.config import Settings
from app.services.store_factory import close_store, make_store, open_store


class AsyncEffectWorkerRuntime:
    """Readiness-gated, no-op worker shell for the lease foundation."""

    def __init__(self, *, settings: Settings, store: Any, worker_id: Optional[str] = None) -> None:
        self._settings = settings
        self._store = store
        self._worker_id = str(worker_id or f"async-effect-worker-{socket.gethostname()}")

    def shadow_once(self, *, limit: int = 20) -> dict[str, Any]:
        readiness = self._readiness()
        status = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=self._settings.async_effect_v1_enabled,
            worker_enabled=self._settings.async_effect_worker_enabled,
            schema_ready=readiness,
        )
        repository_factory = getattr(self._store, "async_effect_lease_repository", None)
        request_uow = getattr(self._store, "request_unit_of_work", None)
        if not callable(repository_factory) or not callable(request_uow):
            return {
                "mode": "shadow",
                "status": "blocked",
                "reason": "asyncEffectWorkerStoreUnsupported",
                "workerId": self._worker_id,
                "readinessEvidence": self._readiness_evidence(
                    status,
                    store_supported=False,
                ),
                "providerQueryOperations": self._provider_query_operations_evidence(
                    store_supported=False,
                ),
            }
        try:
            with request_uow(
                correlation_id="async-effect-worker-shadow",
                command_id="asyncEffectWorkerShadow",
            ):
                repository = repository_factory()
                previews = repository.preview_eligible(limit=limit)
                expired_preview = getattr(repository, "preview_expired_leases", None)
                if callable(expired_preview):
                    try:
                        expired_leases = expired_preview(limit=limit)
                        worker_loss_evidence = self._worker_loss_evidence(
                            status,
                            previews=expired_leases,
                        )
                    except Exception:
                        worker_loss_evidence = self._worker_loss_evidence(
                            status,
                            collection_error_code="asyncEffectWorkerLossObservationFailed",
                        )
                else:
                    worker_loss_evidence = self._worker_loss_evidence(
                        status,
                        store_supported=False,
                    )
                provider_repository_factory = getattr(self._store, "provider_effect_repository", None)
                if callable(provider_repository_factory):
                    try:
                        provider_query_backlog = provider_repository_factory().reconciliation_backlog()
                        provider_query_operations = self._provider_query_operations_evidence(
                            backlog_entries=provider_query_backlog,
                        )
                    except Exception:
                        provider_query_operations = self._provider_query_operations_evidence(
                            collection_error_code="providerQueryOperationsObservationFailed",
                        )
                else:
                    provider_query_operations = self._provider_query_operations_evidence(
                        store_supported=False,
                    )
        except Exception:
            return {
                "mode": "shadow",
                "status": "blocked",
                "reason": "asyncEffectBacklogObservationFailed",
                "workerId": self._worker_id,
                "readinessEvidence": self._readiness_evidence(
                    status,
                    collection_error_code="asyncEffectBacklogObservationFailed",
                ),
                "providerQueryOperations": self._provider_query_operations_evidence(
                    collection_error_code="providerQueryOperationsObservationFailed",
                ),
            }
        return {
            "mode": "shadow",
            "status": "observed",
            "reason": status.reason,
            "workerId": self._worker_id,
            "eligibleJobCount": len(previews),
            "eligibleJobTypes": sorted({item.job_type for item in previews}),
            "readinessEvidence": self._readiness_evidence(status, previews=previews),
            "expiredLeaseCount": worker_loss_evidence["expiredLeaseCount"],
            "workerLossEvidence": worker_loss_evidence,
            "providerQueryOperations": provider_query_operations,
        }

    def run_once(self) -> dict[str, Any]:
        readiness = self._readiness()
        status = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=self._settings.async_effect_v1_enabled,
            worker_enabled=self._settings.async_effect_worker_enabled,
            schema_ready=readiness,
        )
        if not status.allowed:
            return {
                "mode": "run",
                "status": "blocked",
                "reason": status.reason,
                "workerId": self._worker_id,
                "readinessEvidence": self._readiness_evidence(status),
            }
        # No business handler is registered in this foundation slice. This is
        # deliberate: claiming a real job before its consumer contract exists
        # would create false completion evidence.
        return {
            "mode": "run",
            "status": "idle",
            "reason": "asyncEffectNoRunnableHandlers",
            "workerId": self._worker_id,
            "readinessEvidence": self._readiness_evidence(status),
        }

    def _readiness(self) -> bool:
        probe = getattr(self._store, "readiness_probe", None)
        if not callable(probe):
            return False
        return is_async_effect_store_ready(probe())

    def _readiness_evidence(
        self,
        status: Any,
        *,
        previews: Iterable[Any] = (),
        store_supported: bool = True,
        collection_error_code: Optional[str] = None,
    ) -> dict[str, Any]:
        observed_at = datetime.now(timezone.utc)
        evidence = build_async_effect_worker_readiness_evidence(
            runtime_status=status,
            worker_id=self._worker_id,
            previews=previews,
            runnable_handler_count=0,
            observed_at=observed_at,
            expires_at=observed_at + timedelta(minutes=5),
            store_supported=store_supported,
            collection_error_code=collection_error_code,
        )
        return evidence.value_free_summary(now=observed_at)

    def _worker_loss_evidence(
        self,
        status: Any,
        *,
        previews: Iterable[Any] = (),
        store_supported: bool = True,
        collection_error_code: Optional[str] = None,
    ) -> dict[str, Any]:
        observed_at = datetime.now(timezone.utc)
        evidence = build_async_effect_worker_loss_evidence(
            runtime_status=status,
            observer_worker_id=self._worker_id,
            previews=previews,
            observed_at=observed_at,
            expires_at=observed_at + timedelta(minutes=5),
            store_supported=store_supported,
            collection_error_code=collection_error_code,
        )
        return evidence.value_free_summary(now=observed_at)

    def _provider_query_operations_evidence(
        self,
        *,
        backlog_entries: Iterable[ProviderQueryBacklogEntry] = (),
        store_supported: bool = True,
        collection_error_code: Optional[str] = None,
    ) -> dict[str, Any]:
        """Expose a bounded G3 query baseline without invoking a Provider."""

        observed_at = datetime.now(timezone.utc)
        evidence = build_provider_query_operations_evidence(
            backlog_entries=backlog_entries,
            observed_at=observed_at,
            expires_at=observed_at + timedelta(minutes=5),
            store_supported=store_supported,
            collection_error_code=collection_error_code,
        )
        return evidence.value_free_summary(now=observed_at)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DreamJourney async-effect worker foundation")
    parser.add_argument("--shadow-once", action="store_true", help="list value-free eligible job summaries")
    parser.add_argument("--once", action="store_true", help="verify runtime gate without claiming jobs")
    parser.add_argument("--limit", type=int, default=20, help="maximum shadow job summaries")
    parser.add_argument("--worker-id", default=None, help="opaque worker identifier")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    store = make_store(settings)
    open_store(store, wait=True)
    try:
        worker = AsyncEffectWorkerRuntime(
            settings=settings,
            store=store,
            worker_id=args.worker_id,
        )
        payload = worker.run_once() if args.once else worker.shadow_once(limit=args.limit)
        print(json.dumps(payload, sort_keys=True))
        return 0
    finally:
        close_store(store)


if __name__ == "__main__":  # pragma: no cover - exercised through app.worker
    raise SystemExit(main())
