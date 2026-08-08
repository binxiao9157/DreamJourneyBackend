"""Value-free automatic capability shutdown and recovery epochs.

Provider configuration says whether a lane *could* run. This module applies
short-lived operational evidence before that lane may be treated as ready. It
stores no user, object, request, credential, or Provider receipt identifiers.
An expired observation always fails closed, and a blocked lane receives a new
opaque readiness epoch only after fresh evidence makes it ready again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
import secrets
from threading import RLock
from typing import Callable, Dict, Optional


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class RuntimeCapabilityControlError(ValueError):
    """An operational observation crossed the value-free control boundary."""


class RuntimeCapabilityControlState(str, Enum):
    READY = "ready"
    BLOCKED = "blocked"
    STALE = "stale"


class RuntimeCapabilityBudgetState(str, Enum):
    NOT_APPLICABLE = "notApplicable"
    WITHIN_LIMIT = "withinLimit"
    EXCEEDED = "exceeded"
    UNKNOWN = "unknown"


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise RuntimeCapabilityControlError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeCapabilityControlError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise RuntimeCapabilityControlError(f"{field} must be an opaque identifier")
    return normalized


def _optional_bool(value: object, *, field: str) -> Optional[bool]:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise RuntimeCapabilityControlError(f"{field} must be boolean when present")
    return value


def _optional_non_negative_int(value: object, *, field: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeCapabilityControlError(
            f"{field} must be a non-negative integer when present"
        )
    return value


@dataclass(frozen=True)
class RuntimeCapabilityControlObservation:
    """One bounded observation used to decide an operational capability."""

    capability: str
    observation_id: str
    observed_at: datetime
    expires_at: datetime
    provider_ready: bool
    provider_reason: str
    scanner_ready: Optional[bool] = None
    worker_ready: Optional[bool] = None
    worker_evidence_id: Optional[str] = None
    backlog_count: Optional[int] = None
    backlog_limit: Optional[int] = None
    open_dead_letter_count: Optional[int] = None
    dead_letter_limit: Optional[int] = None
    deletion_reconciliation_healthy: Optional[bool] = None
    budget_state: RuntimeCapabilityBudgetState = RuntimeCapabilityBudgetState.NOT_APPLICABLE
    budget_required: bool = False
    kill_switch_active: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability", _identifier(self.capability, field="capability"))
        object.__setattr__(
            self,
            "observation_id",
            _identifier(self.observation_id, field="observation_id"),
        )
        observed = _utc(self.observed_at, field="observed_at")
        expires = _utc(self.expires_at, field="expires_at")
        if expires <= observed:
            raise RuntimeCapabilityControlError("expires_at must be after observed_at")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "expires_at", expires)
        if not isinstance(self.provider_ready, bool):
            raise RuntimeCapabilityControlError("provider_ready must be boolean")
        object.__setattr__(
            self,
            "provider_reason",
            _identifier(self.provider_reason, field="provider_reason"),
        )
        object.__setattr__(
            self,
            "scanner_ready",
            _optional_bool(self.scanner_ready, field="scanner_ready"),
        )
        object.__setattr__(
            self,
            "worker_ready",
            _optional_bool(self.worker_ready, field="worker_ready"),
        )
        if self.worker_evidence_id is not None:
            object.__setattr__(
                self,
                "worker_evidence_id",
                _identifier(self.worker_evidence_id, field="worker_evidence_id"),
            )
        for field in (
            "backlog_count",
            "backlog_limit",
            "open_dead_letter_count",
            "dead_letter_limit",
        ):
            object.__setattr__(
                self,
                field,
                _optional_non_negative_int(getattr(self, field), field=field),
            )
        if (self.backlog_count is None) != (self.backlog_limit is None):
            raise RuntimeCapabilityControlError(
                "backlog_count and backlog_limit must be supplied together"
            )
        if (self.open_dead_letter_count is None) != (self.dead_letter_limit is None):
            raise RuntimeCapabilityControlError(
                "open_dead_letter_count and dead_letter_limit must be supplied together"
            )
        object.__setattr__(
            self,
            "deletion_reconciliation_healthy",
            _optional_bool(
                self.deletion_reconciliation_healthy,
                field="deletion_reconciliation_healthy",
            ),
        )
        if not isinstance(self.budget_state, RuntimeCapabilityBudgetState):
            raise RuntimeCapabilityControlError("budget_state is invalid")
        if not isinstance(self.budget_required, bool):
            raise RuntimeCapabilityControlError("budget_required must be boolean")
        if not isinstance(self.kill_switch_active, bool):
            raise RuntimeCapabilityControlError("kill_switch_active must be boolean")


@dataclass(frozen=True)
class RuntimeCapabilityControlDecision:
    capability: str
    state: RuntimeCapabilityControlState
    reason: str
    operational_ready: bool
    readiness_epoch: Optional[str]
    observation_id: str
    observed_at: datetime
    expires_at: datetime
    backlog_count: Optional[int]
    open_dead_letter_count: Optional[int]
    budget_state: RuntimeCapabilityBudgetState

    def public_descriptor(self) -> Dict[str, object]:
        return {
            "capability": self.capability,
            "controlState": self.state.value,
            "reason": self.reason,
            "operationalReady": self.operational_ready,
            "readinessEpoch": self.readiness_epoch,
            "observationId": self.observation_id,
            "observedAt": self.observed_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "backlogCount": self.backlog_count,
            "openDeadLetterCount": self.open_dead_letter_count,
            "budgetState": self.budget_state.value,
        }


@dataclass(frozen=True)
class _StoredControlState:
    observation: RuntimeCapabilityControlObservation
    decision: RuntimeCapabilityControlDecision


class RuntimeCapabilityControlRegistry:
    """Thread-safe authority for short-lived operational decisions."""

    CONTRACT_VERSION = 1

    def __init__(self, *, epoch_factory: Optional[Callable[[], str]] = None) -> None:
        self._lock = RLock()
        self._states: Dict[str, _StoredControlState] = {}
        self._epoch_factory = epoch_factory or (
            lambda: f"rce-{secrets.token_hex(16)}"
        )

    def observe(
        self,
        observation: RuntimeCapabilityControlObservation,
    ) -> RuntimeCapabilityControlDecision:
        if not isinstance(observation, RuntimeCapabilityControlObservation):
            raise RuntimeCapabilityControlError("control observation is required")
        with self._lock:
            existing = self._states.get(observation.capability)
            if existing is not None:
                if observation.observed_at < existing.observation.observed_at:
                    return self._effective_decision(existing, now=observation.observed_at)
                if observation.observed_at == existing.observation.observed_at:
                    if observation == existing.observation:
                        return self._effective_decision(existing, now=observation.observed_at)
                    existing_decision = self._effective_decision(
                        existing,
                        now=observation.observed_at,
                    )
                    candidate_state, _ = self._evaluate(observation)
                    # Equal timestamps cannot establish ordering. Preserve the
                    # stricter decision so a concurrent healthy sample cannot
                    # overwrite an outage or kill-switch observation.
                    if (
                        not existing_decision.operational_ready
                        or candidate_state is RuntimeCapabilityControlState.READY
                    ):
                        return existing_decision

            state, reason = self._evaluate(observation)
            previous = (
                self._effective_decision(existing, now=observation.observed_at)
                if existing is not None
                else None
            )
            operational_ready = state is RuntimeCapabilityControlState.READY
            readiness_epoch: Optional[str] = None
            if operational_ready:
                if previous is not None and previous.operational_ready:
                    readiness_epoch = previous.readiness_epoch
                else:
                    readiness_epoch = _identifier(
                        self._epoch_factory(),
                        field="readiness_epoch",
                    )
            decision = RuntimeCapabilityControlDecision(
                capability=observation.capability,
                state=state,
                reason=reason,
                operational_ready=operational_ready,
                readiness_epoch=readiness_epoch,
                observation_id=observation.observation_id,
                observed_at=observation.observed_at,
                expires_at=observation.expires_at,
                backlog_count=observation.backlog_count,
                open_dead_letter_count=observation.open_dead_letter_count,
                budget_state=observation.budget_state,
            )
            self._states[observation.capability] = _StoredControlState(
                observation=observation,
                decision=decision,
            )
            return decision

    def decision(
        self,
        capability: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[RuntimeCapabilityControlDecision]:
        normalized = _identifier(capability, field="capability")
        instant = _utc(now or datetime.now(timezone.utc), field="now")
        with self._lock:
            state = self._states.get(normalized)
            if state is None:
                return None
            return self._effective_decision(state, now=instant)

    def public_descriptor(self, *, now: Optional[datetime] = None) -> Dict[str, object]:
        instant = _utc(now or datetime.now(timezone.utc), field="now")
        with self._lock:
            capabilities = {
                capability: self._effective_decision(state, now=instant).public_descriptor()
                for capability, state in sorted(self._states.items())
            }
        return {
            "contractVersion": self.CONTRACT_VERSION,
            "capabilities": capabilities,
        }

    @staticmethod
    def _evaluate(
        observation: RuntimeCapabilityControlObservation,
    ) -> tuple[RuntimeCapabilityControlState, str]:
        if observation.kill_switch_active:
            return (
                RuntimeCapabilityControlState.BLOCKED,
                "runtimeCapabilityKillSwitchActive",
            )
        if not observation.provider_ready:
            return RuntimeCapabilityControlState.BLOCKED, observation.provider_reason
        if observation.scanner_ready is False:
            return (
                RuntimeCapabilityControlState.BLOCKED,
                "runtimeCapabilityScannerUnavailable",
            )
        if observation.worker_ready is False:
            return (
                RuntimeCapabilityControlState.BLOCKED,
                "runtimeCapabilityWorkerUnavailable",
            )
        if (
            observation.backlog_count is not None
            and observation.backlog_limit is not None
            and observation.backlog_count > observation.backlog_limit
        ):
            return (
                RuntimeCapabilityControlState.BLOCKED,
                "runtimeCapabilityBacklogExceeded",
            )
        if (
            observation.open_dead_letter_count is not None
            and observation.dead_letter_limit is not None
            and observation.open_dead_letter_count > observation.dead_letter_limit
        ):
            return (
                RuntimeCapabilityControlState.BLOCKED,
                "runtimeCapabilityDeadLetterThresholdExceeded",
            )
        if observation.deletion_reconciliation_healthy is False:
            return (
                RuntimeCapabilityControlState.BLOCKED,
                "runtimeCapabilityDeletionReconciliationAnomaly",
            )
        if observation.budget_state is RuntimeCapabilityBudgetState.EXCEEDED:
            return (
                RuntimeCapabilityControlState.BLOCKED,
                "runtimeCapabilityBudgetExceeded",
            )
        if (
            observation.budget_required
            and observation.budget_state is RuntimeCapabilityBudgetState.UNKNOWN
        ):
            return (
                RuntimeCapabilityControlState.BLOCKED,
                "runtimeCapabilityBudgetUnknown",
            )
        return RuntimeCapabilityControlState.READY, "runtimeCapabilityReady"

    @staticmethod
    def _effective_decision(
        state: _StoredControlState,
        *,
        now: datetime,
    ) -> RuntimeCapabilityControlDecision:
        if now < state.observation.expires_at:
            return state.decision
        decision = state.decision
        return RuntimeCapabilityControlDecision(
            capability=decision.capability,
            state=RuntimeCapabilityControlState.STALE,
            reason="runtimeCapabilityObservationExpired",
            operational_ready=False,
            readiness_epoch=None,
            observation_id=decision.observation_id,
            observed_at=decision.observed_at,
            expires_at=decision.expires_at,
            backlog_count=decision.backlog_count,
            open_dead_letter_count=decision.open_dead_letter_count,
            budget_state=decision.budget_state,
        )


__all__ = [
    "RuntimeCapabilityBudgetState",
    "RuntimeCapabilityControlDecision",
    "RuntimeCapabilityControlError",
    "RuntimeCapabilityControlObservation",
    "RuntimeCapabilityControlRegistry",
    "RuntimeCapabilityControlState",
]
