"""Default-deny one-way Public Projector contract.

This module models an internal event boundary for a future public store. It
accepts only immutable PublicationVersion event metadata and emits no readable
copy, query response, object operation, index operation, route, or side
effect. Every evaluated event remains blocked until the required gates approve
an actual projector and a separately isolated public store.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from uuid import UUID


PUBLICATION_PUBLIC_PROJECTOR_G0_SCHEMA_VERSION = "publication-public-projector-g0-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class PublicationPublicProjectorError(ValueError):
    """Raised when a synthetic public projector event is malformed."""


class PublicationProjectionEventKind(str, Enum):
    PUBLISHED = "published"
    SUSPENDED = "suspended"
    WITHDRAWN = "withdrawn"


class PublicationProjectionInputSource(str, Enum):
    PUBLICATION_VERSION_EVENT = "publicationVersionEvent"
    PRIVATE_MEMORY_REPOSITORY = "privateMemoryRepository"
    PRIVATE_SEARCH_PROJECTION = "privateSearchProjection"
    LEGACY_GUEST_INDEX = "legacyGuestIndex"


class PublicationProjectorState(str, Enum):
    PENDING_INDEX = "pendingIndex"
    SUSPENDED = "suspended"
    WITHDRAWN = "withdrawn"


class PublicationProjectorDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    SCOPE_MISMATCH = "scope_mismatch"
    PRIVATE_DEPENDENCY_REJECTED = "private_dependency_rejected"
    EXTERNAL_PROVIDER_GATE_REQUIRED = "external_provider_gate_required"
    DUPLICATE_OR_OUT_OF_ORDER = "duplicate_or_out_of_order"
    EVENT_GAP = "event_gap"
    SUSPEND_OR_WITHDRAW = "suspend_or_withdraw"
    POLICY_DISABLED = "policy_disabled"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise PublicationPublicProjectorError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise PublicationPublicProjectorError(f"{field} must be a UUID") from exc


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise PublicationPublicProjectorError(f"{field} must be a SHA-256 digest")
    return normalized


def _positive_int(value: object, *, field: str, zero_allowed: bool = False) -> int:
    minimum = 0 if zero_allowed else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PublicationPublicProjectorError(f"{field} must be at least {minimum}")
    return value


def _hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PublicationProjectorCheckpoint:
    """Opaque monotonic cursor for one private publication authority scope."""

    publication_id: str
    vault_id: str
    last_event_sequence: int
    state: PublicationProjectorState = PublicationProjectorState.PENDING_INDEX

    def __post_init__(self) -> None:
        object.__setattr__(self, "publication_id", _uuid(self.publication_id, field="publication_id"))
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "last_event_sequence",
            _positive_int(self.last_event_sequence, field="last_event_sequence", zero_allowed=True),
        )
        object.__setattr__(self, "state", PublicationProjectorState(self.state))


@dataclass(frozen=True)
class PublicationProjectionEvent:
    """Hash-only event metadata from an immutable PublicationVersion boundary."""

    event_id: str
    publication_id: str
    publication_version_id: str
    vault_id: str
    event_sequence: int
    kind: PublicationProjectionEventKind
    version_content_hash: str
    policy_hash: str
    input_source: PublicationProjectionInputSource
    external_index_requested: bool = False
    object_copy_requested: bool = False

    def __post_init__(self) -> None:
        for field_name in ("event_id", "publication_id", "publication_version_id"):
            object.__setattr__(self, field_name, _uuid(getattr(self, field_name), field=field_name))
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(self, "event_sequence", _positive_int(self.event_sequence, field="event_sequence"))
        object.__setattr__(self, "kind", PublicationProjectionEventKind(self.kind))
        object.__setattr__(
            self,
            "version_content_hash",
            _digest(self.version_content_hash, field="version_content_hash"),
        )
        object.__setattr__(self, "policy_hash", _digest(self.policy_hash, field="policy_hash"))
        object.__setattr__(
            self,
            "input_source",
            PublicationProjectionInputSource(self.input_source),
        )
        object.__setattr__(self, "external_index_requested", bool(self.external_index_requested))
        object.__setattr__(self, "object_copy_requested", bool(self.object_copy_requested))

    def event_hash(self) -> str:
        return _hash(
            {
                "eventId": self.event_id,
                "eventSequence": self.event_sequence,
                "inputSource": self.input_source.value,
                "kind": self.kind.value,
                "objectCopyRequested": self.object_copy_requested,
                "policyHash": self.policy_hash,
                "publicationId": self.publication_id,
                "publicationVersionId": self.publication_version_id,
                "vaultId": self.vault_id,
                "versionContentHash": self.version_content_hash,
            }
        )


@dataclass(frozen=True)
class PublicationProjectorEvaluationResult:
    disposition: PublicationProjectorDisposition
    reason_codes: tuple[str, ...]
    candidate_projection_hash: str | None = None
    candidate_public_citation_hash: str | None = None
    event_sequence: int | None = None
    proposed_state: PublicationProjectorState | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", PublicationProjectorDisposition(self.disposition))
        reasons = tuple(sorted({_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise PublicationPublicProjectorError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.candidate_projection_hash is not None:
            object.__setattr__(
                self,
                "candidate_projection_hash",
                _digest(self.candidate_projection_hash, field="candidate_projection_hash"),
            )
        if self.candidate_public_citation_hash is not None:
            object.__setattr__(
                self,
                "candidate_public_citation_hash",
                _digest(
                    self.candidate_public_citation_hash,
                    field="candidate_public_citation_hash",
                ),
            )
        if self.event_sequence is not None:
            object.__setattr__(
                self,
                "event_sequence",
                _positive_int(self.event_sequence, field="event_sequence"),
            )
        if self.proposed_state is not None:
            object.__setattr__(self, "proposed_state", PublicationProjectorState(self.proposed_state))

    @property
    def projection_write_allowed(self) -> bool:
        return False

    @property
    def public_query_allowed(self) -> bool:
        return False

    @property
    def external_index_allowed(self) -> bool:
        return False

    @property
    def object_copy_allowed(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "externalIndexAllowed": self.external_index_allowed,
            "objectCopyAllowed": self.object_copy_allowed,
            "projectionWriteAllowed": self.projection_write_allowed,
            "publicQueryAllowed": self.public_query_allowed,
            "reasonCodes": list(self.reason_codes),
            "releaseVisible": False,
            "schemaVersion": PUBLICATION_PUBLIC_PROJECTOR_G0_SCHEMA_VERSION,
            "status": self.disposition.value,
        }
        if self.candidate_projection_hash is not None:
            summary["candidateProjectionHash"] = self.candidate_projection_hash
        if self.candidate_public_citation_hash is not None:
            summary["candidatePublicCitationHash"] = self.candidate_public_citation_hash
        if self.event_sequence is not None:
            summary["eventSequence"] = self.event_sequence
        if self.proposed_state is not None:
            summary["proposedState"] = self.proposed_state.value
        return summary


def _candidate_hashes(event: PublicationProjectionEvent) -> tuple[str, str]:
    projection_hash = _hash(
        {
            "eventHash": event.event_hash(),
            "policyHash": event.policy_hash,
            "projectionSchemaVersion": PUBLICATION_PUBLIC_PROJECTOR_G0_SCHEMA_VERSION,
            "versionContentHash": event.version_content_hash,
        }
    )
    citation_hash = _hash(
        {
            "policyHash": event.policy_hash,
            "projectionHash": projection_hash,
            "versionContentHash": event.version_content_hash,
        }
    )
    return projection_hash, citation_hash


def _result(
    disposition: PublicationProjectorDisposition,
    reason: str,
    *,
    event: PublicationProjectionEvent | None = None,
    proposed_state: PublicationProjectorState | None = None,
) -> PublicationProjectorEvaluationResult:
    projection_hash = None
    citation_hash = None
    event_sequence = None
    if event is not None:
        projection_hash, citation_hash = _candidate_hashes(event)
        event_sequence = event.event_sequence
    return PublicationProjectorEvaluationResult(
        disposition=disposition,
        reason_codes=(reason,),
        candidate_projection_hash=projection_hash,
        candidate_public_citation_hash=citation_hash,
        event_sequence=event_sequence,
        proposed_state=proposed_state,
    )


def evaluate_publication_projector(
    *,
    checkpoint: PublicationProjectorCheckpoint | object,
    event: PublicationProjectionEvent | object,
    enabled: bool = False,
) -> PublicationProjectorEvaluationResult:
    """Check a one-way publication event without querying or writing any store."""

    if enabled is not True:
        return _result(
            PublicationProjectorDisposition.SHADOW_DISABLED,
            "publicationPublicProjectorShadowDisabled",
        )
    if not isinstance(checkpoint, PublicationProjectorCheckpoint) or not isinstance(
        event, PublicationProjectionEvent
    ):
        return _result(
            PublicationProjectorDisposition.INVALID_CONTEXT,
            "invalidPublicationProjectorContext",
        )
    if checkpoint.publication_id != event.publication_id or checkpoint.vault_id != event.vault_id:
        return _result(
            PublicationProjectorDisposition.SCOPE_MISMATCH,
            "publicationProjectorCheckpointScopeMismatch",
            event=event,
        )
    if event.input_source is not PublicationProjectionInputSource.PUBLICATION_VERSION_EVENT:
        return _result(
            PublicationProjectorDisposition.PRIVATE_DEPENDENCY_REJECTED,
            "publicationProjectorRequiresPublicationVersionEvent",
            event=event,
        )
    if event.external_index_requested or event.object_copy_requested:
        return _result(
            PublicationProjectorDisposition.EXTERNAL_PROVIDER_GATE_REQUIRED,
            "publicationProjectorExternalIndexOrObjectCopyGateRequired",
            event=event,
        )
    if event.event_sequence <= checkpoint.last_event_sequence:
        return _result(
            PublicationProjectorDisposition.DUPLICATE_OR_OUT_OF_ORDER,
            "publicationProjectorDuplicateOrOutOfOrderEvent",
            event=event,
        )
    if event.event_sequence != checkpoint.last_event_sequence + 1:
        return _result(
            PublicationProjectorDisposition.EVENT_GAP,
            "publicationProjectorEventGapDetected",
            event=event,
        )
    if event.kind is PublicationProjectionEventKind.SUSPENDED:
        return _result(
            PublicationProjectorDisposition.SUSPEND_OR_WITHDRAW,
            "publicationProjectorWouldSuspendWithoutPublicQuery",
            event=event,
            proposed_state=PublicationProjectorState.SUSPENDED,
        )
    if event.kind is PublicationProjectionEventKind.WITHDRAWN:
        return _result(
            PublicationProjectorDisposition.SUSPEND_OR_WITHDRAW,
            "publicationProjectorWouldWithdrawWithoutPublicQuery",
            event=event,
            proposed_state=PublicationProjectorState.WITHDRAWN,
        )
    return _result(
        PublicationProjectorDisposition.POLICY_DISABLED,
        "publicationProjectorPolicyDisabled",
        event=event,
        proposed_state=PublicationProjectorState.PENDING_INDEX,
    )


__all__ = [
    "PUBLICATION_PUBLIC_PROJECTOR_G0_SCHEMA_VERSION",
    "PublicationProjectionEvent",
    "PublicationProjectionEventKind",
    "PublicationProjectionInputSource",
    "PublicationProjectorCheckpoint",
    "PublicationProjectorDisposition",
    "PublicationProjectorEvaluationResult",
    "PublicationProjectorState",
    "PublicationPublicProjectorError",
    "evaluate_publication_projector",
]
