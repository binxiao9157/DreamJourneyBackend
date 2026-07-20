"""Value-free legacy timer and callback inventory validation.

This module supports the G0 inventory slice of ``WI-S1-02-10`` only.  It
describes where legacy or runtime scheduling surfaces exist so a future
cutover can be planned safely.  It does not start a scheduler, claim work,
dispatch a business effect, call a Provider, or inspect host process state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


LEGACY_TIMER_CALLBACK_INVENTORY_SCHEMA = "dreamjourney.legacy-timer-callback-inventory.v1"

EXPECTED_ENTRY_IDS = frozenset(
    {
        "api-startup-store-lifecycle",
        "time-letter-api-direct-dispatch",
        "time-letter-cli-direct-dispatch",
        "time-letter-host-scheduler-documentation",
        "async-effect-scheduler-shadow",
        "digital-human-session-heartbeat-route",
        "provider-effect-external-callback-boundary",
        "operations-db-backup-timer",
        "operations-db-backup-retention-audit-timer",
        "operations-evidence-manifest-retention-timer",
    }
)

REQUIRED_ENTRY_FIELDS = frozenset(
    {
        "id",
        "surface",
        "executionMode",
        "directEffectStatus",
        "ownerBoundary",
        "generationFence",
        "cutoverState",
        "evidenceState",
        "sources",
    }
)

FORBIDDEN_VALUE_KEYS = frozenset(
    {
        "accessToken",
        "apiKey",
        "authorization",
        "body",
        "content",
        "credential",
        "headers",
        "payload",
        "rawValue",
        "secret",
        "secretKey",
        "token",
    }
)


class LegacyTimerCallbackInventoryError(ValueError):
    """The inventory is incomplete, unsafe, or no longer matches source."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise LegacyTimerCallbackInventoryError(message)


def _is_non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _assert_value_free(value: object, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _require(isinstance(key, str), f"inventory key must be text at {path}")
            _require(key not in FORBIDDEN_VALUE_KEYS, f"forbidden value field at {path}.{key}")
            _assert_value_free(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_value_free(child, path=f"{path}[{index}]")


def validate_inventory(payload: Mapping[str, Any]) -> dict[str, int]:
    """Validate value-free inventory structure without observing a live host."""

    _assert_value_free(payload)
    _require(
        payload.get("schemaVersion") == LEGACY_TIMER_CALLBACK_INVENTORY_SCHEMA,
        "unsupported legacy timer/callback inventory schema",
    )
    _require(_is_non_empty_text(payload.get("scope")), "inventory scope is required")
    entries = payload.get("entries")
    _require(isinstance(entries, list) and entries, "inventory entries are required")

    actual_ids: set[str] = set()
    source_count = 0
    host_unverified_count = 0
    external_blocked_count = 0
    legacy_direct_effect_count = 0
    for index, entry in enumerate(entries):
        _require(isinstance(entry, Mapping), f"entry {index} must be an object")
        missing = REQUIRED_ENTRY_FIELDS - set(entry)
        _require(not missing, f"entry {index} missing fields: {sorted(missing)}")
        entry_id = entry["id"]
        _require(_is_non_empty_text(entry_id), f"entry {index} id is required")
        _require(entry_id not in actual_ids, f"duplicate inventory entry: {entry_id}")
        actual_ids.add(entry_id)
        for field in REQUIRED_ENTRY_FIELDS - {"sources"}:
            _require(_is_non_empty_text(entry[field]), f"{entry_id}.{field} must be non-empty text")

        sources = entry["sources"]
        _require(isinstance(sources, list) and sources, f"{entry_id}.sources must be non-empty")
        for source_index, source in enumerate(sources):
            _require(isinstance(source, Mapping), f"{entry_id}.sources[{source_index}] must be an object")
            source_path = source.get("path")
            markers = source.get("markers")
            _require(
                _is_non_empty_text(source_path) and not str(source_path).startswith("/"),
                f"{entry_id}.sources[{source_index}].path must be a repository-relative path",
            )
            _require(
                isinstance(markers, list) and markers and all(_is_non_empty_text(marker) for marker in markers),
                f"{entry_id}.sources[{source_index}].markers must be non-empty text",
            )
            source_count += 1

        if entry["directEffectStatus"] == "LEGACY_DIRECT_BUSINESS_EFFECT":
            legacy_direct_effect_count += 1
            _require(
                entry["cutoverState"] == "NOT_AUTHORIZED",
                f"{entry_id} must remain NOT_AUTHORIZED until an approved cutover",
            )
        if entry["evidenceState"] == "HOST_UNVERIFIED_G2_REQUIRED":
            host_unverified_count += 1
        if entry["evidenceState"] == "EXTERNAL_G3_REQUIRED":
            external_blocked_count += 1

    _require(actual_ids == EXPECTED_ENTRY_IDS, "legacy timer/callback inventory entry set drifted")
    _require(host_unverified_count >= 1, "host timer state must remain explicitly unverified")
    _require(external_blocked_count >= 1, "external Provider callback boundary must remain explicit")
    _require(legacy_direct_effect_count >= 2, "legacy direct TimeLetter dispatch surfaces are missing")
    return {
        "entryCount": len(actual_ids),
        "sourceCount": source_count,
        "hostUnverifiedCount": host_unverified_count,
        "externalBlockedCount": external_blocked_count,
        "legacyDirectEffectCount": legacy_direct_effect_count,
    }


def validate_sources(repo_root: Path, payload: Mapping[str, Any]) -> None:
    """Require every catalogued marker to remain present in the checked source."""

    for entry in payload["entries"]:
        for source in entry["sources"]:
            source_path = repo_root / str(source["path"])
            _require(source_path.is_file(), f"missing inventory source: {source['path']}")
            text = source_path.read_text(encoding="utf-8")
            for marker in source["markers"]:
                _require(
                    str(marker) in text,
                    f"inventory marker drifted: {entry['id']} -> {source['path']} -> {marker}",
                )


def load_and_validate_inventory(inventory_path: Path, repo_root: Path) -> dict[str, int]:
    """Load, validate, and source-check the repository inventory."""

    import json

    _require(inventory_path.is_file(), f"missing inventory: {inventory_path}")
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    _require(isinstance(payload, Mapping), "inventory root must be an object")
    summary = validate_inventory(payload)
    validate_sources(repo_root, payload)
    return summary
