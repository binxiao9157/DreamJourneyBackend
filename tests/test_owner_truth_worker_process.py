from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.async_effects import owner_truth_candidate_extraction_worker
from app.async_effects import owner_truth_memory_projection_worker
from app.async_effects.owner_truth_candidate_extraction_worker import (
    _parser as candidate_worker_parser,
)
from app.async_effects.owner_truth_memory_projection_worker import (
    _parser as projection_worker_parser,
)
from app.core.config import Settings


ROOT = Path(__file__).resolve().parents[1]


class OwnerTruthWorkerProcessTests(unittest.TestCase):
    def test_typed_workers_accept_explicit_loop_mode_and_keep_once_compatible(self) -> None:
        for parser in (candidate_worker_parser(), projection_worker_parser()):
            once = parser.parse_args(["--once"])
            loop = parser.parse_args(["--loop", "--poll-seconds", "1.25"])

            self.assertTrue(once.once)
            self.assertFalse(once.loop)
            self.assertTrue(loop.loop)
            self.assertEqual(loop.poll_seconds, 1.25)
            with redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    parser.parse_args(["--once", "--loop"])

    def test_worker_poll_interval_from_environment_is_bounded(self) -> None:
        with patch.dict(os.environ, {"OWNER_TRUTH_WORKER_POLL_SECONDS": "0"}):
            self.assertEqual(Settings.from_env().owner_truth_worker_poll_seconds, 0.1)

    def test_loop_suppresses_repeated_idle_logs_and_closes_store(self) -> None:
        for module, worker_type in (
            (
                owner_truth_candidate_extraction_worker,
                owner_truth_candidate_extraction_worker.OwnerTruthCandidateExtractionWorkerRuntime,
            ),
            (
                owner_truth_memory_projection_worker,
                owner_truth_memory_projection_worker.OwnerTruthMemoryProjectionWorkerRuntime,
            ),
        ):
            fake_store = object()
            fake_worker = Mock()
            fake_worker.run_once.return_value = {
                "status": "idle",
                "reason": "noEligibleTypedJob",
            }
            output = StringIO()
            with patch.object(module.Settings, "from_env", return_value=Settings()), patch.object(
                module, "make_store", return_value=fake_store
            ), patch.object(module, "open_store") as open_store, patch.object(
                module, "close_store"
            ) as close_store, patch.object(
                module, worker_type.__name__, return_value=fake_worker
            ), patch.object(
                module, "sleep", side_effect=[None, KeyboardInterrupt]
            ), redirect_stdout(output):
                self.assertEqual(module.main(["--loop", "--poll-seconds", "0.1"]), 0)

            self.assertEqual(fake_worker.run_once.call_count, 2)
            self.assertEqual(len(output.getvalue().splitlines()), 1)
            open_store.assert_called_once_with(fake_store, wait=True)
            close_store.assert_called_once_with(fake_store)

    def test_compose_profile_keeps_typed_workers_out_of_default_api_startup(self) -> None:
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("owner-truth-candidate-extraction-worker:", compose)
        self.assertIn("owner-truth-memory-projection-worker:", compose)
        self.assertIn("- owner-truth-worker", compose)
        self.assertIn(
            '"app.async_effects.owner_truth_candidate_extraction_worker", "--loop"',
            compose,
        )
        self.assertIn(
            '"app.async_effects.owner_truth_memory_projection_worker", "--loop"',
            compose,
        )
        self.assertIn("restart: unless-stopped", compose)


if __name__ == "__main__":
    unittest.main()
