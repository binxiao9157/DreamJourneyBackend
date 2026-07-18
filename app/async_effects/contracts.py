"""Contracts for the V1 asynchronous-effect coordination kernel.

The effect kernel persists opaque identifiers and irreversible state evidence.
It deliberately receives only a canonical payload hash; it must never become a
second store for user content, provider credentials, or provider request bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import UUID, uuid5


ASYNC_EFFECT_SCHEMA_VERSION = "async-effect-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OPERATION_NAMESPACE = UUID("89ac236b-d004-44bb-af12-40589f95ac2d")


class AsyncEffectContractError(ValueError):
    """A caller supplied an invalid effect coordination contract."""


class AsyncEffectConflict(AsyncEffectContractError):
    """A stable effect key was reused with different immutable meaning."""


class AsyncEffectOperationState(str, Enum):
    ACCEPTED = "accepted"
    CANCEL_REQUESTED = "cancelRequested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


class AsyncEffectOutboxState(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DISPATCHED = "dispatched"
    CANCELLED = "cancelled"
    DEAD_LETTERED = "deadLettered"


class AsyncEffectJobState(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    RETRY_WAIT = "retryWait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class AsyncEffectBusinessOutcome(str, Enum):
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNKNOWN = "unknown"


def is_async_effect_store_ready(payload: object) -> bool:
    """Accept the supported in-memory and Postgres readiness probe contracts.

    Postgres probes deliberately return evidence reasons instead of an HTTP-style
    status field. An explicit status always takes precedence so a partially
    populated or degraded payload cannot accidentally enable a worker.
    """

    if not isinstance(payload, Mapping):
        return False
    status = payload.get("status")
    if status is not None:
        return status == "ready"
    return (
        payload.get("databaseReason") == "readWriteProbeSucceeded"
        and payload.get("schemaReason") == "migrationHeadVerified"
    )


def _require_nonblank(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise AsyncEffectContractError(f"{field} is required")
    return normalized


def _require_identifier(value: object, *, field: str) -> str:
    normalized = _require_nonblank(value, field=field)
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise AsyncEffectContractError(f"{field} must be an opaque identifier")
    return normalized


def _require_non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AsyncEffectContractError(f"{field} must be a non-negative integer")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    normalized = _require_nonblank(value, field=field).lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise AsyncEffectContractError(f"{field} must be a lowercase SHA-256 hex digest")
    return normalized


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise AsyncEffectContractError("effect stable key material must be serializable") from exc


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AsyncEffectTarget:
    """The authorized aggregate version that owns an effect intent."""

    owner_subject_id: str
    vault_id: str
    resource_type: str
    resource_id: str
    resource_version: int
    purpose: str
    authority_epoch: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "owner_subject_id",
            _require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "vault_id", _require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "resource_type",
            _require_identifier(self.resource_type, field="resource_type"),
        )
        object.__setattr__(
            self,
            "resource_id",
            _require_nonblank(self.resource_id, field="resource_id"),
        )
        object.__setattr__(
            self,
            "resource_version",
            _require_non_negative_int(self.resource_version, field="resource_version"),
        )
        object.__setattr__(self, "purpose", _require_identifier(self.purpose, field="purpose"))
        object.__setattr__(
            self,
            "authority_epoch",
            _require_non_negative_int(self.authority_epoch, field="authority_epoch"),
        )

    def stable_key_material(self) -> dict[str, object]:
        return {
            "authorityEpoch": self.authority_epoch,
            "ownerSubjectId": self.owner_subject_id,
            "purpose": self.purpose,
            "resourceId": self.resource_id,
            "resourceType": self.resource_type,
            "resourceVersion": self.resource_version,
            "schemaVersion": ASYNC_EFFECT_SCHEMA_VERSION,
            "vaultId": self.vault_id,
        }


@dataclass(frozen=True)
class AsyncEffectIntent:
    """A durable request for future work without a payload body.

    IDs are derived from `stable_key`, therefore retrying the same intent is
    naturally idempotent. A changed payload hash with the same key is a hard
    conflict and must be reconciled rather than silently re-enqueued.
    """

    operation_type: str
    target: AsyncEffectTarget
    payload_hash: str
    event_type: str | None = None
    job_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_type",
            _require_identifier(self.operation_type, field="operation_type"),
        )
        if not isinstance(self.target, AsyncEffectTarget):
            raise AsyncEffectContractError("target must be an AsyncEffectTarget")
        object.__setattr__(self, "payload_hash", _require_sha256(self.payload_hash, field="payload_hash"))
        event_type = self.event_type or f"{self.operation_type}.requested"
        job_type = self.job_type or self.operation_type
        object.__setattr__(self, "event_type", _require_identifier(event_type, field="event_type"))
        object.__setattr__(self, "job_type", _require_identifier(job_type, field="job_type"))

    @property
    def stable_key(self) -> str:
        return _sha256(
            _canonical_json(
                {
                    "operationType": self.operation_type,
                    "target": self.target.stable_key_material(),
                }
            )
        )

    @property
    def operation_id(self) -> str:
        return str(uuid5(_OPERATION_NAMESPACE, f"operation:{self.stable_key}"))

    @property
    def outbox_event_id(self) -> str:
        return str(uuid5(_OPERATION_NAMESPACE, f"outbox:{self.stable_key}"))

    @property
    def job_id(self) -> str:
        return str(uuid5(_OPERATION_NAMESPACE, f"job:{self.stable_key}"))

    @property
    def business_receipt_id(self) -> str:
        return str(uuid5(_OPERATION_NAMESPACE, f"business-receipt:{self.stable_key}"))

    @property
    def business_target_key(self) -> str:
        return _sha256(f"business-target:{self.stable_key}")

    def immutable_fingerprint(self) -> str:
        return _sha256(
            _canonical_json(
                {
                    "eventType": self.event_type,
                    "jobType": self.job_type,
                    "operationType": self.operation_type,
                    "payloadHash": self.payload_hash,
                    "stableKey": self.stable_key,
                    "target": self.target.stable_key_material(),
                }
            )
        )


@dataclass(frozen=True)
class EffectReceiptSummary:
    """Value-free client/server receipt summary for an accepted operation."""

    outcome: str
    operation_id: str
    outbox_event_id: str
    job_id: str
    business_receipt_id: str
    stable_key: str
    operation_state: AsyncEffectOperationState
    outbox_state: AsyncEffectOutboxState
    job_state: AsyncEffectJobState
    business_outcome: AsyncEffectBusinessOutcome

    def public_contract(self) -> dict[str, str]:
        return {
            "businessOutcome": self.business_outcome.value,
            "businessReceiptId": self.business_receipt_id,
            "jobId": self.job_id,
            "jobState": self.job_state.value,
            "operationId": self.operation_id,
            "operationState": self.operation_state.value,
            "outboxEventId": self.outbox_event_id,
            "outboxState": self.outbox_state.value,
            "outcome": self.outcome,
            "schemaVersion": ASYNC_EFFECT_SCHEMA_VERSION,
            "stableKey": self.stable_key,
        }


@dataclass(frozen=True)
class AsyncEffectRuntimeStatus:
    enabled: bool
    worker_enabled: bool
    allowed: bool
    reason: str


def resolve_async_effect_runtime_status(
    *,
    async_effect_v1_enabled: bool,
    worker_enabled: bool,
    schema_ready: bool,
) -> AsyncEffectRuntimeStatus:
    """Centralize the fail-closed rollout rule for future API/worker paths."""

    if not async_effect_v1_enabled:
        return AsyncEffectRuntimeStatus(
            enabled=False,
            worker_enabled=False,
            allowed=False,
            reason="asyncEffectV1Disabled",
        )
    if not schema_ready:
        return AsyncEffectRuntimeStatus(
            enabled=True,
            worker_enabled=False,
            allowed=False,
            reason="asyncEffectSchemaNotReady",
        )
    if not worker_enabled:
        return AsyncEffectRuntimeStatus(
            enabled=True,
            worker_enabled=False,
            allowed=False,
            reason="asyncEffectWorkerDisabled",
        )
    return AsyncEffectRuntimeStatus(
        enabled=True,
        worker_enabled=True,
        allowed=True,
        reason="asyncEffectRuntimeReady",
    )
