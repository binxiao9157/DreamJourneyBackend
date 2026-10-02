"""Bounded, best-effort HTTP statistics; never a prerequisite for a response."""
from queue import Empty, Full, Queue
from threading import Lock, Thread

class OperationMetricDispatcher:
    def __init__(self, capacity=128):
        self._queue = Queue(maxsize=capacity)
        self._lock = Lock()
        self._thread = None
        self._accepting = True
        self._accepted = self._dropped = self._completed = self._failed = 0

    def start(self):
        with self._lock:
            self._accepting = True

    def submit(self, recorder, **fields):
        with self._lock:
            if not self._accepting:
                self._dropped += 1
                return False
            try:
                self._queue.put_nowait((recorder, fields))
            except Full:
                self._dropped += 1
                return False
            self._accepted += 1
            if self._thread is None or not self._thread.is_alive():
                self._thread = Thread(target=self._run, name="http-metric-sink", daemon=True)
                self._thread.start()
            return True

    def _run(self):
        while True:
            try:
                recorder, fields = self._queue.get(timeout=.1)
            except Empty:
                with self._lock:
                    if not self._accepting:
                        # Mark absent while holding the same lock used by submit/start.
                        self._thread = None
                        return
                continue
            try:
                result = recorder.record_attempt(**fields)
                with self._lock:
                    if isinstance(result, dict) and result.get("sinkOutcome") == "failed":
                        self._failed += 1
            except Exception:
                with self._lock:
                    self._failed += 1
            finally:
                with self._lock:
                    self._completed += 1
                self._queue.task_done()

    def wait_for_idle(self, timeout=1.0):
        with self._queue.all_tasks_done:
            return self._queue.all_tasks_done.wait_for(lambda: self._queue.unfinished_tasks == 0, timeout)

    def close(self, timeout=1.0):
        with self._lock:
            self._accepting = False
        drained = self.wait_for_idle(timeout)
        if not drained:
            while True:
                try:
                    self._queue.get_nowait()
                except Empty:
                    break
                with self._lock:
                    self._dropped += 1
                self._queue.task_done()
        return drained

    def snapshot(self):
        with self._lock:
            return dict(accepted=self._accepted, dropped=self._dropped, completed=self._completed,
                        failed=self._failed, queued=self._queue.qsize(), capacity=self._queue.maxsize)
