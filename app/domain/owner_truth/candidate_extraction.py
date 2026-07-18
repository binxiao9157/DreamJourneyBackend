"""Provider-neutral ExtractionResult and pending Candidate contracts.

This module intentionally models only the reviewable proposal boundary.  It
cannot create a DecisionReceipt, MemoryRecord, MemoryVersion or Projection.
Real provider execution remains outside this slice; deterministic callers use
the same immutable contract so retries and future workers have one shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping
from uuid import UUID, uuid5

from app.async_effects.contracts import AsyncEffectIntent
from app.domain.owner_truth.contracts import (
    EpistemicStatus,
    MemoryKind,
    OwnerTruthContractError,
    PerspectiveType,
    SensitivityLevel,
    SourceRef,
    require_nonblank,
)
from app.domain.owner_truth.ontology import OWNER_TRUTH_SCHEMA_VERSION, validate_memory_payload


OWNER_TRUTH_EXTRACTION_SCHEMA_VERSION = "owner-truth-extraction-result-v1"
OWNER_TRUTH_CANDIDATE_SCHEMA_VERSION = "owner-truth-candidate-proposal-v1"
_EXTRACTION_NAMESPACE = UUID("3979c19b-f8d9-447c-bc46-f7f5d2d5bf57")
_CANDIDATE_NAMESPACE = UUID("46fa89b7-015d-4421-88d8-314fcdabef28")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OwnerTruthCandidateExtractionContractError(OwnerTruthContractError):
    """An extraction result or candidate proposal cannot enter Owner Truth."""


class OwnerTruthCandidateExtractionConflict(OwnerTruthCandidateExtractionContractError):
    """A stable extraction identity was reused with a different result."""


class ExtractionResultStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class CandidateReviewMode(str, Enum):
    BATCH = "batch"
    SINGLE = "single"


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthCandidateExtractionContractError(
            "candidate extraction values must be JSON serializable"
        ) from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalized_mapping(value: Mapping[str, Any], *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise OwnerTruthCandidateExtractionContractError(f"{field} must be an object")
    normalized = json.loads(_canonical_json(dict(value)))
    if not isinstance(normalized, dict):  # defensive; mappings always decode as objects
        raise OwnerTruthCandidateExtractionContractError(f"{field} must be an object")
    return normalized


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthCandidateExtractionContractError(f"{field} must be an opaque identifier")
    return normalized


def _sha256(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise OwnerTruthCandidateExtractionContractError(f"{field} must be a lowercase SHA-256 hex digest")
    return normalized


def _typed_source_intent(intent: AsyncEffectIntent) -> None:
    if not isinstance(intent, AsyncEffectIntent):
        raise OwnerTruthCandidateExtractionContractError("source extraction requires an async effect intent")
    target = intent.target
    if (
        intent.operation_type != "ownerTruth.source.created"
        or target.resource_type != "source"
        or target.purpose != "candidateExtraction"
    ):
        raise OwnerTruthCandidateExtractionContractError("candidate extraction requires a Source candidateExtraction effect")


@dataclass(frozen=True)
class CandidateEvidenceSpan:
    """A non-empty character span in the immutable Source payload."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if not isinstance(self.start, int) or not isinstance(self.end, int):
            raise OwnerTruthCandidateExtractionContractError("candidate evidence span offsets must be integers")
        if self.start < 0 or self.end <= self.start:
            raise OwnerTruthCandidateExtractionContractError("candidate evidence span must be non-empty")

    def public_contract(self) -> dict[str, int]:
        return {"end": self.end, "start": self.start}


