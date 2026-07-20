"""Value-free dead-letter admission and replay authorization contracts.

This G0 module does not write ``async_effects.dead_letters``, claim jobs, or
invoke a Provider. It fixes the data and authorization boundary required before
those later G2/G3 paths can be enabled: failed work can be described without a
payload body, and a replay cannot be inferred from a client retry alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import re
from typing import Mapping, Optional
from uuid import UUID, uuid5

from app.async_effects.contracts import AsyncEffectIntent, AsyncEffectJobState


DEAD_LETTER_SCHEMA_VERSION = "async-effect-dead-letter-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DEAD_LETTER_NAMESPACE = UUID("796ab4cb-6f40-4f30-87bb-a118f69d6a1c")


class DeadLetterContractError(ValueError):
    """A dead-letter admission or replay request crossed a safety boundary."""


class DeadLetterCause(str, Enum):
    POISON_PAYLOAD = "poisonPayload"
    MAX_ATTEMPTS_EXCEEDED = "maxAttemptsExceeded"
    PROVIDER_UNKNOWN = "providerUnknown"
    MANUAL_INTERVENTION_REQUIRED = "manualInterventionRequired"


class DeadLetterState(str, Enum):
    OPEN = "open"
    RECONCILED = "reconciled"
    DISCARDED = "discarded"


class DeadLetterReplayReason(str, Enum):
    AUTHORIZED = "authorized"
    DEAD_LETTER_MISMATCH = "deadLetterMismatch"
    ACTOR_OWNER_MISMATCH = "actorOwnerMismatch"
    OWNER_MISMATCH = "ownerMismatch"
    VAULT_MISMATCH = "vaultMismatch"
    AUTHORITY_EPOCH_MISMATCH = "authorityEpochMismatch"
    DEAD_LETTER_NOT_OPEN = "deadLetterNotOpen"
    PAYLOAD_CORRECTION_REQUIRED = "payloadCorrectionRequired"
    PROVIDER_RECONCILIATION_REQUIRED = "providerReconciliationRequired"
    MANUAL_INTERVENTION_REQUIRED = "manualInterventionRequired"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise DeadLetterContractError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise DeadLetterContractError(f"{field} must be a UUID") from exc


def _sha256_hex(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise DeadLetterContractError(f"{field} must be a lowercase SHA-256 hex digest")
    return normalized


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DeadLetterContractError(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DeadLetterContractError(f"{field} must be a non-negative integer")
    return value


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeadLetterAdmission:
    """An immutable, value-free description of a terminal failed job attempt."""

    dead_letter_id: str
    intent: AsyncEffectIntent
    job_state: AsyncEffectJobState
    attempt: int
    max_attempts: int
    cause: DeadLetterCause
    failure_hash: str
    last_receipt_hash: str
    state: DeadLetterState = DeadLetterState.OPEN

    def __post_init__(self) -> None:
        object.__setattr__(self, "dead_letter_id", _uuid(self.dead_letter_id, field="dead_letter_id"))
        if not isinstance(self.intent, AsyncEffectIntent):
            raise DeadLetterContractError("intent is required")
        if self.job_state not in {
            AsyncEffectJobState.FAILED,
            AsyncEffectJobState.UNKNOWN,
            AsyncEffectJobState.BLOCKED,
        }:
            raise DeadLetterContractError("dead-letter admission requires a terminal failed job state")
        object.__setattr__(self, "attempt", _positive_int(self.attempt, field="attempt"))
        object.__setattr__(self, "max_attempts", _positive_int(self.max_attempts, field="max_attempts"))
        if not isinstance(self.cause, DeadLetterCause):
            raise DeadLetterContractError("dead-letter cause is required")
        object.__setattr__(self, "failure_hash", _sha256_hex(self.failure_hash, field="failure_hash"))
        object.__setattr__(
            self,
            "last_receipt_hash",
            _sha256_hex(self.last_receipt_hash, field="last_receipt_hash"),
        )
        if not isinstance(self.state, DeadLetterState):
            raise DeadLetterContractError("dead-letter state is required")
        if self.cause is DeadLetterCause.MAX_ATTEMPTS_EXCEEDED and self.attempt < self.max_attempts:
            raise DeadLetterContractError("max-attempts dead letter requires attempt >= max_attempts")
        if self.cause is DeadLetterCause.PROVIDER_UNKNOWN and self.job_state is not AsyncEffectJobState.UNKNOWN:
            raise DeadLetterContractError("provider-unknown dead letter requires unknown job state")

    @property
    def stable_key(self) -> str:
        return self.intent.stable_key

    @property
    def next_action(self) -> str:
        if self.cause is DeadLetterCause.MAX_ATTEMPTS_EXCEEDED:
            return "authorizedReplayRequired"
        if self.cause is DeadLetterCause.POISON_PAYLOAD:
            return "payloadCorrectionRequired"
        if self.cause is DeadLetterCause.PROVIDER_UNKNOWN:
            return "providerReconciliationRequired"
        return "manualInterventionRequired"

    def value_free_summary(self) -> Mapping[str, object]:
        target = self.intent.target
        return {
            "attempt": self.attempt,
            "authorityEpoch": target.authority_epoch,
            "cause": self.cause.value,
            "deadLetterId": self.dead_letter_id,
            "jobId": self.intent.job_id,
            "jobState": self.job_state.value,
            "lastReceiptHash": self.last_receipt_hash,
            "maxAttempts": self.max_attempts,
            "nextAction": self.next_action,
            "operationId": self.intent.operation_id,
            "ownerDigest": _digest(target.owner_subject_id),
            "resourceIdHash": _digest(target.resource_id),
            "resourceType": target.resource_type,
            "resourceVersion": target.resource_version,
            "schemaVersion": DEAD_LETTER_SCHEMA_VERSION,
            "stableKey": self.stable_key,
            "state": self.state.value,
            "vaultDigest": _digest(target.vault_id),
        }


@dataclass(frozen=True)
class DeadLetterReplayCommand:
    """Server-only replay request with an already-issued authorization receipt."""

    dead_letter_id: str
    actor_subject_id: str
    owner_subject_id: str
    vault_id: str
    authority_epoch: int
    authorization_receipt_hash: str
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "dead_letter_id", _uuid(self.dead_letter_id, field="dead_letter_id"))
        object.__setattr__(self, "actor_subject_id", _identifier(self.actor_subject_id, field="actor_subject_id"))
        object.__setattr__(self, "owner_subject_id", _identifier(self.owner_subject_id, field="owner_subject_id"))
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative_int(self.authority_epoch, field="authority_epoch"),
        )
        object.__setattr__(
            self,
            "authorization_receipt_hash",
            _sha256_hex(self.authorization_receipt_hash, field="authorization_receipt_hash"),
        )
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))


@dataclass(frozen=True)
class DeadLetterReplayDecision:
    """Fail-closed replay decision; it does not re-enqueue or mutate a job."""

    authorized: bool
    reason: DeadLetterReplayReason
    stable_key: str
    next_attempt: int
    replay_id: Optional[str]

    def __post_init__(self) -> None:
        if not isinstance(self.authorized, bool):
            raise DeadLetterContractError("replay authorized must be a boolean")
        if not isinstance(self.reason, DeadLetterReplayReason):
            raise DeadLetterContractError("replay reason is required")
        if self.authorized != (self.reason is DeadLetterReplayReason.AUTHORIZED):
            raise DeadLetterContractError("replay authorization must agree with its reason")
        object.__setattr__(self, "stable_key", _sha256_hex(self.stable_key, field="stable_key"))
        object.__setattr__(self, "next_attempt", _positive_int(self.next_attempt, field="next_attempt"))
        if self.replay_id is not None:
            object.__setattr__(self, "replay_id", _uuid(self.replay_id, field="replay_id"))
        if self.authorized != (self.replay_id is not None):
            raise DeadLetterContractError("authorized replay must agree with replay_id presence")

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "authorized": self.authorized,
            "nextAttempt": self.next_attempt,
            "reason": self.reason.value,
            "replayId": self.replay_id,
            "schemaVersion": DEAD_LETTER_SCHEMA_VERSION,
            "stableKey": self.stable_key,
        }


def admit_dead_letter(
    *,
    intent: AsyncEffectIntent,
    job_state: AsyncEffectJobState,
    attempt: int,
    max_attempts: int,
    cause: DeadLetterCause,
    failure_hash: str,
    last_receipt_hash: str,
) -> DeadLetterAdmission:
    """Produce an immutable admission record without persisting it."""

    if not isinstance(intent, AsyncEffectIntent):
        raise DeadLetterContractError("intent is required")
    if not isinstance(job_state, AsyncEffectJobState):
        raise DeadLetterContractError("job state is required")
    if not isinstance(cause, DeadLetterCause):
        raise DeadLetterContractError("dead-letter cause is required")
    normalized_attempt = _positive_int(attempt, field="attempt")
    return DeadLetterAdmission(
        dead_letter_id=str(
            uuid5(
                _DEAD_LETTER_NAMESPACE,
                f"dead-letter:{intent.job_id}:{normalized_attempt}",
            )
        ),
        intent=intent,
        job_state=job_state,
        attempt=normalized_attempt,
        max_attempts=max_attempts,
        cause=cause,
        failure_hash=failure_hash,
        last_receipt_hash=last_receipt_hash,
    )


def authorize_dead_letter_replay(
    admission: DeadLetterAdmission,
    command: DeadLetterReplayCommand,
) -> DeadLetterReplayDecision:
    """Authorize only an owner-scoped retry for a max-attempts dead letter.

    This intentionally models an authorization *receipt*, but does not invent
    AuthZ. Later G2 wiring must verify that receipt against the real authority
    and persist the replay atomically with the new attempt.
    """

    if not isinstance(admission, DeadLetterAdmission):
        raise DeadLetterContractError("dead-letter admission is required")
    if not isinstance(command, DeadLetterReplayCommand):
        raise DeadLetterContractError("dead-letter replay command is required")

    target = admission.intent.target
    next_attempt = admission.attempt + 1

    def rejected(reason: DeadLetterReplayReason) -> DeadLetterReplayDecision:
        return DeadLetterReplayDecision(
            authorized=False,
            reason=reason,
            stable_key=admission.stable_key,
            next_attempt=next_attempt,
            replay_id=None,
        )

    if command.dead_letter_id != admission.dead_letter_id:
        return rejected(DeadLetterReplayReason.DEAD_LETTER_MISMATCH)
    if command.actor_subject_id != target.owner_subject_id:
        return rejected(DeadLetterReplayReason.ACTOR_OWNER_MISMATCH)
    if command.owner_subject_id != target.owner_subject_id:
        return rejected(DeadLetterReplayReason.OWNER_MISMATCH)
    if command.vault_id != target.vault_id:
        return rejected(DeadLetterReplayReason.VAULT_MISMATCH)
    if command.authority_epoch != target.authority_epoch:
        return rejected(DeadLetterReplayReason.AUTHORITY_EPOCH_MISMATCH)
    if admission.state is not DeadLetterState.OPEN:
        return rejected(DeadLetterReplayReason.DEAD_LETTER_NOT_OPEN)
    if admission.cause is DeadLetterCause.POISON_PAYLOAD:
        return rejected(DeadLetterReplayReason.PAYLOAD_CORRECTION_REQUIRED)
    if admission.cause is DeadLetterCause.PROVIDER_UNKNOWN:
        return rejected(DeadLetterReplayReason.PROVIDER_RECONCILIATION_REQUIRED)
    if admission.cause is DeadLetterCause.MANUAL_INTERVENTION_REQUIRED:
        return rejected(DeadLetterReplayReason.MANUAL_INTERVENTION_REQUIRED)

    return DeadLetterReplayDecision(
        authorized=True,
        reason=DeadLetterReplayReason.AUTHORIZED,
        stable_key=admission.stable_key,
        next_attempt=next_attempt,
        replay_id=str(
            uuid5(
                _DEAD_LETTER_NAMESPACE,
                f"dead-letter-replay:{admission.dead_letter_id}:{admission.stable_key}",
            )
        ),
    )


__all__ = [
    "DEAD_LETTER_SCHEMA_VERSION",
    "DeadLetterAdmission",
    "DeadLetterCause",
    "DeadLetterContractError",
    "DeadLetterReplayCommand",
    "DeadLetterReplayDecision",
    "DeadLetterReplayReason",
    "DeadLetterState",
    "admit_dead_letter",
    "authorize_dead_letter_replay",
]
