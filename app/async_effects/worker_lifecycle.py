"""Small process-lifecycle guard shared by typed async-effect workers.

The guard only controls process shutdown. It does not claim jobs, extend
leases, mutate business records, or turn the generic shadow worker into a
consumer. On SIGTERM/SIGINT a typed worker finishes its current ``run_once``
and exits before claiming another lease.
"""

from __future__ import annotations

import signal
from threading import Event, Lock, Thread, current_thread, main_thread
from types import FrameType
from typing import Callable, Optional


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


class WorkerLeaseHeartbeat:
    """Keep one already-claimed worker lease current during an external call.

    The heartbeat callback must own its own transaction. This guard deliberately
    knows nothing about a job's business payload, provider result, or retry
    policy. If it cannot renew the current lease, the caller must treat the
    effect result as unknown rather than persisting a terminal outcome.
    """

    def __init__(
        self,
        *,
        heartbeat: Callable[[], None],
        interval_seconds: float,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._heartbeat = heartbeat
        self._interval_seconds = float(interval_seconds)
        self._stop_requested = Event()
        self._state_lock = Lock()
        self._failure: Exception | None = None
        self._heartbeat_count = 0
        self._thread: Thread | None = None

    @property
    def heartbeat_count(self) -> int:
        with self._state_lock:
            return self._heartbeat_count

    @property
    def failed(self) -> bool:
        with self._state_lock:
            return self._failure is not None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = Thread(
            target=self._run,
            name="dreamjourney-worker-lease-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=max(1.0, self._interval_seconds * 2))

    def raise_if_failed(self) -> None:
        with self._state_lock:
            failure = self._failure
        if failure is not None:
            raise failure

    def _run(self) -> None:
        while not self._stop_requested.wait(self._interval_seconds):
            try:
                self._heartbeat()
            except Exception as exc:
                with self._state_lock:
                    self._failure = exc
                self._stop_requested.set()
                return
            with self._state_lock:
                self._heartbeat_count += 1


__all__ = ["WorkerDrainController", "WorkerLeaseHeartbeat"]
