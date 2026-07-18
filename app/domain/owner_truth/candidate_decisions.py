"""Owner-only terminal review commands for immutable Candidate proposals.

This module deliberately ends at the DecisionReceipt boundary.  It never
creates a MemoryRecord, MemoryVersion, Projection, or public API response.
Corrected values are kept separate from the processor-created Candidate
payload so an Owner edit cannot rewrite extraction history.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import UUID, uuid5

from .contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    OwnerTruthContractError,
    PerspectiveType,
    SensitivityLevel,
    require_nonblank,
    require_uuid,
)
from .ontology import OWNER_TRUTH_SCHEMA_VERSION, validate_memory_payload
from .source_commands import OwnerTruthCommandContext


OWNER_TRUTH_DECISION_BASIS_SCHEMA_VERSION = "owner-truth-decision-basis-v1"
_RECEIPT_NAMESPACE = UUID("883e786a-2e66-49a4-9dac-7406cb3e9df2")
_CORRECTED_VALUE_NAMESPACE = UUID("6973375d-851d-4713-8e37-f22036bbcf5e")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class OwnerTruthCandidateReviewError(OwnerTruthContractError):
    """A Candidate review command cannot enter the Owner Truth authority lane."""


class OwnerTruthCandidateReviewConflict(OwnerTruthCandidateReviewError):
    """A stable command or terminal Candidate was reused with new meaning."""


class OwnerTruthCandidateVersionConflict(OwnerTruthCandidateReviewError):
    """The client reviewed a stale Candidate version."""

    def __init__(self, *, expected_version: int, current_version: int):
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__("owner truth candidate version does not match expectedCandidateVersion")


class OwnerTruthCandidateReviewAccessDenied(OwnerTruthCandidateReviewError):
    """Only the Vault Owner may terminally review a Candidate in this slice."""


class OwnerTruthCandidateReviewSourceInactive(OwnerTruthCandidateReviewError):
    """A Candidate's source or vault changed before review could commit."""


