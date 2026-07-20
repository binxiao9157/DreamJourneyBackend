"""Read-only review composition for one admitted interview review batch.

The composition is a private owner-facing selection boundary.  It groups only
pending Candidates produced from an explicitly admitted review-batch Source.
It does not decide a Candidate, create a DecisionReceipt, or activate a
MemoryVersion.  A later command layer must still use the existing per-Candidate
CAS decision contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .contracts import OwnerTruthContractError, require_nonblank, require_uuid


OWNER_TRUTH_INTERVIEW_CANDIDATE_REVIEW_SCHEMA_VERSION = (
    "owner-truth-interview-candidate-review-v1"
)


class OwnerTruthInterviewCandidateReviewError(OwnerTruthContractError):
    """A review-batch Candidate composition cannot be read safely."""


class OwnerTruthInterviewCandidateReviewAccessDenied(
    OwnerTruthInterviewCandidateReviewError
):
    """The requested batch does not belong to the active Vault Owner."""


class OwnerTruthInterviewCandidateReviewConflict(OwnerTruthInterviewCandidateReviewError):
    """An admitted batch no longer matches its immutable provenance boundary."""


class OwnerTruthInterviewCandidateReviewSourceInactive(
    OwnerTruthInterviewCandidateReviewError
):
    """The admitted Source is no longer active at composition time."""


class InterviewCandidateReviewReadiness(str, Enum):
    AWAITING_EXTRACTION = "awaitingExtraction"
    REVIEW_READY = "reviewReady"
    NO_CANDIDATES = "noCandidates"
    EXTRACTION_FAILED = "extractionFailed"
    EXTRACTION_QUARANTINED = "extractionQuarantined"


class InterviewCandidateReviewPath(str, Enum):
    BATCH = "batch"
    SINGLE = "single"


@dataclass(frozen=True)
class OwnerTruthInterviewReviewCandidateItem:
    """Value-minimized Candidate reference for one review-batch composition."""

    candidate_id: str
    extraction_id: str
    candidate_row_version: int
    candidate_kind: str
    sensitivity: str
    review_mode: str
    review_path: InterviewCandidateReviewPath
    created_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", require_uuid(self.candidate_id, field="candidate_id"))
        object.__setattr__(self, "extraction_id", require_uuid(self.extraction_id, field="extraction_id"))
        if not isinstance(self.candidate_row_version, int) or self.candidate_row_version < 1:
            raise OwnerTruthInterviewCandidateReviewError(
                "candidate_row_version must be a positive integer"
            )
        for field in ("candidate_kind", "sensitivity", "review_mode"):
            object.__setattr__(self, field, require_nonblank(getattr(self, field), field=field))
        try:
            object.__setattr__(self, "review_path", InterviewCandidateReviewPath(self.review_path))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthInterviewCandidateReviewError(
                "review_path is not supported"
            ) from exc
        if self.created_at is not None:
            object.__setattr__(self, "created_at", require_nonblank(self.created_at, field="created_at"))


@dataclass(frozen=True)
class OwnerTruthInterviewCandidateReviewComposition:
    """One immutable batch provenance link plus its current pending candidates."""

    review_batch_id: str
    admission_id: str
    source_id: str
    source_version: int
    authority_epoch: int
    readiness: InterviewCandidateReviewReadiness
    latest_extraction_status: str | None
    batch_candidates: tuple[OwnerTruthInterviewReviewCandidateItem, ...]
    single_candidates: tuple[OwnerTruthInterviewReviewCandidateItem, ...]

    def __post_init__(self) -> None:
        for field in ("review_batch_id", "admission_id", "source_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        if not isinstance(self.source_version, int) or self.source_version < 1:
            raise OwnerTruthInterviewCandidateReviewError(
                "source_version must be a positive integer"
            )
        if not isinstance(self.authority_epoch, int) or self.authority_epoch < 0:
            raise OwnerTruthInterviewCandidateReviewError(
                "authority_epoch must be a non-negative integer"
            )
        try:
            object.__setattr__(self, "readiness", InterviewCandidateReviewReadiness(self.readiness))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthInterviewCandidateReviewError("review readiness is not supported") from exc
        if self.latest_extraction_status is not None:
            object.__setattr__(
                self,
                "latest_extraction_status",
                require_nonblank(self.latest_extraction_status, field="latest_extraction_status"),
            )
        object.__setattr__(self, "batch_candidates", tuple(self.batch_candidates))
        object.__setattr__(self, "single_candidates", tuple(self.single_candidates))
        all_items = self.batch_candidates + self.single_candidates
        if any(not isinstance(item, OwnerTruthInterviewReviewCandidateItem) for item in all_items):
            raise OwnerTruthInterviewCandidateReviewError("review composition items are required")
        if any(item.review_path is not InterviewCandidateReviewPath.BATCH for item in self.batch_candidates):
            raise OwnerTruthInterviewCandidateReviewError("batch candidate must use the batch review path")
        if any(item.review_path is not InterviewCandidateReviewPath.SINGLE for item in self.single_candidates):
            raise OwnerTruthInterviewCandidateReviewError("single candidate must use the single review path")
        identifiers = [item.candidate_id for item in all_items]
        if len(identifiers) != len(set(identifiers)):
            raise OwnerTruthInterviewCandidateReviewError(
                "review composition cannot contain a Candidate more than once"
            )
        if self.readiness is InterviewCandidateReviewReadiness.REVIEW_READY and not all_items:
            raise OwnerTruthInterviewCandidateReviewError(
                "reviewReady composition requires at least one pending Candidate"
            )
        if self.readiness is not InterviewCandidateReviewReadiness.REVIEW_READY and all_items:
            raise OwnerTruthInterviewCandidateReviewError(
                "non-ready composition cannot expose pending Candidates"
            )

    @property
    def candidate_count(self) -> int:
        return len(self.batch_candidates) + len(self.single_candidates)

    def public_summary(self) -> dict[str, Any]:
        """Return a value-free status summary suitable for QA diagnostics."""

        return {
            "schemaVersion": OWNER_TRUTH_INTERVIEW_CANDIDATE_REVIEW_SCHEMA_VERSION,
            "reviewBatchId": self.review_batch_id,
            "admissionId": self.admission_id,
            "sourceId": self.source_id,
            "sourceVersion": self.source_version,
            "authorityEpoch": self.authority_epoch,
            "readiness": self.readiness.value,
            "latestExtractionStatus": self.latest_extraction_status,
            "batchCandidateCount": len(self.batch_candidates),
            "singleCandidateCount": len(self.single_candidates),
        }


__all__ = [
    "InterviewCandidateReviewPath",
    "InterviewCandidateReviewReadiness",
    "OWNER_TRUTH_INTERVIEW_CANDIDATE_REVIEW_SCHEMA_VERSION",
    "OwnerTruthInterviewCandidateReviewAccessDenied",
    "OwnerTruthInterviewReviewCandidateItem",
    "OwnerTruthInterviewCandidateReviewComposition",
    "OwnerTruthInterviewCandidateReviewConflict",
    "OwnerTruthInterviewCandidateReviewError",
    "OwnerTruthInterviewCandidateReviewSourceInactive",
]
