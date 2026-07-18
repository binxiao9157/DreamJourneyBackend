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
    model_validator,
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
SourceCommit = Annotated[
    str,
    StringConstraints(min_length=7, max_length=64, pattern=r"^[0-9a-f]{7,64}$"),
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
    "verificationManifest",
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


OperationMetricOutcome = Literal[
    "succeeded",
    "failed",
    "timedOut",
    "cancelled",
    "deduplicated",
    "unknown",
    "feedbackMissing",
]
OperationMetricFeedbackState = Literal["received", "missing", "notApplicable"]


_OPERATION_METRIC_OUTCOME_STATES: dict[str, EvidenceState] = {
    "succeeded": "succeeded",
    "failed": "failed",
    "timedOut": "unknown",
    "cancelled": "cancelled",
    "deduplicated": "succeeded",
    "unknown": "unknown",
    "feedbackMissing": "unknown",
}


class OperationMetricEvidenceEvent(EvidenceEventBase):
    """One value-free attempt sample used to derive request/operation metrics."""

    type: Literal["operationMetric"] = "operationMetric"
    metricVersion: Literal[1] = 1
    requestIdHash: Digest
    attemptIdHash: Digest
    operation: MachineCode
    route: RouteCode
    outcome: OperationMetricOutcome
    feedbackState: OperationMetricFeedbackState
    latencyMs: Optional[int] = Field(default=None, ge=0, le=86_400_000)
    httpStatus: Optional[int] = Field(default=None, ge=100, le=599)

    @model_validator(mode="after")
    def require_outcome_state_alignment(self) -> "OperationMetricEvidenceEvent":
        expected_state = _OPERATION_METRIC_OUTCOME_STATES[self.outcome]
        if self.state != expected_state:
            raise ValueError("operation metric state does not match outcome")
        return self


class RightsEvidenceEvent(EvidenceEventBase):
    type: Literal["rights"] = "rights"
    right: MachineCode
    action: MachineCode
    authority: MachineCode
    receiptIdHash: Optional[Digest] = None


IncidentLifecycleState = Literal["open", "acknowledged", "fenced", "resolved"]


class IncidentEvidenceEvent(EvidenceEventBase):
    type: Literal["incident"] = "incident"
    incidentClass: MachineCode
    severity: Literal["info", "warning", "critical"]
    action: MachineCode
    surface: Optional[MachineCode] = None
    # Legacy incident observations intentionally remain valid. New lifecycle
    # events opt into the fields below and are replayed from the append-only
    # evidence sink by IncidentLifecycleService.
    operation: Optional[MachineCode] = None
    incidentId: Optional[MachineCode] = None
    incidentState: Optional[IncidentLifecycleState] = None
    owner: Optional[MachineCode] = None
    runbookId: Optional[MachineCode] = None
    requiredFenceActions: tuple[MachineCode, ...] = ()
    fenceActions: tuple[MachineCode, ...] = ()
    evidenceIdHashes: tuple[Digest, ...] = ()
    ackByAt: Optional[datetime] = None
    reopenedFrom: Optional[MachineCode] = None

    @field_validator("ackByAt")
    @classmethod
    def require_ack_deadline_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ackByAt must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def require_lifecycle_transition_contract(self) -> "IncidentEvidenceEvent":
        lifecycle_fields_present = any(
            value is not None
            for value in (
                self.operation,
                self.incidentId,
                self.incidentState,
                self.owner,
                self.runbookId,
                self.ackByAt,
                self.reopenedFrom,
            )
        ) or bool(
            self.requiredFenceActions
            or self.fenceActions
            or self.evidenceIdHashes
        )
        if not lifecycle_fields_present:
            return self

        expected = {
            "open": ("open", "started"),
            "ack": ("acknowledged", "observed"),
            "fence": ("fenced", "observed"),
            "resolve": ("resolved", "succeeded"),
            "reopen": ("open", "started"),
        }.get(self.action)
        if expected is None:
            raise ValueError("incident lifecycle action is unsupported")
        if self.operation != "incidentLifecycle":
            raise ValueError("incident lifecycle operation is required")
        if not self.incidentId or not self.incidentState or not self.owner:
            raise ValueError("incident lifecycle id, state, and owner are required")
        if (self.incidentState, self.state) != expected:
            raise ValueError("incident lifecycle state does not match action")
        if len(set(self.requiredFenceActions)) != len(self.requiredFenceActions):
            raise ValueError("requiredFenceActions must not contain duplicates")
        if len(set(self.fenceActions)) != len(self.fenceActions):
            raise ValueError("fenceActions must not contain duplicates")
        if len(set(self.evidenceIdHashes)) != len(self.evidenceIdHashes):
            raise ValueError("evidenceIdHashes must not contain duplicates")
        if self.action in {"open", "reopen"} and self.ackByAt is None:
            raise ValueError("incident lifecycle open requires ackByAt")
        if self.action == "reopen" and not self.reopenedFrom:
            raise ValueError("incident lifecycle reopen requires reopenedFrom")
        if self.action != "reopen" and self.reopenedFrom is not None:
            raise ValueError("reopenedFrom is only valid for reopen")
        if self.action == "fence" and not self.fenceActions:
            raise ValueError("incident lifecycle fence requires fenceActions")
        if self.action == "resolve" and not self.evidenceIdHashes:
            raise ValueError("incident lifecycle resolve requires evidenceIdHashes")
        if (
            self.severity == "critical"
            and self.action in {"open", "reopen"}
            and not self.requiredFenceActions
        ):
            raise ValueError("critical incident lifecycle open requires requiredFenceActions")
        return self


class ProviderCostEvidenceEvent(EvidenceEventBase):
    type: Literal["providerCost"] = "providerCost"
    provider: MachineCode
    capability: MachineCode
    providerRequestHash: Optional[Digest] = None
    unitType: MachineCode
    units: int = Field(ge=0)
    costMicros: Optional[int] = Field(default=None, ge=0)
    # Unknown is intentionally the default. A missing provider billing receipt
    # or approved rate card must never be silently promoted to a known cost.
    costSource: Literal["unknown", "providerMetered", "approvedRateCard"] = "unknown"
    rateCardVersion: Optional[MachineCode] = None
    latencyMs: Optional[int] = Field(default=None, ge=0, le=86_400_000)

    @model_validator(mode="after")
    def require_cost_provenance(self) -> "ProviderCostEvidenceEvent":
        if self.costSource == "approvedRateCard" and not self.rateCardVersion:
            raise ValueError("approvedRateCard requires rateCardVersion")
        if self.costSource != "approvedRateCard" and self.rateCardVersion is not None:
            raise ValueError("rateCardVersion is only valid for approvedRateCard")
        if self.costSource != "unknown" and self.costMicros is None:
            raise ValueError("known cost source requires costMicros")
        return self


EvidenceManifestStatus = Literal[
    "passed",
    "failed",
    "blocked",
    "notRun",
    "legacyUnverified",
]

_EVIDENCE_MANIFEST_STATUS_STATES: dict[str, EvidenceState] = {
    "passed": "succeeded",
    "failed": "failed",
    "blocked": "denied",
    "notRun": "unknown",
    "legacyUnverified": "unknown",
}


class EvidenceManifestEvent(EvidenceEventBase):
    """Immutable, value-free metadata for one verifiable acceptance package."""

    type: Literal["evidenceManifest"] = "evidenceManifest"
    manifestVersion: Literal[1] = 1
    manifestType: MachineCode
    sourceCommit: SourceCommit
    commandId: MachineCode
    sampleCount: int = Field(ge=0, le=1_000_000)
    sampleSetHash: Digest
    exclusionCodes: tuple[MachineCode, ...] = Field(default=(), max_length=32)
    sourceSchemaVersions: tuple[MachineCode, ...] = Field(min_length=1, max_length=32)
    artifactHashes: tuple[Digest, ...] = Field(min_length=1, max_length=32)
    windowStartedAt: datetime
    windowEndedAt: datetime
    issuedAt: datetime
    expiresAt: datetime
    issuer: MachineCode
    manifestStatus: EvidenceManifestStatus
    ownerLeaseHash: Optional[Digest] = None

    @field_validator("windowStartedAt", "windowEndedAt", "issuedAt", "expiresAt")
    @classmethod
    def require_manifest_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evidence manifest timestamps must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def require_manifest_integrity(self) -> "EvidenceManifestEvent":
        expected_state = _EVIDENCE_MANIFEST_STATUS_STATES[self.manifestStatus]
        if self.state != expected_state:
            raise ValueError("evidence manifest state does not match manifestStatus")
        if self.windowStartedAt > self.windowEndedAt:
            raise ValueError("evidence manifest window is invalid")
        if self.issuedAt < self.windowEndedAt:
            raise ValueError("evidence manifest issuedAt must follow its window")
        if self.expiresAt <= self.issuedAt:
            raise ValueError("evidence manifest expiresAt must follow issuedAt")
        if len(set(self.exclusionCodes)) != len(self.exclusionCodes):
            raise ValueError("evidence manifest exclusionCodes must not contain duplicates")
        if len(set(self.sourceSchemaVersions)) != len(self.sourceSchemaVersions):
            raise ValueError("evidence manifest sourceSchemaVersions must not contain duplicates")
        if len(set(self.artifactHashes)) != len(self.artifactHashes):
            raise ValueError("evidence manifest artifactHashes must not contain duplicates")
        return self


EvidenceEvent = Annotated[
    Union[
        OperationEvidenceEvent,
        OperationMetricEvidenceEvent,
        RightsEvidenceEvent,
        IncidentEvidenceEvent,
        ProviderCostEvidenceEvent,
        EvidenceManifestEvent,
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