class CandidateReviewAction(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    CORRECT = "correct"

    @property
    def terminal_decision(self) -> CandidateDecision:
        return {
            CandidateReviewAction.ACCEPT: CandidateDecision.ACCEPTED,
            CandidateReviewAction.REJECT: CandidateDecision.REJECTED,
            CandidateReviewAction.CORRECT: CandidateDecision.CORRECTED,
        }[self]


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthCandidateReviewError("candidate review values must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalized_mapping(value: Mapping[str, Any], *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthCandidateReviewError(f"{field} must be an object")
    normalized = json.loads(_canonical_json(dict(value)))
    if not isinstance(normalized, dict):  # defensive; mappings decode to objects
        raise OwnerTruthCandidateReviewError(f"{field} must be an object")
    return normalized


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthCandidateReviewError(f"{field} must be an opaque identifier")
    return normalized


@dataclass(frozen=True)
class OwnerTruthCandidateSnapshot:
    """A repository-owned view of one immutable proposal and its review state."""

    candidate_id: str
    vault_id: str
    owner_subject_id: str
    source_id: str
    memory_kind: MemoryKind
    perspective_type: PerspectiveType
    epistemic_status: EpistemicStatus
    sensitivity: SensitivityLevel
    decision: CandidateDecision
    policy_version: str
    authority_epoch: int
    row_version: int
    content_hash: str
    content_schema_version: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", require_uuid(self.candidate_id, field="candidate_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "owner_subject_id", require_nonblank(self.owner_subject_id, field="owner_subject_id"))
        object.__setattr__(self, "source_id", require_uuid(self.source_id, field="source_id"))
        try:
            object.__setattr__(self, "memory_kind", MemoryKind(self.memory_kind))
            object.__setattr__(self, "perspective_type", PerspectiveType(self.perspective_type))
            object.__setattr__(self, "epistemic_status", EpistemicStatus(self.epistemic_status))
            object.__setattr__(self, "sensitivity", SensitivityLevel(self.sensitivity))
            object.__setattr__(self, "decision", CandidateDecision(self.decision))
        except ValueError as exc:
            raise OwnerTruthCandidateReviewError("candidate contains an unsupported enum value") from exc
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        object.__setattr__(self, "content_hash", require_nonblank(self.content_hash, field="content_hash"))
        object.__setattr__(
            self,
            "content_schema_version",
            require_nonblank(self.content_schema_version, field="content_schema_version"),
        )
        if self.authority_epoch < 0:
            raise OwnerTruthCandidateReviewError("candidate authority_epoch must not be negative")
        if self.row_version < 1:
            raise OwnerTruthCandidateReviewError("candidate row_version must be positive")
        normalized_payload = _normalized_mapping(self.payload, field="candidate payload")
        object.__setattr__(self, "payload", normalized_payload)
        content = self.content
        validation = validate_memory_payload(
            kind=self.memory_kind,
            payload=content,
            schema_version=self.content_schema_version,
        )
        if not validation.accepted:
            raise OwnerTruthCandidateReviewError(
                f"candidate content is not admitted: {validation.code}"
            )
        if _digest(content) != self.content_hash:
            raise OwnerTruthCandidateReviewError("candidate content_hash does not match immutable payload")
        source_refs = self.source_refs
        if not source_refs:
            raise OwnerTruthCandidateReviewError("candidate requires at least one source reference")
        if any(str(item.get("sourceId") or "") != self.source_id for item in source_refs):
            raise OwnerTruthCandidateReviewError("candidate source references must match source_id")

    @property
    def content(self) -> dict[str, Any]:
        return _normalized_mapping(self.payload.get("content"), field="candidate content")

    @property
    def source_refs(self) -> tuple[dict[str, Any], ...]:
        raw = self.payload.get("evidenceRefs")
        if not isinstance(raw, list):
            raise OwnerTruthCandidateReviewError("candidate evidenceRefs must be a list")
        refs: list[dict[str, Any]] = []
        for value in raw:
            item = _normalized_mapping(value, field="candidate evidence reference")
            if not str(item.get("sourceId") or "").strip():
                raise OwnerTruthCandidateReviewError("candidate evidence reference requires sourceId")
            try:
                source_version = int(item.get("sourceVersion"))
            except (TypeError, ValueError) as exc:
                raise OwnerTruthCandidateReviewError(
                    "candidate evidence reference requires sourceVersion"
                ) from exc
            if source_version < 1:
                raise OwnerTruthCandidateReviewError(
                    "candidate evidence sourceVersion must be positive"
                )
            refs.append(item)
        return tuple(refs)


@dataclass(frozen=True)
class OwnerTruthCandidateReviewCommand:
    """One idempotent Owner decision over exactly one pending Candidate."""

    command_id: str
    candidate_id: str
    expected_candidate_version: int
    action: CandidateReviewAction
    corrected_value: Mapping[str, Any] | None
    corrected_value_schema_version: str
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _identifier(self.command_id, field="command_id"))
        object.__setattr__(self, "candidate_id", require_uuid(self.candidate_id, field="candidate_id"))
        if self.expected_candidate_version < 1:
            raise OwnerTruthCandidateReviewError("expected_candidate_version must be positive")
        try:
            object.__setattr__(self, "action", CandidateReviewAction(self.action))
        except ValueError as exc:
            raise OwnerTruthCandidateReviewError("candidate review action is unsupported") from exc
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))
        object.__setattr__(
            self,
            "corrected_value_schema_version",
            require_nonblank(
                self.corrected_value_schema_version,
                field="corrected_value_schema_version",
            ),
        )
        if self.action is CandidateReviewAction.CORRECT:
            if self.corrected_value is None:
                raise OwnerTruthCandidateReviewError("correct action requires corrected_value")
            object.__setattr__(
                self,
                "corrected_value",
                _normalized_mapping(self.corrected_value, field="corrected_value"),
            )
        elif self.corrected_value is not None:
            raise OwnerTruthCandidateReviewError(
                "only correct action may include corrected_value"
            )

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "action": self.action.value,
                "candidateId": self.candidate_id,
                "correctedValue": self.corrected_value,
                "correctedValueSchemaVersion": self.corrected_value_schema_version,
                "expectedCandidateVersion": self.expected_candidate_version,
                "reasonCode": self.reason_code,
            }
        )

    def write_record(
        self,
        *,
        candidate: OwnerTruthCandidateSnapshot,
        context: OwnerTruthCommandContext,
    ) -> "OwnerTruthCandidateDecisionWriteRecord":
        if not isinstance(context, OwnerTruthCommandContext):
            raise OwnerTruthCandidateReviewError("owner truth command context is required")
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthCandidateReviewAccessDenied(
                "only the Vault Owner may review a Candidate"
            )
        if (
            candidate.candidate_id != self.candidate_id
            or candidate.vault_id != context.vault_id
            or candidate.owner_subject_id != context.owner_subject_id
        ):
            raise OwnerTruthCandidateReviewAccessDenied(
                "candidate does not belong to the command Owner"
            )
        if candidate.policy_version != context.policy_version:
            raise OwnerTruthCandidateReviewConflict("candidate policy version no longer matches command policy")
        if candidate.decision is not CandidateDecision.PENDING:
            raise OwnerTruthCandidateReviewConflict("terminal Candidate cannot receive a new decision")
        if candidate.row_version != self.expected_candidate_version:
            raise OwnerTruthCandidateVersionConflict(
                expected_version=self.expected_candidate_version,
                current_version=candidate.row_version,
            )

        corrected_value = None
        corrected_value_id = None
        candidate_after_hash = candidate.content_hash
        if self.action is CandidateReviewAction.CORRECT:
            corrected_value = _normalized_mapping(self.corrected_value or {}, field="corrected_value")
            validation = validate_memory_payload(
                kind=candidate.memory_kind,
                payload=corrected_value,
                schema_version=self.corrected_value_schema_version,
            )
            if not validation.accepted:
                raise OwnerTruthCandidateReviewError(
                    f"corrected_value is not admitted: {validation.code}"
                )
            candidate_after_hash = _digest(corrected_value)
            corrected_value_id = str(
                uuid5(
                    _CORRECTED_VALUE_NAMESPACE,
                    f"candidate-decision-value:{context.vault_id}:{candidate.candidate_id}:{self.command_id_hash}",
                )
            )

        receipt_id = str(
            uuid5(
                _RECEIPT_NAMESPACE,
                f"candidate-decision:{context.vault_id}:{candidate.candidate_id}:{self.command_id_hash}",
            )
        )
        basis = {
            "action": self.action.value,
            "candidateKind": candidate.memory_kind.value,
            "reasonCode": self.reason_code,
            "schemaVersion": OWNER_TRUTH_DECISION_BASIS_SCHEMA_VERSION,
            "sourceRefs": [dict(item) for item in candidate.source_refs],
        }
        return OwnerTruthCandidateDecisionWriteRecord(
            receipt_id=receipt_id,
            corrected_value_id=corrected_value_id,
            command_id_hash=self.command_id_hash,
            payload_hash=self.payload_hash,
            candidate_id=candidate.candidate_id,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            expected_candidate_version=self.expected_candidate_version,
            decision=self.action.terminal_decision,
            policy_version=context.policy_version,
            authority_epoch=candidate.authority_epoch,
            reason_code=self.reason_code,
            candidate_before_hash=candidate.content_hash,
            candidate_after_hash=candidate_after_hash,
            decision_basis=basis,
            corrected_value_schema_version=(
                self.corrected_value_schema_version if corrected_value is not None else None
            ),
            corrected_value=corrected_value,
        )


