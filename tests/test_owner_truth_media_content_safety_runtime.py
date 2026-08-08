import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.owner_truth_media_source_object import clamav_scanner_runtime_ready


class ClamAVMediaContentSafetyRuntimeTests(unittest.TestCase):
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
