#!/usr/bin/env python3
"""Verify an internal ClamAV sidecar without touching user media or COS.

The script is intended for an API container with the optional Compose profile
already started. It scans a fixed clean probe and the standard EICAR anti-virus
test string via clamd's internal TCP service. It prints only pass/fail metadata,
never the configured host, credentials, or scanned payloads.
"""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import Settings
from app.services.owner_truth_media_source_object import (
    ClamAVDaemonMediaContentSafetyScanner,
    clamav_daemon_runtime_ready,
)


_CLEAN_PROBE = b"dreamjourney-clamav-sidecar-clean-probe-v1"
_EICAR_TEST_PROBE = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    settings = Settings.from_env()
    host = str(settings.owner_truth_media_clamav_host or "").strip()
    _require(
        str(settings.owner_truth_media_content_safety_provider or "").strip().lower()
        == "clamav",
        "ClamAV content safety provider is not configured",
    )
    _require(host, "ClamAV sidecar host is not configured")
    _require(
        clamav_daemon_runtime_ready(
            host=host,
            port=settings.owner_truth_media_clamav_port,
            timeout_seconds=settings.owner_truth_media_clamav_timeout_seconds,
        ),
        "ClamAV sidecar did not complete a clean runtime probe",
    )

    scanner = ClamAVDaemonMediaContentSafetyScanner(
        host=host,
        port=settings.owner_truth_media_clamav_port,
        timeout_seconds=settings.owner_truth_media_clamav_timeout_seconds,
    )
    clean = scanner.inspect(
        media_kind="document",
        content_type="text/plain",
        payload=_CLEAN_PROBE,
    )
    _require(clean.status == "clean", "ClamAV clean probe was not accepted")

    blocked = scanner.inspect(
        media_kind="document",
        content_type="text/plain",
        payload=_EICAR_TEST_PROBE,
    )
    _require(
        blocked.status == "blocked"
        and blocked.reason_code == "contentSafetyScanBlocked",
        "ClamAV EICAR probe was not blocked",
    )
    print("Owner Truth ClamAV sidecar smoke passed (clean=clean, eicar=blocked)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"Owner Truth ClamAV sidecar smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1)
