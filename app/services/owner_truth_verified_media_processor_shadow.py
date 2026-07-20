"""Default-off admission planner for future verified media processors.

This is a G0-only contract. It never reads media bytes, calls a provider,
enqueues a job, or persists an ExtractionResult/Candidate. It only proves the
preconditions and deterministic next action a later sourceExtraction worker
would need to satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import math
import re
from typing import Any, Mapping

from app.services.owner_truth_media_source_object_shadow import (
    MediaSourceObjectAdmissionContext,
    build_media_source_object_admission_shadow,
)


VERIFIED_MEDIA_PROCESSOR_ADMISSION_SCHEMA_VERSION = (
    "owner-truth-verified-media-processor-admission-shadow-v1"
)
VERIFIED_MEDIA_EXTRACTION_RESULT_SHADOW_SCHEMA_VERSION = (
    "owner-truth-media-extraction-result-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PRIOR_ATTEMPT_OUTCOMES = frozenset(
    {"succeeded", "failed_retryable", "failed_terminal", "unknown"}
)


class VerifiedMediaProcessorContractError(ValueError):
    """The future processor metadata is not safe to plan."""


class VerifiedMediaProcessorDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_DESCRIPTOR = "invalid_descriptor"
    PARENT_NOT_ELIGIBLE = "parent_not_eligible"
    PROCESSOR_MEDIA_KIND_MISMATCH = "processor_media_kind_mismatch"
    PROCESSOR_DISABLED = "processor_disabled"
    PROCESSOR_MODE_UNAVAILABLE = "processor_mode_unavailable"
    INVALID_PRIOR_ATTEMPT = "invalid_prior_attempt"
    STALE_OR_FOREIGN_ATTEMPT = "stale_or_foreign_attempt"
    WOULD_DEDUPLICATE = "would_deduplicate"
    WOULD_RETRY_SOURCE_EXTRACTION = "would_retry_source_extraction"
    TERMINAL_FAILURE_RECORDED = "terminal_failure_recorded"
    WOULD_QUERY_RECONCILE = "would_query_reconcile"
    WOULD_ENQUEUE_SOURCE_EXTRACTION = "would_enqueue_source_extraction"


class VerifiedMediaExtractionResultStatus(str, Enum):
    """The only result states a future media processor may report."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    QUARANTINED = "quarantined"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise VerifiedMediaProcessorContractError(f"{field} must be an opaque identifier")
    return normalized


def _sha256(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise VerifiedMediaProcessorContractError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise VerifiedMediaProcessorContractError("processor plan must be serializable") from exc


@dataclass(frozen=True)
class VerifiedMediaProcessorDescriptor:
    """Versioned processor and policy metadata, never a provider credential."""

    processor_id: str
    processor_version: str
    policy_version: str
    media_kind: str
    enabled: bool
    execution_mode: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "processor_id", _identifier(self.processor_id, field="processor_id"))
        object.__setattr__(
            self,
            "processor_version",
            _identifier(self.processor_version, field="processor_version"),
        )
        object.__setattr__(self, "policy_version", _identifier(self.policy_version, field="policy_version"))
        object.__setattr__(self, "media_kind", _identifier(self.media_kind, field="media_kind").lower())
        if not isinstance(self.enabled, bool):
            raise VerifiedMediaProcessorContractError("enabled must be a boolean")
        object.__setattr__(self, "execution_mode", _identifier(self.execution_mode, field="execution_mode"))