@dataclass(frozen=True)
class CandidateProposal:
    """One atomic, reviewable fact suggestion from an ExtractionResult."""

    memory_kind: MemoryKind
    perspective_type: PerspectiveType
    epistemic_status: EpistemicStatus
    sensitivity: SensitivityLevel
    content: Mapping[str, Any]
    evidence_span: CandidateEvidenceSpan
    confidence: float
    review_mode: CandidateReviewMode
    payload_schema_version: str = OWNER_TRUTH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "memory_kind", MemoryKind(self.memory_kind))
            object.__setattr__(self, "perspective_type", PerspectiveType(self.perspective_type))
            object.__setattr__(self, "epistemic_status", EpistemicStatus(self.epistemic_status))
            object.__setattr__(self, "sensitivity", SensitivityLevel(self.sensitivity))
            object.__setattr__(self, "review_mode", CandidateReviewMode(self.review_mode))
        except ValueError as exc:
            raise OwnerTruthCandidateExtractionContractError("candidate enum value is not supported") from exc
        if not isinstance(self.evidence_span, CandidateEvidenceSpan):
            raise OwnerTruthCandidateExtractionContractError("candidate evidence span is required")
        object.__setattr__(self, "content", _normalized_mapping(self.content, field="candidate content"))
        object.__setattr__(
            self,
            "payload_schema_version",
            require_nonblank(self.payload_schema_version, field="payload_schema_version"),
        )
        validation = validate_memory_payload(
            kind=self.memory_kind,
            payload=self.content,
            schema_version=self.payload_schema_version,
        )
        if not validation.accepted:
            raise OwnerTruthCandidateExtractionContractError(
                f"candidate content is not admitted: {validation.code}"
            )
        try:
            normalized_confidence = float(self.confidence)
        except (TypeError, ValueError) as exc:
            raise OwnerTruthCandidateExtractionContractError("candidate confidence must be numeric") from exc
        if not math.isfinite(normalized_confidence) or not 0.0 <= normalized_confidence <= 1.0:
            raise OwnerTruthCandidateExtractionContractError("candidate confidence must be within [0, 1]")
        object.__setattr__(self, "confidence", normalized_confidence)
        if self.sensitivity is not SensitivityLevel.STANDARD and self.review_mode is not CandidateReviewMode.SINGLE:
            raise OwnerTruthCandidateExtractionContractError(
                "sensitive candidates require single-item review"
            )

    def write_record(
        self,
        *,
        extraction_id: str,
        source_ref: SourceRef,
    ) -> "OwnerTruthCandidateProposalWriteRecord":
        normalized_extraction_id = str(UUID(extraction_id))
        evidence_ref = {
            "sourceId": source_ref.source_id,
            "sourceVersion": source_ref.source_version,
            "span": self.evidence_span.public_contract(),
        }
        proposal_body = {
            "candidateKind": self.memory_kind.value,
            "content": dict(self.content),
            "contentSchemaVersion": self.payload_schema_version,
            "confidence": self.confidence,
            "epistemicStatus": self.epistemic_status.value,
            "evidenceRefs": [evidence_ref],
            "perspectiveType": self.perspective_type.value,
            "reviewMode": self.review_mode.value,
            "sensitivity": self.sensitivity.value,
        }
        proposal_hash = _digest(proposal_body)
        candidate_id = str(uuid5(_CANDIDATE_NAMESPACE, f"{normalized_extraction_id}:{proposal_hash}"))
        payload = {
            "schemaVersion": OWNER_TRUTH_CANDIDATE_SCHEMA_VERSION,
            **proposal_body,
            "proposalHash": proposal_hash,
        }
        return OwnerTruthCandidateProposalWriteRecord(
            candidate_id=candidate_id,
            candidate_kind=self.memory_kind,
            perspective_type=self.perspective_type,
            epistemic_status=self.epistemic_status,
            sensitivity=self.sensitivity,
            source_ref=source_ref,
            proposal_hash=proposal_hash,
            content_hash=_digest(self.content),
            payload_schema_version=self.payload_schema_version,
            payload=payload,
        )


@dataclass(frozen=True)
class OwnerTruthCandidateProposalWriteRecord:
    candidate_id: str
    candidate_kind: MemoryKind
    perspective_type: PerspectiveType
    epistemic_status: EpistemicStatus
    sensitivity: SensitivityLevel
    source_ref: SourceRef
    proposal_hash: str
    content_hash: str
    payload_schema_version: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", str(UUID(self.candidate_id)))
        object.__setattr__(self, "proposal_hash", _sha256(self.proposal_hash, field="proposal_hash"))
        object.__setattr__(self, "content_hash", _sha256(self.content_hash, field="content_hash"))
        object.__setattr__(self, "payload_schema_version", require_nonblank(self.payload_schema_version, field="payload_schema_version"))
        object.__setattr__(self, "payload", _normalized_mapping(self.payload, field="candidate payload"))


