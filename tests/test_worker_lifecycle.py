from __future__ import annotations

import signal
from threading import Event
from time import sleep
import unittest

from app.async_effects.worker_lifecycle import WorkerDrainController, WorkerLeaseHeartbeat


class WorkerDrainControllerTests(unittest.TestCase):
    def test_stop_request_is_value_free_and_handler_registration_is_reversible(self) -> None:
        prior_term = signal.getsignal(signal.SIGTERM)
        prior_int = signal.getsignal(signal.SIGINT)
        controller = WorkerDrainController()
        try:
            controller.install()
            controller._request_drain(signal.SIGTERM, None)
            self.assertTrue(controller.stop_requested)
        finally:
            controller.restore()
        self.assertEqual(signal.getsignal(signal.SIGTERM), prior_term)
        self.assertEqual(signal.getsignal(signal.SIGINT), prior_int)


class WorkerLeaseHeartbeatTests(unittest.TestCase):
    def test_heartbeat_runs_until_stop_and_does_not_leak_business_state(self) -> None:
        called = Event()
        calls: list[str] = []

        def heartbeat() -> None:
            calls.append("renewed")
            called.set()

        guard = WorkerLeaseHeartbeat(heartbeat=heartbeat, interval_seconds=0.01)
        guard.start()
        self.assertTrue(called.wait(timeout=1.0))
        guard.stop()
        call_count = len(calls)
        sleep(0.03)

        self.assertFalse(guard.failed)
        self.assertGreaterEqual(guard.heartbeat_count, 1)
        self.assertEqual(len(calls), call_count)

    def test_heartbeat_failure_is_rethrown_to_the_worker(self) -> None:
        called = Event()

        def heartbeat() -> None:
            called.set()
            raise RuntimeError("leaseLostForTest")

        guard = WorkerLeaseHeartbeat(heartbeat=heartbeat, interval_seconds=0.01)
        guard.start()
        self.assertTrue(called.wait(timeout=1.0))
        guard.stop()

        self.assertTrue(guard.failed)
        with self.assertRaisesRegex(RuntimeError, "leaseLostForTest"):
            guard.raise_if_failed()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