@dataclass(frozen=True)
class VerifiedMediaProcessorAdmissionShadow:
    """Value-free plan for a later sourceExtraction effect admission."""

    enabled: bool
    disposition: VerifiedMediaProcessorDisposition
    reason_code: str
    media_kind: str | None = None
    processor_id: str | None = None
    processor_version: str | None = None
    policy_version: str | None = None
    source_object_fingerprint: str | None = None
    extraction_request_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise VerifiedMediaProcessorContractError("enabled must be a boolean")
        if not isinstance(self.disposition, VerifiedMediaProcessorDisposition):
            raise VerifiedMediaProcessorContractError("disposition is required")
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))
        for field in ("media_kind", "processor_id", "processor_version", "policy_version"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _identifier(value, field=field))
        for field in ("source_object_fingerprint", "extraction_request_fingerprint"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _sha256(value, field=field))

    @property
    def would_enqueue_source_extraction(self) -> bool:
        return self.disposition in {
            VerifiedMediaProcessorDisposition.WOULD_ENQUEUE_SOURCE_EXTRACTION,
            VerifiedMediaProcessorDisposition.WOULD_RETRY_SOURCE_EXTRACTION,
        }

    @property
    def requires_separate_candidate_proposal(self) -> bool:
        return self.would_enqueue_source_extraction

    @property
    def requires_reconcile(self) -> bool:
        return self.disposition is VerifiedMediaProcessorDisposition.WOULD_QUERY_RECONCILE

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "candidateProposalPerformed": False,
            "confirmedMemoryWritten": False,
            "enabled": self.enabled,
            "extractionResultPersisted": False,
            "objectReadPerformed": False,
            "personaWritten": False,
            "providerCallPerformed": False,
            "reasonCode": self.reason_code,
            "requiresReconcile": self.requires_reconcile,
            "requiresSeparateCandidateProposal": self.requires_separate_candidate_proposal,
            "schemaVersion": VERIFIED_MEDIA_PROCESSOR_ADMISSION_SCHEMA_VERSION,
            "shadowOnly": True,
            "sourceExtractionEnqueued": False,
            "status": self.disposition.value,
            "wouldEnqueueSourceExtraction": self.would_enqueue_source_extraction,
        }
        if self.media_kind is not None:
            summary["mediaKind"] = self.media_kind
        if self.processor_id is not None:
            summary["processorId"] = self.processor_id
        if self.processor_version is not None:
            summary["processorVersion"] = self.processor_version
        if self.policy_version is not None:
            summary["policyVersion"] = self.policy_version
        if self.source_object_fingerprint is not None:
            summary["sourceObjectFingerprint"] = self.source_object_fingerprint
        if self.extraction_request_fingerprint is not None:
            summary["extractionRequestFingerprint"] = self.extraction_request_fingerprint
        return summary


@dataclass(frozen=True)
class VerifiedMediaExtractionSegmentRef:
    """A value-minimized segment reference, never raw media or extracted text."""

    segment_id: str
    locator_fingerprint: str
    content_fingerprint: str
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "segment_id", _identifier(self.segment_id, field="segment_id"))
        object.__setattr__(
            self,
            "locator_fingerprint",
            _sha256(self.locator_fingerprint, field="locator_fingerprint"),
        )
        object.__setattr__(
            self,
            "content_fingerprint",
            _sha256(self.content_fingerprint, field="content_fingerprint"),
        )
        try:
            confidence = float(self.confidence)
        except (TypeError, ValueError) as exc:
            raise VerifiedMediaProcessorContractError("segment confidence must be numeric") from exc
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise VerifiedMediaProcessorContractError("segment confidence must be within [0, 1]")
        object.__setattr__(self, "confidence", confidence)

    def fingerprint_material(self) -> dict[str, object]:
        return {
            "confidence": self.confidence,
            "contentFingerprint": self.content_fingerprint,
            "locatorFingerprint": self.locator_fingerprint,
            "segmentId": self.segment_id,
        }