@dataclass(frozen=True)
class OwnerTruthCandidateExtractionWriteRecord:
    intent: AsyncEffectIntent
    extraction_id: str
    source_ref: SourceRef
    source_content_hash: str
    extractor_id: str
    model_id: str
    prompt_version: str
    policy_version: str
    status: ExtractionResultStatus
    result_hash: str
    payload: Mapping[str, Any]
    failure_code: str | None
    retryable: bool
    candidate_records: tuple[OwnerTruthCandidateProposalWriteRecord, ...]

    def __post_init__(self) -> None:
        _typed_source_intent(self.intent)
        object.__setattr__(self, "extraction_id", str(UUID(self.extraction_id)))
        object.__setattr__(self, "source_content_hash", _sha256(self.source_content_hash, field="source_content_hash"))
        object.__setattr__(self, "extractor_id", _identifier(self.extractor_id, field="extractor_id"))
        object.__setattr__(self, "model_id", require_nonblank(self.model_id, field="model_id"))
        object.__setattr__(self, "prompt_version", require_nonblank(self.prompt_version, field="prompt_version"))
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        try:
            object.__setattr__(self, "status", ExtractionResultStatus(self.status))
        except ValueError as exc:
            raise OwnerTruthCandidateExtractionContractError("unsupported extraction status") from exc
        object.__setattr__(self, "result_hash", _sha256(self.result_hash, field="result_hash"))
        object.__setattr__(self, "payload", _normalized_mapping(self.payload, field="extraction payload"))
        object.__setattr__(self, "candidate_records", tuple(self.candidate_records))
        if self.source_ref.vault_id != self.intent.target.vault_id or self.source_ref.source_id != self.intent.target.resource_id:
            raise OwnerTruthCandidateExtractionContractError("extraction Source reference must match its effect target")
        if self.source_ref.source_version != self.intent.target.resource_version:
            raise OwnerTruthCandidateExtractionContractError("extraction Source version must match its effect target")
        if self.status is ExtractionResultStatus.SUCCEEDED:
            if self.failure_code is not None or self.retryable:
                raise OwnerTruthCandidateExtractionContractError("successful extraction cannot carry failure state")
        else:
            if self.candidate_records:
                raise OwnerTruthCandidateExtractionContractError("failed or quarantined extraction cannot create candidates")
            object.__setattr__(self, "failure_code", _identifier(self.failure_code, field="failure_code"))
            if self.status is ExtractionResultStatus.QUARANTINED and self.retryable:
                raise OwnerTruthCandidateExtractionContractError("quarantined extraction is not retryable")
        seen_hashes: set[str] = set()
        for candidate in self.candidate_records:
            if not isinstance(candidate, OwnerTruthCandidateProposalWriteRecord):
                raise OwnerTruthCandidateExtractionContractError("candidate record is required")
            if candidate.source_ref != self.source_ref:
                raise OwnerTruthCandidateExtractionContractError("candidate evidence must reference this extraction Source")
            if candidate.proposal_hash in seen_hashes:
                raise OwnerTruthCandidateExtractionContractError("an extraction cannot contain duplicate candidate proposals")
            seen_hashes.add(candidate.proposal_hash)

    @property
    def business_target_key(self) -> str:
        return sha256(f"owner-truth-extraction:{self.extraction_id}".encode("utf-8")).hexdigest()

    @property
    def completion_outcome(self) -> str:
        return "failed" if self.status is ExtractionResultStatus.FAILED else "completed"

    @property
    def completion_reason_code(self) -> str:
        if self.status is ExtractionResultStatus.SUCCEEDED:
            return "candidateProposalsPersisted"
        if self.status is ExtractionResultStatus.QUARANTINED:
            return "resultQuarantined"
        return str(self.failure_code)

    def immutable_fingerprint(self) -> str:
        return _digest(
            {
                "candidateIds": [candidate.candidate_id for candidate in self.candidate_records],
                "candidateProposalHashes": [candidate.proposal_hash for candidate in self.candidate_records],
                "extractionId": self.extraction_id,
                "failureCode": self.failure_code,
                "intentOperationId": self.intent.operation_id,
                "modelId": self.model_id,
                "payload": self.payload,
                "policyVersion": self.policy_version,
                "promptVersion": self.prompt_version,
                "resultHash": self.result_hash,
                "retryable": self.retryable,
                "sourceContentHash": self.source_content_hash,
                "sourceId": self.source_ref.source_id,
                "sourceVersion": self.source_ref.source_version,
                "status": self.status.value,
            }
        )


