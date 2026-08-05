"""Small process-lifecycle guard shared by typed async-effect workers.

The guard only controls process shutdown. It does not claim jobs, extend
leases, mutate business records, or turn the generic shadow worker into a
consumer. On SIGTERM/SIGINT a typed worker finishes its current ``run_once``
and exits before claiming another lease.
"""

from __future__ import annotations

import signal
from threading import Event, current_thread, main_thread
from types import FrameType
from typing import Optional


class WorkerDrainController:
    """Turn a process stop signal into a bounded drain request."""

    def __init__(self) -> None:
        self._stop_requested = Event()
        self._previous_handlers: dict[int, signal.Handlers] = {}

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested.is_set()

    def install(self) -> None:
        """Install handlers only for the CLI's main thread.

        Signal registration is intentionally a no-op in test/background
        contexts. That keeps runtime policy independent of import order.
        """

        if current_thread() is not main_thread():
            return
        for signum in (signal.SIGTERM, signal.SIGINT):
            self._previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._request_drain)

    def restore(self) -> None:
        if current_thread() is not main_thread():
            return
        for signum, handler in self._previous_handlers.items():
            signal.signal(signum, handler)
        self._previous_handlers.clear()

    def _request_drain(self, _signum: int, _frame: Optional[FrameType]) -> None:
        self._stop_requested.set()


__all__ = ["WorkerDrainController"]