@dataclass(frozen=True)
class VerifiedMediaExtractionResultShadow:
    """Immutable, side-effect-free media result awaiting a separate proposal command.

    This is deliberately not compatible with the text-bearing Candidate writer.
    It retains only fingerprints and confidence, never bytes, transcripts,
    provider locators, Candidate payloads, or owner authority.
    """

    admission: VerifiedMediaProcessorAdmissionShadow
    status: VerifiedMediaExtractionResultStatus
    segments: tuple[VerifiedMediaExtractionSegmentRef, ...] = ()
    failure_code: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.admission, VerifiedMediaProcessorAdmissionShadow):
            raise VerifiedMediaProcessorContractError("media result requires a processor admission")
        if not self.admission.would_enqueue_source_extraction:
            raise VerifiedMediaProcessorContractError(
                "media result requires an eligible enqueue or retry processor admission"
            )
        if (
            self.admission.media_kind is None
            or self.admission.processor_id is None
            or self.admission.processor_version is None
            or self.admission.policy_version is None
            or self.admission.extraction_request_fingerprint is None
        ):
            raise VerifiedMediaProcessorContractError("media result admission is incomplete")
        try:
            object.__setattr__(self, "status", VerifiedMediaExtractionResultStatus(self.status))
        except ValueError as exc:
            raise VerifiedMediaProcessorContractError("unsupported media extraction result status") from exc
        if not isinstance(self.retryable, bool):
            raise VerifiedMediaProcessorContractError("retryable must be a boolean")
        object.__setattr__(self, "segments", tuple(self.segments))
        for segment in self.segments:
            if not isinstance(segment, VerifiedMediaExtractionSegmentRef):
                raise VerifiedMediaProcessorContractError("media result segment reference is required")
        if len({segment.segment_id for segment in self.segments}) != len(self.segments):
            raise VerifiedMediaProcessorContractError("media result segment ids must be unique")
        if self.status is VerifiedMediaExtractionResultStatus.SUCCEEDED:
            if self.failure_code is not None or self.retryable:
                raise VerifiedMediaProcessorContractError(
                    "successful media result cannot carry failure state"
                )
        else:
            if self.segments:
                raise VerifiedMediaProcessorContractError(
                    "failed or quarantined media result cannot carry segments"
                )
            object.__setattr__(self, "failure_code", _identifier(self.failure_code, field="failure_code"))
            if self.status is VerifiedMediaExtractionResultStatus.QUARANTINED and self.retryable:
                raise VerifiedMediaProcessorContractError("quarantined media result is not retryable")

    @property
    def result_fingerprint(self) -> str:
        material = {
            "admissionFingerprint": self.admission.extraction_request_fingerprint,
            "failureCode": self.failure_code,
            "retryable": self.retryable,
            "segments": [segment.fingerprint_material() for segment in self.segments],
            "status": self.status.value,
        }
        return sha256(_canonical_json(material).encode("utf-8")).hexdigest()

    @property
    def requires_separate_candidate_proposal(self) -> bool:
        return self.status is VerifiedMediaExtractionResultStatus.SUCCEEDED and bool(self.segments)

    def value_free_summary(self) -> dict[str, object]:
        return {
            "candidateProposalPerformed": False,
            "confirmedMemoryWritten": False,
            "extractionRequestFingerprint": self.admission.extraction_request_fingerprint,
            "extractionResultPersisted": False,
            "mediaKind": self.admission.media_kind,
            "objectReadPerformed": False,
            "personaWritten": False,
            "policyVersion": self.admission.policy_version,
            "processorId": self.admission.processor_id,
            "processorVersion": self.admission.processor_version,
            "providerCallPerformed": False,
            "requiresSeparateCandidateProposal": self.requires_separate_candidate_proposal,
            "resultFingerprint": self.result_fingerprint,
            "retryable": self.retryable,
            "schemaVersion": VERIFIED_MEDIA_EXTRACTION_RESULT_SHADOW_SCHEMA_VERSION,
            "segmentCount": len(self.segments),
            "shadowOnly": True,
            "status": self.status.value,
        }


def build_verified_media_extraction_result_shadow(
    admission: VerifiedMediaProcessorAdmissionShadow,
    *,
    status: VerifiedMediaExtractionResultStatus,
    segments: tuple[VerifiedMediaExtractionSegmentRef, ...] = (),
    failure_code: str | None = None,
    retryable: bool = False,
) -> VerifiedMediaExtractionResultShadow:
    """Build a synthetic, immutable result without reading or persisting media."""

    return VerifiedMediaExtractionResultShadow(
        admission=admission,
        status=status,
        segments=segments,
        failure_code=failure_code,
        retryable=retryable,
    )


