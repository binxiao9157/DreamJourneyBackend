"""Default-disabled async-effect scheduler shadow entry support.

This foundation reports value-free scheduler lease summaries only. It never
claims a scheduler lease or invokes a business handler.
"""

from __future__ import annotations

import argparse
import json
import socket
from typing import Any, Optional

from app.async_effects.contracts import (
    is_async_effect_store_ready,
    resolve_async_effect_runtime_status,
)
from app.core.config import Settings
from app.services.store_factory import close_store, make_store, open_store


class AsyncEffectSchedulerRuntime:
    """Readiness-gated, no-op scheduler shell for the lease foundation."""

    def __init__(self, *, settings: Settings, store: Any, scheduler_id: Optional[str] = None) -> None:
        self._settings = settings
        self._store = store
        self._scheduler_id = str(scheduler_id or f"async-effect-scheduler-{socket.gethostname()}")

    def shadow_once(self, *, limit: int = 20) -> dict[str, Any]:
        readiness = self._readiness()
        status = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=self._settings.async_effect_v1_enabled,
            worker_enabled=self._settings.async_effect_worker_enabled,
            schema_ready=readiness,
        )
        repository_factory = getattr(self._store, "async_effect_scheduler_repository", None)
        request_uow = getattr(self._store, "request_unit_of_work", None)
        if not callable(repository_factory) or not callable(request_uow):
            return {
                "mode": "shadow",
                "status": "blocked",
                "reason": "asyncEffectSchedulerStoreUnsupported",
                "schedulerId": self._scheduler_id,
            }
        with request_uow(
            correlation_id="async-effect-scheduler-shadow",
            command_id="asyncEffectSchedulerShadow",
        ):
            previews = repository_factory().preview_eligible(limit=limit)
        return {
            "mode": "shadow",
            "status": "observed",
            "reason": status.reason,
            "schedulerId": self._scheduler_id,
            "eligibleSchedulerLeaseCount": len(previews),
            "eligibleSchedulerKeys": sorted({item.scheduler_key for item in previews}),
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
                "schedulerId": self._scheduler_id,
            }
        # Scheduler admission intentionally remains separate from worker
        # admission. Claiming a due operation without a typed handler would
        # create scheduling evidence without a lawful consumer.
        return {
            "mode": "run",
            "status": "idle",
            "reason": "asyncEffectNoRunnableSchedulers",
            "schedulerId": self._scheduler_id,
        }

    def _readiness(self) -> bool:
        probe = getattr(self._store, "readiness_probe", None)
        if not callable(probe):
            return False
        return is_async_effect_store_ready(probe())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DreamJourney async-effect scheduler foundation")
    parser.add_argument("--shadow-once", action="store_true", help="list value-free eligible scheduler summaries")
    parser.add_argument("--once", action="store_true", help="verify runtime gate without claiming leases")
    parser.add_argument("--limit", type=int, default=20, help="maximum shadow lease summaries")
    parser.add_argument("--scheduler-id", default=None, help="opaque scheduler identifier")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    store = make_store(settings)
    open_store(store, wait=True)
    try:
        scheduler = AsyncEffectSchedulerRuntime(
            settings=settings,
            store=store,
            scheduler_id=args.scheduler_id,
        )
        payload = scheduler.run_once() if args.once else scheduler.shadow_once(limit=args.limit)
        print(json.dumps(payload, sort_keys=True))
        return 0
    finally:
        close_store(store)


if __name__ == "__main__":  # pragma: no cover - exercised through app.scheduler
    raise SystemExit(main())
