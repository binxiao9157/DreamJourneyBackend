"""Value-free worker readiness and backlog evidence for async effects.

This G0 module summarizes whether the default-disabled worker could legally
run. It never claims, requeues, persists, or executes an effect. In
particular, skipped, unknown, and expired observations are explicit negative
states rather than readiness evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Iterable, Optional

from app.async_effects.contracts import AsyncEffectRuntimeStatus
from app.async_effects.lease_repository import AsyncEffectJobPreview


ASYNC_EFFECT_READINESS_EVIDENCE_SCHEMA_VERSION = "async-effect-readiness-evidence-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class AsyncEffectReadinessEvidenceError(ValueError):
    """A readiness observation cannot safely become evidence."""


class AsyncEffectReadinessObservationState(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"
    EXPIRED = "expired"


class AsyncEffectReadinessManifestStatus(str, Enum):
    PASSED = "passed"
    BLOCKED = "blocked"
    NOT_RUN = "notRun"


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise AsyncEffectReadinessEvidenceError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AsyncEffectReadinessEvidenceError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise AsyncEffectReadinessEvidenceError(f"{field} must be an opaque identifier")
    return normalized


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AsyncEffectReadinessEvidenceError(f"{field} must be a non-negative integer")
    return value


def _parse_available_at(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise AsyncEffectReadinessEvidenceError("preview available_at must be an ISO timestamp") from exc
    return _utc(parsed, field="preview available_at")


def _worker_hash(worker_id: object) -> str:
    normalized = _identifier(worker_id, field="worker_id")
    return sha256(f"async-effect-worker-v1|{normalized}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AsyncEffectWorkerReadinessEvidence:
    """Immutable, value-free readiness/backlog observation."""

    observation_id: str
    observation_state: AsyncEffectReadinessObservationState
    reason: str
    observed_at: datetime
    expires_at: datetime
    runtime_enabled: bool
    worker_enabled: bool
    runnable_handler_count: int
    backlog_eligible_count: int
    backlog_job_type_counts: tuple[tuple[str, int], ...]
    oldest_eligible_age_seconds: Optional[int]
    worker_id_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _identifier(self.observation_id, field="observation_id"))
        if not isinstance(self.observation_state, AsyncEffectReadinessObservationState):
            raise AsyncEffectReadinessEvidenceError("observation_state is invalid")
        object.__setattr__(self, "reason", _identifier(self.reason, field="reason"))
        observed = _utc(self.observed_at, field="observed_at")
        expires = _utc(self.expires_at, field="expires_at")
        if expires <= observed:
            raise AsyncEffectReadinessEvidenceError("expires_at must be after observed_at")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)
        object.__setattr__(
            self,
            "runnable_handler_count",
            _non_negative_int(self.runnable_handler_count, field="runnable_handler_count"),
        )
        object.__setattr__(
            self,
            "backlog_eligible_count",
            _non_negative_int(self.backlog_eligible_count, field="backlog_eligible_count"),
        )
        if self.oldest_eligible_age_seconds is not None:
            object.__setattr__(
                self,
                "oldest_eligible_age_seconds",
                _non_negative_int(
                    self.oldest_eligible_age_seconds,
                    field="oldest_eligible_age_seconds",
                ),
            )
        normalized_counts: list[tuple[str, int]] = []
        for job_type, count in self.backlog_job_type_counts:
            normalized_counts.append(
                (
                    _identifier(job_type, field="backlog job type"),
                    _non_negative_int(count, field="backlog job type count"),
                )
            )
        if tuple(sorted(normalized_counts)) != tuple(normalized_counts):
            raise AsyncEffectReadinessEvidenceError("backlog_job_type_counts must be sorted")
        if sum(count for _, count in normalized_counts) != self.backlog_eligible_count:
            raise AsyncEffectReadinessEvidenceError("backlog_job_type_counts must match backlog_eligible_count")
        object.__setattr__(self, "backlog_job_type_counts", tuple(normalized_counts))
        normalized_hash = str(self.worker_id_hash or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized_hash):
            raise AsyncEffectReadinessEvidenceError("worker_id_hash must be a lowercase SHA-256 digest")
        object.__setattr__(self, "worker_id_hash", normalized_hash)

    def effective_state(self, *, now: Optional[datetime] = None) -> AsyncEffectReadinessObservationState:
        instant = _utc(now or datetime.now(timezone.utc), field="now")
        if instant >= self.expires_at:
            return AsyncEffectReadinessObservationState.EXPIRED
        return self.observation_state

    def is_ready(self, *, now: Optional[datetime] = None) -> bool:
        return self.effective_state(now=now) is AsyncEffectReadinessObservationState.READY

    def value_free_summary(self, *, now: Optional[datetime] = None) -> dict[str, object]:
        state = self.effective_state(now=now)
        reason = (
            "asyncEffectReadinessEvidenceExpired"
            if state is AsyncEffectReadinessObservationState.EXPIRED
            else self.reason
        )
        return {
            "schemaVersion": ASYNC_EFFECT_READINESS_EVIDENCE_SCHEMA_VERSION,
            "observationId": self.observation_id,
            "observationState": state.value,
            "reason": reason,
            "ready": state is AsyncEffectReadinessObservationState.READY,
            "observedAt": self.observed_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "runtimeEnabled": self.runtime_enabled,
            "workerEnabled": self.worker_enabled,
            "runnableHandlerCount": self.runnable_handler_count,
            "backlogEligibleCount": self.backlog_eligible_count,
            "backlogJobTypeCounts": dict(self.backlog_job_type_counts),
            "oldestEligibleAgeSeconds": self.oldest_eligible_age_seconds,
            "workerIdHash": self.worker_id_hash,
        }


@dataclass(frozen=True)
class AsyncEffectReadinessManifestPlan:
    """A value-free plan for the generic evidence manifest sink.

    It does not write evidence. Later durable wiring must pass this plan to the
    shared append-only manifest service. The mapping is intentionally strict:
    skipped, unknown, and expired observations are all ``notRun``.
    """

    manifest_id: str
    observation_id: str
    observation_state: AsyncEffectReadinessObservationState
    status: AsyncEffectReadinessManifestStatus
    reason: str
    artifact_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_id", _identifier(self.manifest_id, field="manifest_id"))
        object.__setattr__(self, "observation_id", _identifier(self.observation_id, field="observation_id"))
        if not isinstance(self.observation_state, AsyncEffectReadinessObservationState):
            raise AsyncEffectReadinessEvidenceError("observation_state is invalid")
        if not isinstance(self.status, AsyncEffectReadinessManifestStatus):
            raise AsyncEffectReadinessEvidenceError("manifest status is invalid")
        object.__setattr__(self, "reason", _identifier(self.reason, field="reason"))
        normalized_hash = str(self.artifact_hash or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized_hash):
            raise AsyncEffectReadinessEvidenceError("artifact_hash must be a lowercase SHA-256 digest")
        object.__setattr__(self, "artifact_hash", normalized_hash)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "schemaVersion": ASYNC_EFFECT_READINESS_EVIDENCE_SCHEMA_VERSION,
            "manifestId": self.manifest_id,
            "observationId": self.observation_id,
            "observationState": self.observation_state.value,
            "manifestStatus": self.status.value,
            "reason": self.reason,
            "artifactHash": self.artifact_hash,
        }


def build_async_effect_worker_readiness_evidence(
    *,
    runtime_status: AsyncEffectRuntimeStatus,
    worker_id: str,
    previews: Iterable[AsyncEffectJobPreview],
    runnable_handler_count: int,
    observed_at: datetime,
    expires_at: datetime,
    store_supported: bool = True,
    collection_error_code: Optional[str] = None,
) -> AsyncEffectWorkerReadinessEvidence:
    """Build a value-free snapshot without executing or claiming a job."""

    if not isinstance(runtime_status, AsyncEffectRuntimeStatus):
        raise AsyncEffectReadinessEvidenceError("runtime_status is required")
    observed = _utc(observed_at, field="observed_at")
    expires = _utc(expires_at, field="expires_at")
    normalized_handlers = _non_negative_int(
        runnable_handler_count,
        field="runnable_handler_count",
    )
    normalized_error = (
        _identifier(collection_error_code, field="collection_error_code")
        if collection_error_code is not None
        else None
    )
    type_counts: dict[str, int] = {}
    ages: list[int] = []
    for preview in previews:
        if not isinstance(preview, AsyncEffectJobPreview):
            raise AsyncEffectReadinessEvidenceError("previews must contain AsyncEffectJobPreview")
        job_type = _identifier(preview.job_type, field="preview job_type")
        available_at = _parse_available_at(preview.available_at)
        type_counts[job_type] = type_counts.get(job_type, 0) + 1
        ages.append(max(0, int((observed - available_at).total_seconds())))
    sorted_counts = tuple(sorted(type_counts.items()))
    if normalized_error is not None:
        state = AsyncEffectReadinessObservationState.UNKNOWN
        reason = normalized_error
    elif not store_supported:
        state = AsyncEffectReadinessObservationState.SKIPPED
        reason = "asyncEffectWorkerStoreUnsupported"
    elif not runtime_status.allowed:
        state = AsyncEffectReadinessObservationState.BLOCKED
        reason = _identifier(runtime_status.reason, field="runtime_status reason")
    elif normalized_handlers == 0:
        state = AsyncEffectReadinessObservationState.BLOCKED
        reason = "asyncEffectNoRunnableHandlers"
    else:
        state = AsyncEffectReadinessObservationState.READY
        reason = "asyncEffectRuntimeReady"
    worker_id_hash = _worker_hash(worker_id)
    identity_seed = {
        "backlogEligibleCount": len(ages),
        "backlogJobTypeCounts": sorted_counts,
        "expiresAt": expires.isoformat(),
        "observationState": state.value,
        "observedAt": observed.isoformat(),
        "reason": reason,
        "runnableHandlerCount": normalized_handlers,
        "workerIdHash": worker_id_hash,
    }
    digest = sha256(json.dumps(identity_seed, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return AsyncEffectWorkerReadinessEvidence(
        observation_id=f"aer-{digest[:32]}",
        observation_state=state,
        reason=reason,
        observed_at=observed,
        expires_at=expires,
        runtime_enabled=bool(runtime_status.enabled),
        worker_enabled=bool(runtime_status.worker_enabled),
        runnable_handler_count=normalized_handlers,
        backlog_eligible_count=len(ages),
        backlog_job_type_counts=sorted_counts,
        oldest_eligible_age_seconds=max(ages) if ages else None,
        worker_id_hash=worker_id_hash,
    )


def build_async_effect_readiness_manifest_plan(
    evidence: AsyncEffectWorkerReadinessEvidence,
    *,
    now: Optional[datetime] = None,
) -> AsyncEffectReadinessManifestPlan:
    """Map an observation to a future append-only evidence manifest plan."""

    if not isinstance(evidence, AsyncEffectWorkerReadinessEvidence):
        raise AsyncEffectReadinessEvidenceError("readiness evidence is required")
    state = evidence.effective_state(now=now)
    if state is AsyncEffectReadinessObservationState.READY:
        status = AsyncEffectReadinessManifestStatus.PASSED
    elif state is AsyncEffectReadinessObservationState.BLOCKED:
        status = AsyncEffectReadinessManifestStatus.BLOCKED
    else:
        status = AsyncEffectReadinessManifestStatus.NOT_RUN
    summary = evidence.value_free_summary(now=now)
    artifact_hash = sha256(
        json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest_seed = f"{evidence.observation_id}|{state.value}|{status.value}|{artifact_hash}"
    manifest_id = f"aem-{sha256(manifest_seed.encode('utf-8')).hexdigest()[:32]}"
    return AsyncEffectReadinessManifestPlan(
        manifest_id=manifest_id,
        observation_id=evidence.observation_id,
        observation_state=state,
        status=status,
        reason=str(summary["reason"]),
        artifact_hash=artifact_hash,
    )


__all__ = [
    "ASYNC_EFFECT_READINESS_EVIDENCE_SCHEMA_VERSION",
    "AsyncEffectReadinessEvidenceError",
    "AsyncEffectReadinessManifestPlan",
    "AsyncEffectReadinessManifestStatus",
    "AsyncEffectReadinessObservationState",
    "AsyncEffectWorkerReadinessEvidence",
    "build_async_effect_worker_readiness_evidence",
    "build_async_effect_readiness_manifest_plan",
]