def _extraction_request_fingerprint(
    *,
    source_object_fingerprint: str,
    descriptor: VerifiedMediaProcessorDescriptor,
) -> str:
    material = {
        "mediaKind": descriptor.media_kind,
        "policyVersion": descriptor.policy_version,
        "processorId": descriptor.processor_id,
        "processorVersion": descriptor.processor_version,
        "purpose": "candidateExtraction",
        "sourceObjectFingerprint": source_object_fingerprint,
    }
    return sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _result(
    *,
    enabled: bool,
    disposition: VerifiedMediaProcessorDisposition,
    reason_code: str,
    media_kind: str | None = None,
    descriptor: VerifiedMediaProcessorDescriptor | None = None,
    source_object_fingerprint: str | None = None,
    extraction_request_fingerprint: str | None = None,
) -> VerifiedMediaProcessorAdmissionShadow:
    return VerifiedMediaProcessorAdmissionShadow(
        enabled=enabled,
        disposition=disposition,
        reason_code=reason_code,
        media_kind=media_kind,
        processor_id=descriptor.processor_id if descriptor else None,
        processor_version=descriptor.processor_version if descriptor else None,
        policy_version=descriptor.policy_version if descriptor else None,
        source_object_fingerprint=source_object_fingerprint,
        extraction_request_fingerprint=extraction_request_fingerprint,
    )


def _prior_attempt_disposition(
    prior_attempt: Mapping[str, Any] | None,
    *,
    extraction_request_fingerprint: str,
) -> tuple[VerifiedMediaProcessorDisposition, str] | None:
    if prior_attempt is None:
        return None
    if not isinstance(prior_attempt, Mapping):
        return (
            VerifiedMediaProcessorDisposition.INVALID_PRIOR_ATTEMPT,
            "invalidPriorAttempt",
        )
    try:
        prior_fingerprint = _sha256(
            prior_attempt.get("requestFingerprint"),
            field="prior_request_fingerprint",
        )
        outcome = _identifier(prior_attempt.get("outcome"), field="prior_outcome").lower()
    except VerifiedMediaProcessorContractError:
        return (
            VerifiedMediaProcessorDisposition.INVALID_PRIOR_ATTEMPT,
            "invalidPriorAttempt",
        )
    if prior_fingerprint != extraction_request_fingerprint:
        return (
            VerifiedMediaProcessorDisposition.STALE_OR_FOREIGN_ATTEMPT,
            "staleOrForeignAttempt",
        )
    if outcome not in _PRIOR_ATTEMPT_OUTCOMES:
        return (
            VerifiedMediaProcessorDisposition.INVALID_PRIOR_ATTEMPT,
            "invalidPriorAttempt",
        )
    dispositions = {
        "succeeded": (
            VerifiedMediaProcessorDisposition.WOULD_DEDUPLICATE,
            "matchingSucceededAttempt",
        ),
        "failed_retryable": (
            VerifiedMediaProcessorDisposition.WOULD_RETRY_SOURCE_EXTRACTION,
            "matchingRetryableFailure",
        ),
        "failed_terminal": (
            VerifiedMediaProcessorDisposition.TERMINAL_FAILURE_RECORDED,
            "matchingTerminalFailure",
        ),
        "unknown": (
            VerifiedMediaProcessorDisposition.WOULD_QUERY_RECONCILE,
            "matchingUnknownAttemptRequiresReconcile",
        ),
    }
    return dispositions[outcome]