@dataclass(frozen=True)
class OwnerTruthCandidateDecisionWriteRecord:
    receipt_id: str
    corrected_value_id: str | None
    command_id_hash: str
    payload_hash: str
    candidate_id: str
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    expected_candidate_version: int
    decision: CandidateDecision
    policy_version: str
    authority_epoch: int
    reason_code: str
    candidate_before_hash: str
    candidate_after_hash: str
    decision_basis: Mapping[str, Any]
    corrected_value_schema_version: str | None
    corrected_value: Mapping[str, Any] | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", require_uuid(self.receipt_id, field="receipt_id"))
        if self.corrected_value_id is not None:
            object.__setattr__(
                self,
                "corrected_value_id",
                require_uuid(self.corrected_value_id, field="corrected_value_id"),
            )
        object.__setattr__(self, "candidate_id", require_uuid(self.candidate_id, field="candidate_id"))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(
            self,
            "actor_subject_id",
            require_nonblank(self.actor_subject_id, field="actor_subject_id"),
        )
        try:
            object.__setattr__(self, "decision", CandidateDecision(self.decision))
        except ValueError as exc:
            raise OwnerTruthCandidateReviewError("decision is unsupported") from exc
        if self.decision not in {
            CandidateDecision.ACCEPTED,
            CandidateDecision.REJECTED,
            CandidateDecision.CORRECTED,
        }:
            raise OwnerTruthCandidateReviewError("Owner review may only create terminal decisions")
        if self.expected_candidate_version < 1 or self.authority_epoch < 0:
            raise OwnerTruthCandidateReviewError("candidate version or authority epoch is invalid")
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))
        for field in (
            "command_id_hash",
            "payload_hash",
            "candidate_before_hash",
            "candidate_after_hash",
        ):
            value = str(getattr(self, field) or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise OwnerTruthCandidateReviewError(f"{field} must be a lowercase SHA-256 digest")
            object.__setattr__(self, field, value)
        object.__setattr__(self, "decision_basis", _normalized_mapping(self.decision_basis, field="decision_basis"))
        if self.decision_basis.get("schemaVersion") != OWNER_TRUTH_DECISION_BASIS_SCHEMA_VERSION:
            raise OwnerTruthCandidateReviewError("decision_basis schemaVersion is unsupported")
        if self.decision is CandidateDecision.CORRECTED:
            if self.corrected_value is None or self.corrected_value_id is None:
                raise OwnerTruthCandidateReviewError("corrected decision requires one corrected value")
            object.__setattr__(
                self,
                "corrected_value",
                _normalized_mapping(self.corrected_value, field="corrected_value"),
            )
            object.__setattr__(
                self,
                "corrected_value_schema_version",
                require_nonblank(
                    self.corrected_value_schema_version or "",
                    field="corrected_value_schema_version",
                ),
            )
        elif self.corrected_value is not None or self.corrected_value_id is not None:
            raise OwnerTruthCandidateReviewError("only corrected decisions may persist a corrected value")


__all__ = [
    "CandidateReviewAction",
    "OWNER_TRUTH_DECISION_BASIS_SCHEMA_VERSION",
    "OwnerTruthCandidateDecisionWriteRecord",
    "OwnerTruthCandidateReviewAccessDenied",
    "OwnerTruthCandidateReviewCommand",
    "OwnerTruthCandidateReviewConflict",
    "OwnerTruthCandidateReviewError",
    "OwnerTruthCandidateReviewSourceInactive",
    "OwnerTruthCandidateSnapshot",
    "OwnerTruthCandidateVersionConflict",
]
