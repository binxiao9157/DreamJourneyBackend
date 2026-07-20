"""Value-free evidence for expired async-effect worker leases.

The worker remains disabled by default. This module observes expired leases
without reclaiming work, changing attempts, reissuing a Provider call, or
exposing any job/owner/resource coordinate. Durable recording is intentionally
kept in a separate append-only repository.
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
from app.async_effects.lease_repository import AsyncEffectExpiredLeasePreview


ASYNC_EFFECT_WORKER_LOSS_EVIDENCE_SCHEMA_VERSION = "async-effect-worker-loss-evidence-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AsyncEffectWorkerLossEvidenceError(ValueError):
    """An expired-lease observation cannot safely become durable evidence."""


class AsyncEffectWorkerLossObservationState(str, Enum):
    OBSERVED = "observed"
    CLEAR = "clear"
    SKIPPED = "skipped"
    UNKNOWN = "unknown"
    EXPIRED = "expired"


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise AsyncEffectWorkerLossEvidenceError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AsyncEffectWorkerLossEvidenceError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise AsyncEffectWorkerLossEvidenceError(f"{field} must be an opaque identifier")
    return normalized


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AsyncEffectWorkerLossEvidenceError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, *, field: str) -> int:
    normalized = _non_negative_int(value, field=field)
    if normalized < 1:
        raise AsyncEffectWorkerLossEvidenceError(f"{field} must be a positive integer")
    return normalized


def _sha256_hex(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise AsyncEffectWorkerLossEvidenceError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _parse_timestamp(value: object, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise AsyncEffectWorkerLossEvidenceError(f"{field} must be an ISO timestamp") from exc
    return _utc(parsed, field=field)


def _worker_hash(worker_id: object, *, field: str) -> str:
    normalized = _identifier(worker_id, field=field)
    return sha256(f"async-effect-worker-loss-v1|{normalized}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AsyncEffectWorkerLossEvidence:
    """Immutable aggregate of expired-lease observations with no raw IDs."""

    observation_id: str
    observation_state: AsyncEffectWorkerLossObservationState
    reason: str
    observed_at: datetime
    expires_at: datetime
    runtime_enabled: bool
    worker_enabled: bool
    expired_lease_count: int
    expired_job_type_counts: tuple[tuple[str, int], ...]
    oldest_expired_lease_age_seconds: Optional[int]
    lease_owner_hash_count: int
    observer_worker_id_hash: str
    artifact_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _identifier(self.observation_id, field="observation_id"))
        if not isinstance(self.observation_state, AsyncEffectWorkerLossObservationState):
            raise AsyncEffectWorkerLossEvidenceError("observation_state is invalid")
        object.__setattr__(self, "reason", _identifier(self.reason, field="reason"))
        observed = _utc(self.observed_at, field="observed_at")
        expires = _utc(self.expires_at, field="expires_at")
        if expires <= observed:
            raise AsyncEffectWorkerLossEvidenceError("expires_at must be after observed_at")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)
        count = _non_negative_int(self.expired_lease_count, field="expired_lease_count")
        object.__setattr__(self, "expired_lease_count", count)
        counts: dict[str, int] = {}
        for job_type, job_count in self.expired_job_type_counts:
            normalized_type = _identifier(job_type, field="expired job_type")
            normalized_count = _positive_int(job_count, field="expired job_type count")
            if normalized_type in counts:
                raise AsyncEffectWorkerLossEvidenceError("expired job_type counts must be unique")
            counts[normalized_type] = normalized_count
        normalized_counts = tuple(sorted(counts.items()))
        if sum(counts.values()) != count:
            raise AsyncEffectWorkerLossEvidenceError("expired job_type counts must equal expired_lease_count")
        object.__setattr__(self, "expired_job_type_counts", normalized_counts)
        owner_count = _non_negative_int(self.lease_owner_hash_count, field="lease_owner_hash_count")
        if owner_count > count:
            raise AsyncEffectWorkerLossEvidenceError("lease_owner_hash_count cannot exceed expired_lease_count")
        if count == 0:
            if self.oldest_expired_lease_age_seconds is not None or owner_count != 0:
                raise AsyncEffectWorkerLossEvidenceError("empty worker-loss evidence cannot carry lease age or owner count")
        else:
            if self.oldest_expired_lease_age_seconds is None:
                raise AsyncEffectWorkerLossEvidenceError("expired lease evidence requires its oldest age")
            _non_negative_int(
                self.oldest_expired_lease_age_seconds,
                field="oldest_expired_lease_age_seconds",
            )
            if owner_count < 1:
                raise AsyncEffectWorkerLossEvidenceError("expired lease evidence requires hashed owner count")
        object.__setattr__(self, "lease_owner_hash_count", owner_count)
        object.__setattr__(
            self,
            "observer_worker_id_hash",
            _sha256_hex(self.observer_worker_id_hash, field="observer_worker_id_hash"),
        )
        expected_artifact_hash = sha256(
            json.dumps(self._artifact_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        supplied_artifact_hash = str(self.artifact_hash or "").strip().lower()
        if supplied_artifact_hash and supplied_artifact_hash != expected_artifact_hash:
            raise AsyncEffectWorkerLossEvidenceError("artifact_hash does not match immutable observation")
        object.__setattr__(self, "artifact_hash", expected_artifact_hash)

    def _artifact_payload(self) -> dict[str, object]:
        return {
            "expiresAt": self.expires_at.isoformat(),
            "expiredJobTypeCounts": dict(self.expired_job_type_counts),
            "expiredLeaseCount": self.expired_lease_count,
            "leaseOwnerHashCount": self.lease_owner_hash_count,
            "observationId": self.observation_id,
            "observationState": self.observation_state.value,
            "observedAt": self.observed_at.isoformat(),
            "observerWorkerIdHash": self.observer_worker_id_hash,
            "oldestExpiredLeaseAgeSeconds": self.oldest_expired_lease_age_seconds,
            "reason": self.reason,
            "runtimeEnabled": self.runtime_enabled,
            "schemaVersion": ASYNC_EFFECT_WORKER_LOSS_EVIDENCE_SCHEMA_VERSION,
            "workerEnabled": self.worker_enabled,
        }

    def effective_state(self, *, now: Optional[datetime] = None) -> AsyncEffectWorkerLossObservationState:
        instant = _utc(now, field="now") if now is not None else datetime.now(timezone.utc)
        if instant >= self.expires_at:
            return AsyncEffectWorkerLossObservationState.EXPIRED
        return self.observation_state

    def requires_manual_review(self, *, now: Optional[datetime] = None) -> bool:
        return self.effective_state(now=now) is not AsyncEffectWorkerLossObservationState.CLEAR

    def value_free_summary(self, *, now: Optional[datetime] = None) -> dict[str, object]:
        state = self.effective_state(now=now)
        return {
            "schemaVersion": ASYNC_EFFECT_WORKER_LOSS_EVIDENCE_SCHEMA_VERSION,
            "observationId": self.observation_id,
            "observationState": state.value,
            "reason": self.reason,
            "observedAt": self.observed_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "runtimeEnabled": self.runtime_enabled,
            "workerEnabled": self.worker_enabled,
            "expiredLeaseCount": self.expired_lease_count,
            "expiredJobTypeCounts": dict(self.expired_job_type_counts),
            "oldestExpiredLeaseAgeSeconds": self.oldest_expired_lease_age_seconds,
            "leaseOwnerHashCount": self.lease_owner_hash_count,
            "observerWorkerIdHash": self.observer_worker_id_hash,
            "artifactHash": self.artifact_hash,
            "requiresManualReview": self.requires_manual_review(now=now),
        }


def build_async_effect_worker_loss_evidence(
    *,
    runtime_status: AsyncEffectRuntimeStatus,
    observer_worker_id: str,
    previews: Iterable[AsyncEffectExpiredLeasePreview],
    observed_at: datetime,
    expires_at: datetime,
    store_supported: bool = True,
    collection_error_code: Optional[str] = None,
) -> AsyncEffectWorkerLossEvidence:
    """Summarize expired leases without changing their ownership or state."""

    if not isinstance(runtime_status, AsyncEffectRuntimeStatus):
        raise AsyncEffectWorkerLossEvidenceError("runtime_status is required")
    observed = _utc(observed_at, field="observed_at")
    expires = _utc(expires_at, field="expires_at")
    normalized_error = (
        _identifier(collection_error_code, field="collection_error_code")
        if collection_error_code is not None
        else None
    )
    type_counts: dict[str, int] = {}
    owner_hashes: set[str] = set()
    ages: list[int] = []
    if normalized_error is None and store_supported:
        for preview in previews:
            if not isinstance(preview, AsyncEffectExpiredLeasePreview):
                raise AsyncEffectWorkerLossEvidenceError(
                    "previews must contain AsyncEffectExpiredLeasePreview"
                )
            lease_until = _parse_timestamp(preview.lease_until, field="preview lease_until")
            if lease_until > observed:
                raise AsyncEffectWorkerLossEvidenceError("expired lease preview is not yet expired")
            type_counts[preview.job_type] = type_counts.get(preview.job_type, 0) + 1
            owner_hashes.add(_worker_hash(preview.lease_owner, field="preview lease_owner"))
            ages.append(max(0, int((observed - lease_until).total_seconds())))
    if normalized_error is not None:
        state = AsyncEffectWorkerLossObservationState.UNKNOWN
        reason = normalized_error
    elif not store_supported:
        state = AsyncEffectWorkerLossObservationState.SKIPPED
        reason = "asyncEffectWorkerLossStoreUnsupported"
    elif ages:
        state = AsyncEffectWorkerLossObservationState.OBSERVED
        reason = "asyncEffectExpiredLeaseObserved"
    else:
        state = AsyncEffectWorkerLossObservationState.CLEAR
        reason = "asyncEffectNoExpiredLease"
    sorted_counts = tuple(sorted(type_counts.items()))
    observer_hash = _worker_hash(observer_worker_id, field="observer_worker_id")
    identity_seed = {
        "expiredJobTypeCounts": sorted_counts,
        "expiredLeaseCount": len(ages),
        "expiresAt": expires.isoformat(),
        "leaseOwnerHashCount": len(owner_hashes),
        "observationState": state.value,
        "observedAt": observed.isoformat(),
        "observerWorkerIdHash": observer_hash,
        "oldestExpiredLeaseAgeSeconds": max(ages) if ages else None,
        "reason": reason,
        "runtimeEnabled": bool(runtime_status.enabled),
        "workerEnabled": bool(runtime_status.worker_enabled),
    }
    digest = sha256(json.dumps(identity_seed, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return AsyncEffectWorkerLossEvidence(
        observation_id=f"aew-{digest[:32]}",
        observation_state=state,
        reason=reason,
        observed_at=observed,
        expires_at=expires,
        runtime_enabled=bool(runtime_status.enabled),
        worker_enabled=bool(runtime_status.worker_enabled),
        expired_lease_count=len(ages),
        expired_job_type_counts=sorted_counts,
        oldest_expired_lease_age_seconds=max(ages) if ages else None,
        lease_owner_hash_count=len(owner_hashes),
        observer_worker_id_hash=observer_hash,
    )


__all__ = [
    "ASYNC_EFFECT_WORKER_LOSS_EVIDENCE_SCHEMA_VERSION",
    "AsyncEffectWorkerLossEvidence",
    "AsyncEffectWorkerLossEvidenceError",
    "AsyncEffectWorkerLossObservationState",
    "build_async_effect_worker_loss_evidence",
]
