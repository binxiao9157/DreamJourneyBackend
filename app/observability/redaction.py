"""Value-free contracts for provider diagnostics and dry-run responses.

Provider calls may contain private memory text, media payloads, direct
identifiers, or credentials. These helpers make the externally observable
diagnostics deliberately metadata-only. They are not intended to reproduce an
upstream request for debugging.
"""

from __future__ import annotations

import re
from typing import Any, Mapping


PROVIDER_DRY_RUN_SCHEMA_VERSION = 1
PROVIDER_DRY_RUN_REDACTION_VERSION = "providerDryRun-v2"

_MACHINE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_EXCLUDED_INPUT_CLASSES = (
    "body",
    "prompt",
    "media",
    "base64",
    "secret",
    "directIdentity",
    "providerResponse",
)


def provider_dry_run_report(
    *,
    provider: str,
    capability: str,
    method: str,
    configured: bool,
    input_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a strictly allowlisted provider dry-run report.

    Callers may only supply fixed machine values, booleans, and numeric counts
    in ``input_summary``. This keeps user-entered text, IDs, URLs, and media
    out of debug responses by construction.
    """

    normalized_method = method.upper()
    if normalized_method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError("provider dry-run method is unsupported")

    return {
        "schemaVersion": PROVIDER_DRY_RUN_SCHEMA_VERSION,
        "redactionPolicyVersion": PROVIDER_DRY_RUN_REDACTION_VERSION,
        "provider": _machine_value(provider, "provider"),
        "capability": _machine_value(capability, "capability"),
        "configured": bool(configured),
        "transport": {
            "method": normalized_method,
            "credentialMode": "serverSide",
            "payloadIncluded": False,
        },
        "inputSummary": {
            key: _summary_value(value)
            for key, value in sorted(input_summary.items())
        },
        "excludedInputClasses": list(_EXCLUDED_INPUT_CLASSES),
    }


def provider_error_detail(
    *,
    code: str,
    provider: str,
    capability: str,
    retryable: bool,
    configured: bool,
) -> dict[str, Any]:
    """Build a provider error response without raw exception/provider text."""

    return {
        "code": _machine_value(code, "code"),
        "message": "Provider request is unavailable.",
        "provider": _machine_value(provider, "provider"),
        "capability": _machine_value(capability, "capability"),
        "configured": bool(configured),
        "retryable": bool(retryable),
        "redactionPolicyVersion": PROVIDER_DRY_RUN_REDACTION_VERSION,
    }


def _machine_value(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not _MACHINE_VALUE.fullmatch(normalized):
        raise ValueError(f"{field} must be a machine value")
    return normalized


def _summary_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, min(value, 2_147_483_647))
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("provider dry-run summary must be finite")
        return round(value, 3)
    if isinstance(value, str):
        return _machine_value(value, "inputSummary value")
    raise ValueError("provider dry-run summary contains an unsupported value")
