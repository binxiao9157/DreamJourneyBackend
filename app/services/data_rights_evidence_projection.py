"""Read-only, redacted projections for data-rights evidence.

The rights request and its execution/receipt records remain the only authority.
This module never writes to those stores or derives a completed result from an
account tombstone. It gives operations an honest, value-minimized view of what
has evidence and what remains unknown.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


DATA_RIGHTS_EVIDENCE_PROJECTION_SCHEMA_VERSION = 1

_TERMINAL_OUTCOMES = frozenset({"completed", "partial", "unsupported", "failed"})
_KNOWN_OUTCOMES = _TERMINAL_OUTCOMES | {"pending"}
_LAYERS = ("module", "object", "provider", "backup")


class DataRightsEvidenceProjectionError(ValueError):
    """Raised when a persisted rights summary cannot be projected safely."""


def build_data_rights_evidence_projection(
    summary: Mapping[str, Any],
    *,
    access_revocation_events: Iterable[Mapping[str, Any]] = (),
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    """Project immutable rights evidence without creating a second state machine.

    ``summary`` is the store-owned result from ``summarize_rights_request``.
    The original request scope remains intentionally hashed, so the report only
    claims an ``observedEvidenceOnly`` denominator. Missing evidence is always
    surfaced as ``unknown`` instead of being inferred from a soft deletion.
    """

    if not isinstance(summary, Mapping):
        raise DataRightsEvidenceProjectionError("rights summary must be an object")
    request = _mapping(summary.get("request"), "rights summary request")
    request_id = _text(request.get("id"), "request id")
    request_status = _known_or_unknown(request.get("status"))
    timestamp = _timestamp(now) if now is not None else datetime.now(timezone.utc)

    executions = _mappings(summary.get("executions"), "rights executions")
    receipts = _mappings(summary.get("receipts"), "rights receipts")
    resources, unmatched_receipt_count, invalid_timestamp_count = _resource_evidence(
        executions,
        receipts,
        now=timestamp,
    )
    access_revocation = _access_revocation_projection(
        access_revocation_events,
        now=timestamp,
    )
    physical_status = _aggregate_resource_status(resources)
    layer_summary = _layer_summary(resources)
    missing_receipt_count = sum(
        1
        for item in resources
        if "terminalExecutionMissingReceipt" in item["reasonCodes"]
    )
    unobserved_layers = [
        item["layer"]
        for item in layer_summary
        if int(item["observedResourceCount"]) == 0
    ]
    gap_summary = {
        "missingReceiptCount": missing_receipt_count,
        "unmatchedReceiptCount": unmatched_receipt_count,
        "invalidTimestampCount": invalid_timestamp_count,
        "unobservedLayers": unobserved_layers,
        "scopeCoverage": "unverifiableFromRedactedScopeHash",
    }

    return {
        "schemaVersion": DATA_RIGHTS_EVIDENCE_PROJECTION_SCHEMA_VERSION,
        "generatedAt": timestamp.isoformat(),
        "denominatorMode": "observedEvidenceOnly",
        "request": {
            "requestId": request_id,
            "action": _text_or_unknown(request.get("action")),
            "requestStatus": request_status,
            "createdAt": _text_or_none(request.get("createdAt")),
            "updatedAt": _text_or_none(request.get("updatedAt")),
        },
        "accessRevocation": access_revocation,
        "physicalCleanup": {
            "status": physical_status,
            "observedResourceCount": len(resources),
            "terminalEvidenceCount": sum(
                1 for item in resources if item["status"] in _TERMINAL_OUTCOMES
            ),
        },
        "resources": resources,
        "layerSummary": layer_summary,
        "gapSummary": gap_summary,
    }


def _resource_evidence(
    executions: Sequence[Mapping[str, Any]],
    receipts: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
) -> Tuple[List[Dict[str, Any]], int, int]:
    receipts_by_execution: Dict[Tuple[str, str], List[Mapping[str, Any]]] = {}
    for receipt in receipts:
        module_id = _text_or_none(receipt.get("moduleId"))
        execution_id_hash = _text_or_none(receipt.get("executionIdHash"))
        if not module_id or not execution_id_hash:
            continue
        receipts_by_execution.setdefault((module_id, execution_id_hash), []).append(receipt)

    resources: List[Dict[str, Any]] = []
    matched_receipt_ids: set[int] = set()
    invalid_timestamp_count = 0
    for execution in sorted(
        executions,
        key=lambda item: (
            _text_or_unknown(item.get("moduleId")),
            _text_or_unknown(item.get("resourceType")),
            _text_or_unknown(item.get("executionIdHash")),
        ),
    ):
        module_id = _text(execution.get("moduleId"), "execution module id")
        resource_type = _text(execution.get("resourceType"), "execution resource type")
        execution_id_hash = _text(
            execution.get("executionIdHash"),
            "execution id hash",
        )
        execution_outcome = _known_or_unknown(execution.get("outcome"))
        matching = receipts_by_execution.get((module_id, execution_id_hash), [])
        receipt = _latest_receipt(matching)
        if receipt is not None:
            matched_receipt_ids.add(id(receipt))
        receipt_outcome = _known_or_unknown(receipt.get("outcome")) if receipt else None
        reason_codes: List[str] = []
        if execution_outcome in _TERMINAL_OUTCOMES and receipt is None:
            status = "unknown"
            reason_codes.append("terminalExecutionMissingReceipt")
        elif receipt is not None and receipt_outcome != execution_outcome:
            status = "unknown"
            reason_codes.append("receiptOutcomeMismatch")
        else:
            status = execution_outcome
            if status == "pending":
                reason_codes.append("executionPending")
            elif status == "unknown":
                reason_codes.append("executionOutcomeUnknown")

        age_seconds, timestamp_invalid = _age_seconds(
            receipt.get("createdAt") if receipt is not None else execution.get("updatedAt"),
            now,
        )
        if timestamp_invalid:
            invalid_timestamp_count += 1
            reason_codes.append("evidenceTimestampInvalid")
        resources.append(
            {
                "layer": _layer_for(module_id, resource_type),
                "moduleId": module_id,
                "resourceType": resource_type,
                "executionOutcome": execution_outcome,
                "receiptOutcome": receipt_outcome,
                "receiptPresent": receipt is not None,
                "status": status,
                "ageSeconds": age_seconds,
                "reasonCodes": reason_codes,
            }
        )

    unmatched_receipt_count = sum(1 for receipt in receipts if id(receipt) not in matched_receipt_ids)
    return resources, unmatched_receipt_count, invalid_timestamp_count


def _access_revocation_projection(
    events: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
) -> Dict[str, Any]:
    candidates = [
        event
        for event in events
        if isinstance(event, Mapping)
        and str(event.get("eventType") or "") == "RightsAccessRevoked"
    ]
    if not candidates:
        return {
            "status": "unknown",
            "eventStatus": None,
            "observedAt": None,
            "ageSeconds": None,
            "reasonCodes": ["missingAccessRevocationEvidence"],
        }
    event = max(candidates, key=lambda item: _timestamp_sort_key(item.get("createdAt")))
    provider_capability_state = str(event.get("providerCapabilityState") or "")
    status = "revoked" if provider_capability_state == "revoked" else "unknown"
    reason_codes = (
        ["accessRevocationRecorded"]
        if status == "revoked"
        else ["accessRevocationStateUnverifiable"]
    )
    age_seconds, timestamp_invalid = _age_seconds(event.get("createdAt"), now)
    if timestamp_invalid:
        reason_codes.append("accessRevocationTimestampInvalid")
    return {
        "status": status,
        "eventStatus": _text_or_unknown(event.get("status")),
        "observedAt": _text_or_none(event.get("createdAt")),
        "ageSeconds": age_seconds,
        "reasonCodes": reason_codes,
    }


def _layer_summary(resources: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for layer in _LAYERS:
        entries = [item for item in resources if item["layer"] == layer]
        counts = Counter(str(item["status"]) for item in entries)
        output.append(
            {
                "layer": layer,
                "observedResourceCount": len(entries),
                "status": _aggregate_statuses([str(item["status"]) for item in entries]),
                "statusCounts": {key: counts[key] for key in sorted(counts)},
            }
        )
    return output


def _aggregate_resource_status(resources: Sequence[Mapping[str, Any]]) -> str:
    return _aggregate_statuses([str(item["status"]) for item in resources])


def _aggregate_statuses(statuses: Sequence[str]) -> str:
    if not statuses:
        return "unknown"
    values = set(statuses)
    if "unknown" in values:
        return "unknown"
    if "pending" in values:
        return "pending"
    if values == {"completed"}:
        return "completed"
    if values == {"failed"}:
        return "failed"
    if values == {"unsupported"}:
        return "unsupported"
    return "partial"


def _layer_for(module_id: str, resource_type: str) -> str:
    normalized_module = module_id.lower()
    normalized_resource = resource_type.lower()
    if normalized_module == "backupretention" or "backup" in normalized_resource:
        return "backup"
    if normalized_module == "objectstorage" or "object" in normalized_resource:
        return "object"
    if normalized_module.startswith("provider") or "provider" in normalized_resource:
        return "provider"
    return "module"


def _latest_receipt(receipts: Sequence[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    if not receipts:
        return None
    return max(receipts, key=lambda item: _timestamp_sort_key(item.get("createdAt")))


def _timestamp_sort_key(value: Any) -> Tuple[int, datetime]:
    try:
        return (1, _timestamp(value))
    except DataRightsEvidenceProjectionError:
        return (0, datetime.min.replace(tzinfo=timezone.utc))


def _age_seconds(value: Any, now: datetime) -> Tuple[Optional[int], bool]:
    try:
        observed = _timestamp(value)
    except DataRightsEvidenceProjectionError:
        return None, value is not None
    return max(0, int((now - observed).total_seconds())), False


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise DataRightsEvidenceProjectionError("timestamp must be ISO-8601") from exc
    else:
        raise DataRightsEvidenceProjectionError("timestamp is required")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataRightsEvidenceProjectionError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DataRightsEvidenceProjectionError(f"{field} must be an object")
    return value


def _mappings(value: Any, field: str) -> List[Mapping[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise DataRightsEvidenceProjectionError(f"{field} must be a list")
    return [_mapping(item, field) for item in value]


def _text(value: Any, field: str) -> str:
    normalized = _text_or_none(value)
    if normalized is None:
        raise DataRightsEvidenceProjectionError(f"{field} is required")
    return normalized


def _text_or_none(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _text_or_unknown(value: Any) -> str:
    return _text_or_none(value) or "unknown"


def _known_or_unknown(value: Any) -> str:
    normalized = _text_or_none(value)
    return normalized if normalized in _KNOWN_OUTCOMES else "unknown"


__all__ = [
    "DATA_RIGHTS_EVIDENCE_PROJECTION_SCHEMA_VERSION",
    "DataRightsEvidenceProjectionError",
    "build_data_rights_evidence_projection",
]
