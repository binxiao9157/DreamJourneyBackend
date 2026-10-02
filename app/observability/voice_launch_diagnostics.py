"""Best-effort, bounded diagnostics for the Live ticket route."""

import logging
from queue import Full, Queue
from threading import Lock, Thread
from typing import Optional


class VoiceLaunchDiagnosticQueue:
    def __init__(self, *, capacity: int = 128) -> None:
        self._queue: Queue[tuple[logging.Logger, str, str, int, Optional[int]]] = Queue(
            maxsize=capacity
        )
        self._lock = Lock()
        self._dropped = 0
        self._worker: Optional[Thread] = None

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped

    def submit(
        self,
        logger: logging.Logger,
        trace: str,
        stage: str,
        elapsed_ms: int,
        http_status: Optional[int],
    ) -> bool:
        try:
            self._queue.put_nowait((logger, trace, stage, elapsed_ms, http_status))
        except Full:
            self._mark_dropped()
            return False
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = Thread(target=self._run, daemon=True, name="voice-launch-log")
                self._worker.start()
        return True

    def wait_for_idle(self, timeout: float = 2.0) -> bool:
        with self._queue.all_tasks_done:
            return self._queue.all_tasks_done.wait_for(
                lambda: self._queue.unfinished_tasks == 0, timeout=timeout
            )

    def _mark_dropped(self) -> None:
        with self._lock:
            self._dropped += 1

    def _run(self) -> None:
        while True:
            logger, trace, stage, elapsed_ms, http_status = self._queue.get()
            try:
                logger.info(
                    "voiceLaunch trace=%s stage=%s elapsedMs=%d httpStatus=%s dropped=%d",
                    trace,
                    stage,
                    elapsed_ms,
                    http_status,
                    self.dropped_count,
                )
            except Exception:
                self._mark_dropped()
            finally:
                self._queue.task_done()
