"""Explicit review-batch admission into the existing Candidate proposal lane.

An interview message remains private conversation data until the Owner has
acknowledged its frozen review batch and explicitly issues this command. The
command can create one immutable ``conversation`` Source and the existing
default-off source candidate-extraction effect. It cannot create a Candidate
decision, DecisionReceipt, MemoryVersion, projection, public route, or
provider request body.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping
from uuid import UUID, uuid5

from .contracts import OwnerTruthContractError, require_nonblank, require_uuid
from .source_commands import OwnerTruthCommandContext


OWNER_TRUTH_INTERVIEW_CANDIDATE_PROPOSAL_SCHEMA_VERSION = (
    "owner-truth-interview-candidate-proposal-v1"
)
_ADMISSION_NAMESPACE = UUID("08c7d44f-a75c-4d79-8d3d-cb70b1b3a4d1")
_SOURCE_NAMESPACE = UUID("049c9b72-4e8e-450b-afb5-3c33d2aeba61")


class OwnerTruthInterviewCandidateProposalError(OwnerTruthContractError):
    """A review batch cannot enter the controlled Candidate proposal lane."""


class OwnerTruthInterviewCandidateProposalConflict(OwnerTruthInterviewCandidateProposalError):
    """A stable review-batch admission key was reused with new meaning."""


class OwnerTruthInterviewCandidateProposalAccessDenied(OwnerTruthInterviewCandidateProposalError):
    """The command is not from the active Owner of the review batch Vault."""


class OwnerTruthInterviewCandidateProposalVersionConflict(OwnerTruthInterviewCandidateProposalError):
    """The Owner attempted to admit a stale review batch snapshot."""

    def __init__(self, *, expected_version: int, current_version: int):
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__("owner truth review batch version does not match expectedReviewBatchVersion")


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthInterviewCandidateProposalError(
            "interview candidate proposal payload must be JSON serializable"
        ) from exc


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _positive_version(value: int, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OwnerTruthInterviewCandidateProposalError(f"{field} must be a positive integer")
    return value


def _normalised_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthInterviewCandidateProposalError("source_metadata must be an object")
    try:
        normalised = json.loads(_canonical_json(dict(value)))
    except OwnerTruthInterviewCandidateProposalError:
        raise
    if not isinstance(normalised, dict):  # defensive: mappings serialise as objects
        raise OwnerTruthInterviewCandidateProposalError("source_metadata must be an object")
    return normalised


@dataclass(frozen=True)
class AdmitInterviewReviewBatchForCandidateProposalCommand:
    """One explicit Owner action over an already acknowledged review batch."""

    command_id: str
    review_batch_id: str
    expected_review_batch_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "review_batch_id", require_uuid(self.review_batch_id, field="review_batch_id"))
        _positive_version(
            self.expected_review_batch_version,
            field="expected_review_batch_version",
        )

    def write_record(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> "OwnerTruthInterviewCandidateProposalWriteRecord":
        if not isinstance(context, OwnerTruthCommandContext):
            raise OwnerTruthInterviewCandidateProposalAccessDenied(
                "owner truth command context is required"
            )
        command_id_hash = _sha256(self.command_id)
        payload = {
            "schemaVersion": OWNER_TRUTH_INTERVIEW_CANDIDATE_PROPOSAL_SCHEMA_VERSION,
            "commandType": "admitInterviewReviewBatchForCandidateProposal",
            "reviewBatchId": self.review_batch_id,
            "expectedReviewBatchVersion": self.expected_review_batch_version,
        }
        admission_id = str(
            uuid5(
                _ADMISSION_NAMESPACE,
                f"admission:{context.vault_id}:{self.review_batch_id}:{command_id_hash}",
            )
        )
        source_id = str(
            uuid5(
                _SOURCE_NAMESPACE,
                f"review-batch-source:{context.vault_id}:{self.review_batch_id}",
            )
        )
        return OwnerTruthInterviewCandidateProposalWriteRecord(
            admission_id=admission_id,
            command_id_hash=command_id_hash,
            payload_hash=_sha256(_canonical_json(payload)),
            review_batch_id=self.review_batch_id,
            expected_review_batch_version=self.expected_review_batch_version,
            source_id=source_id,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            actor_subject_id=context.actor_subject_id,
            policy_version=context.policy_version,
        )


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateProposalWriteRecord:
    admission_id: str
    command_id_hash: str
    payload_hash: str
    review_batch_id: str
    expected_review_batch_version: int
    source_id: str
    vault_id: str
    owner_subject_id: str
    actor_subject_id: str
    policy_version: str

    def __post_init__(self) -> None:
        for field in ("admission_id", "review_batch_id", "source_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        for field in ("command_id_hash", "payload_hash"):
            value = str(getattr(self, field) or "").strip().lower()
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise OwnerTruthInterviewCandidateProposalError(
                    f"{field} must be a lowercase SHA-256 digest"
                )
            object.__setattr__(self, field, value)
        _positive_version(
            self.expected_review_batch_version,
            field="expected_review_batch_version",
        )
        for field in ("vault_id", "owner_subject_id", "actor_subject_id", "policy_version"):
            object.__setattr__(self, field, require_nonblank(getattr(self, field), field=field))

    @property
    def source_command_id(self) -> str:
        return f"review-batch-candidate-source:{self.admission_id}"


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateProposalPreparation:
    """Private material held only while one admission transaction is open."""

    review_batch_id: str
    thread_id: str
    session_id: str
    source_text: str
    source_metadata: Mapping[str, Any]
    owner_message_count: int
    first_message_sequence: int
    last_message_sequence: int

    def __post_init__(self) -> None:
        for field in ("review_batch_id", "thread_id", "session_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        object.__setattr__(self, "source_text", require_nonblank(self.source_text, field="source_text"))
        object.__setattr__(self, "source_metadata", _normalised_metadata(self.source_metadata))
        for field in ("owner_message_count", "first_message_sequence", "last_message_sequence"):
            _positive_version(getattr(self, field), field=field)
        if self.last_message_sequence < self.first_message_sequence:
            raise OwnerTruthInterviewCandidateProposalError(
                "last_message_sequence must not precede first_message_sequence"
            )


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateProposalResult:
    """Value-free receipt for one admitted review batch."""

    outcome: str
    admission_id: str
    review_batch_id: str
    source_id: str
    source_version: int
    source_content_hash: str
    effect_operation_id: str
    owner_message_count: int

    def public_receipt(self) -> dict[str, Any]:
        return {
            "schemaVersion": OWNER_TRUTH_INTERVIEW_CANDIDATE_PROPOSAL_SCHEMA_VERSION,
            "status": self.outcome,
            "admissionId": self.admission_id,
            "reviewBatchId": self.review_batch_id,
            "sourceId": self.source_id,
            "sourceVersion": self.source_version,
            "effectOperationId": self.effect_operation_id,
            "ownerMessageCount": self.owner_message_count,
        }


__all__ = [
    "AdmitInterviewReviewBatchForCandidateProposalCommand",
    "OWNER_TRUTH_INTERVIEW_CANDIDATE_PROPOSAL_SCHEMA_VERSION",
    "OwnerTruthInterviewCandidateProposalAccessDenied",
    "OwnerTruthInterviewCandidateProposalConflict",
    "OwnerTruthInterviewCandidateProposalError",
    "OwnerTruthInterviewCandidateProposalPreparation",
    "OwnerTruthInterviewCandidateProposalResult",
    "OwnerTruthInterviewCandidateProposalVersionConflict",
    "OwnerTruthInterviewCandidateProposalWriteRecord",
]
