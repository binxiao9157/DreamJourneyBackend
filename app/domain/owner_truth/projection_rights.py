"""Vault-level rights revision contracts for Owner Truth projections.

The MemoryVersion projection is a derived compatibility read model.  A Source
or Memory change already invalidates it, but a separate consent or rights
change must invalidate it as well.  This module deliberately carries only
revision/state/hash evidence: it never stores consent text, identity proof,
or memory content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re

from .contracts import OwnerTruthContractError, require_nonblank


OWNER_TRUTH_PROJECTION_RIGHTS_SCHEMA_VERSION = "owner-truth-projection-rights-v1"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_IMPLICIT_RIGHTS_EVENT_HASH = "none"


class OwnerTruthProjectionRightsError(OwnerTruthContractError):
    """A Vault-level Projection rights fence is invalid or unavailable."""


class OwnerTruthProjectionRightsAccessDenied(OwnerTruthProjectionRightsError):
    """Only the active Vault Owner may record a Projection rights revision."""


class OwnerTruthProjectionRightsRevisionConflict(OwnerTruthProjectionRightsError):
    """A rights command does not match the immutable current revision."""


class ProjectionRightsState(str, Enum):
    """Minimal state needed to decide whether a Projection may expose data."""

    ACTIVE = "active"
    REVOKED = "revoked"


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthProjectionRightsError(
            "projection rights values must be JSON serializable"
        ) from exc


def _snapshot_hash(
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
    revision: int,
    state: ProjectionRightsState,
    event_hash: str,
) -> str:
    payload = {
        "schemaVersion": OWNER_TRUTH_PROJECTION_RIGHTS_SCHEMA_VERSION,
        "vaultId": vault_id,
        "ownerSubjectId": owner_subject_id,
        "authorityEpoch": authority_epoch,
        "revision": revision,
        "state": state.value,
        "eventHash": event_hash,
    }
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OwnerTruthProjectionRightsSnapshot:
    """Current value-free rights fence for one Vault authority epoch."""

    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    revision: int
    state: ProjectionRightsState
    event_hash: str = _IMPLICIT_RIGHTS_EVENT_HASH

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        try:
            object.__setattr__(self, "state", ProjectionRightsState(self.state))
        except ValueError as exc:
            raise OwnerTruthProjectionRightsError("projection rights state is unsupported") from exc
        if self.authority_epoch < 0:
            raise OwnerTruthProjectionRightsError("authority_epoch must not be negative")
        if self.revision < 0:
            raise OwnerTruthProjectionRightsError("rights revision must not be negative")
        normalized_event_hash = str(self.event_hash or "").strip()
        if self.revision == 0:
            if self.state is not ProjectionRightsState.ACTIVE:
                raise OwnerTruthProjectionRightsError(
                    "implicit projection rights state must be active"
                )
            if normalized_event_hash != _IMPLICIT_RIGHTS_EVENT_HASH:
                raise OwnerTruthProjectionRightsError(
                    "implicit projection rights event hash must be none"
                )
        elif not _SHA256_HEX.fullmatch(normalized_event_hash):
            raise OwnerTruthProjectionRightsError("rights event hash must be a SHA-256 hex digest")
        object.__setattr__(self, "event_hash", normalized_event_hash)

    @property
    def projection_allowed(self) -> bool:
        return self.state is ProjectionRightsState.ACTIVE

    @property
    def snapshot_hash(self) -> str:
        return _snapshot_hash(
            vault_id=self.vault_id,
            owner_subject_id=self.owner_subject_id,
            authority_epoch=self.authority_epoch,
            revision=self.revision,
            state=self.state,
            event_hash=self.event_hash,
        )

    def projection_fence(self) -> dict[str, object]:
        """Return only fields that may participate in a projection checkpoint."""

        return {
            "rightsRevision": self.revision,
            "rightsState": self.state.value,
            "rightsEventHash": self.event_hash,
            "rightsSnapshotHash": self.snapshot_hash,
        }


@dataclass(frozen=True)
class OwnerTruthProjectionRightsRevisionCommand:
    """One idempotent, value-free internal rights revision command.

    The public data-rights/consent ingress is intentionally not wired in this
    slice.  An authoritative future ingress must normalize its own evidence to
    ``event_hash`` before constructing this command; raw consent text and
    identity proof data must not cross this boundary.
    """

    command_id: str
    authority_epoch: int
    expected_revision: int
    state: ProjectionRightsState
    event_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        try:
            object.__setattr__(self, "state", ProjectionRightsState(self.state))
        except ValueError as exc:
            raise OwnerTruthProjectionRightsError("projection rights state is unsupported") from exc
        if self.authority_epoch < 0:
            raise OwnerTruthProjectionRightsError("authority_epoch must not be negative")
        if self.expected_revision < 0:
            raise OwnerTruthProjectionRightsError("expected rights revision must not be negative")
        normalized_event_hash = str(self.event_hash or "").strip()
        if not _SHA256_HEX.fullmatch(normalized_event_hash):
            raise OwnerTruthProjectionRightsError("rights event hash must be a SHA-256 hex digest")
        object.__setattr__(self, "event_hash", normalized_event_hash)

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OwnerTruthProjectionRightsRevisionResult:
    outcome: str
    snapshot: OwnerTruthProjectionRightsSnapshot


def implicit_projection_rights_snapshot(
    *,
    vault_id: str,
    owner_subject_id: str,
    authority_epoch: int,
) -> OwnerTruthProjectionRightsSnapshot:
    """Return the no-event default without treating absence as an approval event."""

    return OwnerTruthProjectionRightsSnapshot(
        vault_id=vault_id,
        owner_subject_id=owner_subject_id,
        authority_epoch=authority_epoch,
        revision=0,
        state=ProjectionRightsState.ACTIVE,
        event_hash=_IMPLICIT_RIGHTS_EVENT_HASH,
    )


__all__ = [
    "OWNER_TRUTH_PROJECTION_RIGHTS_SCHEMA_VERSION",
    "OwnerTruthProjectionRightsAccessDenied",
    "OwnerTruthProjectionRightsError",
    "OwnerTruthProjectionRightsRevisionCommand",
    "OwnerTruthProjectionRightsRevisionConflict",
    "OwnerTruthProjectionRightsRevisionResult",
    "OwnerTruthProjectionRightsSnapshot",
    "ProjectionRightsState",
    "implicit_projection_rights_snapshot",
]