def plan_verified_media_processor_admission(
    source_object: Mapping[str, Any] | object,
    *,
    context: MediaSourceObjectAdmissionContext,
    descriptor: VerifiedMediaProcessorDescriptor | object,
    prior_attempt: Mapping[str, Any] | None = None,
    enabled: bool = False,
) -> VerifiedMediaProcessorAdmissionShadow:
    """Plan a future Candidate-only extraction from a verified source object.

    The global switch is intentionally default-off. A later worker may consume
    the returned fingerprint only after independent effect, provider and
    persistence gates are enabled; this function itself performs none of them.
    """

    if not enabled:
        return _result(
            enabled=False,
            disposition=VerifiedMediaProcessorDisposition.SHADOW_DISABLED,
            reason_code="shadowDisabled",
        )
    if not isinstance(context, MediaSourceObjectAdmissionContext):
        raise VerifiedMediaProcessorContractError("admission context is required")
    if not isinstance(descriptor, VerifiedMediaProcessorDescriptor):
        return _result(
            enabled=True,
            disposition=VerifiedMediaProcessorDisposition.INVALID_DESCRIPTOR,
            reason_code="invalidProcessorDescriptor",
        )

    parent = build_media_source_object_admission_shadow(
        source_object,
        context=context,
        enabled=True,
    )
    if not parent.would_be_processor_eligible:
        return _result(
            enabled=True,
            disposition=VerifiedMediaProcessorDisposition.PARENT_NOT_ELIGIBLE,
            reason_code="parentNotEligible",
            media_kind=parent.media_kind,
            descriptor=descriptor,
            source_object_fingerprint=parent.source_object_fingerprint,
        )

    media_kind = parent.media_kind
    source_object_fingerprint = parent.source_object_fingerprint
    if media_kind is None or source_object_fingerprint is None:
        return _result(
            enabled=True,
            disposition=VerifiedMediaProcessorDisposition.PARENT_NOT_ELIGIBLE,
            reason_code="parentEligibilityEvidenceMissing",
            descriptor=descriptor,
        )
    if descriptor.media_kind != media_kind:
        return _result(
            enabled=True,
            disposition=VerifiedMediaProcessorDisposition.PROCESSOR_MEDIA_KIND_MISMATCH,
            reason_code="processorMediaKindMismatch",
            media_kind=media_kind,
            descriptor=descriptor,
            source_object_fingerprint=source_object_fingerprint,
        )
    if not descriptor.enabled:
        return _result(
            enabled=True,
            disposition=VerifiedMediaProcessorDisposition.PROCESSOR_DISABLED,
            reason_code="processorDisabled",
            media_kind=media_kind,
            descriptor=descriptor,
            source_object_fingerprint=source_object_fingerprint,
        )
    if descriptor.execution_mode != "synthetic":
        return _result(
            enabled=True,
            disposition=VerifiedMediaProcessorDisposition.PROCESSOR_MODE_UNAVAILABLE,
            reason_code="processorModeUnavailable",
            media_kind=media_kind,
            descriptor=descriptor,
            source_object_fingerprint=source_object_fingerprint,
        )

    extraction_request_fingerprint = _extraction_request_fingerprint(
        source_object_fingerprint=source_object_fingerprint,
        descriptor=descriptor,
    )
    prior_disposition = _prior_attempt_disposition(
        prior_attempt,
        extraction_request_fingerprint=extraction_request_fingerprint,
    )
    if prior_disposition is not None:
        disposition, reason_code = prior_disposition
        return _result(
            enabled=True,
            disposition=disposition,
            reason_code=reason_code,
            media_kind=media_kind,
            descriptor=descriptor,
            source_object_fingerprint=source_object_fingerprint,
            extraction_request_fingerprint=extraction_request_fingerprint,
        )

    return _result(
        enabled=True,
        disposition=VerifiedMediaProcessorDisposition.WOULD_ENQUEUE_SOURCE_EXTRACTION,
        reason_code="verifiedParentSyntheticProcessorEligible",
        media_kind=media_kind,
        descriptor=descriptor,
        source_object_fingerprint=source_object_fingerprint,
        extraction_request_fingerprint=extraction_request_fingerprint,
    )


__all__ = [
    "VERIFIED_MEDIA_EXTRACTION_RESULT_SHADOW_SCHEMA_VERSION",
    "VERIFIED_MEDIA_PROCESSOR_ADMISSION_SCHEMA_VERSION",
    "VerifiedMediaExtractionResultShadow",
    "VerifiedMediaExtractionResultStatus",
    "VerifiedMediaExtractionSegmentRef",
    "VerifiedMediaProcessorAdmissionShadow",
    "VerifiedMediaProcessorContractError",
    "VerifiedMediaProcessorDescriptor",
    "VerifiedMediaProcessorDisposition",
    "build_verified_media_extraction_result_shadow",
    "plan_verified_media_processor_admission",
]
