import unittest
import logging
from types import SimpleNamespace
from threading import Event, Thread
import time

import app.main as main
from app.observability.voice_launch_diagnostics import VoiceLaunchDiagnosticQueue


class _BlockedLogger:
    def __init__(self):
        self.started = Event()
        self.release = Event()
        self.lines = []

    def info(self, format_string, *args):
        self.started.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("synthetic sink timeout")
        self.lines.append((format_string, args))


class VoiceLaunchDiagnosticQueueTests(unittest.TestCase):
    def test_default_stage_logger_has_independent_output(self):
        stage_logger = main.VOICE_LAUNCH_STAGE_LOGGER
        self.assertFalse(stage_logger.propagate)
        self.assertTrue(stage_logger.handlers)
        self.assertTrue(any(
            isinstance(handler, logging.StreamHandler)
            for handler in stage_logger.handlers
        ))

    def test_slow_stage_handler_does_not_lock_ticket_warning_handler(self):
        class BlockingStageHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.entered = Event()
                self.release_emit = Event()

            def emit(self, _record):
                self.entered.set()
                self.release_emit.wait(timeout=2)

        class WarningHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.emitted = Event()

            def emit(self, _record):
                self.emitted.set()

        stage_logger = main.VOICE_LAUNCH_STAGE_LOGGER
        parent_logger = main.logger
        stage_handler = BlockingStageHandler()
        warning_handler = WarningHandler()
        diagnostic_owner = stage_logger if not stage_logger.propagate else parent_logger
        diagnostic_owner.addHandler(stage_handler)
        parent_logger.addHandler(warning_handler)
        previous_level = parent_logger.level
        parent_logger.setLevel(logging.WARNING)
        try:
            request = SimpleNamespace(state=SimpleNamespace(
                voice_launch_trace_id="synthetic-trace"
            ))
            main._log_voice_launch_stage(request, "inbound", time.perf_counter())
            self.assertTrue(stage_handler.entered.wait(timeout=2))
            warning_done = Event()

            def warn():
                parent_logger.warning("synthetic ticket deny")
                warning_done.set()

            warning_thread = Thread(target=warn, daemon=True)
            warning_thread.start()
            self.assertTrue(warning_done.wait(timeout=0.2))
            self.assertTrue(warning_handler.emitted.is_set())
        finally:
            stage_handler.release_emit.set()
            main.VOICE_LAUNCH_DIAGNOSTICS.wait_for_idle(timeout=2)
            diagnostic_owner.removeHandler(stage_handler)
            parent_logger.removeHandler(warning_handler)
            parent_logger.setLevel(previous_level)

    def test_route_stage_is_emitted_under_default_warning_parent_level(self):
        class RecordingHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.messages = []

            def emit(self, record):
                self.messages.append(record.getMessage())

        handler = RecordingHandler()
        previous_level = main.logger.level
        main.logger.setLevel(logging.WARNING)
        main.VOICE_LAUNCH_STAGE_LOGGER.addHandler(handler)
        try:
            request = SimpleNamespace(state=SimpleNamespace(
                voice_launch_trace_id="synthetic-trace"
            ))
            main._log_voice_launch_stage(request, "ticketStoreCommitted", time.perf_counter())
            self.assertTrue(main.VOICE_LAUNCH_DIAGNOSTICS.wait_for_idle(timeout=2))
            self.assertTrue(any(
                "stage=ticketStoreCommitted" in message for message in handler.messages
            ))
        finally:
            main.VOICE_LAUNCH_STAGE_LOGGER.removeHandler(handler)
            main.logger.setLevel(previous_level)

    def test_blocked_sink_does_not_block_ticket_and_full_queue_is_visible(self):
        queue = VoiceLaunchDiagnosticQueue(capacity=1)
        logger = _BlockedLogger()
        self.assertTrue(queue.submit(logger, "synthetic-trace", "inbound", 0, None))
        self.assertTrue(logger.started.wait(timeout=2))
        self.assertTrue(queue.submit(logger, "synthetic-trace", "ownerAuthorized", 1, None))
        self.assertFalse(queue.submit(logger, "synthetic-trace", "responseReady", 2, 200))
        self.assertEqual(queue.dropped_count, 1)
        logger.release.set()
        self.assertTrue(queue.wait_for_idle(timeout=2))
        self.assertEqual(len(logger.lines), 2)

    def test_logger_exception_is_counted_and_next_diagnostic_can_continue(self):
        class FailingLogger:
            def info(self, *_args):
                raise RuntimeError("synthetic logger failure")

        queue = VoiceLaunchDiagnosticQueue(capacity=2)
        self.assertTrue(queue.submit(FailingLogger(), "synthetic-trace", "inbound", 0, None))
        self.assertTrue(queue.wait_for_idle(timeout=2))
        self.assertEqual(queue.dropped_count, 1)
