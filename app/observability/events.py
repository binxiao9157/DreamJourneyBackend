from __future__ import annotations

import hashlib
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


def validate_evidence_event(payload: object) -> EvidenceEvent:
    return _EVIDENCE_EVENT_ADAPTER.validate_python(payload)


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
