"""Default-off reconciliation worker for P2-S4C lifecycle cleanup effects.

This worker has one deliberately narrow job: find lifecycle receipts whose
local access deny has already committed, then bind them to the generic async
effect and provider-effect records. It does *not* call public-index, cache,
Digital Human, voice, or object-storage providers. Every resulting domain is
therefore still persisted as ``pending`` with an ``unknown`` provider effect.

Keeping this recovery lane separate means a failed or delayed materialization
can never weaken the completed local access deny boundary.
"""

from __future__ import annotations

import argparse
import json
import socket
from time import sleep
from typing import Any, Optional

from app.async_effects.contracts import is_async_effect_store_ready
from app.async_effects.worker_lifecycle import WorkerDrainController
from app.core.config import Settings
from app.services.publication_external_cleanup import (
    PublicationExternalCleanupCoordinator,
    PublicationExternalCleanupError,
)
from app.services.store_factory import close_store, make_store, open_store


class PublicationExternalCleanupMaterializerWorkerRuntime:
    """Materialize at most a bounded batch of already-denied receipts."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: Any,
        worker_id: Optional[str] = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._worker_id = str(
            worker_id or f"publication-external-cleanup-materializer-{socket.gethostname()}"
        )

    def run_once(self, *, limit: int = 20) -> dict[str, object]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        reason = self._runtime_block_reason()
        if reason is not None:
            return self._payload(status="blocked", reason=reason)
        if not self._store_ready():
            return self._payload(status="blocked", reason="asyncEffectSchemaNotReady")
        required_factories = (
            "request_unit_of_work",
            "publication_external_cleanup_repository",
            "effect_kernel_repository",
            "provider_effect_repository",
        )
        if not all(callable(getattr(self._store, name, None)) for name in required_factories):
            return self._payload(
                status="blocked",
                reason="publicationExternalCleanupStoreUnsupported",
            )
        try:
            with self._store.request_unit_of_work(
                correlation_id="publication-external-cleanup-materializer",
                command_id=f"publicationExternalCleanupMaterializer:{self._worker_id}",
            ):
                cleanup_repository = self._store.publication_external_cleanup_repository()
                targets = cleanup_repository.list_pending_materializations(limit=limit)
                coordinator = PublicationExternalCleanupCoordinator(
                    effect_repository=self._store.effect_kernel_repository(),
                    provider_effect_repository=self._store.provider_effect_repository(),
                    cleanup_repository=cleanup_repository,
                )
                materialized = tuple(
                    (target, coordinator.materialize(target)) for target in targets
                )
        except PublicationExternalCleanupError:
            return self._payload(
                status="failed",
                reason="publicationExternalCleanupMaterializationRejected",
            )
        except Exception:
            return self._payload(
                status="failed",
                reason="publicationExternalCleanupMaterializationFailed",
            )
        if not materialized:
            return self._payload(status="idle", reason="noPendingPublicationExternalCleanup")
        domain_states: dict[str, int] = {}
        for _target, statuses in materialized:
            for item in statuses:
                key = f"{item.domain.value}:{item.state.value}"
                domain_states[key] = domain_states.get(key, 0) + 1
        return self._payload(
            status="materialized",
            reason="publicationExternalCleanupEffectsQueued",
            materialized_receipt_count=len(materialized),
            materialized_effect_count=sum(len(statuses) for _target, statuses in materialized),
            domain_states=dict(sorted(domain_states.items())),
        )

    def _runtime_block_reason(self) -> str | None:
        if not self._settings.async_effect_v1_enabled:
            return "asyncEffectV1Disabled"
        if not self._settings.async_effect_worker_enabled:
            return "asyncEffectWorkerDisabled"
        if not self._settings.publication_external_cleanup_materializer_enabled:
            return "publicationExternalCleanupMaterializerDisabled"
        return None

    def _store_ready(self) -> bool:
        readiness_probe = getattr(self._store, "readiness_probe", None)
        return callable(readiness_probe) and is_async_effect_store_ready(readiness_probe())

    def _payload(
        self,
        *,
        status: str,
        reason: str,
        materialized_receipt_count: int = 0,
        materialized_effect_count: int = 0,
        domain_states: dict[str, int] | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "mode": "run",
            "status": status,
            "reason": reason,
            "workerId": self._worker_id,
        }
        if status == "materialized":
            payload.update(
                {
                    "materializedReceiptCount": materialized_receipt_count,
                    "materializedEffectCount": materialized_effect_count,
                    "domainStates": domain_states or {},
                }
            )
        return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DreamJourney default-off publication external cleanup materializer"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="materialize one bounded receipt batch")
    mode.add_argument("--loop", action="store_true", help="continuously materialize bounded batches")
    parser.add_argument("--worker-id", default=None, help="opaque worker identifier")
    parser.add_argument("--limit", type=int, default=20, help="maximum receipts per iteration")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=None,
        help="idle delay between loop iterations; defaults to OWNER_TRUTH_WORKER_POLL_SECONDS",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    store = make_store(settings)
    open_store(store, wait=True)
    drain_controller = WorkerDrainController()
    drain_controller.install()
    try:
        worker = PublicationExternalCleanupMaterializerWorkerRuntime(
            settings=settings,
            store=store,
            worker_id=args.worker_id,
        )
        poll_seconds = max(
            0.1,
            float(
                args.poll_seconds
                if args.poll_seconds is not None
                else settings.owner_truth_worker_poll_seconds
            ),
        )
        last_result_key: tuple[str, str] | None = None
        try:
            while True:
                payload = worker.run_once(limit=args.limit)
                result_key = (str(payload.get("status") or ""), str(payload.get("reason") or ""))
                if not args.loop or result_key != last_result_key:
                    print(json.dumps(payload, sort_keys=True))
                    last_result_key = result_key
                if not args.loop:
                    return 0
                if drain_controller.stop_requested:
                    print(
                        json.dumps(
                            {
                                "mode": "run",
                                "status": "drained",
                                "reason": "workerShutdownRequested",
                            },
                            sort_keys=True,
                        )
                    )
                    return 0
                sleep(poll_seconds)
        except KeyboardInterrupt:
            return 0
    finally:
        drain_controller.restore()
        close_store(store)


if __name__ == "__main__":  # pragma: no cover - exercised through CLI tests
    raise SystemExit(main())
