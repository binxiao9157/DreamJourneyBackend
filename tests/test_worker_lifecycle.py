from __future__ import annotations

import signal
import unittest

from app.async_effects.worker_lifecycle import WorkerDrainController


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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
