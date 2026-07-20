"""Restore-bound authorization evidence for dead-letter replay.

This is a G0 fence only. It does not validate a real authorization receipt,
write a replay, claim a job, or contact a Provider. It requires a separate
post-restore receipt before a prior dead-letter replay decision may proceed to
later durable wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import re
from typing import Mapping, Optional
from uuid import UUID, uuid5

from app.async_effects.dead_letter_effects import (
    DeadLetterAdmission,
    DeadLetterReplayCommand,
    DeadLetterReplayDecision,
    authorize_dead_letter_replay,
)


ASYNC_EFFECT_RECOVERY_EVIDENCE_SCHEMA_VERSION = "async-effect-recovery-evidence-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RECOVERY_NAMESPACE = UUID("1db21a9e-2d24-4a7d-9f7b-3f9a49880d87")


class AsyncEffectRecoveryEvidenceError(ValueError):
    """A recovery-bound replay command crossed a safety boundary."""


class DeadLetterRestoreReplayReason(str, Enum):
    AUTHORIZED = "authorized"
    BASE_REPLAY_REJECTED = "baseReplayRejected"
    RESTORE_OWNER_MISMATCH = "restoreOwnerMismatch"
    RESTORE_VAULT_MISMATCH = "restoreVaultMismatch"
    RESTORE_AUTHORITY_EPOCH_MISMATCH = "restoreAuthorityEpochMismatch"
    FRESH_RECOVERY_AUTHORIZATION_REQUIRED = "freshRecoveryAuthorizationRequired"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise AsyncEffectRecoveryEvidenceError(f"{field} must be an opaque identifier")
    return normalized


def _sha256_hex(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise AsyncEffectRecoveryEvidenceError(f"{field} must be a lowercase SHA-256 hex digest")
    return normalized


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AsyncEffectRecoveryEvidenceError(f"{field} must be a non-negative integer")
    return value


def _uuid(value: object, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise AsyncEffectRecoveryEvidenceError(f"{field} must be a UUID") from exc


@dataclass(frozen=True)
class DeadLetterRestoreReplayContext:
    """Authoritative coordinates supplied after a completed restore boundary."""

    restore_id: str
    owner_subject_id: str
    vault_id: str
    authority_epoch: int
    restore_checkpoint_hash: str
    recovery_authorization_receipt_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "restore_id", _identifier(self.restore_id, field="restore_id"))
        object.__setattr__(self, "owner_subject_id", _identifier(self.owner_subject_id, field="owner_subject_id"))
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "authority_epoch",
            _non_negative_int(self.authority_epoch, field="authority_epoch"),
        )
        object.__setattr__(
            self,
            "restore_checkpoint_hash",
            _sha256_hex(self.restore_checkpoint_hash, field="restore_checkpoint_hash"),
        )
        object.__setattr__(
            self,
            "recovery_authorization_receipt_hash",
            _sha256_hex(
                self.recovery_authorization_receipt_hash,
                field="recovery_authorization_receipt_hash",
            ),
        )


@dataclass(frozen=True)
class DeadLetterRestoreReplayDecision:
    """A replay decision that is additionally fenced by a restore checkpoint."""

    authorized: bool
    reason: DeadLetterRestoreReplayReason
    stable_key: str
    next_attempt: int
    replay_id: Optional[str]
    restore_id_hash: str
    restore_checkpoint_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.authorized, bool):
            raise AsyncEffectRecoveryEvidenceError("authorized must be a boolean")
        if not isinstance(self.reason, DeadLetterRestoreReplayReason):
            raise AsyncEffectRecoveryEvidenceError("restore replay reason is required")
        if self.authorized != (self.reason is DeadLetterRestoreReplayReason.AUTHORIZED):
            raise AsyncEffectRecoveryEvidenceError("authorization must agree with restore replay reason")
        object.__setattr__(self, "stable_key", _sha256_hex(self.stable_key, field="stable_key"))
        if isinstance(self.next_attempt, bool) or not isinstance(self.next_attempt, int) or self.next_attempt < 1:
            raise AsyncEffectRecoveryEvidenceError("next_attempt must be a positive integer")
        if self.replay_id is not None:
            object.__setattr__(self, "replay_id", _uuid(self.replay_id, field="replay_id"))
        if self.authorized != (self.replay_id is not None):
            raise AsyncEffectRecoveryEvidenceError("authorization must agree with replay_id presence")
        object.__setattr__(self, "restore_id_hash", _sha256_hex(self.restore_id_hash, field="restore_id_hash"))
        object.__setattr__(
            self,
            "restore_checkpoint_hash",
            _sha256_hex(self.restore_checkpoint_hash, field="restore_checkpoint_hash"),
        )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "authorized": self.authorized,
            "nextAttempt": self.next_attempt,
            "reason": self.reason.value,
            "replayId": self.replay_id,
            "restoreCheckpointHash": self.restore_checkpoint_hash,
            "restoreIdHash": self.restore_id_hash,
            "schemaVersion": ASYNC_EFFECT_RECOVERY_EVIDENCE_SCHEMA_VERSION,
            "stableKey": self.stable_key,
        }


def authorize_restored_dead_letter_replay(
    admission: DeadLetterAdmission,
    command: DeadLetterReplayCommand,
    restore_context: DeadLetterRestoreReplayContext,
) -> DeadLetterRestoreReplayDecision:
    """Require a distinct recovery receipt before later durable replay wiring."""

    if not isinstance(admission, DeadLetterAdmission):
        raise AsyncEffectRecoveryEvidenceError("dead-letter admission is required")
    if not isinstance(command, DeadLetterReplayCommand):
        raise AsyncEffectRecoveryEvidenceError("dead-letter replay command is required")
    if not isinstance(restore_context, DeadLetterRestoreReplayContext):
        raise AsyncEffectRecoveryEvidenceError("restore replay context is required")
    base = authorize_dead_letter_replay(admission, command)
    target = admission.intent.target
    restore_id_hash = sha256(
        f"async-effect-restore-v1|{restore_context.restore_id}".encode("utf-8")
    ).hexdigest()

    def rejected(reason: DeadLetterRestoreReplayReason) -> DeadLetterRestoreReplayDecision:
        return DeadLetterRestoreReplayDecision(
            authorized=False,
            reason=reason,
            stable_key=base.stable_key,
            next_attempt=base.next_attempt,
            replay_id=None,
            restore_id_hash=restore_id_hash,
            restore_checkpoint_hash=restore_context.restore_checkpoint_hash,
        )

    if not base.authorized:
        return rejected(DeadLetterRestoreReplayReason.BASE_REPLAY_REJECTED)
    if restore_context.owner_subject_id != target.owner_subject_id:
        return rejected(DeadLetterRestoreReplayReason.RESTORE_OWNER_MISMATCH)
    if restore_context.vault_id != target.vault_id:
        return rejected(DeadLetterRestoreReplayReason.RESTORE_VAULT_MISMATCH)
    if restore_context.authority_epoch != target.authority_epoch:
        return rejected(DeadLetterRestoreReplayReason.RESTORE_AUTHORITY_EPOCH_MISMATCH)
    if restore_context.recovery_authorization_receipt_hash == command.authorization_receipt_hash:
        return rejected(DeadLetterRestoreReplayReason.FRESH_RECOVERY_AUTHORIZATION_REQUIRED)
    replay_id = str(
        uuid5(
            _RECOVERY_NAMESPACE,
            "restore-dead-letter-replay:"
            f"{admission.dead_letter_id}:{base.stable_key}:{restore_context.restore_id}:"
            f"{restore_context.restore_checkpoint_hash}",
        )
    )
    return DeadLetterRestoreReplayDecision(
        authorized=True,
        reason=DeadLetterRestoreReplayReason.AUTHORIZED,
        stable_key=base.stable_key,
        next_attempt=base.next_attempt,
        replay_id=replay_id,
        restore_id_hash=restore_id_hash,
        restore_checkpoint_hash=restore_context.restore_checkpoint_hash,
    )


__all__ = [
    "ASYNC_EFFECT_RECOVERY_EVIDENCE_SCHEMA_VERSION",
    "AsyncEffectRecoveryEvidenceError",
    "DeadLetterRestoreReplayContext",
    "DeadLetterRestoreReplayDecision",
    "DeadLetterRestoreReplayReason",
    "authorize_restored_dead_letter_replay",
]
