"""Pure data-rights request, execution, and receipt contracts.

This module deliberately has no FastAPI, store, or migration dependency.  It
is the G0 contract boundary for WI-S0-05-01; persistence and route integration
are separate work items.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple


DATA_RIGHTS_SCHEMA_VERSION = 1
EXECUTION_OUTCOMES = frozenset(
    {"pending", "completed", "partial", "unsupported", "failed"}
)
SUPPORTED_ACTIONS = frozenset(
    {"account.delete", "account.restore", "account.purge"}
)
_MODULE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")


class DataRightsContractError(ValueError):
    """Base error for invalid or conflicting data-rights commands."""


class DataRightsValidationError(DataRightsContractError):
    """A data-rights request or execution is not contract-valid."""


class DataRightsCommandConflict(DataRightsContractError):
    """A command id was reused with a different payload."""


class DataRightsExecutionConflict(DataRightsContractError):
    """An execution id was reused with different execution data."""


class DataRightsRequestNotFound(DataRightsContractError):
    """The requested opaque data-rights request id is unknown."""


def _canonical_json(value: Any, *, field: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DataRightsValidationError(f"{field} must be JSON serializable") from exc


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_value(value: Any, *, field: str) -> str:
    return _sha256(_canonical_json(value, field=field))


def _required_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataRightsValidationError(f"{field} is required")
    return value.strip()


def _required_proof(value: Any) -> str:
    if value is None:
        raise DataRightsValidationError("identity proof is required")
    if isinstance(value, str):
        if not value.strip():
            raise DataRightsValidationError("identity proof is required")
        return value.strip()
    if isinstance(value, (Mapping, list, tuple)) and not value:
        raise DataRightsValidationError("identity proof is required")
    return _canonical_json(value, field="identity proof")


def _timestamp(value: Optional[Any]) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise DataRightsValidationError("timestamp must include timezone")
        return value.astimezone(timezone.utc).isoformat()
    text = _required_text(value, field="timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataRightsValidationError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataRightsValidationError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _normalize_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise DataRightsValidationError("payload must be an object")
    normalized = dict(payload)
    _canonical_json(normalized, field="payload")

    action = _required_text(normalized.get("action"), field="payload.action")
    if action not in SUPPORTED_ACTIONS:
        raise DataRightsValidationError("payload.action is unsupported")
    scope = normalized.get("scope")
    if not isinstance(scope, list) or not scope:
        raise DataRightsValidationError("payload.scope must be a non-empty list")
    scope_values = [_required_text(item, field="payload.scope") for item in scope]
    if len(set(scope_values)) != len(scope_values):
        raise DataRightsValidationError("payload.scope must not contain duplicates")
    normalized["action"] = action
    normalized["scope"] = scope_values
    return normalized


def _aggregate_status(executions: Tuple["DataRightsExecution", ...]) -> str:
    if not executions:
        return "requested"
    outcomes = {execution.outcome for execution in executions}
    if "pending" in outcomes:
        return "pending"
    if len(outcomes) == 1:
        return next(iter(outcomes))
    return "partial"


def aggregate_data_rights_status(outcomes: Any) -> str:
    """Aggregate persisted module outcomes without exposing a boolean result."""

    if not isinstance(outcomes, (list, tuple, set, frozenset)):
        raise DataRightsValidationError("outcomes must be a collection")
    normalized = tuple(_required_text(outcome, field="outcome") for outcome in outcomes)
    if any(outcome not in EXECUTION_OUTCOMES for outcome in normalized):
        raise DataRightsValidationError("outcome is unsupported")
    return _aggregate_status(
        tuple(
            DataRightsExecution(
                module="persisted",
                execution_id_hash="persisted",
                outcome=outcome,
                evidence_id_hash=None,
                updated_at="1970-01-01T00:00:00+00:00",
            )
            for outcome in normalized
        )
    )


@dataclass(frozen=True)
class DataRightsExecution:
    """Current result for one module in a request."""

    module: str
    execution_id_hash: str
    outcome: str
    evidence_id_hash: Optional[str]
    updated_at: str


@dataclass(frozen=True)
class DataRightsRequest:
    """Hashed request state; raw subject, proof, and payload are not retained."""

    request_id: str
    command_id_hash: str
    payload_hash: str
    subject_hash: str
    identity_proof_hash: str
    action: str
    scope_hash: str
    executions: Tuple[DataRightsExecution, ...]
    created_at: str
    updated_at: str

    @property
    def status(self) -> str:
        return _aggregate_status(self.executions)

    def public_receipt(self) -> Dict[str, Any]:
        """Return a redacted receipt suitable for an API response or audit log."""

        return {
            "schemaVersion": DATA_RIGHTS_SCHEMA_VERSION,
            "requestId": self.request_id,
            "commandIdHash": self.command_id_hash,
            "payloadHash": self.payload_hash,
            "subjectHash": self.subject_hash,
            "identityProofPresent": True,
            "action": self.action,
            "scopeHash": self.scope_hash,
            "status": self.status,
            "executionCount": len(self.executions),
            "executions": [
                {
                    "module": execution.module,
                    "executionIdHash": execution.execution_id_hash,
                    "outcome": execution.outcome,
                    "evidenceIdHash": execution.evidence_id_hash,
                    "updatedAt": execution.updated_at,
                }
                for execution in self.executions
            ],
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass(frozen=True)
class DataRightsRequestResult:
    request: DataRightsRequest
    outcome: str


@dataclass(frozen=True)
class DataRightsExecutionResult:
    request: DataRightsRequest
    outcome: str


class DataRightsRequestAuthority:
    """Thread-safe in-memory authority used to validate the G0 contract.

    The lookup key is scoped by a hashed subject and command id.  Therefore a
    command id can be reused by different accounts without cross-account
    deduplication.  A module has one current execution result; recording a new
    execution id replaces that module's previous result so ``pending`` can
    progress to a terminal outcome without leaving stale pending state behind.
    """

    def __init__(self) -> None:
        from threading import RLock

        self._lock = RLock()
        self._requests: Dict[Tuple[str, str], DataRightsRequest] = {}
        self._request_ids: Dict[str, Tuple[str, str]] = {}

    def create_request(
        self,
        *,
        command_id: Any,
        payload: Any,
        subject_id: Any,
        identity_proof: Any,
        now: Optional[Any] = None,
    ) -> DataRightsRequestResult:
        command = _required_text(command_id, field="commandId")
        subject = _required_text(subject_id, field="subject")
        proof = _required_proof(identity_proof)
        normalized_payload = _normalize_payload(payload)
        payload_hash = _hash_value(normalized_payload, field="payload")
        command_id_hash = _sha256(command)
        subject_hash = _sha256(subject)
        lookup_key = (subject_hash, command_id_hash)
        timestamp = _timestamp(now)

        with self._lock:
            existing = self._requests.get(lookup_key)
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    raise DataRightsCommandConflict(
                        "commandId cannot be reused with a different payload"
                    )
                return DataRightsRequestResult(existing, "deduplicated")

            scope_hash = _hash_value(normalized_payload["scope"], field="payload.scope")
            request_id = "rr_" + _sha256(
                f"{subject_hash}:{command_id_hash}:{payload_hash}"
            )
            request = DataRightsRequest(
                request_id=request_id,
                command_id_hash=command_id_hash,
                payload_hash=payload_hash,
                subject_hash=subject_hash,
                identity_proof_hash=_hash_value(proof, field="identity proof"),
                action=normalized_payload["action"],
                scope_hash=scope_hash,
                executions=(),
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._requests[lookup_key] = request
            self._request_ids[request_id] = lookup_key
            return DataRightsRequestResult(request, "created")

    def get_request(self, request_id: Any) -> DataRightsRequest:
        request_key = _required_text(request_id, field="requestId")
        with self._lock:
            lookup_key = self._request_ids.get(request_key)
            if lookup_key is None:
                raise DataRightsRequestNotFound("data-rights request was not found")
            return self._requests[lookup_key]

    def record_execution(
        self,
        *,
        request_id: Any,
        module: Any,
        execution_id: Any,
        outcome: Any,
        evidence_id: Optional[Any] = None,
        now: Optional[Any] = None,
    ) -> DataRightsExecutionResult:
        request = self.get_request(request_id)
        module_name = _required_text(module, field="module")
        if _MODULE_ID_PATTERN.fullmatch(module_name) is None:
            raise DataRightsValidationError("module contains unsupported characters")
        execution = _required_text(execution_id, field="executionId")
        result = _required_text(outcome, field="outcome")
        if result not in EXECUTION_OUTCOMES:
            raise DataRightsValidationError("outcome is unsupported")
        evidence = None
        if evidence_id is not None:
            evidence = _required_text(evidence_id, field="evidenceId")
        timestamp = _timestamp(now)
        execution_record = DataRightsExecution(
            module=module_name,
            execution_id_hash=_sha256(execution),
            outcome=result,
            evidence_id_hash=None if evidence is None else _sha256(evidence),
            updated_at=timestamp,
        )

        with self._lock:
            current = self.get_request(request.request_id)
            by_module = {item.module: item for item in current.executions}
            existing = by_module.get(module_name)
            if existing is not None and existing.execution_id_hash == execution_record.execution_id_hash:
                if (
                    existing.outcome != execution_record.outcome
                    or existing.evidence_id_hash != execution_record.evidence_id_hash
                ):
                    raise DataRightsExecutionConflict(
                        "executionId cannot be reused with different execution data"
                    )
                return DataRightsExecutionResult(current, "deduplicated")

            by_module[module_name] = execution_record
            updated = replace(
                current,
                executions=tuple(by_module[name] for name in sorted(by_module)),
                updated_at=timestamp,
            )
            lookup_key = self._request_ids[current.request_id]
            self._requests[lookup_key] = updated
            return DataRightsExecutionResult(updated, "recorded" if existing is None else "updated")

    def public_receipt(self, request_id: Any) -> Dict[str, Any]:
        return self.get_request(request_id).public_receipt()


__all__ = [
    "DATA_RIGHTS_SCHEMA_VERSION",
    "EXECUTION_OUTCOMES",
    "SUPPORTED_ACTIONS",
    "DataRightsCommandConflict",
    "DataRightsContractError",
    "DataRightsExecution",
    "DataRightsExecutionConflict",
    "DataRightsExecutionResult",
    "DataRightsRequest",
    "DataRightsRequestAuthority",
    "DataRightsRequestNotFound",
    "DataRightsRequestResult",
    "DataRightsValidationError",
    "aggregate_data_rights_status",
]
