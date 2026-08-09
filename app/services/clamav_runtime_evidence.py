"""Bounded ClamAV engine/signature evidence for production readiness."""

from __future__ import annotations

import re
import socket
import subprocess
from typing import Callable, Optional

from app.core.config import Settings


_VERSION_PATTERN = re.compile(
    r"^ClamAV\s+(?P<engine>[A-Za-z0-9][A-Za-z0-9_.-]{0,63})/"
    r"(?P<signature>[0-9]{1,20})(?:/|$)"
)


def parse_clamav_version(value: object) -> Optional[dict[str, str]]:
    """Parse only non-sensitive version counters from Provider output."""

    normalized = str(value or "").strip().replace("\x00", "")
    match = _VERSION_PATTERN.match(normalized)
    if match is None:
        return None
    return {
        "engineVersion": match.group("engine"),
        "signatureVersion": match.group("signature"),
    }


def collect_clamav_runtime_evidence(
    settings: Settings,
    *,
    probe: Optional[Callable[[], str]] = None,
) -> dict[str, object]:
    """Return a value-free readiness descriptor without host or command output."""

    provider = str(settings.owner_truth_media_content_safety_provider or "").strip().lower()
    if provider != "clamav":
        return _blocked("clamavRuntimeNotConfigured")
    try:
        raw_version = (probe or (lambda: _probe_clamav(settings)))()
    except Exception:
        return _blocked("clamavRuntimeProbeFailed")
    parsed = parse_clamav_version(raw_version)
    if parsed is None:
        return _blocked("clamavRuntimeVersionInvalid")
    return {
        "ready": True,
        "reason": "clamavRuntimeReady",
        **parsed,
    }


def _probe_clamav(settings: Settings) -> str:
    host = str(settings.owner_truth_media_clamav_host or "").strip()
    # An operational report must not inherit the full media scan timeout.
    timeout = min(3, max(1, int(settings.owner_truth_media_clamav_timeout_seconds or 1)))
    if host:
        with socket.create_connection(
            (host, int(settings.owner_truth_media_clamav_port)),
            timeout=timeout,
        ) as connection:
            connection.settimeout(timeout)
            connection.sendall(b"zVERSION\0")
            return connection.recv(512).decode("utf-8", errors="replace")
    result = subprocess.run(
        ["clamscan", "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout


def _blocked(reason: str) -> dict[str, object]:
    return {
        "ready": False,
        "reason": reason,
        "engineVersion": None,
        "signatureVersion": None,
    }


__all__ = [
    "collect_clamav_runtime_evidence",
    "parse_clamav_version",
]