@dataclass(frozen=True)
class SyntheticCandidateExtractionCommand:
    """A deterministic, provider-neutral result used before G3 provider enablement."""

    intent: AsyncEffectIntent
    extractor_id: str
    model_id: str
    prompt_version: str
    policy_version: str
    source_content_hash: str
    status: ExtractionResultStatus
    proposals: tuple[CandidateProposal, ...]
    failure_code: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        _typed_source_intent(self.intent)
        object.__setattr__(self, "extractor_id", _identifier(self.extractor_id, field="extractor_id"))
        object.__setattr__(self, "model_id", require_nonblank(self.model_id, field="model_id"))
        object.__setattr__(self, "prompt_version", require_nonblank(self.prompt_version, field="prompt_version"))
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        object.__setattr__(self, "source_content_hash", _sha256(self.source_content_hash, field="source_content_hash"))
        try:
            object.__setattr__(self, "status", ExtractionResultStatus(self.status))
        except ValueError as exc:
            raise OwnerTruthCandidateExtractionContractError("unsupported extraction status") from exc
        object.__setattr__(self, "proposals", tuple(self.proposals))
        for proposal in self.proposals:
            if not isinstance(proposal, CandidateProposal):
                raise OwnerTruthCandidateExtractionContractError("candidate proposal is required")
        if self.status is ExtractionResultStatus.SUCCEEDED:
            if self.failure_code is not None or self.retryable:
                raise OwnerTruthCandidateExtractionContractError("successful extraction cannot carry failure state")
        else:
            if self.proposals:
                raise OwnerTruthCandidateExtractionContractError("failed or quarantined extraction cannot carry proposals")
            object.__setattr__(self, "failure_code", _identifier(self.failure_code, field="failure_code"))
            if self.status is ExtractionResultStatus.QUARANTINED and self.retryable:
                raise OwnerTruthCandidateExtractionContractError("quarantined extraction is not retryable")

    @property
    def extraction_id(self) -> str:
        return str(
            uuid5(
                _EXTRACTION_NAMESPACE,
                ":".join(
                    (
                        self.intent.operation_id,
                        self.source_content_hash,
                        self.extractor_id,
                        self.model_id,
                        self.prompt_version,
                        self.policy_version,
                    )
                ),
            )
        )

    def write_record(self) -> OwnerTruthCandidateExtractionWriteRecord:
        source_ref = SourceRef(
            vault_id=self.intent.target.vault_id,
            source_id=self.intent.target.resource_id,
            source_version=self.intent.target.resource_version,
        )
        candidate_records = tuple(
            proposal.write_record(extraction_id=self.extraction_id, source_ref=source_ref)
            for proposal in self.proposals
        )
        payload = {
            "candidateIds": [candidate.candidate_id for candidate in candidate_records],
            "candidateProposalHashes": [candidate.proposal_hash for candidate in candidate_records],
            "extractorId": self.extractor_id,
            "modelId": self.model_id,
            "policyVersion": self.policy_version,
            "promptVersion": self.prompt_version,
            "retryable": bool(self.retryable),
            "schemaVersion": OWNER_TRUTH_EXTRACTION_SCHEMA_VERSION,
            "sourceRef": {
                "contentHash": self.source_content_hash,
                "sourceId": source_ref.source_id,
                "sourceVersion": source_ref.source_version,
            },
            "status": self.status.value,
        }
        if self.failure_code is not None:
            payload["failureCode"] = self.failure_code
        result_hash = _digest(payload)
        return OwnerTruthCandidateExtractionWriteRecord(
            intent=self.intent,
            extraction_id=self.extraction_id,
            source_ref=source_ref,
            source_content_hash=self.source_content_hash,
            extractor_id=self.extractor_id,
            model_id=self.model_id,
            prompt_version=self.prompt_version,
            policy_version=self.policy_version,
            status=self.status,
            result_hash=result_hash,
            payload=payload,
            failure_code=self.failure_code,
            retryable=bool(self.retryable),
            candidate_records=candidate_records,
        )


__all__ = [
    "CandidateEvidenceSpan",
    "CandidateProposal",
    "CandidateReviewMode",
    "ExtractionResultStatus",
    "OWNER_TRUTH_CANDIDATE_SCHEMA_VERSION",
    "OWNER_TRUTH_EXTRACTION_SCHEMA_VERSION",
    "OwnerTruthCandidateExtractionConflict",
    "OwnerTruthCandidateExtractionContractError",
    "OwnerTruthCandidateExtractionWriteRecord",
    "SyntheticCandidateExtractionCommand",
]
