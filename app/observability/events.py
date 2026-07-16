from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
)


MachineCode = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:\-]*$",
    ),
]
Digest = Annotated[
    str,
    StringConstraints(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
]
RouteCode = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=180,
        pattern=r"^(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD) /[A-Za-z0-9_./{}*:\-]*$",
    ),
]
EvidenceState = Literal[
    "started",
    "succeeded",
    "failed",
    "denied",
    "observed",
    "cancelled",
    "unknown",
]
EvidenceRetentionClass = Literal[
    "rolloutObservation",
    "operationalTemporary",
    "rightsAudit",
    "incidentAudit",
    "providerCost",
    "legalHold",
]


class EvidenceEventBase(BaseModel):
    """Common envelope. Classification fields are codes, never free text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    eventId: MachineCode
    schemaVersion: Literal[1] = 1
    operationId: MachineCode
    correlationId: Optional[MachineCode] = None
    principalHash: Optional[Digest] = None
    resourceType: Optional[MachineCode] = None
    resourceIdHash: Optional[Digest] = None
    state: EvidenceState
    reason: MachineCode
    attempt: int = Field(default=1, ge=1, le=1000)
    occurredAt: datetime
    env: MachineCode
    build: MachineCode
    redactionVersion: Literal[1] = 1

    @field_validator("occurredAt")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurredAt must include a timezone")
        return value.astimezone(timezone.utc)


class OperationEvidenceEvent(EvidenceEventBase):
    type: Literal["operation"] = "operation"
    operation: MachineCode
    route: Optional[RouteCode] = None
    latencyMs: Optional[int] = Field(default=None, ge=0, le=86_400_000)
    policyVersion: Optional[MachineCode] = None
    clientBuild: Optional[int] = Field(default=None, ge=0)
    feature: Optional[MachineCode] = None
    decision: Optional[MachineCode] = None


class RightsEvidenceEvent(EvidenceEventBase):
    type: Literal["rights"] = "rights"
    right: MachineCode
    action: MachineCode
    authority: MachineCode
    receiptIdHash: Optional[Digest] = None


class IncidentEvidenceEvent(EvidenceEventBase):
    type: Literal["incident"] = "incident"
    incidentClass: MachineCode
    severity: Literal["info", "warning", "critical"]
    action: MachineCode
    surface: Optional[MachineCode] = None


class ProviderCostEvidenceEvent(EvidenceEventBase):
    type: Literal["providerCost"] = "providerCost"
    provider: MachineCode
    capability: MachineCode
    providerRequestHash: Optional[Digest] = None
    unitType: MachineCode
    units: int = Field(ge=0)
    costMicros: Optional[int] = Field(default=None, ge=0)
    latencyMs: Optional[int] = Field(default=None, ge=0, le=86_400_000)


EvidenceEvent = Annotated[
    Union[
        OperationEvidenceEvent,
        RightsEvidenceEvent,
        IncidentEvidenceEvent,
        ProviderCostEvidenceEvent,
    ],
    Field(discriminator="type"),
]
_EVIDENCE_EVENT_ADAPTER = TypeAdapter(EvidenceEvent)
_MACHINE_CODE_ADAPTER = TypeAdapter(MachineCode)
_RETENTION_CLASS_ADAPTER = TypeAdapter(EvidenceRetentionClass)
MAX_EVIDENCE_EVENT_BYTES = 16_384


class EvidenceEventConflict(RuntimeError):
    pass


class EvidenceEventPayloadTooLarge(ValueError):
    pass


def validate_evidence_event(payload: object) -> EvidenceEvent:
    return _EVIDENCE_EVENT_ADAPTER.validate_python(payload)


def canonicalize_evidence_event(
    payload: object,
    *,
    max_payload_bytes: int = MAX_EVIDENCE_EVENT_BYTES,
) -> tuple[EvidenceEvent, dict[str, object], str]:
    event = validate_evidence_event(payload)
    normalized = event.model_dump(mode="json")
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > max(1, max_payload_bytes):
        raise EvidenceEventPayloadTooLarge("evidence event exceeds payload limit")
    return event, normalized, hashlib.sha256(encoded).hexdigest()


def normalize_retention_class(value: str) -> str:
    _MACHINE_CODE_ADAPTER.validate_python(value)
    return _RETENTION_CLASS_ADAPTER.validate_python(value)


def normalize_machine_code(value: str) -> str:
    return _MACHINE_CODE_ADAPTER.validate_python(value)


def normalize_evidence_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("evidence timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def hash_evidence_identifier(value: str) -> str:
    return hashlib.sha256(f"evidence-id-v1|{value}".encode("utf-8")).hexdigest()


def map_release_policy_operation_event(
    *,
    feature: str,
    policy_version: str,
    client_build: int,
    decision: str,
    reason: str,
    route: str,
    occurred_at: datetime,
    environment: str,
) -> OperationEvidenceEvent:
    instant = occurred_at
    if instant.tzinfo is None or instant.utcoffset() is None:
        instant = instant.replace(tzinfo=timezone.utc)
    instant = instant.astimezone(timezone.utc)
    fingerprint = hashlib.sha256(
        "|".join(
            [
                feature,
                policy_version,
                str(max(0, client_build)),
                decision,
                reason,
                route,
                instant.isoformat(),
            ]
        ).encode("utf-8")
    ).hexdigest()[:32]
    state: EvidenceState
    if decision in {"allow", "typedRuntimeContract"}:
        state = "succeeded"
    elif decision == "deny":
        state = "denied"
    else:
        state = "observed"

    return OperationEvidenceEvent(
        eventId=f"evt_{fingerprint}",
        operationId=f"op_{fingerprint}",
        correlationId=None,
        principalHash=None,
        resourceType="releasePolicy",
        resourceIdHash=None,
        state=state,
        reason=reason,
        occurredAt=instant,
        env=environment,
        build=str(max(0, client_build)),
        operation="releasePolicyDecision",
        route=route,
        policyVersion=policy_version,
        clientBuild=max(0, client_build),
        feature=feature,
        decision=decision,
    )
