"""Private single-Candidate review commands for admitted interview batches."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from uuid import UUID, uuid5

from .candidate_decisions import CandidateReviewAction, OwnerTruthCandidateReviewCommand
from .contracts import OwnerTruthContractError, require_nonblank, require_uuid
from .ontology import OWNER_TRUTH_SCHEMA_VERSION


OWNER_TRUTH_INTERVIEW_CANDIDATE_SINGLE_REVIEW_SCHEMA_VERSION = (
    "owner-truth-interview-candidate-single-review-v1"
)
_SINGLE_REVIEW_NAMESPACE = UUID("b7246130-484a-4bb8-8ccb-22dfa39e6fce")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class OwnerTruthInterviewCandidateSingleReviewError(OwnerTruthContractError):
    """A single-review command is malformed or outside its batch boundary."""


class OwnerTruthInterviewCandidateSingleReviewConflict(
    OwnerTruthInterviewCandidateSingleReviewError
):
    """The selected Candidate is stale or belongs to another review path."""


class OwnerTruthInterviewCandidateSingleReviewNotReady(
    OwnerTruthInterviewCandidateSingleReviewError
):
    """The admitted batch is not ready for an individual Candidate decision."""


class OwnerTruthInterviewCandidateSingleReviewBatchRequired(
    OwnerTruthInterviewCandidateSingleReviewError
):
    """A batch-eligible ordinary Candidate must stay in the partial-batch path."""


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthInterviewCandidateSingleReviewError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthInterviewCandidateSingleReviewError(
            "single-review payload must be JSON serializable"
        ) from exc


def _normalized_mapping(value: Mapping[str, Any], *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthInterviewCandidateSingleReviewError(f"{field} must be an object")
    normalized = json.loads(_canonical_json(dict(value)))
    if not isinstance(normalized, dict):  # defensive; mappings decode as objects
        raise OwnerTruthInterviewCandidateSingleReviewError(f"{field} must be an object")
    return normalized


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateSingleReviewCommand:
    """Review exactly one sensitive or explicitly single-review Candidate.

    The command preserves the existing per-Candidate action vocabulary, but it
    adds the admitted review-batch provenance required by M0-A. It never
    activates a MemoryVersion.
    """

    command_id: str
    review_batch_id: str
    candidate_id: str
    expected_candidate_version: int
    action: CandidateReviewAction
    corrected_value: Mapping[str, Any] | None
    corrected_value_schema_version: str
    reason_code: str
    expected_memory_revision: int | None = None
    expected_change_set_id: str | None = None
    expected_proposal_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _identifier(self.command_id, field="command_id"))
        object.__setattr__(
            self,
            "review_batch_id",
            require_uuid(self.review_batch_id, field="review_batch_id"),
        )
        object.__setattr__(self, "candidate_id", require_uuid(self.candidate_id, field="candidate_id"))
        if not isinstance(self.expected_candidate_version, int) or self.expected_candidate_version < 1:
            raise OwnerTruthInterviewCandidateSingleReviewError(
                "expected_candidate_version must be a positive integer"
            )
        try:
            object.__setattr__(self, "action", CandidateReviewAction(self.action))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthInterviewCandidateSingleReviewError(
                "single-review action is unsupported"
            ) from exc
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
                raise OwnerTruthInterviewCandidateSingleReviewError(
                    "correct action requires corrected_value"
                )
            object.__setattr__(
                self,
                "corrected_value",
                _normalized_mapping(self.corrected_value, field="corrected_value"),
            )
        elif self.corrected_value is not None:
            raise OwnerTruthInterviewCandidateSingleReviewError(
                "only correct action may include corrected_value"
            )
        if self.expected_memory_revision is not None and (
            isinstance(self.expected_memory_revision, bool)
            or not isinstance(self.expected_memory_revision, int)
            or self.expected_memory_revision < 0
        ):
            raise OwnerTruthInterviewCandidateSingleReviewError(
                "expected_memory_revision must be a non-negative integer when provided"
            )
        if (self.expected_change_set_id is None) != (
            self.expected_proposal_hash is None
        ):
            raise OwnerTruthInterviewCandidateSingleReviewError(
                "expected_change_set_id and expected_proposal_hash must be provided together"
            )
        if self.expected_change_set_id is not None:
            object.__setattr__(
                self,
                "expected_change_set_id",
                require_uuid(
                    self.expected_change_set_id,
                    field="expected_change_set_id",
                ),
            )
            normalized_hash = str(self.expected_proposal_hash or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", normalized_hash):
                raise OwnerTruthInterviewCandidateSingleReviewError(
                    "expected_proposal_hash must be a lowercase SHA-256 digest"
                )
            object.__setattr__(self, "expected_proposal_hash", normalized_hash)
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()

    @property
    def selection_count(self) -> int:
        return 1

    @property
    def payload_hash(self) -> str:
        payload = {
            "action": self.action.value,
            "candidateId": self.candidate_id,
            "correctedValue": self.corrected_value,
            "correctedValueSchemaVersion": self.corrected_value_schema_version,
            "expectedCandidateVersion": self.expected_candidate_version,
            "reasonCode": self.reason_code,
            "reviewBatchId": self.review_batch_id,
            "schemaVersion": OWNER_TRUTH_INTERVIEW_CANDIDATE_SINGLE_REVIEW_SCHEMA_VERSION,
        }
        if self.expected_memory_revision is not None:
            payload["expectedMemoryRevision"] = self.expected_memory_revision
        if self.expected_change_set_id is not None:
            payload["expectedChangeSetId"] = self.expected_change_set_id
        if self.expected_proposal_hash is not None:
            payload["expectedProposalHash"] = self.expected_proposal_hash
        return sha256(
            _canonical_json(payload).encode("utf-8")
        ).hexdigest()

    def batch_decision_id(self, *, vault_id: str) -> str:
        return str(
            uuid5(
                _SINGLE_REVIEW_NAMESPACE,
                f"interview-single-review:{require_nonblank(vault_id, field='vault_id')}:{self.command_id_hash}",
            )
        )

    def child_command(self) -> OwnerTruthCandidateReviewCommand:
        suffix = sha256(
            f"{self.command_id_hash}:{self.candidate_id}".encode("utf-8")
        ).hexdigest()[:32]
        return OwnerTruthCandidateReviewCommand(
            command_id=f"interviewSingleReview-{suffix}",
            candidate_id=self.candidate_id,
            expected_candidate_version=self.expected_candidate_version,
            action=self.action,
            corrected_value=self.corrected_value,
            corrected_value_schema_version=self.corrected_value_schema_version,
            reason_code=self.reason_code,
            expected_memory_revision=self.expected_memory_revision,
            expected_change_set_id=self.expected_change_set_id,
            expected_proposal_hash=self.expected_proposal_hash,
        )


__all__ = [
    "OWNER_TRUTH_INTERVIEW_CANDIDATE_SINGLE_REVIEW_SCHEMA_VERSION",
    "OwnerTruthInterviewCandidateSingleReviewBatchRequired",
    "OwnerTruthInterviewCandidateSingleReviewCommand",
    "OwnerTruthInterviewCandidateSingleReviewConflict",
    "OwnerTruthInterviewCandidateSingleReviewError",
    "OwnerTruthInterviewCandidateSingleReviewNotReady",
]
