"""Default-disabled async-effect worker entry support.

The first worker slice is intentionally observation-only. It can report
value-free eligible job coordinates, but it has no registered business handler
and therefore never claims or executes existing product jobs.
"""

from __future__ import annotations

import argparse
import json
import socket
from typing import Any, Mapping, Optional

from app.async_effects.contracts import resolve_async_effect_runtime_status
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
            }
        with request_uow(
            correlation_id="async-effect-worker-shadow",
            command_id="asyncEffectWorkerShadow",
        ):
            previews = repository_factory().preview_eligible(limit=limit)
        return {
            "mode": "shadow",
            "status": "observed",
            "reason": status.reason,
            "workerId": self._worker_id,
            "eligibleJobCount": len(previews),
            "eligibleJobTypes": sorted({item.job_type for item in previews}),
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
            }
        # No business handler is registered in this foundation slice. This is
        # deliberate: claiming a real job before its consumer contract exists
        # would create false completion evidence.
        return {
            "mode": "run",
            "status": "idle",
            "reason": "asyncEffectNoRunnableHandlers",
            "workerId": self._worker_id,
        }

    def _readiness(self) -> bool:
        probe = getattr(self._store, "readiness_probe", None)
        if not callable(probe):
            return False
        payload = probe()
        return isinstance(payload, Mapping) and payload.get("status") == "ready"


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
