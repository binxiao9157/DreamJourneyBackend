"""Private partial-accept commands for an admitted interview review batch.

This command is intentionally narrower than the generic Candidate review
contract. It may accept only explicitly selected, standard batch-reviewable
Candidates. Sensitive or single-review Candidates remain on the individual
review path, and MemoryVersion activation remains a separate Authority step.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any
from uuid import UUID, uuid5

from .candidate_decisions import CandidateReviewAction, OwnerTruthCandidateReviewCommand
from .contracts import OwnerTruthContractError, require_nonblank, require_uuid
from .ontology import OWNER_TRUTH_SCHEMA_VERSION


OWNER_TRUTH_INTERVIEW_CANDIDATE_BATCH_DECISION_SCHEMA_VERSION = (
    "owner-truth-interview-candidate-batch-decision-v1"
)
_BATCH_DECISION_NAMESPACE = UUID("92b72a0b-5a93-4f35-bf00-05fca55c0eb6")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_MAX_SELECTION_COUNT = 50


class OwnerTruthInterviewCandidateBatchDecisionError(OwnerTruthContractError):
    """A private interview batch-decision command is malformed or unsafe."""


class OwnerTruthInterviewCandidateBatchDecisionConflict(
    OwnerTruthInterviewCandidateBatchDecisionError
):
    """The command, batch provenance, or selection cannot be replayed safely."""


class OwnerTruthInterviewCandidateBatchDecisionNotReady(
    OwnerTruthInterviewCandidateBatchDecisionError
):
    """Extraction has not produced a reviewable batch composition yet."""


class OwnerTruthInterviewCandidateBatchDecisionSingleReviewRequired(
    OwnerTruthInterviewCandidateBatchDecisionError
):
    """Sensitive or explicitly single-review Candidates cannot be batch accepted."""


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthInterviewCandidateBatchDecisionError(
            f"{field} must be an opaque identifier"
        )
    return normalized


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthInterviewCandidateBatchDecisionError(
            "batch decision payload must be JSON serializable"
        ) from exc


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateBatchSelection:
    """One value-minimized Candidate reference selected for batch acceptance."""

    candidate_id: str
    expected_candidate_version: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "candidate_id",
            require_uuid(self.candidate_id, field="candidate_id"),
        )
        if not isinstance(self.expected_candidate_version, int) or self.expected_candidate_version < 1:
            raise OwnerTruthInterviewCandidateBatchDecisionError(
                "expected_candidate_version must be a positive integer"
            )

    def payload(self) -> dict[str, object]:
        return {
            "candidateId": self.candidate_id,
            "expectedCandidateVersion": self.expected_candidate_version,
        }


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateBatchAcceptCommand:
    """Idempotently accept a selected subset of standard batch Candidates.

    Omitted Candidates are deliberately left pending. This preserves partial
    acceptance without silently rejecting, correcting, or activating any
    MemoryVersion.
    """

    command_id: str
    review_batch_id: str
    selections: tuple[OwnerTruthInterviewCandidateBatchSelection, ...]
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _identifier(self.command_id, field="command_id"))
        object.__setattr__(
            self,
            "review_batch_id",
            require_uuid(self.review_batch_id, field="review_batch_id"),
        )
        normalized = tuple(self.selections)
        if not normalized:
            raise OwnerTruthInterviewCandidateBatchDecisionError(
                "batch acceptance requires at least one selected Candidate"
            )
        if len(normalized) > _MAX_SELECTION_COUNT:
            raise OwnerTruthInterviewCandidateBatchDecisionError(
                "batch acceptance exceeds the maximum selected Candidate count"
            )
        if any(
            not isinstance(item, OwnerTruthInterviewCandidateBatchSelection)
            for item in normalized
        ):
            raise OwnerTruthInterviewCandidateBatchDecisionError(
                "batch acceptance selections are required"
            )
        identifiers = [item.candidate_id for item in normalized]
        if len(identifiers) != len(set(identifiers)):
            raise OwnerTruthInterviewCandidateBatchDecisionError(
                "batch acceptance cannot select a Candidate more than once"
            )
        object.__setattr__(
            self,
            "selections",
            tuple(sorted(normalized, key=lambda item: item.candidate_id)),
        )
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()

    @property
    def selection_count(self) -> int:
        return len(self.selections)

    @property
    def payload_hash(self) -> str:
        return sha256(
            _canonical_json(
                {
                    "reviewBatchId": self.review_batch_id,
                    "selections": [selection.payload() for selection in self.selections],
                    "reasonCode": self.reason_code,
                    "schemaVersion": OWNER_TRUTH_INTERVIEW_CANDIDATE_BATCH_DECISION_SCHEMA_VERSION,
                }
            ).encode("utf-8")
        ).hexdigest()

    def batch_decision_id(self, *, vault_id: str) -> str:
        return str(
            uuid5(
                _BATCH_DECISION_NAMESPACE,
                f"interview-batch-decision:{require_nonblank(vault_id, field='vault_id')}:{self.command_id_hash}",
            )
        )

    def child_command(self, *, selection: OwnerTruthInterviewCandidateBatchSelection) -> OwnerTruthCandidateReviewCommand:
        """Derive one immutable generic-Candidate command without activation."""

        suffix = sha256(
            f"{self.command_id_hash}:{selection.candidate_id}".encode("utf-8")
        ).hexdigest()[:32]
        return OwnerTruthCandidateReviewCommand(
            command_id=f"interviewBatchAccept-{suffix}",
            candidate_id=selection.candidate_id,
            expected_candidate_version=selection.expected_candidate_version,
            action=CandidateReviewAction.ACCEPT,
            corrected_value=None,
            corrected_value_schema_version=OWNER_TRUTH_SCHEMA_VERSION,
            reason_code=self.reason_code,
        )


__all__ = [
    "OWNER_TRUTH_INTERVIEW_CANDIDATE_BATCH_DECISION_SCHEMA_VERSION",
    "OwnerTruthInterviewCandidateBatchAcceptCommand",
    "OwnerTruthInterviewCandidateBatchDecisionConflict",
    "OwnerTruthInterviewCandidateBatchDecisionError",
    "OwnerTruthInterviewCandidateBatchDecisionNotReady",
    "OwnerTruthInterviewCandidateBatchDecisionSingleReviewRequired",
    "OwnerTruthInterviewCandidateBatchSelection",
]
