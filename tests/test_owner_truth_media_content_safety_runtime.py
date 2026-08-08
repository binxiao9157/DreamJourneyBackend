import socket
import struct
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.owner_truth_media_source_object import (
    ClamAVDaemonMediaContentSafetyScanner,
    build_media_content_safety_scanner,
    clamav_daemon_runtime_ready,
    clamav_scanner_runtime_ready,
)


class _FakeClamdConnection:
    def __init__(self, *replies: bytes) -> None:
        self.replies = list(replies)
        self.sent: list[bytes] = []
        self.timeout: object = None

    def __enter__(self) -> "_FakeClamdConnection":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    def settimeout(self, value: object) -> None:
        self.timeout = value

    def sendall(self, payload: bytes) -> None:
        self.sent.append(payload)

    def recv(self, size: int) -> bytes:
        if not self.replies:
            return b""
        reply = self.replies.pop(0)
        if len(reply) <= size:
            return reply
        self.replies.insert(0, reply[size:])
        return reply[:size]


class ClamAVMediaContentSafetyRuntimeTests(unittest.TestCase):
    def test_daemon_scanner_streams_clean_file_over_instream(self):
        connection = _FakeClamdConnection(b"stream: OK\0")
        scanner = ClamAVDaemonMediaContentSafetyScanner(
            host="clamav",
            port=3310,
            timeout_seconds=2,
        )

        with patch(
            "app.services.owner_truth_media_source_object.socket.create_connection",
            return_value=connection,
        ) as create_connection:
            verdict = scanner.inspect(
                media_kind="document",
                content_type="text/plain",
                payload=b"safe document",
            )

        self.assertEqual(verdict.status, "clean")
        self.assertEqual(verdict.provider, "clamav")
        self.assertEqual(connection.timeout, 2)
        self.assertEqual(
            b"".join(connection.sent),
            b"zINSTREAM\0"
            + struct.pack(">I", len(b"safe document"))
            + b"safe document"
            + struct.pack(">I", 0),
        )
        create_connection.assert_called_once_with(("clamav", 3310), timeout=2)

    def test_daemon_scanner_blocks_detected_content(self):
        connection = _FakeClamdConnection(b"stream: Eicar-Signature FOUND\0")
        scanner = ClamAVDaemonMediaContentSafetyScanner(host="clamav")

        with patch(
            "app.services.owner_truth_media_source_object.socket.create_connection",
            return_value=connection,
        ):
            verdict = scanner.inspect(
                media_kind="document",
                content_type="text/plain",
                payload=b"EICAR test fixture",
            )

        self.assertEqual(verdict.status, "blocked")
        self.assertEqual(verdict.reason_code, "contentSafetyScanBlocked")

    def test_daemon_failure_or_unknown_reply_fails_closed(self):
        scanner = ClamAVDaemonMediaContentSafetyScanner(host="clamav", timeout_seconds=2)
        with patch(
            "app.services.owner_truth_media_source_object.socket.create_connection",
            side_effect=socket.timeout(),
        ):
            unavailable = scanner.inspect(
                media_kind="document",
                content_type="text/plain",
                payload=b"safe document",
            )
        self.assertEqual(unavailable.status, "unavailable")
        self.assertEqual(unavailable.reason_code, "contentSafetyScannerUnavailable")

        with patch(
            "app.services.owner_truth_media_source_object.socket.create_connection",
            return_value=_FakeClamdConnection(b"stream: ERROR\0"),
        ):
            unknown = scanner.inspect(
                media_kind="document",
                content_type="text/plain",
                payload=b"safe document",
            )
        self.assertEqual(unknown.status, "unavailable")

    def test_daemon_runtime_probe_requires_a_clean_empty_scan(self):
        clean = _FakeClamdConnection(b"stream: OK\0")
        with patch(
            "app.services.owner_truth_media_source_object.socket.create_connection",
            return_value=clean,
        ):
            self.assertTrue(
                clamav_daemon_runtime_ready(host="clamav", port=3310, timeout_seconds=3)
            )
        self.assertEqual(
            b"".join(clean.sent),
            b"zINSTREAM\0"
            + struct.pack(">I", len(b"dreamjourney-clamav-runtime-probe-v1"))
            + b"dreamjourney-clamav-runtime-probe-v1"
            + struct.pack(">I", 0),
        )

        with patch(
            "app.services.owner_truth_media_source_object.socket.create_connection",
            return_value=_FakeClamdConnection(b"stream: Database not found ERROR\0"),
        ):
            self.assertFalse(clamav_daemon_runtime_ready(host="clamav"))

    def test_scanner_builder_prefers_explicit_daemon_host(self):
        scanner = build_media_content_safety_scanner(
            provider="clamav",
            environment="production",
            clamav_host="clamav",
            clamav_port=3310,
            clamav_timeout_seconds=7,
        )
        self.assertIsInstance(scanner, ClamAVDaemonMediaContentSafetyScanner)

    def test_missing_binary_is_not_ready(self):
        with patch(
            "app.services.owner_truth_media_source_object.shutil.which",
            return_value=None,
        ):
            self.assertFalse(clamav_scanner_runtime_ready())

    def test_clean_empty_probe_is_ready(self):
        with (
            patch(
                "app.services.owner_truth_media_source_object.shutil.which",
                return_value="/usr/bin/clamscan",
            ),
            patch(
                "app.services.owner_truth_media_source_object.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ) as run,
        ):
            self.assertTrue(clamav_scanner_runtime_ready(timeout_seconds=2))

        run.assert_called_once_with(
            ["/usr/bin/clamscan", "--no-summary", "-"],
            input=b"",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=2,
        )

    def test_nonzero_or_timeout_probe_fails_closed(self):
        with (
            patch(
                "app.services.owner_truth_media_source_object.shutil.which",
                return_value="/usr/bin/clamscan",
            ),
            patch(
                "app.services.owner_truth_media_source_object.subprocess.run",
                return_value=SimpleNamespace(returncode=2),
            ),
        ):
            self.assertFalse(clamav_scanner_runtime_ready())

        with (
            patch(
                "app.services.owner_truth_media_source_object.shutil.which",
                return_value="/usr/bin/clamscan",
            ),
            patch(
                "app.services.owner_truth_media_source_object.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="clamscan", timeout=5),
            ),
        ):
            self.assertFalse(clamav_scanner_runtime_ready())
