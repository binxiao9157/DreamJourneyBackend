"""Default-disabled worker for private Owner Truth Candidates.

The worker consumes only the value-free ``ownerTruth.source.created`` effect
for a live text-bearing Source. Ordinary owner-authored Sources remain
deterministic. An explicitly marked closed Live conversation may invoke the
server-owned DeepSeek organizer after user consent; assistant turns are
context-only and every draft remains pending explicit Owner review. Source
text never appears in worker output, logs, or a public API.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import re
import socket
import sys
from time import perf_counter, sleep
from typing import Any, Callable, Mapping, Optional, Protocol

import httpx

from app.async_effects.consumer_repository import OwnerTruthSourceBlockedConsumerCommand
from app.async_effects.contracts import (
    AsyncEffectIntent,
    AsyncEffectJobState,
    is_async_effect_store_ready,
    resolve_async_effect_runtime_status,
)
from app.async_effects.dead_letter_effects import DeadLetterCause, admit_dead_letter
from app.async_effects.lease_repository import (
    AsyncEffectJobLease,
    AsyncEffectLeaseCancelled,
    AsyncEffectLeaseLost,
)
from app.async_effects.message_notification_effects import InAppMessageKind
from app.async_effects.owner_business_message_projection import (
    enqueue_owner_business_message,
)
from app.async_effects.worker_lifecycle import WorkerDrainController, WorkerLeaseHeartbeat
from app.core.config import Settings
from app.domain.owner_truth.candidate_extraction import (
    CandidateEvidenceSpan,
    CandidateProposal,
    CandidateReviewMode,
    ExtractionResultStatus,
    SyntheticCandidateExtractionCommand,
)
from app.domain.owner_truth.contracts import (
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_SCHEMA_VERSION,
    OWNER_TRUTH_SCHEMA_VERSION_V5,
    empty_memory_facets,
    enrich_memory_payload_v5,
    validate_memory_facets,
)
from app.observability.operation_metrics import OperationMetricRecorder
from app.services.deepseek import (
    DeepSeekLiveMemoryOrganizationProxy,
    DeepSeekTextMemoryOrganizationProxy,
    PreparedLiveModelRequest,
)
from app.services.owner_truth_live_memory_support import (
    LIVE_MEMORY_SUPPORT_VALIDATOR_VERSION,
    build_live_memory_evidence_catalog,
    validate_live_memory_support,
)
from app.services.owner_truth_live_memory_contract_errors import (
    LiveMemoryContractFailure,
    LiveMemoryContractRetryContext,
    contract_failure,
)
from app.services.owner_truth_live_long_memory import (
    InMemoryLiveLongMemoryRepository,
    LiveLongMemoryAtomRecord,
    LiveLongMemoryBudgetExhausted,
    LiveLongMemoryBudgetPolicy,
    LiveLongMemoryConflict,
    LiveLongMemoryRepository,
    LiveLongMemoryRunIdentity,
    LiveLongMemoryUnitPlan,
    StoreBackedLiveLongMemoryRepository,
    build_publication_manifest,
    conservative_token_estimate,
)
from app.services.owner_truth_candidate_extraction import (
    OwnerTruthCandidateExtractionInput,
    OwnerTruthCandidateExtractionResult,
    OwnerTruthCandidateExtractionService,
)
from app.services.store_factory import close_store, make_store, open_store


_CONSUMER_NAME = "ownerTruth.source.extraction"
_SOURCE_CANDIDATE_EXTRACTION_JOB_TYPE = "ownerTruth.source.created"
_DEFAULT_LEASE_SECONDS = 60
_DEFAULT_RETRY_SECONDS = 30
_WORKER_METRIC_COMPONENT_ID = "ownerTruthCandidateExtractionWorker"
_TERMINAL_FAILURE_EXTRACTOR_ID = "candidateWorkerTerminalizer"
_TERMINAL_FAILURE_MODEL_ID = "notApplicable"
_TERMINAL_FAILURE_PROMPT_VERSION = "owner-truth-candidate-worker-terminal-v1"
_TERMINAL_FAILURE_REASON = "candidateExtractionRetriesExhausted"
_LOGGER = logging.getLogger(__name__)
_SAFE_STAGE_DIAGNOSTIC_NAMES = frozenset(
    {
        "organizationInputBuilt",
        "organizationRequestStarted",
        "organizationResponseReceived",
        "organizationDecoded",
        "organizationValidated",
        "supportInputBuilt",
        "supportRequestStarted",
        "supportResponseReceived",
        "supportDecoded",
        "supportValidated",
        "proposalBuildStarted",
        "proposalBuildCompleted",
        "candidateCommitStarted",
        "candidateCommitSucceeded",
    }
)
_SAFE_STAGE_DIAGNOSTIC_COUNTS = frozenset(
    {"turnCount", "userTurnCount", "memoryCount", "candidateCount", "statusCode"}
)


def _log_stage_diagnostic(event: Mapping[str, Any]) -> None:
    print(
        json.dumps(
            {
                "event": "ownerTruthCandidateExtractionStage",
                **dict(event),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


@dataclass(frozen=True)
class CandidateExtractionFailure:
    code: str
    stage: str
    error_type: str
    retryable: bool
    dead_letter_cause: DeadLetterCause
    provider_status: int | None = None
    contract_retry_eligible: bool = False
    retry_after_seconds: int | None = None


def _classify_candidate_extraction_failure(error: Exception) -> CandidateExtractionFailure:
    if isinstance(error, LiveLongMemoryBudgetExhausted):
        return CandidateExtractionFailure(
            code="candidateExtraction.live.workerExecution.budgetExhausted",
            stage="workerExecution",
            error_type="budget",
            retryable=False,
            dead_letter_cause=DeadLetterCause.MAX_ATTEMPTS_EXCEEDED,
        )
    if isinstance(error, LiveMemoryContractFailure):
        return CandidateExtractionFailure(
            code=error.code,
            stage=error.stage,
            error_type=error.category,
            retryable=error.transport_retryable,
            dead_letter_cause=(
                DeadLetterCause.MAX_ATTEMPTS_EXCEEDED
                if error.transport_retryable
                else DeadLetterCause.POISON_PAYLOAD
            ),
            provider_status=error.provider_status,
            contract_retry_eligible=error.contract_retry_eligible,
            retry_after_seconds=error.retry_after_seconds,
        )
    if isinstance(error, httpx.HTTPStatusError):
        status = int(error.response.status_code)
        retryable = status == 429 or status >= 500
        if status in {401, 403}:
            code = "candidateExtraction.providerAuthorization.rejected"
        elif status == 402:
            code = "candidateExtraction.providerQuota.unavailable"
        elif status == 404:
            code = "candidateExtraction.providerModel.unavailable"
        elif status == 429:
            code = "candidateExtraction.providerRateLimited"
        elif status >= 500:
            code = "candidateExtraction.providerHttp.transient"
        else:
            code = "candidateExtraction.providerRequest.rejected"
        return CandidateExtractionFailure(
            code=code,
            stage="providerRequest",
            error_type="httpStatus",
            retryable=retryable,
            dead_letter_cause=(
                DeadLetterCause.MAX_ATTEMPTS_EXCEEDED
                if retryable
                else DeadLetterCause.MANUAL_INTERVENTION_REQUIRED
            ),
            provider_status=status,
        )
    if isinstance(error, httpx.TimeoutException):
        return CandidateExtractionFailure(
            code="candidateExtraction.providerRequest.timeout",
            stage="providerRequest",
            error_type="timeout",
            retryable=True,
            dead_letter_cause=DeadLetterCause.MAX_ATTEMPTS_EXCEEDED,
        )
    if isinstance(error, httpx.TransportError):
        return CandidateExtractionFailure(
            code="candidateExtraction.providerRequest.transport",
            stage="providerRequest",
            error_type="transport",
            retryable=True,
            dead_letter_cause=DeadLetterCause.MAX_ATTEMPTS_EXCEEDED,
        )
    if isinstance(error, ValueError):
        return CandidateExtractionFailure(
            code="candidateExtraction.responseContract.invalid",
            stage="responseValidation",
            error_type="contract",
            retryable=False,
            dead_letter_cause=DeadLetterCause.POISON_PAYLOAD,
        )
    if isinstance(error, RuntimeError):
        return CandidateExtractionFailure(
            code="candidateExtraction.runtime.blocked",
            stage="runtimeConfiguration",
            error_type="configuration",
            retryable=False,
            dead_letter_cause=DeadLetterCause.MANUAL_INTERVENTION_REQUIRED,
        )
    return CandidateExtractionFailure(
        code="candidateExtraction.internal.transient",
        stage="workerExecution",
        error_type="internal",
        retryable=True,
        dead_letter_cause=DeadLetterCause.MAX_ATTEMPTS_EXCEEDED,
    )


class OwnerTruthCandidateExtractionWorkerError(RuntimeError):
    """The typed Candidate worker cannot safely terminalize its current job."""


def _result_hash(*parts: str) -> str:
    return sha256(":".join(parts).encode("utf-8")).hexdigest()


_FAMILY_REPORT_ORIGINS = frozenset(
    {
        "familyContributionGrant",
        "familyContributionReview",
    }
)


def _source_provenance(
    metadata: Mapping[str, Any],
) -> tuple[PerspectiveType, EpistemicStatus]:
    """Keep server-established source provenance through model organization."""

    origin = str(metadata.get("origin") or "").strip()
    is_family_report = (
        origin in _FAMILY_REPORT_ORIGINS
        or bool(str(metadata.get("familyContributionGrantId") or "").strip())
        or bool(str(metadata.get("familyContributionSubmissionId") or "").strip())
        or metadata.get("perspectiveType") == "familyReport"
    )
    if is_family_report:
        return PerspectiveType.REPORTED, EpistemicStatus.REPORTED
    return PerspectiveType.FIRST_PERSON, EpistemicStatus.RECALLED


def _typed_source_provenance(
    *,
    source: OwnerTruthCandidateExtractionInput,
    inferred: bool = False,
) -> dict[str, Any]:
    """Build provenance only from the already-persisted Source boundary.

    The extraction model is never allowed to choose who spoke, which account
    contributed, or what evidence supports a Candidate.  Tests may use an
    input without a database identity, in which case the immutable candidate
    write record still retains its source span and this typed field remains
    intentionally empty rather than fabricated.
    """

    metadata = source.source_metadata or {}
    perspective, _epistemic = _source_provenance(metadata)
    origin = str(metadata.get("origin") or "").strip()
    if inferred:
        mode = "inferred"
    elif perspective is PerspectiveType.REPORTED:
        mode = "familyReport"
    elif origin in {
        "documentImport",
        "ocrSourceProcessing",
        "mediaSourceObjectProcessing",
    } or str(metadata.get("mediaKind") or "").strip() in {"document", "ocr"}:
        mode = "documented"
    elif str(metadata.get("speakerIdentity") or "").strip() == "unknown":
        mode = "unknown"
    else:
        mode = "selfReport"

    evidence_refs: list[dict[str, Any]] = []
    if source.source_id is not None and source.source_version is not None:
        evidence_refs.append(
            {
                "sourceId": source.source_id,
                "sourceVersion": source.source_version,
                "relation": "supports",
            }
        )
    return {
        "mode": mode,
        "speakerPersonId": metadata.get("speakerPersonId"),
        "contributorAccountId": (
            metadata.get("contributorAccountId")
            or metadata.get("submittedByAccountId")
        ),
        "evidenceRefs": evidence_refs,
    }


def _typed_subjects(
    source: OwnerTruthCandidateExtractionInput,
) -> tuple[str | None, str | None]:
    """Retain server-established subject scope without guessing from text."""

    metadata = source.source_metadata or {}
    memory_subject_id = metadata.get("memorySubjectId")
    claim_subject_id = metadata.get("claimSubjectId")
    return (
        memory_subject_id if isinstance(memory_subject_id, str) else None,
        claim_subject_id if isinstance(claim_subject_id, str) else None,
    )


class OwnerTruthCandidateExtractor(Protocol):
    """A private, provider-neutral Source-to-Candidate adapter."""

    def extract(
        self,
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
    ) -> SyntheticCandidateExtractionCommand:
        ...


class DeterministicOwnerTruthCandidateExtractor:
    """Create one reviewable Candidate from an explicit text Source.

    Owner text preserves first-person recalled provenance. A server-authorized
    family source preserves reported provenance, and trusted image inference
    preserves inferred provenance. Every proposal remains pending until review.
    The extractor never turns a model inference into a fact or bypasses review.
    """

    _EXTRACTOR_ID = "deterministicSourceEcho"
    _MODEL_ID = "deterministic-owner-source-v2"
    _PROMPT_VERSION = "owner-truth-candidate-extraction-owner-source-v2"
    _LIVE_EXTRACTOR_ID = "deterministicLiveConversationDigest"
    _LIVE_MODEL_ID = "deterministic-live-conversation-digest-v1"
    _LIVE_PROMPT_VERSION = "owner-truth-live-conversation-digest-v1"
    _IMAGE_EXTRACTOR_ID = "deterministicImageUnderstandingReview"
    _IMAGE_MODEL_ID = "provider-image-understanding-v1"
    _IMAGE_PROMPT_VERSION = "owner-truth-image-understanding-review-v1"
    _IMAGE_INVALID_REASON = "imageInferenceMetadataInvalid"
    _MAXIMUM_LIVE_SUMMARY_CHARACTERS = 800

    def extract(
        self,
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
    ) -> SyntheticCandidateExtractionCommand:
        normalized_text = source.source_text.strip()
        if not normalized_text:
            return SyntheticCandidateExtractionCommand(
                intent=intent,
                extractor_id=self._EXTRACTOR_ID,
                model_id=self._MODEL_ID,
                prompt_version=self._PROMPT_VERSION,
                policy_version=OWNER_TRUTH_SCHEMA_VERSION,
                source_content_hash=source.source_content_hash,
                status=ExtractionResultStatus.QUARANTINED,
                proposals=(),
                failure_code="sourceTextInvalid",
            )

        try:
            image_facets = self._trusted_image_facets(source)
        except ValueError:
            return SyntheticCandidateExtractionCommand(
                intent=intent,
                extractor_id=self._IMAGE_EXTRACTOR_ID,
                model_id=self._IMAGE_MODEL_ID,
                prompt_version=self._IMAGE_PROMPT_VERSION,
                policy_version=OWNER_TRUTH_SCHEMA_VERSION,
                source_content_hash=source.source_content_hash,
                status=ExtractionResultStatus.QUARANTINED,
                proposals=(),
                failure_code=self._IMAGE_INVALID_REASON,
            )

        live_summary = self._live_conversation_digest(source)
        summary = live_summary or normalized_text
        is_live_digest = live_summary is not None
        is_image_inference = image_facets is not None
        source_perspective, source_epistemic = _source_provenance(
            source.source_metadata or {}
        )
        memory_subject_id, claim_subject_id = _typed_subjects(source)
        proposal = CandidateProposal(
            memory_kind=MemoryKind.EXPERIENCE,
            perspective_type=(
                PerspectiveType.INFERRED if is_image_inference else source_perspective
            ),
            epistemic_status=(
                EpistemicStatus.INFERRED if is_image_inference else source_epistemic
            ),
            sensitivity=SensitivityLevel.STANDARD,
            content=enrich_memory_payload_v5(
                kind=MemoryKind.EXPERIENCE,
                payload={
                    "summary": summary,
                    "facets": image_facets or empty_memory_facets(confidence=0.0),
                },
                provenance=_typed_source_provenance(
                    source=source,
                    inferred=is_image_inference,
                ),
                memory_subject_id=memory_subject_id,
                claim_subject_id=claim_subject_id,
            ),
            evidence_span=CandidateEvidenceSpan(start=0, end=len(source.source_text)),
            confidence=0.0,
            review_mode=CandidateReviewMode.SINGLE,
            payload_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
        )
        return SyntheticCandidateExtractionCommand(
            intent=intent,
            extractor_id=(
                self._IMAGE_EXTRACTOR_ID
                if is_image_inference
                else self._LIVE_EXTRACTOR_ID
                if is_live_digest
                else self._EXTRACTOR_ID
            ),
            model_id=(
                self._IMAGE_MODEL_ID
                if is_image_inference
                else self._LIVE_MODEL_ID
                if is_live_digest
                else self._MODEL_ID
            ),
            prompt_version=(
                self._IMAGE_PROMPT_VERSION
                if is_image_inference
                else self._LIVE_PROMPT_VERSION
                if is_live_digest
                else self._PROMPT_VERSION
            ),
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            source_content_hash=source.source_content_hash,
            status=ExtractionResultStatus.SUCCEEDED,
            proposals=(proposal,),
        )

    @staticmethod
    def _trusted_image_facets(
        source: OwnerTruthCandidateExtractionInput,
    ) -> Mapping[str, Any] | None:
        metadata = source.source_metadata or {}
        has_facets = "candidateFacets" in metadata
        has_hash = "candidateFacetsHash" in metadata
        if not has_facets and not has_hash:
            return None
        if (
            not has_facets
            or not has_hash
            or metadata.get("origin") != "mediaSourceObjectProcessing"
            or metadata.get("mediaKind") != "image"
            or metadata.get("processorVersion") != "v2"
        ):
            raise ValueError("image inference metadata is not trusted")
        facets = metadata.get("candidateFacets")
        if not isinstance(facets, Mapping) or not validate_memory_facets(facets).accepted:
            raise ValueError("image inference facets are invalid")
        for facet_name, entries in facets.items():
            if facet_name == "confidence":
                continue
            if facet_name not in {"people", "time", "places"} and entries:
                raise ValueError("image inference facet scope is invalid")
            if any(
                not isinstance(entry, Mapping) or entry.get("evidenceMode") != "inferred"
                for entry in entries
            ):
                raise ValueError("image inference evidence mode is invalid")
        expected_hash = sha256(
            json.dumps(
                {"facets": dict(facets)},
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if metadata.get("candidateFacetsHash") != expected_hash:
            raise ValueError("image inference facets hash is invalid")
        return facets

    @classmethod
    def _live_conversation_digest(
        cls,
        source: OwnerTruthCandidateExtractionInput,
    ) -> str | None:
        metadata = source.source_metadata or {}
        if metadata.get("sourcePolicy") != "userEvidenceOnly":
            return None
        raw_turns = metadata.get("conversationTurns")
        if not isinstance(raw_turns, list):
            return None
        owner_texts = [
            str(turn.get("text") or "").strip()
            for turn in raw_turns
            if isinstance(turn, dict) and turn.get("role") == "user"
        ]
        owner_texts = [text for text in owner_texts if text]
        if not owner_texts:
            return None

        fragments: list[str] = []
        seen: set[str] = set()
        for text in owner_texts:
            for fragment in re.split(r"(?<=[。！？!?])\s*|[，,；;\r\n]+", text):
                normalized = re.sub(r"\s+", "", fragment).strip("，,；;。！？!? ")
                if not normalized:
                    continue
                dedupe_key = re.sub(r"[，,；;。！？!?]", "", normalized)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                fragments.append(normalized)

        summary = "；".join(fragments).strip("；")
        if not summary:
            return None
        if len(summary) > cls._MAXIMUM_LIVE_SUMMARY_CHARACTERS:
            bounded = summary[: cls._MAXIMUM_LIVE_SUMMARY_CHARACTERS]
            boundary = max(bounded.rfind("；"), bounded.rfind("。"))
            summary = bounded[:boundary] if boundary >= 40 else bounded
            summary = summary.rstrip("；。") + "……"
        elif not summary.endswith(("。", "！", "？", "……")):
            summary += "。"
        return summary


class LiveMemoryOrganizationProvider(Protocol):
    model: str
    prompt_version: str

    def request_organization(self, *, turns: list[dict[str, Any]]) -> dict[str, Any]:
        ...


class LiveMemorySupportReviewProvider(Protocol):
    support_prompt_version: str

    def request_support_review(
        self,
        *,
        turns: list[dict[str, Any]],
        memories: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ...


class LiveMemoryRelationReviewProvider(Protocol):
    model: str
    relation_prompt_version: str

    def request_relation_review(
        self,
        *,
        turns: list[dict[str, Any]],
        incoming: dict[str, Any],
        existing: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


class TextMemoryOrganizationProvider(Protocol):
    model: str
    prompt_version: str

    def request_organization(self, *, text: str) -> dict[str, Any]:
        ...

    def request_family_organization(self, *, text: str) -> dict[str, Any]:
        ...


class ModelAssistedOwnerTruthLiveConversationExtractor:
    """Use semantic organization only for explicitly marked closed Live text."""

    _EXTRACTOR_ID = "deepSeekLiveMemoryOrganizer"

    def __init__(
        self,
        *,
        settings: Settings,
        organizer: LiveMemoryOrganizationProvider | None = None,
        support_reviewer: LiveMemorySupportReviewProvider | None = None,
        relation_reviewer: LiveMemoryRelationReviewProvider | None = None,
        fallback: OwnerTruthCandidateExtractor | None = None,
        run_repository: LiveLongMemoryRepository | None = None,
    ) -> None:
        default_provider = DeepSeekLiveMemoryOrganizationProxy(settings)
        self._organizer = organizer or default_provider
        if support_reviewer is not None:
            self._support_reviewer = support_reviewer
        elif hasattr(self._organizer, "request_support_review"):
            self._support_reviewer = self._organizer
        elif organizer is None:
            self._support_reviewer = default_provider
        else:
            raise ValueError("Live memory organizer requires an independent support reviewer")
        if relation_reviewer is not None:
            self._relation_reviewer = relation_reviewer
        elif hasattr(self._organizer, "request_relation_review"):
            self._relation_reviewer = self._organizer
        elif organizer is None:
            self._relation_reviewer = default_provider
        else:
            self._relation_reviewer = None
        self._fallback = fallback or DeterministicOwnerTruthCandidateExtractor()
        self._live_organization_enabled = (
            settings.owner_truth_live_memory_organization_enabled
        )
        self._long_memory_pipeline_enabled = (
            settings.owner_truth_live_long_memory_pipeline_enabled
        )
        self._run_repository = run_repository or InMemoryLiveLongMemoryRepository()
        self._run_budget_policy = LiveLongMemoryBudgetPolicy()

    def preorganize_once(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 90,
    ) -> dict[str, Any]:
        """Build one private Live atom batch without creating a Source/Candidate.

        The durable unit lease is deliberately independent from the later
        candidate-extraction lease.  A stale worker must prove the same lease
        generation when it commits its private result.
        """

        if not self._long_memory_pipeline_enabled:
            return {"status": "idle", "reason": "liveLongMemoryPipelineDisabled"}
        lease = self._run_repository.claim_planned_unit(
            worker_id=worker_id,
            lease_seconds=max(1, min(900, int(lease_seconds))),
            maximum_concurrency=self._run_budget_policy.maximum_provider_concurrency,
        )
        if lease is None:
            return {"status": "idle", "reason": "noPlannedLiveMemoryUnit"}

        snapshot = self._run_repository.snapshot(lease.plan.run_id) or {}
        recovery = any(
            item.get("unitId") == lease.plan.unit_id
            for item in snapshot.get("attempts") or []
        )
        chunk = [dict(turn) for turn in lease.turns]
        try:
            validated, required, excluded, fragment_coverage = self._organize_validate_fragment(
                chunk=chunk,
                unit_plan=lease.plan,
                retry_context=(
                    LiveMemoryContractRetryContext(
                        attempt=2,
                        stage="workerExecution",
                        reason="sameUnitRecovery",
                    )
                    if recovery
                    else None
                ),
                stage_reporter=None,
            )
            atoms: list[LiveLongMemoryAtomRecord] = []
            published: list[dict[str, Any]] = []
            for memory in validated:
                atom = LiveLongMemoryAtomRecord.make(
                    run_id=lease.plan.run_id,
                    unit_id=lease.plan.unit_id,
                    memory=memory,
                )
                atoms.append(atom)
                published.append({**memory, "_atomIds": [atom.atom_id]})
            self._run_repository.record_unit_result(
                plan=lease.plan,
                atoms=atoms,
                output_hash=sha256(
                    json.dumps(published, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                coverage={
                    "requiredUserTurnIndices": sorted(required),
                    "excludedUserTurnIndices": sorted(excluded),
                    "fragmentCoverage": fragment_coverage,
                },
                lease_owner=lease.lease_owner,
                lease_generation=lease.lease_generation,
            )
            return {
                "status": "completed",
                "reason": "liveMemoryUnitPreorganized",
                "atomCount": len(atoms),
                "unitId": lease.plan.unit_id,
            }
        except Exception as error:
            failure = _classify_candidate_extraction_failure(error)
            current = self._run_repository.snapshot(lease.plan.run_id) or {}
            current_unit = next(
                (
                    item
                    for item in current.get("units") or []
                    if item.get("unitId") == lease.plan.unit_id
                ),
                {},
            )
            retry_eligible = failure.retryable or failure.contract_retry_eligible
            retry_spent = int(current_unit.get("extraRequestCount") or 0)
            terminal = (
                not retry_eligible
                or retry_spent >= self._run_budget_policy.maximum_unit_extra_requests
            )
            retry_seconds = 0
            if not terminal and failure.retryable:
                try:
                    policy = LiveLongMemoryBudgetPolicy.from_snapshot(
                        current.get("budgetPolicySnapshot"), current.get("budgetPolicyHash")
                    )
                    retry_seconds = max(5, failure.retry_after_seconds or 0)
                    remaining = policy.absolute_deadline_seconds
                    if current.get("sourceId") is not None:
                        started = datetime.fromisoformat(str(current["organizationStartedAt"]))
                        progressed = datetime.fromisoformat(str(current["lastProgressAt"]))
                        now = datetime.now(timezone.utc)
                        remaining = min(
                            (started + timedelta(seconds=policy.absolute_deadline_seconds) - now).total_seconds(),
                            (progressed + timedelta(seconds=policy.inactivity_deadline_seconds) - now).total_seconds(),
                        )
                    terminal = retry_seconds >= remaining
                except (LiveLongMemoryConflict, KeyError, TypeError, ValueError):
                    terminal = True
            self._run_repository.record_unit_failure(
                plan=lease.plan,
                failure_code=failure.code,
                terminal=terminal,
                lease_owner=lease.lease_owner,
                lease_generation=lease.lease_generation,
                retry_seconds=retry_seconds if not terminal else 0,
            )
            return {
                "status": "failed" if terminal else "retryWait",
                "reason": failure.code,
                "unitId": lease.plan.unit_id,
            }

        # Legacy body retained below only to keep patch locality while the
        # production path above owns all new long-memory units.
        request_payload = {"stage": "atomExtraction", "turns": chunk}
        reservation = self._run_repository.reserve_provider_attempt(
            run_id=lease.plan.run_id,
            unit_id=lease.plan.unit_id,
            stage="atomExtraction",
            request_hash=sha256(
                json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            reserved_input_tokens=conservative_token_estimate(request_payload),
            reserved_output_tokens=4_096,
            recovery=recovery,
            policy=self._run_budget_policy,
        )
        try:
            organization = self._organizer.request_organization(turns=chunk)
            memories = organization.get("memories")
            if not isinstance(memories, list) or any(
                not isinstance(memory, dict) for memory in memories
            ):
                raise contract_failure("organizationValidate", "schemaInvalid", eligible=True)
        except Exception as error:
            self._run_repository.complete_provider_attempt(
                reservation,
                exposure_state=self._provider_failure_exposure(error),
                model=str(getattr(self._organizer, "model", "unknown")),
                finish_reason=None,
                usage=None,
                response_hash=None,
            )
            raise
        organization_hash = sha256(
            json.dumps(organization, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self._run_repository.complete_provider_attempt(
            reservation,
            exposure_state="responseAccepted",
            model=str(getattr(self._organizer, "model", "unknown")),
            finish_reason="stop",
            usage=None,
            response_hash=organization_hash,
        )

        support_payload = {"stage": "atomSupport", "turns": chunk, "memories": memories}
        support_reservation = self._run_repository.reserve_provider_attempt(
            run_id=lease.plan.run_id,
            unit_id=lease.plan.unit_id,
            stage="atomSupport",
            request_hash=sha256(
                json.dumps(support_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            reserved_input_tokens=conservative_token_estimate(support_payload),
            reserved_output_tokens=4_096,
            recovery=False,
            policy=self._run_budget_policy,
        )
        support_finish_reason: str | None = None
        support_usage: Mapping[str, Any] | None = None
        try:
            review = self._request_support_review(
                turns=chunk,
                memories=memories,
                retry_context=None,
                stage_reporter=None,
            )
            validated = validate_live_memory_support(
                turns=chunk,
                memories=memories,
                review=review,
            )
        except Exception as error:
            self._run_repository.complete_provider_attempt(
                support_reservation,
                exposure_state=self._provider_failure_exposure(error),
                model=str(getattr(self._support_reviewer, "model", "unknown")),
                finish_reason=None,
                usage=None,
                response_hash=None,
            )
            raise
        support_hash = sha256(
            json.dumps(review, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self._run_repository.complete_provider_attempt(
            support_reservation,
            exposure_state="responseAccepted",
            model=str(getattr(self._support_reviewer, "model", "unknown")),
            finish_reason="stop",
            usage=None,
            response_hash=support_hash,
        )
        fact_acts = {"assertion", "correction", "timeSupplement"}
        coverage = {
            "requiredUserTurnIndices": sorted(
                int(item["turnIndex"])
                for item in review.get("turnAssessments") or []
                if isinstance(item.get("turnIndex"), int)
                and item.get("speechAct") in fact_acts
            ),
            "excludedUserTurnIndices": sorted(
                int(item["turnIndex"])
                for item in review.get("turnAssessments") or []
                if isinstance(item.get("turnIndex"), int)
                and item.get("speechAct") not in fact_acts
            ),
        }
        atoms = tuple(
            LiveLongMemoryAtomRecord.make(
                run_id=lease.plan.run_id,
                unit_id=lease.plan.unit_id,
                memory=memory,
            )
            for memory in validated
        )
        output_hash = sha256(
            json.dumps(validated, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self._run_repository.record_unit_result(
            plan=lease.plan,
            atoms=atoms,
            output_hash=output_hash,
            coverage=coverage,
            lease_owner=lease.lease_owner,
            lease_generation=lease.lease_generation,
        )
        return {
            "status": "completed",
            "reason": "liveMemoryUnitPreorganized",
            "atomCount": len(atoms),
            "unitId": lease.plan.unit_id,
        }

    def publication_binding(
        self,
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
    ) -> tuple[str, str] | None:
        if not self._long_memory_pipeline_enabled:
            return None
        identity = self._run_identity(intent=intent, source=source)
        snapshot = self._run_repository.snapshot(identity.run_id)
        manifest_hash = str((snapshot or {}).get("manifestHash") or "")
        if not manifest_hash or (snapshot or {}).get("state") != "readyToPublish":
            raise contract_failure("candidateCommit", "publicationManifestUnavailable")
        return identity.run_id, manifest_hash

    @staticmethod
    def _run_identity(
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
    ) -> LiveLongMemoryRunIdentity:
        metadata = source.source_metadata or {}
        product_session_id = str(
            metadata.get("productSessionId")
            or metadata.get("sessionId")
            or source.source_id
            or ""
        ).strip()
        raw_generation = metadata.get("productCaptureGeneration", 1)
        if not product_session_id:
            raise contract_failure("pipelineInput", "productSessionMissing")
        if isinstance(raw_generation, bool) or not isinstance(raw_generation, int):
            raise contract_failure("pipelineInput", "captureGenerationInvalid")
        return LiveLongMemoryRunIdentity(
            owner_subject_id=intent.target.owner_subject_id,
            vault_id=intent.target.vault_id,
            product_session_id=product_session_id,
            capture_generation=raw_generation,
            authority_epoch=intent.target.authority_epoch,
        )

    def extract(
        self,
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
        retry_context: LiveMemoryContractRetryContext | None = None,
        stage_reporter: Callable[[str, Mapping[str, int]], None] | None = None,
    ) -> SyntheticCandidateExtractionCommand:
        turns = self._live_turns(source)
        if turns is None:
            return self._fallback.extract(intent=intent, source=source)
        if not self._live_organization_enabled:
            raise RuntimeError("Live transcript organization is disabled")

        run_identity: LiveLongMemoryRunIdentity | None = None
        if self._long_memory_pipeline_enabled:
            if source.source_id is None or source.source_version is None:
                raise contract_failure("pipelineInput", "sourceBindingMissing")
            run_identity = self._run_identity(intent=intent, source=source)
            self._run_repository.begin_or_load(run_identity, self._run_budget_policy)
            self._run_repository.bind_source(
                run_id=run_identity.run_id,
                authority_epoch=intent.target.authority_epoch,
                source_id=source.source_id,
                source_version=source.source_version,
                source_content_hash=source.source_content_hash,
                final_watermark=max(int(turn.get("index") or 0) for turn in turns),
            )
            bound_snapshot = self._run_repository.snapshot(run_identity.run_id) or {}
            bound_state = str(bound_snapshot.get("state") or "")
            if bound_state in {"failed", "cancelled"}:
                raise contract_failure(
                    "pipelineInput",
                    "runTerminal",
                    category="domain",
                )

        memories: list[dict[str, Any]] = []
        seen_memories: set[tuple[str, str]] = set()
        pipeline_required_indices: set[int] = set()
        pipeline_excluded_indices: set[int] = set()
        pipeline_fragment_coverage: list[dict[str, Any]] = []
        chunks = self._organization_chunks(
            turns,
            maximum_user_finals=(8 if self._long_memory_pipeline_enabled else None),
        )
        unit_plans: list[LiveLongMemoryUnitPlan | None] = []
        for chunk_ordinal, chunk in enumerate(chunks):
            unit_plan = None
            if run_identity is not None:
                unit_plan = LiveLongMemoryUnitPlan(
                    run_id=run_identity.run_id,
                    ordinal=chunk_ordinal,
                    kind="atomExtraction",
                    generation=1,
                    ownership=tuple(
                        {
                            "index": int(turn["index"]),
                            "role": "user",
                            "textHash": sha256(
                                str(turn.get("text") or "").encode("utf-8")
                            ).hexdigest(),
                        }
                        for turn in chunk
                        if turn.get("role") == "user"
                    ),
                    context=tuple(
                        {
                            "index": int(turn["index"]),
                            "role": str(turn["role"]),
                            "textHash": sha256(
                                str(turn.get("text") or "").encode("utf-8")
                            ).hexdigest(),
                        }
                        for turn in chunk
                        if turn.get("role") != "user"
                    ),
                )
                unit_snapshot = self._run_repository.record_unit(unit_plan)
                if unit_snapshot.get("state") == "completed":
                    run_snapshot = self._run_repository.snapshot(run_identity.run_id)
                    recovered_atoms = sorted(
                        (
                            atom
                            for atom in (run_snapshot or {}).get("atoms") or []
                            if atom.get("unitId") == unit_plan.unit_id
                        ),
                        key=lambda atom: (
                            min(atom.get("sourceTurnIndices") or [2**63 - 1]),
                            str(atom.get("atomId") or ""),
                        ),
                    )
                    memories.extend(
                        {
                            **json.loads(json.dumps(atom["memory"], ensure_ascii=False)),
                            "_atomIds": [str(atom["atomId"])],
                        }
                        for atom in recovered_atoms
                    )
                    coverage = unit_snapshot.get("coverage") or {}
                    recovered_fragment_coverage = list(
                        coverage.get("fragmentCoverage") or []
                    )
                    if recovered_fragment_coverage:
                        pipeline_fragment_coverage.extend(
                            json.loads(json.dumps(recovered_fragment_coverage))
                        )
                        required, excluded = self._project_fragment_coverage(
                            recovered_fragment_coverage
                        )
                        pipeline_required_indices.update(required)
                        pipeline_excluded_indices.update(excluded)
                    else:
                        pipeline_required_indices.update(
                            int(value)
                            for value in coverage.get("requiredUserTurnIndices") or []
                        )
                        pipeline_excluded_indices.update(
                            int(value)
                            for value in coverage.get("excludedUserTurnIndices") or []
                        )
                    unit_plans.append(unit_plan)
                    continue
                chunk_memories, required, excluded, fragment_coverage = (
                    self._organize_validate_fragment(
                    chunk=chunk,
                    unit_plan=unit_plan,
                    retry_context=retry_context,
                    stage_reporter=stage_reporter,
                    )
                )
                atoms: list[LiveLongMemoryAtomRecord] = []
                published_memories: list[dict[str, Any]] = []
                for chunk_memory in chunk_memories:
                    atom = LiveLongMemoryAtomRecord.make(
                        run_id=run_identity.run_id,
                        unit_id=unit_plan.unit_id,
                        memory=chunk_memory,
                    )
                    atoms.append(atom)
                    published_memories.append(
                        {**chunk_memory, "_atomIds": [atom.atom_id]}
                    )
                output_hash = sha256(
                    json.dumps(
                        published_memories, ensure_ascii=False, sort_keys=True
                    ).encode("utf-8")
                ).hexdigest()
                self._run_repository.record_unit_result(
                    plan=unit_plan,
                    atoms=atoms,
                    output_hash=output_hash,
                    coverage={
                        "requiredUserTurnIndices": sorted(required),
                        "excludedUserTurnIndices": sorted(excluded),
                        "fragmentCoverage": fragment_coverage,
                    },
                )
                memories.extend(published_memories)
                pipeline_required_indices.update(required)
                pipeline_excluded_indices.update(excluded)
                pipeline_fragment_coverage.extend(fragment_coverage)
                unit_plans.append(unit_plan)
                continue
            try:
                if (
                    retry_context is not None
                    and getattr(self._organizer, "supports_contract_repair_hint", False)
                ):
                    organization = self._organizer.request_organization(
                        turns=chunk,
                        repair_hint=retry_context.repair_hint,
                        **(
                            {"stage_reporter": stage_reporter}
                            if getattr(self._organizer, "supports_stage_diagnostics", False)
                            else {}
                        ),
                    )
                else:
                    organization = self._organizer.request_organization(
                        turns=chunk,
                        **(
                            {"stage_reporter": stage_reporter}
                            if getattr(self._organizer, "supports_stage_diagnostics", False)
                            else {}
                        ),
                    )
            except Exception as error:
                # A provider or response-contract failure is not a memory
                # result. Let the worker retry or terminalize it explicitly;
                # never concatenate transcript text into a false success.
                if unit_plan is not None:
                    self._run_repository.complete_provider_attempt(
                        organization_reservation,
                        exposure_state=self._provider_failure_exposure(error),
                        model=str(getattr(self._organizer, "model", "unknown")),
                        finish_reason=None,
                        usage=None,
                        response_hash=None,
                    )
                raise
            if unit_plan is not None:
                self._run_repository.complete_provider_attempt(
                    organization_reservation,
                    exposure_state="responseAccepted",
                    model=str(getattr(self._organizer, "model", "unknown")),
                    finish_reason="stop",
                    usage=None,
                    response_hash=sha256(
                        json.dumps(organization, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                )
            unit_plans.append(unit_plan)
            chunk_memories = organization.get("memories")
            if not isinstance(chunk_memories, list):
                raise contract_failure(
                    "organizationValidate", "schemaInvalid", eligible=True
                )
            for memory in chunk_memories:
                if not isinstance(memory, dict):
                    raise contract_failure(
                        "organizationValidate", "schemaInvalid", eligible=True
                    )
                memory_kind = str(memory.get("memoryKind") or "")
                primary_field = {
                    "experience": "summary",
                    "knowledge": "claim",
                    "emotion": "label",
                }.get(memory_kind)
                primary_value = str(memory.get(primary_field or "") or "").strip()
                dedupe_key = (memory_kind, primary_value)
                if dedupe_key in seen_memories and not self._long_memory_pipeline_enabled:
                    continue
                seen_memories.add(dedupe_key)
                memories.append(memory)
        if self._long_memory_pipeline_enabled:
            if pipeline_fragment_coverage:
                pipeline_required_indices, pipeline_excluded_indices = (
                    self._project_fragment_coverage(pipeline_fragment_coverage)
                )
            required_indices = tuple(sorted(pipeline_required_indices))
            excluded_indices = tuple(sorted(pipeline_excluded_indices))
            memories, exact_merge_changed = self._merge_exact_memories(memories)
            memories, retracted_atom_ids, relations_changed = self._resolve_cross_batch_relations(
                turns=turns,
                memories=memories,
                run_identity=run_identity,
                retry_context=retry_context,
            )
            if exact_merge_changed or relations_changed:
                memories = self._revalidate_resolved_memories(
                    turns=turns,
                    memories=memories,
                    run_identity=run_identity,
                    retry_context=retry_context,
                    stage_reporter=stage_reporter,
                )
            assert run_identity is not None
            run_snapshot = self._run_repository.snapshot(run_identity.run_id)
            if run_snapshot is None:
                raise contract_failure("manifestBuild", "runUnavailable")
            manifest = build_publication_manifest(
                run_snapshot=run_snapshot,
                source_id=str(source.source_id),
                source_version=int(source.source_version),
                source_content_hash=source.source_content_hash,
                generation=1,
                memories=memories,
                required_user_turn_indices=required_indices,
                excluded_user_turn_indices=excluded_indices,
                retracted_atom_ids=tuple(sorted(retracted_atom_ids)),
            )
            self._run_repository.freeze_manifest(manifest)
        else:
            support_review = self._request_support_review(
                turns=turns,
                memories=memories,
                retry_context=retry_context,
                stage_reporter=stage_reporter,
            )
            memories = validate_live_memory_support(
                turns=turns,
                memories=memories,
                review=support_review,
            )
        if stage_reporter is not None:
            stage_reporter("supportValidated", {"memoryCount": len(memories)})
        spans = self._owner_evidence_spans(source=source, turns=turns)
        source_perspective, source_epistemic = _source_provenance(
            source.source_metadata or {}
        )
        memory_subject_id, claim_subject_id = _typed_subjects(source)
        proposals: list[CandidateProposal] = []
        if stage_reporter is not None:
            stage_reporter("proposalBuildStarted", {"memoryCount": len(memories)})
        for memory in memories:
            if not isinstance(memory, dict):
                raise contract_failure("proposalBuild", "contractInvalid")
            try:
                memory_kind = MemoryKind(str(memory.get("memoryKind") or ""))
            except ValueError as error:
                raise contract_failure("proposalBuild", "contractInvalid") from error
            primary_field = {
                MemoryKind.EXPERIENCE: "summary",
                MemoryKind.KNOWLEDGE: "claim",
                MemoryKind.EMOTION: "label",
            }[memory_kind]
            primary_value = str(memory.get(primary_field) or "").strip()
            source_indices = memory.get("sourceTurnIndices")
            if not primary_value or not isinstance(source_indices, list) or not source_indices:
                raise contract_failure("proposalBuild", "contractInvalid")
            try:
                selected_spans = [spans[index] for index in source_indices]
            except (KeyError, TypeError) as error:
                raise contract_failure("proposalBuild", "evidenceInvalid") from error
            try:
                content = enrich_memory_payload_v5(
                    kind=memory_kind,
                    payload={
                        primary_field: primary_value,
                        "sourceTurnIndices": list(source_indices),
                        "facets": memory.get("facets"),
                        "factType": memory.get("factType"),
                        "dimensions": memory.get("dimensions"),
                        "predicate": memory.get("predicate"),
                        "object": memory.get("object"),
                        "qualifiers": memory.get("qualifiers"),
                        "affect": memory.get("affect"),
                    },
                    provenance=_typed_source_provenance(source=source),
                    memory_subject_id=memory_subject_id,
                    claim_subject_id=claim_subject_id,
                )
            except (TypeError, ValueError) as error:
                raise contract_failure("proposalBuild", "contractInvalid") from error
            proposals.append(
                CandidateProposal(
                    memory_kind=memory_kind,
                    perspective_type=source_perspective,
                    epistemic_status=source_epistemic,
                    sensitivity=SensitivityLevel.STANDARD,
                    content=content,
                    evidence_span=CandidateEvidenceSpan(
                        start=min(span.start for span in selected_spans),
                        end=max(span.end for span in selected_spans),
                    ),
                    confidence=0.0,
                    review_mode=CandidateReviewMode.SINGLE,
                    payload_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                )
            )
        if stage_reporter is not None:
            stage_reporter("proposalBuildCompleted", {"candidateCount": len(proposals)})
        return SyntheticCandidateExtractionCommand(
            intent=intent,
            extractor_id=self._EXTRACTOR_ID,
            model_id=self._organizer.model,
            prompt_version=(
                f"{self._organizer.prompt_version}+"
                f"{getattr(self._support_reviewer, 'support_prompt_version', LIVE_MEMORY_SUPPORT_VALIDATOR_VERSION)}"
            ),
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            source_content_hash=source.source_content_hash,
            status=ExtractionResultStatus.SUCCEEDED,
            proposals=tuple(proposals),
        )

    def _organization_chunks(
        self,
        turns: list[dict[str, Any]],
        *,
        maximum_user_finals: int | None = None,
    ) -> tuple[list[dict[str, Any]], ...]:
        """Send every transcript turn without holding one oversized model request."""

        maximum_turn_count = max(
            2,
            int(getattr(self._organizer, "maximum_turn_count", 200)),
        )
        maximum_turn_characters = max(
            1,
            int(getattr(self._organizer, "maximum_turn_characters", 4_000)),
        )
        maximum_total_characters = max(
            maximum_turn_characters * 2,
            int(getattr(self._organizer, "maximum_total_characters", 30_000)),
        )
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_characters = 0
        latest_user_turn: dict[str, Any] | None = None

        segmented_turns: list[dict[str, Any]] = []
        for turn in turns:
            source_text = str(turn.get("text") or "")
            source_start = int(turn.get("_sourceStart") or 0)
            if len(source_text) <= maximum_turn_characters:
                segmented_turns.append(dict(turn))
                continue
            spans: list[tuple[int, int]] = []
            current_start = 0
            current_end = 0
            clause_count = 0
            for match in re.finditer(r"[^。！？；;!?]+[。！？；;!?]?", source_text):
                clause_start, clause_end = match.span()
                if clause_end - clause_start > maximum_turn_characters:
                    if current_end > current_start:
                        spans.append((current_start, current_end))
                    spans.extend(
                        (offset, min(offset + maximum_turn_characters, clause_end))
                        for offset in range(clause_start, clause_end, maximum_turn_characters)
                    )
                    current_start = clause_end
                    current_end = clause_end
                    clause_count = 0
                    continue
                if current_end > current_start and (
                    clause_end - current_start > maximum_turn_characters
                    or clause_count >= 8
                ):
                    spans.append((current_start, current_end))
                    current_start = clause_start
                    clause_count = 0
                current_end = clause_end
                clause_count += 1
            if current_end > current_start:
                spans.append((current_start, current_end))
            for start, end in spans:
                piece = source_text[start:end]
                segmented_turns.append({
                    **turn,
                    "text": piece,
                    "_sourceStart": source_start + start,
                })

        current_indices: set[int] = set()
        current_user_finals = 0
        for turn in segmented_turns:
            text = str(turn.get("text") or "").strip()
            turn_index = turn.get("index")
            current_has_user_evidence = any(
                item.get("role") == "user" for item in current
            )
            would_overflow = current and current_has_user_evidence and (
                len(current) >= maximum_turn_count
                or current_characters + len(text) > maximum_total_characters
                or turn_index in current_indices
                or (
                    maximum_user_finals is not None
                    and turn.get("role") == "user"
                    and current_user_finals >= maximum_user_finals
                )
            )
            if would_overflow:
                chunks.append(current)
                current = []
                current_characters = 0
                current_indices = set()
                current_user_finals = 0

            if not current and turn.get("role") == "assistant":
                if latest_user_turn is not None:
                    current.append(latest_user_turn)
                    current_characters = len(str(latest_user_turn.get("text") or ""))
                    current_indices.add(latest_user_turn["index"])
            current.append(turn)
            current_characters += len(text)
            current_indices.add(turn_index)
            if turn.get("role") == "user":
                latest_user_turn = turn
                current_user_finals += 1

        if current and any(item.get("role") == "user" for item in current):
            chunks.append(current)
        elif current:
            raise ValueError("Live transcript chunk contains no user evidence")
        if not chunks:
            raise ValueError("Live transcript has no organization input")
        return tuple(chunks)

    def _request_support_review(
        self,
        *,
        turns: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        retry_context: LiveMemoryContractRetryContext | None,
        stage_reporter: Callable[[str, Mapping[str, int]], None] | None,
        prepared_request: PreparedLiveModelRequest | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        if retry_context is not None and getattr(
            self._support_reviewer,
            "supports_contract_repair_hint",
            False,
        ):
            arguments["repair_hint"] = retry_context.repair_hint
        if getattr(self._support_reviewer, "supports_stage_diagnostics", False):
            arguments["stage_reporter"] = stage_reporter
        if prepared_request is not None:
            arguments["prepared_request"] = prepared_request
        return self._support_reviewer.request_support_review(
            turns=turns,
            memories=memories,
            **arguments,
        )

    @staticmethod
    def _bind_support_proof(
        *,
        turns: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        review: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        proof_hash = sha256(
            json.dumps(
                {"review": dict(review), "memories": memories},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        turns_by_index: dict[int, list[dict[str, Any]]] = {}
        for turn in turns:
            if turn.get("role") != "user" or not isinstance(turn.get("index"), int):
                continue
            turns_by_index.setdefault(int(turn["index"]), []).append(turn)
        # The adapter strips each fragment before assigning evidence IDs. Keep
        # the original fragment for Source binding, but build the same catalog.
        catalog_turns = [
            {**turn, "text": str(turn.get("text") or "").strip()}
            for turn in turns
        ]
        evidence_catalog = build_live_memory_evidence_catalog(catalog_turns)
        evidence_by_id = {
            str(item["evidenceId"]): item for item in evidence_catalog
        }

        bound: list[dict[str, Any]] = []
        for raw_memory in memories:
            memory = json.loads(json.dumps(raw_memory, ensure_ascii=False))
            ranges: list[dict[str, Any]] = []
            primary_field = {
                "experience": "summary",
                "knowledge": "claim",
                "emotion": "label",
            }.get(str(memory.get("memoryKind") or ""))
            primary_value = str(memory.get(primary_field or "") or "").strip()
            source_indices = {
                int(raw_index) for raw_index in memory.get("sourceTurnIndices") or []
            }
            raw_evidence_ids = memory.pop("_sourceEvidenceFragmentIds", None)
            if raw_evidence_ids:
                for evidence_id in raw_evidence_ids:
                    evidence = evidence_by_id.get(str(evidence_id))
                    if (
                        evidence is None
                        or int(evidence["turnIndex"]) not in source_indices
                    ):
                        raise contract_failure(
                            "supportValidate", "evidenceOutOfRange", eligible=True
                        )
                    turn_ordinal = int(evidence["turnOrdinal"])
                    if turn_ordinal < 0 or turn_ordinal >= len(turns):
                        raise contract_failure(
                            "supportValidate", "evidenceOutOfRange", eligible=True
                        )
                    source_turn = turns[turn_ordinal]
                    source_text = str(source_turn.get("text") or "")
                    leading = len(source_text) - len(source_text.lstrip())
                    source_start = int(source_turn.get("_sourceStart") or 0) + leading
                    start = source_start + int(evidence["start"])
                    end = source_start + int(evidence["end"])
                    text_hash = str(evidence["textHash"])
                    ranges.append(
                        {
                            "evidenceId": sha256(
                                f'{int(evidence["turnIndex"])}:{start}:{end}:{text_hash}'.encode(
                                    "utf-8"
                                )
                            ).hexdigest(),
                            "turnIndex": int(evidence["turnIndex"]),
                            "start": start,
                            "end": end,
                            "textHash": text_hash,
                        }
                    )
            else:
                # Compatibility for existing exact-expression providers.  The
                # fallback is accepted only when every cited turn has one
                # unambiguous literal occurrence.  A paraphrase must carry an
                # immutable evidence fragment ID; it may never claim a whole
                # turn merely because the generated wording was not found.
                for index in sorted(source_indices):
                    source_turns = turns_by_index.get(index) or []
                    matches: list[tuple[dict[str, Any], int]] = []
                    for turn in source_turns:
                        text = str(turn.get("text") or "")
                        if not text or not primary_value:
                            continue
                        cursor = 0
                        while True:
                            local_start = text.find(primary_value, cursor)
                            if local_start < 0:
                                break
                            matches.append((turn, local_start))
                            cursor = local_start + max(1, len(primary_value))
                    if len(matches) != 1:
                        raise contract_failure(
                            "supportValidate",
                            "evidenceBindingMissing" if not matches else "evidenceBindingAmbiguous",
                            eligible=True,
                        )
                    turn, local_start = matches[0]
                    source_start = int(turn.get("_sourceStart") or 0)
                    start = source_start + local_start
                    text_hash = sha256(primary_value.encode("utf-8")).hexdigest()
                    ranges.append(
                        {
                            "evidenceId": sha256(
                                f"{index}:{start}:{start + len(primary_value)}:{text_hash}".encode(
                                    "utf-8"
                                )
                            ).hexdigest(),
                            "turnIndex": index,
                            "start": start,
                            "end": start + len(primary_value),
                            "textHash": text_hash,
                        }
                    )
            unique_ranges = {
                (
                    int(item["turnIndex"]),
                    int(item["start"]),
                    int(item["end"]),
                    str(item["textHash"]),
                ): item
                for item in ranges
            }
            memory["_sourceEvidenceRanges"] = [
                unique_ranges[key] for key in sorted(unique_ranges)
            ]
            memory["_supportProofHash"] = proof_hash
            bound.append(memory)
        return bound

    @staticmethod
    def _split_chunk_for_refinement(
        chunk: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        user_positions = [
            position for position, turn in enumerate(chunk) if turn.get("role") == "user"
        ]
        if len(user_positions) > 1:
            split_at = user_positions[len(user_positions) // 2]
            left = chunk[:split_at]
            right = chunk[split_at:]
            if any(item.get("role") == "user" for item in left) and any(
                item.get("role") == "user" for item in right
            ):
                return left, right

        if len(user_positions) != 1:
            return None
        user_position = user_positions[0]
        turn = chunk[user_position]
        text = str(turn.get("text") or "")
        clauses = [
            match.group(0)
            for match in re.finditer(r"[^。！？；;!?]+[。！？；;!?]?", text)
            if match.group(0).strip()
        ]
        if len(clauses) < 2:
            midpoint = len(text) // 2
            if midpoint < 4 or len(text) - midpoint < 4:
                return None
            clauses = [text[:midpoint], text[midpoint:]]
        split_clause = max(1, len(clauses) // 2)
        left_text = "".join(clauses[:split_clause]).strip()
        right_text = "".join(clauses[split_clause:]).strip()
        if not left_text or not right_text:
            return None
        base_start = int(turn.get("_sourceStart") or 0)
        right_offset = text.find(right_text, len(left_text))
        if right_offset < 0:
            right_offset = len(left_text)
        left_turn = {**turn, "text": left_text, "_sourceStart": base_start}
        right_turn = {
            **turn,
            "text": right_text,
            "_sourceStart": base_start + right_offset,
        }
        context = [dict(item) for index, item in enumerate(chunk) if index != user_position]
        return [*context, left_turn], [*context, right_turn]

    @staticmethod
    def _provider_failure_exposure(error: Exception) -> str:
        if not isinstance(error, LiveMemoryContractFailure):
            return "outcomeUnknown"
        if error.stage.endswith("Input"):
            return "notSent"
        if error.stage.endswith("Decode") or error.stage.endswith("Validate"):
            return "rejected"
        return "outcomeUnknown"

    def _organization_budget_payload(
        self,
        *,
        turns: list[dict[str, Any]],
        retry_context: LiveMemoryContractRetryContext | None,
    ) -> Mapping[str, Any]:
        builder = getattr(self._organizer, "build_request", None)
        if callable(builder):
            request = builder(
                turns=turns,
                **(
                    {"repair_hint": retry_context.repair_hint}
                    if retry_context is not None
                    else {}
                ),
            )
            if isinstance(request, Mapping) and isinstance(request.get("json"), Mapping):
                return dict(request["json"])
        return {"stage": "atomExtraction", "turns": turns}

    @staticmethod
    def _freeze_provider_request(
        provider: Any, builder_name: str, **arguments: Any
    ) -> tuple[Mapping[str, Any] | None, PreparedLiveModelRequest | None]:
        builder = getattr(provider, builder_name, None)
        if not callable(builder):
            return None, None
        request = builder(**arguments)
        if not isinstance(request, Mapping) or not isinstance(request.get("json"), Mapping):
            raise contract_failure("providerInput", "inputInvalid", category="input")
        if getattr(provider, "supports_prepared_request", False):
            prepared = PreparedLiveModelRequest.freeze(request)
            return prepared.payload(), prepared
        return dict(request["json"]), None

    def _support_budget_payload(
        self,
        *,
        turns: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        retry_context: LiveMemoryContractRetryContext | None,
    ) -> Mapping[str, Any]:
        builder = getattr(self._support_reviewer, "build_support_request", None)
        if callable(builder):
            request = builder(
                turns=turns,
                memories=memories,
                **(
                    {"repair_hint": retry_context.repair_hint}
                    if retry_context is not None
                    else {}
                ),
            )
            if isinstance(request, Mapping) and isinstance(request.get("json"), Mapping):
                return dict(request["json"])
        return {"stage": "atomSupport", "turns": turns, "memories": memories}

    def _organize_validate_fragment(
        self,
        *,
        chunk: list[dict[str, Any]],
        unit_plan: LiveLongMemoryUnitPlan,
        retry_context: LiveMemoryContractRetryContext | None,
        stage_reporter: Callable[[str, Mapping[str, int]], None] | None,
        depth: int = 0,
    ) -> tuple[list[dict[str, Any]], set[int], set[int], list[dict[str, Any]]]:
        if depth > 8:
            raise contract_failure("organizationValidate", "refinementBudgetExhausted")
        organization_hint = retry_context.repair_hint if retry_context is not None and depth == 0 else None
        prepared_payload, prepared_organization = self._freeze_provider_request(
            self._organizer, "build_request", turns=chunk,
            **({"repair_hint": organization_hint} if organization_hint is not None else {}),
        )
        organization_payload = prepared_payload or {"stage": "atomExtraction", "turns": chunk}
        organization_reservation = self._run_repository.reserve_provider_attempt(
            run_id=unit_plan.run_id,
            unit_id=unit_plan.unit_id,
            stage="atomExtraction" if depth == 0 else "atomExtractionRefinement",
            request_hash=sha256(
                json.dumps(organization_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            reserved_input_tokens=conservative_token_estimate(organization_payload),
            reserved_output_tokens=4_096,
            recovery=retry_context is not None and depth == 0,
            policy=self._run_budget_policy,
        )
        try:
            arguments: dict[str, Any] = {}
            if (
                retry_context is not None
                and depth == 0
                and getattr(self._organizer, "supports_contract_repair_hint", False)
            ):
                arguments["repair_hint"] = retry_context.repair_hint
            if getattr(self._organizer, "supports_stage_diagnostics", False):
                arguments["stage_reporter"] = stage_reporter
            if prepared_organization is not None:
                arguments["prepared_request"] = prepared_organization
            organization = self._organizer.request_organization(turns=chunk, **arguments)
            chunk_memories = organization.get("memories")
            if not isinstance(chunk_memories, list) or any(
                not isinstance(memory, dict) for memory in chunk_memories
            ):
                raise contract_failure("organizationValidate", "schemaInvalid", eligible=True)
        except Exception as error:
            exposure = self._provider_failure_exposure(error)
            observed_finish_reason = (
                "length"
                if isinstance(error, LiveMemoryContractFailure)
                and error.reason == "outputTruncated"
                else None
            )
            self._run_repository.complete_provider_attempt(
                organization_reservation,
                exposure_state=exposure,
                model=str(getattr(self._organizer, "model", "unknown")),
                finish_reason=observed_finish_reason,
                usage=None,
                response_hash=None,
            )
            if (
                isinstance(error, LiveMemoryContractFailure)
                and error.reason in {"outputTruncated", "outputOverCapacity"}
            ):
                split = self._split_chunk_for_refinement(chunk)
                if split is not None:
                    return self._combine_refined_fragments(
                        fragments=split,
                        unit_plan=unit_plan,
                        stage_reporter=stage_reporter,
                        depth=depth + 1,
                    )
            raise
        organization_hash = sha256(
            json.dumps(organization, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self._run_repository.complete_provider_attempt(
            organization_reservation,
            exposure_state="responseAccepted",
            model=str(getattr(self._organizer, "model", "unknown")),
            finish_reason=str(organization.pop("_providerFinishReason", "stop") or "stop"),
            usage=organization.pop("_providerUsage", None),
            response_hash=organization_hash,
        )

        support_hint = retry_context.repair_hint if retry_context is not None and depth == 0 else None
        prepared_payload, prepared_support = self._freeze_provider_request(
            self._support_reviewer, "build_support_request", turns=chunk,
            memories=chunk_memories,
            **({"repair_hint": support_hint} if support_hint is not None else {}),
        )
        support_payload = prepared_payload or {
            "stage": "atomSupport", "turns": chunk, "memories": chunk_memories
        }
        support_reservation = self._run_repository.reserve_provider_attempt(
            run_id=unit_plan.run_id,
            unit_id=unit_plan.unit_id,
            stage="atomSupport" if depth == 0 else "atomSupportRefinement",
            request_hash=sha256(
                json.dumps(support_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            reserved_input_tokens=conservative_token_estimate(support_payload),
            reserved_output_tokens=4_096,
            recovery=False,
            policy=self._run_budget_policy,
        )
        support_finish_reason: str | None = None
        support_usage: Mapping[str, Any] | None = None
        try:
            review = self._request_support_review(
                turns=chunk,
                memories=chunk_memories,
                retry_context=retry_context if depth == 0 else None,
                stage_reporter=stage_reporter,
                prepared_request=prepared_support,
            )
            support_finish_reason = str(
                review.pop("_providerFinishReason", "stop") or "stop"
            )
            support_usage = review.pop("_providerUsage", None)
            validated = validate_live_memory_support(
                turns=chunk,
                memories=chunk_memories,
                review=review,
            )
        except Exception as error:
            self._run_repository.complete_provider_attempt(
                support_reservation,
                exposure_state=self._provider_failure_exposure(error),
                model=str(getattr(self._support_reviewer, "model", "unknown")),
                finish_reason=support_finish_reason,
                usage=support_usage,
                response_hash=None,
            )
            if (
                isinstance(error, LiveMemoryContractFailure)
                and error.reason in {"factOmitted", "factWithoutFinalDraft", "outputTruncated"}
            ):
                split = self._split_chunk_for_refinement(chunk)
                if split is not None:
                    return self._combine_refined_fragments(
                        fragments=split,
                        unit_plan=unit_plan,
                        stage_reporter=stage_reporter,
                        depth=depth + 1,
                    )
            raise
        support_hash = sha256(
            json.dumps(review, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self._run_repository.complete_provider_attempt(
            support_reservation,
            exposure_state="responseAccepted",
            model=str(getattr(self._support_reviewer, "model", "unknown")),
            finish_reason=support_finish_reason,
            usage=support_usage,
            response_hash=support_hash,
        )
        bound = self._bind_support_proof(turns=chunk, memories=validated, review=review)
        required: set[int] = set()
        excluded: set[int] = set()
        assessment_by_index: dict[int, str] = {}
        for assessment in review.get("turnAssessments") or []:
            index = assessment.get("turnIndex")
            if not isinstance(index, int):
                continue
            speech_act = str(assessment.get("speechAct") or "")
            assessment_by_index[index] = speech_act
            if speech_act in {"assertion", "correction", "timeSupplement"}:
                required.add(index)
            else:
                excluded.add(index)
        fragment_coverage = self._fragment_coverage(
            chunk=chunk,
            assessment_by_index=assessment_by_index,
        )
        required, excluded = self._project_fragment_coverage(fragment_coverage)
        return bound, required, excluded, fragment_coverage

    def _combine_refined_fragments(
        self,
        *,
        fragments: tuple[list[dict[str, Any]], list[dict[str, Any]]],
        unit_plan: LiveLongMemoryUnitPlan,
        stage_reporter: Callable[[str, Mapping[str, int]], None] | None,
        depth: int,
    ) -> tuple[list[dict[str, Any]], set[int], set[int], list[dict[str, Any]]]:
        memories: list[dict[str, Any]] = []
        fragment_coverage: list[dict[str, Any]] = []
        for fragment in fragments:
            fragment_memories, _fragment_required, _fragment_excluded, coverage = (
                self._organize_validate_fragment(
                    chunk=fragment,
                    unit_plan=unit_plan,
                    retry_context=None,
                    stage_reporter=stage_reporter,
                    depth=depth,
                )
            )
            memories.extend(fragment_memories)
            fragment_coverage.extend(coverage)
        required, excluded = self._project_fragment_coverage(fragment_coverage)
        return memories, required, excluded, fragment_coverage

    @staticmethod
    def _fragment_coverage(
        *,
        chunk: list[dict[str, Any]],
        assessment_by_index: Mapping[int, str],
    ) -> list[dict[str, Any]]:
        coverage: list[dict[str, Any]] = []
        for turn in chunk:
            if turn.get("role") != "user" or not isinstance(turn.get("index"), int):
                continue
            turn_index = int(turn["index"])
            text = str(turn.get("text") or "")
            start = int(turn.get("_sourceStart") or 0)
            end = start + len(text)
            speech_act = assessment_by_index.get(turn_index, "")
            disposition = (
                "required"
                if speech_act in {"assertion", "correction", "timeSupplement"}
                else "excluded"
            )
            text_hash = sha256(text.encode("utf-8")).hexdigest()
            coverage.append(
                {
                    "fragmentId": sha256(
                        f"{turn_index}:{start}:{end}:{text_hash}".encode("utf-8")
                    ).hexdigest(),
                    "turnIndex": turn_index,
                    "start": start,
                    "end": end,
                    "textHash": text_hash,
                    "disposition": disposition,
                }
            )
        return coverage

    @staticmethod
    def _project_fragment_coverage(
        coverage: Sequence[Mapping[str, Any]],
    ) -> tuple[set[int], set[int]]:
        required = {
            int(item["turnIndex"])
            for item in coverage
            if item.get("disposition") == "required"
            and isinstance(item.get("turnIndex"), int)
        }
        excluded_candidates = {
            int(item["turnIndex"])
            for item in coverage
            if item.get("disposition") == "excluded"
            and isinstance(item.get("turnIndex"), int)
        }
        return required, excluded_candidates - required

    def _validate_bounded_chunks(
        self,
        *,
        chunks: tuple[list[dict[str, Any]], ...],
        unit_plans: tuple[LiveLongMemoryUnitPlan | None, ...],
        memories: list[dict[str, Any]],
        retry_context: LiveMemoryContractRetryContext | None,
        stage_reporter: Callable[[str, Mapping[str, int]], None] | None,
    ) -> tuple[list[dict[str, Any]], tuple[int, ...], tuple[int, ...]]:
        """Validate each ownership page without rebuilding one unbounded request."""

        validated: list[dict[str, Any]] = []
        required_indices: set[int] = set()
        excluded_indices: set[int] = set()
        for chunk, unit_plan in zip(chunks, unit_plans):
            indices = {
                int(turn["index"])
                for turn in chunk
                if turn.get("role") == "user" and isinstance(turn.get("index"), int)
            }
            chunk_memories = [
                memory
                for memory in memories
                if any(index in indices for index in memory.get("sourceTurnIndices") or [])
            ]
            if unit_plan is not None:
                run_snapshot = self._run_repository.snapshot(unit_plan.run_id)
                completed_unit = next(
                    (
                        item
                        for item in (run_snapshot or {}).get("units") or []
                        if item.get("unitId") == unit_plan.unit_id
                        and item.get("state") == "completed"
                    ),
                    None,
                )
                if completed_unit is not None:
                    coverage = completed_unit.get("coverage") or {}
                    required_indices.update(
                        int(value)
                        for value in coverage.get("requiredUserTurnIndices") or []
                    )
                    excluded_indices.update(
                        int(value)
                        for value in coverage.get("excludedUserTurnIndices") or []
                    )
                    validated.extend(chunk_memories)
                    continue
            support_reservation = None
            prepared_support = None
            if unit_plan is not None:
                prepared_payload, prepared_support = self._freeze_provider_request(
                    self._support_reviewer, "build_support_request",
                    turns=chunk, memories=chunk_memories,
                    **({"repair_hint": retry_context.repair_hint} if retry_context else {}),
                )
                request_payload = prepared_payload or {
                    "stage": "atomSupport", "turns": chunk, "memories": chunk_memories
                }
                support_reservation = self._run_repository.reserve_provider_attempt(
                    run_id=unit_plan.run_id,
                    unit_id=unit_plan.unit_id,
                    stage="atomSupport",
                    request_hash=sha256(
                        json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    reserved_input_tokens=conservative_token_estimate(request_payload),
                    reserved_output_tokens=4_096,
                    recovery=retry_context is not None,
                    policy=self._run_budget_policy,
                )
            try:
                review = self._request_support_review(
                    turns=chunk,
                    memories=chunk_memories,
                    retry_context=retry_context,
                    stage_reporter=stage_reporter,
                    prepared_request=prepared_support,
                )
                chunk_validated = validate_live_memory_support(
                    turns=chunk,
                    memories=chunk_memories,
                    review=review,
                )
            except Exception as error:
                if support_reservation is not None:
                    self._run_repository.complete_provider_attempt(
                        support_reservation,
                        exposure_state=self._provider_failure_exposure(error),
                        model=str(getattr(self._support_reviewer, "model", "unknown")),
                        finish_reason=None,
                        usage=None,
                        response_hash=None,
                    )
                raise
            if support_reservation is not None:
                self._run_repository.complete_provider_attempt(
                    support_reservation,
                    exposure_state="responseAccepted",
                    model=str(getattr(self._support_reviewer, "model", "unknown")),
                    finish_reason="stop",
                    usage=None,
                    response_hash=sha256(
                        json.dumps(review, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                )
            for assessment in review.get("turnAssessments") or []:
                index = assessment.get("turnIndex")
                act = assessment.get("speechAct")
                if isinstance(index, int):
                    if act in {"assertion", "correction", "timeSupplement"}:
                        required_indices.add(index)
                    else:
                        excluded_indices.add(index)
            atoms = [
                LiveLongMemoryAtomRecord.make(
                    run_id=unit_plan.run_id,
                    unit_id=unit_plan.unit_id,
                    memory=memory,
                )
                for memory in chunk_validated
            ] if unit_plan is not None else []
            if unit_plan is not None:
                self._run_repository.record_unit_result(
                    plan=unit_plan,
                    atoms=atoms,
                    output_hash=sha256(
                        json.dumps(chunk_validated, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    coverage={
                        "requiredUserTurnIndices": sorted(
                            int(assessment["turnIndex"])
                            for assessment in review.get("turnAssessments") or []
                            if isinstance(assessment.get("turnIndex"), int)
                            and assessment.get("speechAct")
                            in {"assertion", "correction", "timeSupplement"}
                        ),
                        "excludedUserTurnIndices": sorted(
                            int(assessment["turnIndex"])
                            for assessment in review.get("turnAssessments") or []
                            if isinstance(assessment.get("turnIndex"), int)
                            and assessment.get("speechAct")
                            not in {"assertion", "correction", "timeSupplement"}
                        ),
                    },
                )
            for memory in chunk_validated:
                validated.append(memory)
        return validated, tuple(sorted(required_indices)), tuple(sorted(excluded_indices))

    @staticmethod
    def _merge_exact_memories(
        memories: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        order: list[tuple[str, str]] = []
        changed = False
        for memory in memories:
            memory_kind = str(memory.get("memoryKind") or "")
            primary_field = {
                "experience": "summary",
                "knowledge": "claim",
                "emotion": "label",
            }.get(memory_kind)
            key = (memory_kind, str(memory.get(primary_field or "") or "").strip())
            existing = merged.get(key)
            if existing is None:
                merged[key] = json.loads(json.dumps(memory, ensure_ascii=False))
                order.append(key)
                continue
            merged[key] = ModelAssistedOwnerTruthLiveConversationExtractor._merge_memory_evidence(
                existing, memory
            )
            changed = True
        return [merged[key] for key in order], changed

    @staticmethod
    def _compact_relation_memory(
        memory: Mapping[str, Any],
        original_by_index: Mapping[int, Mapping[str, Any]],
    ) -> dict[str, Any]:
        projected = dict(memory)
        ranges = list(memory.get("_sourceEvidenceRanges") or [])
        compact: list[dict[str, Any]] = []
        for raw in sorted(ranges, key=lambda item: (item["turnIndex"], item["start"])):
            item = dict(raw)
            index = int(item["turnIndex"])
            start = int(item["start"])
            end = int(item["end"])
            original = original_by_index.get(index)
            if original is None or end > len(str(original.get("text") or "")):
                raise contract_failure("relationInput", "evidenceOutOfRange")
            text = str(original["text"])
            if sha256(text[start:end].encode("utf-8")).hexdigest() != item.get("textHash"):
                raise contract_failure("relationInput", "evidenceInvalid")
            if compact and compact[-1]["turnIndex"] == index and compact[-1]["end"] == start:
                compact[-1]["end"] = end
                compact[-1]["textHash"] = sha256(
                    text[compact[-1]["start"]:end].encode("utf-8")
                ).hexdigest()
                compact[-1]["evidenceId"] = sha256(
                    f"{index}:{compact[-1]['start']}:{end}:{compact[-1]['textHash']}".encode("utf-8")
                ).hexdigest()
            else:
                compact.append(item)
        projected["_sourceEvidenceRanges"] = compact
        return projected

    @staticmethod
    def _project_relation_turns(
        *,
        turns: Sequence[Mapping[str, Any]],
        memories: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        original_by_index = {
            int(turn["index"]): turn
            for turn in turns
            if turn.get("role") == "user" and type(turn.get("index")) is int
        }
        ranges_by_index: dict[int, dict[tuple[int, int, str], dict[str, Any]]] = {}
        for memory in memories:
            ranges = memory.get("_sourceEvidenceRanges")
            if not isinstance(ranges, list) or not ranges:
                raise contract_failure("relationInput", "evidenceBindingMissing")
            for raw_range in ranges:
                if not isinstance(raw_range, Mapping):
                    raise contract_failure("relationInput", "evidenceOutOfRange")
                index, start, end = (
                    raw_range.get("turnIndex"), raw_range.get("start"), raw_range.get("end")
                )
                if (
                    type(index) is not int or type(start) is not int or type(end) is not int
                    or start < 0 or end <= start or index not in original_by_index
                ):
                    raise contract_failure("relationInput", "evidenceOutOfRange")
                original_text = str(original_by_index[index].get("text") or "")
                if end > len(original_text):
                    raise contract_failure("relationInput", "evidenceOutOfRange")
                fragment = original_text[start:end]
                text_hash = sha256(fragment.encode("utf-8")).hexdigest()
                if text_hash != raw_range.get("textHash"):
                    raise contract_failure("relationInput", "evidenceInvalid")
                ranges_by_index.setdefault(index, {})[(start, end, text_hash)] = {
                    "start": start,
                    "end": end,
                    "textHash": text_hash,
                    "text": fragment,
                }
        projected = []
        next_projection_index = max(original_by_index, default=0) + 1
        for index in sorted(ranges_by_index):
            fragments = [
                ranges_by_index[index][key]
                for key in sorted(ranges_by_index[index])
            ]
            compact_fragments: list[dict[str, Any]] = []
            for fragment in fragments:
                if compact_fragments and compact_fragments[-1]["end"] == fragment["start"]:
                    previous = compact_fragments[-1]
                    previous["end"] = fragment["end"]
                    previous["text"] += fragment["text"]
                    previous["textHash"] = sha256(previous["text"].encode("utf-8")).hexdigest()
                else:
                    compact_fragments.append(dict(fragment))
            pieces: list[dict[str, Any]] = []
            for fragment in compact_fragments:
                for offset in range(0, len(fragment["text"]), 4_000):
                    text = fragment["text"][offset:offset + 4_000]
                    start = fragment["start"] + offset
                    pieces.append({
                        "start": start,
                        "end": start + len(text),
                        "textHash": sha256(text.encode("utf-8")).hexdigest(),
                        "text": text,
                    })
            current_text = ""
            current_ranges: list[dict[str, Any]] = []
            for piece in pieces:
                separator = " " if current_text else ""
                if current_text and len(current_text) + len(separator) + len(piece["text"]) > 4_000:
                    projection_index = index if not any(turn.get("_originalTurnIndex") == index for turn in projected) else next_projection_index
                    if projection_index == next_projection_index:
                        next_projection_index += 1
                    projected.append({"index": projection_index, "role": "user", "text": current_text,
                                      "_originalTurnIndex": index, "_evidenceRanges": current_ranges})
                    current_text, current_ranges, separator = "", [], ""
                current_text += separator + piece["text"]
                current_ranges.append(piece)
            if current_ranges:
                projection_index = index if not any(turn.get("_originalTurnIndex") == index for turn in projected) else next_projection_index
                if projection_index == next_projection_index:
                    next_projection_index += 1
                projected.append({"index": projection_index, "role": "user", "text": current_text,
                                  "_originalTurnIndex": index, "_evidenceRanges": current_ranges})
        return projected

    @staticmethod
    def _relation_request_fits(payload: Mapping[str, Any]) -> bool:
        turns = list(payload["turns"])
        return (
            len(turns) <= 200
            and all(len(str(turn["text"])) <= 4_000 for turn in turns)
            and sum(len(str(turn["text"])) for turn in turns) <= 30_000
        )

    def _resolve_cross_batch_relations(
        self,
        *,
        turns: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        run_identity: LiveLongMemoryRunIdentity,
        retry_context: LiveMemoryContractRetryContext | None,
    ) -> tuple[list[dict[str, Any]], set[str], bool]:
        """Resolve bounded semantic relations without an unbounded final prompt."""

        if len(memories) < 2:
            return memories, set(), False
        if self._relation_reviewer is None:
            raise contract_failure("relationInput", "reviewerUnavailable")
        if (
            len(memories) > 8
            and callable(
                getattr(self._relation_reviewer, "request_relation_batch_review", None)
            )
        ):
            return self._resolve_cross_batch_relations_batched(
                turns=turns,
                memories=memories,
                run_identity=run_identity,
                retry_context=retry_context,
            )
        resolved: list[dict[str, Any]] = []
        retracted_atom_ids: set[str] = set()
        relations_changed = False
        for incoming_ordinal, raw_incoming in enumerate(memories):
            incoming = json.loads(json.dumps(raw_incoming, ensure_ascii=False))
            if not resolved:
                resolved.append(incoming)
                continue
            page_start = 0
            drop_incoming = False
            matching_decisions: list[tuple[int, dict[str, Any]]] = []
            while page_start < len(resolved):
                page_count = min(32, len(resolved) - page_start)
                builder = getattr(self._relation_reviewer, "build_relation_request", None)
                while True:
                    page = resolved[page_start:page_start + page_count]
                    originals = {
                        int(turn["index"]): turn for turn in turns
                        if turn.get("role") == "user" and type(turn.get("index")) is int
                    }
                    request_payload = {
                        "incoming": self._compact_relation_memory(incoming, originals),
                        "existing": [self._compact_relation_memory(item, originals) for item in page],
                        "turns": self._project_relation_turns(
                            turns=turns, memories=[incoming, *page]
                        ),
                    }
                    fits = self._relation_request_fits(request_payload)
                    measured_request = None
                    prepared_relation = None
                    if fits:
                        measured_request, prepared_relation = self._freeze_provider_request(
                            self._relation_reviewer, "build_relation_request",
                            **request_payload,
                        )
                        measured_request = measured_request or request_payload
                        fits = len(json.dumps(measured_request, ensure_ascii=False).encode("utf-8")) <= 55_000
                    if fits:
                        break
                    if page_count == 1:
                        raise contract_failure("relationInput", "capacityExceeded")
                    page_count = max(1, page_count // 2)
                plan = LiveLongMemoryUnitPlan(
                    run_id=run_identity.run_id,
                    ordinal=1_000_000 + incoming_ordinal * 10_000 + page_start,
                    kind="relationReview",
                    generation=1,
                    ownership=tuple(
                        {
                            "incomingHash": sha256(
                                json.dumps(incoming, ensure_ascii=False, sort_keys=True).encode("utf-8")
                            ).hexdigest(),
                            "existingHash": sha256(
                                json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")
                            ).hexdigest(),
                        }
                        for item in page
                    ),
                )
                unit = self._run_repository.record_unit(plan)
                if unit.get("state") == "completed":
                    decisions = list((unit.get("coverage") or {}).get("decisions") or [])
                    self._validate_relation_decisions(decisions, existing_count=len(page))
                else:
                    reservation = self._run_repository.reserve_provider_attempt(
                        run_id=run_identity.run_id,
                        unit_id=plan.unit_id,
                        stage="relationReview",
                        request_hash=sha256(
                            json.dumps(measured_request, ensure_ascii=False, sort_keys=True).encode("utf-8")
                        ).hexdigest(),
                        reserved_input_tokens=conservative_token_estimate(measured_request),
                        reserved_output_tokens=2_048,
                        recovery=retry_context is not None,
                        policy=self._run_budget_policy,
                    )
                    try:
                        review = self._relation_reviewer.request_relation_review(
                            turns=request_payload["turns"],
                            incoming=request_payload["incoming"],
                            existing=request_payload["existing"],
                            **({"prepared_request": prepared_relation} if prepared_relation else {}),
                        )
                        decisions = list(review.get("decisions") or [])
                        self._validate_relation_decisions(decisions, existing_count=len(page))
                    except Exception as error:
                        self._run_repository.complete_provider_attempt(
                            reservation,
                            exposure_state=self._provider_failure_exposure(error),
                            model=str(getattr(self._relation_reviewer, "model", "unknown")),
                            finish_reason=None,
                            usage=None,
                            response_hash=None,
                        )
                        raise
                    finish_reason = str(review.pop("_providerFinishReason", "stop") or "stop")
                    usage = review.pop("_providerUsage", None)
                    response_hash = sha256(
                        json.dumps(review, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest()
                    self._run_repository.complete_provider_attempt(
                        reservation,
                        exposure_state="responseAccepted",
                        model=str(getattr(self._relation_reviewer, "model", "unknown")),
                        finish_reason=finish_reason,
                        usage=usage,
                        response_hash=response_hash,
                    )
                    self._run_repository.record_unit_result(
                        plan=plan,
                        atoms=(),
                        output_hash=response_hash,
                        coverage={"decisions": decisions},
                    )
                non_distinct = [
                    decision
                    for decision in decisions
                    if decision.get("relation") != "distinct"
                ]
                if any(item.get("relation") == "unresolved" for item in non_distinct):
                    raise contract_failure("relationValidate", "unresolved")
                matching_decisions.extend(
                    (page_start + int(decision["existingIndex"]), decision)
                    for decision in non_distinct
                )
                page_start += len(page)
            if len(matching_decisions) > 1:
                raise contract_failure("relationValidate", "ambiguousTargets")
            if matching_decisions:
                relations_changed = True
                target, decision = matching_decisions[0]
                relation = str(decision["relation"])
                if relation == "duplicate":
                    resolved[target] = self._merge_memory_evidence(resolved[target], incoming)
                    drop_incoming = True
                elif relation == "supplement":
                    replacement = decision.get("resolvedMemory")
                    if not isinstance(replacement, dict):
                        raise contract_failure("relationValidate", "resolvedMemoryMissing")
                    replacement = self._merge_memory_lineage(
                        replacement, resolved[target], incoming
                    )
                    resolved[target] = replacement
                    drop_incoming = True
                elif relation == "correction":
                    incoming = self._merge_memory_lineage(incoming, resolved[target])
                    resolved[target] = incoming
                    drop_incoming = True
                elif relation == "retraction":
                    retracted_atom_ids.update(
                        str(value)
                        for value in (
                            list(resolved[target].get("_atomIds") or [])
                            + list(incoming.get("_atomIds") or [])
                        )
                    )
                    resolved.pop(target)
                    drop_incoming = True
                else:
                    raise contract_failure("relationValidate", "relationInvalid")
            if not drop_incoming:
                resolved.append(incoming)
        return resolved, retracted_atom_ids, relations_changed

    def _planned_support_pages(
        self,
        *,
        turns: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        retry_context: LiveMemoryContractRetryContext | None,
    ) -> list[tuple[int, list[dict[str, Any]], list[dict[str, Any]]]]:
        pages: list[tuple[int, list[dict[str, Any]], list[dict[str, Any]]]] = []

        def add_page(start: int, page: list[dict[str, Any]]) -> None:
            evidence_turns = self._owned_evidence_turns(turns=turns, memories=page)
            if not evidence_turns:
                raise contract_failure("supportValidate", "evidenceOutOfRange")
            lengths = [len(str(turn["text"])) for turn in evidence_turns]
            fits = (
                len(evidence_turns) <= 200
                and all(length <= 4_000 for length in lengths)
                and sum(lengths) <= 30_000
            )
            if fits:
                prepared = self._support_budget_payload(
                    turns=evidence_turns, memories=page,
                    retry_context=retry_context,
                )
                fits = len(json.dumps(prepared, ensure_ascii=False).encode("utf-8")) <= 55_000
            if fits:
                pages.append((start, page, evidence_turns))
            elif len(page) > 1:
                midpoint = len(page) // 2
                add_page(start, page[:midpoint])
                add_page(start + midpoint, page[midpoint:])
            else:
                raise contract_failure("supportInput", "capacityExceeded")

        for start in range(0, len(memories), 4):
            page = [
                json.loads(json.dumps(memory, ensure_ascii=False))
                for memory in memories[start:start + 4]
            ]
            add_page(start, page)
        return pages

    def _revalidate_resolved_memories(
        self,
        *,
        turns: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        run_identity: LiveLongMemoryRunIdentity,
        retry_context: LiveMemoryContractRetryContext | None,
        stage_reporter: Callable[[str, Mapping[str, int]], None] | None,
    ) -> list[dict[str, Any]]:
        """Invalidate old support after relation edits and bind fresh proof."""

        validated_all: list[dict[str, Any]] = []
        for page_start, page, evidence_turns in self._planned_support_pages(
            turns=turns, memories=memories, retry_context=retry_context
        ):
            plan = LiveLongMemoryUnitPlan(
                run_id=run_identity.run_id,
                ordinal=3_000_000 + page_start,
                kind="relationSupport",
                generation=1,
                ownership=tuple(
                    {
                        "memoryHash": sha256(
                            json.dumps(memory, ensure_ascii=False, sort_keys=True).encode("utf-8")
                        ).hexdigest(),
                        "atomIds": list(memory.get("_atomIds") or []),
                    }
                    for memory in page
                ),
            )
            unit = self._run_repository.record_unit(plan)
            if unit.get("state") == "completed":
                recovered = (unit.get("coverage") or {}).get("memories")
                if not isinstance(recovered, list) or any(
                    not isinstance(memory, dict) for memory in recovered
                ):
                    raise contract_failure("supportValidate", "coverageIncomplete")
                validated_all.extend(
                    json.loads(json.dumps(memory, ensure_ascii=False))
                    for memory in recovered
                )
                continue

            prepared_payload, prepared_support = self._freeze_provider_request(
                self._support_reviewer, "build_support_request",
                turns=evidence_turns, memories=page,
                **({"repair_hint": retry_context.repair_hint} if retry_context else {}),
            )
            request_payload = prepared_payload or {
                "stage": "atomSupport", "turns": evidence_turns, "memories": page
            }
            reservation = self._run_repository.reserve_provider_attempt(
                run_id=run_identity.run_id,
                unit_id=plan.unit_id,
                stage="relationSupport",
                request_hash=sha256(
                    json.dumps(request_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                reserved_input_tokens=conservative_token_estimate(request_payload),
                reserved_output_tokens=4_096,
                recovery=retry_context is not None,
                policy=self._run_budget_policy,
            )
            try:
                review = self._request_support_review(
                    turns=evidence_turns,
                    memories=page,
                    retry_context=retry_context,
                    stage_reporter=stage_reporter,
                    prepared_request=prepared_support,
                )
                responsibility_atom_ids = tuple(
                    dict.fromkeys(
                        str(atom_id)
                        for memory in page
                        for atom_id in memory.get("_atomIds", ())
                        if str(atom_id)
                    )
                )
                validated = validate_live_memory_support(
                    turns=evidence_turns,
                    memories=page,
                    review=review,
                    source_index_by_projected_index={
                        int(turn["index"]): int(turn.get("_originalTurnIndex", turn["index"]))
                        for turn in evidence_turns
                    },
                    responsibility_atom_ids=(
                        responsibility_atom_ids
                        if getattr(
                            self._support_reviewer,
                            "supports_atom_responsibility_contract",
                            False,
                        )
                        else None
                    ),
                )
            except Exception as error:
                self._run_repository.complete_provider_attempt(
                    reservation,
                    exposure_state=self._provider_failure_exposure(error),
                    model=str(getattr(self._support_reviewer, "model", "unknown")),
                    finish_reason=None,
                    usage=None,
                    response_hash=None,
                )
                raise
            response_hash = sha256(
                json.dumps(review, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            self._run_repository.complete_provider_attempt(
                reservation,
                exposure_state="responseAccepted",
                model=str(getattr(self._support_reviewer, "model", "unknown")),
                finish_reason=str(review.pop("_providerFinishReason", "stop") or "stop"),
                usage=review.pop("_providerUsage", None),
                response_hash=response_hash,
            )
            bound = self._rebind_owned_support_proof(
                memories=validated,
                review=review,
            )
            self._run_repository.record_unit_result(
                plan=plan,
                atoms=(),
                output_hash=sha256(
                    json.dumps(bound, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                coverage={"memories": bound},
            )
            validated_all.extend(bound)
        return validated_all

    @staticmethod
    def _owned_evidence_turns(
        *,
        turns: Sequence[Mapping[str, Any]],
        memories: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        turns_by_index = {
            int(turn["index"]): turn
            for turn in turns
            if isinstance(turn.get("index"), int)
        }
        owned_fragments: dict[int, list[tuple[int, int, str]]] = {}
        for memory in memories:
            for raw_range in memory.get("_sourceEvidenceRanges") or []:
                if not isinstance(raw_range, Mapping):
                    raise contract_failure("supportValidate", "evidenceOutOfRange")
                turn_index = raw_range.get("turnIndex")
                start = raw_range.get("start")
                end = raw_range.get("end")
                expected_hash = str(raw_range.get("textHash") or "")
                if (
                    isinstance(turn_index, bool)
                    or not isinstance(turn_index, int)
                    or isinstance(start, bool)
                    or not isinstance(start, int)
                    or isinstance(end, bool)
                    or not isinstance(end, int)
                    or start < 0
                    or end <= start
                    or turn_index not in turns_by_index
                ):
                    raise contract_failure("supportValidate", "evidenceOutOfRange")
                original = turns_by_index[turn_index]
                full_text = str(original.get("text") or "")
                if end > len(full_text):
                    raise contract_failure("supportValidate", "evidenceOutOfRange")
                fragment = full_text[start:end]
                if sha256(fragment.encode("utf-8")).hexdigest() != expected_hash:
                    raise contract_failure("supportValidate", "evidenceInvalid")
                owned_fragments.setdefault(turn_index, []).append(
                    (start, end, fragment)
                )
        owned_turns: list[dict[str, Any]] = []
        next_projection_index = max(turns_by_index, default=0) + 1
        for turn_index in sorted(owned_fragments):
            original = turns_by_index[turn_index]
            fragments = sorted(set(owned_fragments[turn_index]))
            combined_text = " ".join(fragment for _start, _end, fragment in fragments)
            if len(combined_text) > 4_000:
                first_piece = True
                for start, end, _fragment in fragments:
                    for piece_start in range(start, end, 4_000):
                        piece_end = min(piece_start + 4_000, end)
                        piece = str(original.get("text") or "")[piece_start:piece_end]
                        index = turn_index if first_piece else next_projection_index
                        if not first_piece:
                            next_projection_index += 1
                        first_piece = False
                        owned_turns.append({
                            "index": index,
                            "role": "user",
                            "text": piece,
                            "_originalTurnIndex": turn_index,
                            "_evidenceRanges": [{
                                "start": piece_start,
                                "end": piece_end,
                                "textHash": sha256(piece.encode("utf-8")).hexdigest(),
                                "text": piece,
                            }],
                        })
                continue
            owned_turns.append(
                {
                    **dict(original),
                    "text": combined_text,
                    "_ownedEvidenceRanges": [
                        {"start": start, "end": end}
                        for start, end, _fragment in fragments
                    ],
                }
            )
        return owned_turns

    @staticmethod
    def _rebind_owned_support_proof(
        *,
        memories: Sequence[Mapping[str, Any]],
        review: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        proof_hash = sha256(
            json.dumps(
                {"review": dict(review), "memories": list(memories)},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        rebound: list[dict[str, Any]] = []
        for raw_memory in memories:
            memory = json.loads(json.dumps(raw_memory, ensure_ascii=False))
            if not memory.get("_sourceEvidenceRanges"):
                raise contract_failure("supportValidate", "evidenceOutOfRange")
            memory["_supportProofHash"] = proof_hash
            rebound.append(memory)
        return rebound

    def _resolve_cross_batch_relations_batched(
        self,
        *,
        turns: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        run_identity: LiveLongMemoryRunIdentity,
        retry_context: LiveMemoryContractRetryContext | None,
    ) -> tuple[list[dict[str, Any]], set[str], bool]:
        resolved: list[dict[str, Any]] = []
        retracted_atom_ids: set[str] = set()
        relations_changed = False
        for batch_start in range(0, len(memories), 8):
            incoming_page = [
                json.loads(json.dumps(memory, ensure_ascii=False))
                for memory in memories[batch_start : batch_start + 8]
            ]
            resolved_snapshot = [
                json.loads(json.dumps(memory, ensure_ascii=False))
                for memory in resolved
            ]
            matches: dict[int, list[tuple[str, int, dict[str, Any]]]] = {
                index: [] for index in range(len(incoming_page))
            }
            for page_start in range(0, len(resolved_snapshot), 32):
                existing_page = resolved_snapshot[page_start : page_start + 32]
                results = self._relation_batch_page(
                    turns=turns,
                    incoming=incoming_page,
                    existing=existing_page,
                    run_identity=run_identity,
                    retry_context=retry_context,
                    ordinal=2_000_000 + batch_start * 10_000 + page_start,
                    intra_batch=False,
                )
                for result in results:
                    for decision in result["decisions"]:
                        matches[int(result["incomingIndex"])].append(
                            (
                                "resolved",
                                page_start + int(decision["existingIndex"]),
                                decision,
                            )
                        )

            intra_results = self._relation_batch_page(
                turns=turns,
                incoming=incoming_page,
                existing=incoming_page,
                run_identity=run_identity,
                retry_context=retry_context,
                ordinal=2_000_000 + batch_start * 10_000 + 9_000,
                intra_batch=True,
            )
            for result in intra_results:
                incoming_index = int(result["incomingIndex"])
                for decision in result["decisions"]:
                    target = int(decision["existingIndex"])
                    if target >= incoming_index:
                        raise contract_failure("relationValidate", "forwardBatchTarget")
                    matches[incoming_index].append(("incoming", target, decision))

            local_results: list[dict[str, Any] | None] = []
            for incoming_index, incoming in enumerate(incoming_page):
                decisions = matches[incoming_index]
                if any(item[2].get("relation") == "unresolved" for item in decisions):
                    raise contract_failure("relationValidate", "unresolved")
                if len(decisions) > 1:
                    raise contract_failure("relationValidate", "ambiguousTargets")
                if not decisions:
                    local_results.append(incoming)
                    continue
                relations_changed = True
                scope, target, decision = decisions[0]
                target_list: list[dict[str, Any] | None]
                if scope == "resolved":
                    target_list = resolved  # type: ignore[assignment]
                else:
                    target_list = local_results
                if target >= len(target_list) or target_list[target] is None:
                    raise contract_failure("relationValidate", "targetUnavailable")
                retracted_atom_ids.update(self._apply_relation_decision(
                    target_list=target_list,
                    target=target,
                    incoming=incoming,
                    decision=decision,
                ))
                local_results.append(None)
            resolved = [memory for memory in resolved if memory is not None]
            resolved.extend(memory for memory in local_results if memory is not None)
        return resolved, retracted_atom_ids, relations_changed

    def _relation_batch_page(
        self,
        *,
        turns: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
        existing: list[dict[str, Any]],
        run_identity: LiveLongMemoryRunIdentity,
        retry_context: LiveMemoryContractRetryContext | None,
        ordinal: int,
        intra_batch: bool,
    ) -> list[dict[str, Any]]:
        if not incoming or not existing:
            return [
                {
                    "incomingIndex": index,
                    "scannedExistingCount": len(existing),
                    "decisions": [],
                }
                for index in range(len(incoming))
            ]
        original_by_index = {
            int(turn["index"]): turn for turn in turns
            if turn.get("role") == "user" and type(turn.get("index")) is int
        }
        request_payload = {
            "incoming": [
                self._compact_relation_memory(item, original_by_index)
                for item in incoming
            ],
            "existing": [
                self._compact_relation_memory(item, original_by_index)
                for item in existing
            ],
            "intraBatch": intra_batch,
            "turns": self._project_relation_turns(
                turns=turns, memories=[*incoming, *existing]
            ),
        }
        builder = getattr(self._relation_reviewer, "build_relation_batch_request", None)
        fits = self._relation_request_fits(request_payload)
        measured_request = None
        prepared_relation = None
        if fits:
            measured_request, prepared_relation = self._freeze_provider_request(
                self._relation_reviewer, "build_relation_batch_request",
                turns=request_payload["turns"], incoming=request_payload["incoming"],
                existing=request_payload["existing"], intra_batch=intra_batch,
            )
            measured_request = measured_request or request_payload
            fits = len(json.dumps(measured_request, ensure_ascii=False).encode("utf-8")) <= 55_000
        if not fits:
            if len(incoming) > 1 and intra_batch:
                results = []
                for incoming_index in range(len(incoming)):
                    if incoming_index == 0:
                        results.append({
                            "incomingIndex": 0,
                            "scannedExistingCount": len(existing),
                            "decisions": [],
                        })
                        continue
                    prior = self._relation_batch_page(
                        turns=turns,
                        incoming=[incoming[incoming_index]],
                        existing=existing[:incoming_index],
                        run_identity=run_identity,
                        retry_context=retry_context,
                        ordinal=ordinal + incoming_index,
                        intra_batch=False,
                    )[0]
                    results.append({
                        **prior,
                        "incomingIndex": incoming_index,
                        "scannedExistingCount": len(existing),
                    })
                return results
            if len(existing) > 1:
                midpoint = len(existing) // 2
                sections = (existing[:midpoint], existing[midpoint:])
                results_by_incoming: dict[int, list[dict[str, Any]]] = {
                    index: [] for index in range(len(incoming))
                }
                for offset, section in ((0, sections[0]), (midpoint, sections[1])):
                    subresults = self._relation_batch_page(
                        turns=turns, incoming=incoming, existing=section,
                        run_identity=run_identity, retry_context=retry_context,
                        ordinal=ordinal, intra_batch=intra_batch,
                    )
                    for result in subresults:
                        results_by_incoming[int(result["incomingIndex"])].extend(
                            {**decision, "existingIndex": int(decision["existingIndex"]) + offset}
                            for decision in result["decisions"]
                        )
                return [
                    {
                        "incomingIndex": index,
                        "scannedExistingCount": len(existing),
                        "decisions": decisions,
                    }
                    for index, decisions in results_by_incoming.items()
                ]
            if len(incoming) > 1:
                midpoint = len(incoming) // 2
                return [
                    {**result, "incomingIndex": int(result["incomingIndex"]) + offset}
                    for offset, section in ((0, incoming[:midpoint]), (midpoint, incoming[midpoint:]))
                    for result in self._relation_batch_page(
                        turns=turns, incoming=section, existing=existing,
                        run_identity=run_identity, retry_context=retry_context,
                        ordinal=ordinal, intra_batch=intra_batch,
                    )
                ]
            raise contract_failure("relationInput", "capacityExceeded")
        plan = LiveLongMemoryUnitPlan(
            run_id=run_identity.run_id,
            ordinal=ordinal,
            kind="relationReviewBatch",
            generation=1,
            ownership=tuple(
                {
                    "incomingIndex": incoming_index,
                    "existingIndex": existing_index,
                    "incomingHash": sha256(
                        json.dumps(incoming_memory, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "existingHash": sha256(
                        json.dumps(existing_memory, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                }
                for incoming_index, incoming_memory in enumerate(incoming)
                for existing_index, existing_memory in enumerate(existing)
            ),
        )
        unit = self._run_repository.record_unit(plan)
        if unit.get("state") == "completed":
            results = list((unit.get("coverage") or {}).get("results") or [])
            self._validate_relation_batch_results(
                results,
                incoming_count=len(incoming),
                existing_count=len(existing),
            )
            return results
        try:
            reservation = self._run_repository.reserve_provider_attempt(
                run_id=run_identity.run_id,
                unit_id=plan.unit_id,
                stage="relationReviewBatch",
                request_hash=sha256(
                    json.dumps(
                        measured_request or request_payload, ensure_ascii=False, sort_keys=True
                    ).encode("utf-8")
                ).hexdigest(),
                reserved_input_tokens=conservative_token_estimate(measured_request or request_payload),
                reserved_output_tokens=4_096,
                recovery=retry_context is not None,
                policy=self._run_budget_policy,
            )
        except LiveLongMemoryBudgetExhausted:
            run = self._run_repository.snapshot(run_identity.run_id) or {}
            _log_stage_diagnostic({
                "stage": "relationBudgetExhausted",
                "requestCount": run.get("providerRequestCount"),
                "unitCount": run.get("plannedUnitCount"),
                "inputTokens": run.get("reservedInputTokens"),
                "outputTokens": run.get("reservedOutputTokens"),
                "incomingCount": len(incoming),
                "existingCount": len(existing),
                "requestBytes": len(json.dumps(measured_request or request_payload, ensure_ascii=False).encode("utf-8")),
                "evidenceBytes": len(json.dumps(request_payload["turns"], ensure_ascii=False).encode("utf-8")),
                "memoryBytes": len(json.dumps([*incoming, *existing], ensure_ascii=False).encode("utf-8")),
            })
            raise
        try:
            review = self._relation_reviewer.request_relation_batch_review(
                turns=request_payload["turns"],
                incoming=request_payload["incoming"],
                existing=request_payload["existing"],
                intra_batch=intra_batch,
                **({"prepared_request": prepared_relation} if prepared_relation else {}),
            )
            results = list(review.get("results") or [])
            self._validate_relation_batch_results(
                results,
                incoming_count=len(incoming),
                existing_count=len(existing),
            )
        except Exception as error:
            self._run_repository.complete_provider_attempt(
                reservation,
                exposure_state=self._provider_failure_exposure(error),
                model=str(getattr(self._relation_reviewer, "model", "unknown")),
                finish_reason=None,
                usage=None,
                response_hash=None,
            )
            raise
        finish_reason = str(review.pop("_providerFinishReason", "stop") or "stop")
        usage = review.pop("_providerUsage", None)
        response_hash = sha256(
            json.dumps(review, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self._run_repository.complete_provider_attempt(
            reservation,
            exposure_state="responseAccepted",
            model=str(getattr(self._relation_reviewer, "model", "unknown")),
            finish_reason=finish_reason,
            usage=usage,
            response_hash=response_hash,
        )
        self._run_repository.record_unit_result(
            plan=plan,
            atoms=(),
            output_hash=response_hash,
            coverage={"results": results},
        )
        return results

    @staticmethod
    def _validate_relation_batch_results(
        results: list[dict[str, Any]],
        *,
        incoming_count: int,
        existing_count: int,
    ) -> set[str]:
        if len(results) != incoming_count:
            raise contract_failure("relationValidate", "coverageInvalid")
        seen: set[int] = set()
        total_decisions = 0
        for result in results:
            if not isinstance(result, dict):
                raise contract_failure("relationValidate", "schemaInvalid")
            incoming_index = result.get("incomingIndex")
            decisions = result.get("decisions")
            if (
                isinstance(incoming_index, bool)
                or not isinstance(incoming_index, int)
                or not 0 <= incoming_index < incoming_count
                or incoming_index in seen
                or result.get("scannedExistingCount") != existing_count
                or not isinstance(decisions, list)
                or len(decisions) > 1
            ):
                raise contract_failure("relationValidate", "coverageInvalid")
            for decision in decisions:
                if not isinstance(decision, dict):
                    raise contract_failure("relationValidate", "schemaInvalid")
                target = decision.get("existingIndex")
                if (
                    isinstance(target, bool)
                    or not isinstance(target, int)
                    or not 0 <= target < existing_count
                ):
                    raise contract_failure("relationValidate", "targetInvalid")
            total_decisions += len(decisions)
            seen.add(incoming_index)
        if total_decisions > 64:
            raise contract_failure("relationValidate", "pageSaturated")

    @staticmethod
    def _apply_relation_decision(
        *,
        target_list: list[dict[str, Any] | None],
        target: int,
        incoming: dict[str, Any],
        decision: dict[str, Any],
    ) -> None:
        current = target_list[target]
        if current is None:
            raise contract_failure("relationValidate", "targetUnavailable")
        relation = str(decision.get("relation") or "")
        if relation == "duplicate":
            target_list[target] = ModelAssistedOwnerTruthLiveConversationExtractor._merge_memory_evidence(
                current, incoming
            )
        elif relation == "supplement":
            replacement = decision.get("resolvedMemory")
            if not isinstance(replacement, dict):
                raise contract_failure("relationValidate", "resolvedMemoryMissing")
            replacement = ModelAssistedOwnerTruthLiveConversationExtractor._merge_memory_lineage(
                replacement, current, incoming
            )
            target_list[target] = replacement
        elif relation == "correction":
            incoming = ModelAssistedOwnerTruthLiveConversationExtractor._merge_memory_lineage(
                incoming, current
            )
            target_list[target] = incoming
        elif relation == "retraction":
            target_list[target] = None
            return {
                str(value)
                for value in (
                    list(current.get("_atomIds") or [])
                    + list(incoming.get("_atomIds") or [])
                )
            }
        elif relation == "unresolved":
            raise contract_failure("relationValidate", "unresolved")
        else:
            raise contract_failure("relationValidate", "relationInvalid")
        return set()

    @staticmethod
    def _validate_relation_decisions(
        decisions: list[dict[str, Any]],
        *,
        existing_count: int,
    ) -> None:
        if len(decisions) != existing_count or len(decisions) > 64:
            raise contract_failure("relationValidate", "coverageInvalid")
        indices: set[int] = set()
        allowed = {"duplicate", "supplement", "correction", "retraction", "distinct", "unresolved"}
        for decision in decisions:
            if not isinstance(decision, dict):
                raise contract_failure("relationValidate", "schemaInvalid")
            index = decision.get("existingIndex")
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < existing_count:
                raise contract_failure("relationValidate", "indexInvalid")
            if index in indices or decision.get("relation") not in allowed:
                raise contract_failure("relationValidate", "schemaInvalid")
            indices.add(index)

    @staticmethod
    def _merge_memory_evidence(
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        return ModelAssistedOwnerTruthLiveConversationExtractor._merge_memory_lineage(
            existing, incoming
        )

    @staticmethod
    def _merge_memory_lineage(
        primary: dict[str, Any],
        *contributors: dict[str, Any],
    ) -> dict[str, Any]:
        merged = json.loads(json.dumps(primary, ensure_ascii=False))
        values = (primary, *contributors)
        merged["sourceTurnIndices"] = sorted(
            {
                int(index)
                for value in values
                for index in value.get("sourceTurnIndices") or []
            }
        )
        merged["_atomIds"] = sorted(
            {
                str(atom_id)
                for value in values
                for atom_id in value.get("_atomIds") or []
                if atom_id
            }
        )
        ranges = {
            (
                int(item["turnIndex"]),
                int(item["start"]),
                int(item["end"]),
                str(item["textHash"]),
            ): dict(item)
            for value in values
            for item in value.get("_sourceEvidenceRanges") or []
        }
        merged["_sourceEvidenceRanges"] = [ranges[key] for key in sorted(ranges)]
        merged.pop("_supportProofHash", None)
        return merged

    @staticmethod
    def _live_turns(
        source: OwnerTruthCandidateExtractionInput,
    ) -> list[dict[str, Any]] | None:
        metadata = source.source_metadata or {}
        if (
            metadata.get("captureMode") != "live"
            or metadata.get("sourcePolicy") != "userEvidenceOnly"
        ):
            return None
        raw_turns = metadata.get("conversationTurns")
        if not isinstance(raw_turns, list) or not raw_turns:
            raise ValueError("Live Source is missing its structured conversation turns")
        if any(
            not isinstance(turn, dict) or turn.get("captureMode") != "live"
            for turn in raw_turns
        ):
            raise ValueError("Live Source contains a turn outside the Live consent boundary")
        return [
            {
                "index": turn.get("index"),
                "role": turn.get("role"),
                "text": turn.get("text"),
            }
            for turn in raw_turns
        ]

    @staticmethod
    def _owner_evidence_spans(
        *,
        source: OwnerTruthCandidateExtractionInput,
        turns: list[dict[str, Any]],
    ) -> dict[int, CandidateEvidenceSpan]:
        spans: dict[int, CandidateEvidenceSpan] = {}
        search_start = 0
        for turn in turns:
            if turn.get("role") != "user":
                continue
            index = turn.get("index")
            text = str(turn.get("text") or "").strip()
            if isinstance(index, bool) or not isinstance(index, int) or not text:
                raise ValueError("Live Source contains invalid user evidence")
            start = source.source_text.find(text, search_start)
            if start < 0:
                raise ValueError("Live Source text does not match its user evidence turns")
            end = start + len(text)
            spans[index] = CandidateEvidenceSpan(start=start, end=end)
            search_start = end
        if not spans:
            raise ValueError("Live Source contains no user evidence")
        return spans


class ModelAssistedOwnerTruthSourceExtractor:
    """Route ordinary Owner text to typed organization without changing Live."""

    _EXTRACTOR_ID = "deepSeekTextMemoryOrganizer"

    def __init__(
        self,
        *,
        settings: Settings,
        organizer: TextMemoryOrganizationProvider | None = None,
        live_extractor: OwnerTruthCandidateExtractor | None = None,
    ) -> None:
        self._organizer = organizer or DeepSeekTextMemoryOrganizationProxy(settings)
        self._live_extractor = live_extractor or ModelAssistedOwnerTruthLiveConversationExtractor(
            settings=settings
        )
        self._text_organization_enabled = (
            settings.owner_truth_text_memory_organization_enabled
        )

    def publication_binding(
        self,
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
    ) -> tuple[str, str] | None:
        binding = getattr(self._live_extractor, "publication_binding", None)
        if not callable(binding):
            return None
        return binding(intent=intent, source=source)

    def extract(
        self,
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
        retry_context: LiveMemoryContractRetryContext | None = None,
        stage_reporter: Callable[[str, Mapping[str, int]], None] | None = None,
    ) -> SyntheticCandidateExtractionCommand:
        metadata = source.source_metadata or {}
        if self._is_live(metadata):
            if isinstance(
                self._live_extractor,
                ModelAssistedOwnerTruthLiveConversationExtractor,
            ):
                return self._live_extractor.extract(
                    intent=intent,
                    source=source,
                    retry_context=retry_context,
                    stage_reporter=stage_reporter,
                )
            return self._live_extractor.extract(intent=intent, source=source)
        if self._is_image_processing(metadata):
            return self._live_extractor.extract(intent=intent, source=source)
        if not self._text_organization_enabled:
            return self._live_extractor.extract(intent=intent, source=source)

        normalized_text = source.source_text.strip()
        if not normalized_text:
            return self._live_extractor.extract(intent=intent, source=source)

        perspective_type, epistemic_status = _source_provenance(metadata)
        memory_subject_id, claim_subject_id = _typed_subjects(source)
        family_organization = getattr(
            self._organizer,
            "request_family_organization",
            None,
        )
        family_scope_enforced = perspective_type is PerspectiveType.REPORTED
        if family_scope_enforced and not callable(family_organization):
            raise RuntimeError(
                "family text organization requires subject-role classification"
            )
        if family_scope_enforced:
            organization = family_organization(text=normalized_text)
        else:
            organization = self._organizer.request_organization(text=normalized_text)
        memories = organization.get("memories")
        if not isinstance(memories, list):
            raise ValueError("text memory organizer returned an invalid memories contract")

        proposals: list[CandidateProposal] = []
        for memory in memories:
            if not isinstance(memory, Mapping):
                raise ValueError("text memory organizer returned an invalid memory")
            if family_scope_enforced:
                subject_role = str(memory.get("subjectRole") or "").strip()
                if subject_role in {"reporterSelf", "unknown"}:
                    continue
                if subject_role != "memorySubject":
                    raise ValueError(
                        "family text memory organizer returned an invalid subject role"
                    )
            memory_kind = MemoryKind(str(memory.get("memoryKind") or ""))
            content = memory.get("content")
            if not isinstance(content, Mapping):
                raise ValueError("text memory organizer returned invalid typed content")
            normalized_content = enrich_memory_payload_v5(
                kind=memory_kind,
                payload=content,
                provenance=_typed_source_provenance(source=source),
                memory_subject_id=memory_subject_id,
                claim_subject_id=claim_subject_id,
            )
            proposals.append(
                CandidateProposal(
                    memory_kind=memory_kind,
                    perspective_type=perspective_type,
                    epistemic_status=epistemic_status,
                    sensitivity=SensitivityLevel.STANDARD,
                    content=normalized_content,
                    evidence_span=CandidateEvidenceSpan(
                        start=0,
                        end=len(source.source_text),
                    ),
                    confidence=0.0,
                    review_mode=CandidateReviewMode.SINGLE,
                    payload_schema_version=OWNER_TRUTH_SCHEMA_VERSION_V5,
                )
            )
        return SyntheticCandidateExtractionCommand(
            intent=intent,
            extractor_id=self._EXTRACTOR_ID,
            model_id=self._organizer.model,
            prompt_version=self._organizer.prompt_version,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            source_content_hash=source.source_content_hash,
            status=ExtractionResultStatus.SUCCEEDED,
            proposals=tuple(proposals),
        )

    @staticmethod
    def _is_live(metadata: Mapping[str, Any]) -> bool:
        return metadata.get("captureMode") == "live" or isinstance(
            metadata.get("conversationTurns"), list
        )

    @staticmethod
    def _is_image_processing(metadata: Mapping[str, Any]) -> bool:
        return (
            metadata.get("mediaKind") == "image"
            and (
                metadata.get("origin") == "mediaSourceObjectProcessing"
                or "candidateFacets" in metadata
                or "candidateFacetsHash" in metadata
            )
        )

class OwnerTruthCandidateExtractionWorkerRuntime:
    """One-shot, fail-closed consumer for default-off Source extraction work."""

    def __init__(
        self,
        *,
        settings: Settings,
        store: Any,
        worker_id: Optional[str] = None,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
        retry_seconds: int = _DEFAULT_RETRY_SECONDS,
        heartbeat_interval_seconds: float | None = None,
        extractor: OwnerTruthCandidateExtractor | None = None,
        operation_metric_recorder: OperationMetricRecorder | None = None,
        stage_diagnostic_recorder: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._worker_id = str(
            worker_id or f"owner-truth-candidate-extraction-worker-{socket.gethostname()}"
        )
        self._lease_seconds = max(1, int(lease_seconds))
        self._retry_seconds = max(1, int(retry_seconds))
        self._heartbeat_interval_seconds = _heartbeat_interval_seconds(
            lease_seconds=self._lease_seconds,
            configured=heartbeat_interval_seconds,
        )
        self._live_preorganizer: ModelAssistedOwnerTruthLiveConversationExtractor | None = None
        if extractor is None:
            self._live_preorganizer = ModelAssistedOwnerTruthLiveConversationExtractor(
                settings=settings,
                run_repository=StoreBackedLiveLongMemoryRepository(store),
            )
            self._extractor = ModelAssistedOwnerTruthSourceExtractor(
                settings=settings,
                live_extractor=self._live_preorganizer,
            )
        else:
            self._extractor = extractor
        self._operation_metric_recorder = operation_metric_recorder or self._make_metric_recorder()
        self._stage_diagnostic_recorder = stage_diagnostic_recorder or _log_stage_diagnostic

    def run_once(self) -> dict[str, Any]:
        started_at = perf_counter()
        reason = self._runtime_block_reason()
        if reason is not None:
            return self._payload(status="blocked", reason=reason)
        store_reason = self._worker_store_block_reason()
        if store_reason is not None:
            return self._payload(status="blocked", reason=store_reason)

        preorganization = self._preorganize_live_unit_once()
        lease = self._claim_next()
        if lease is None:
            if preorganization.get("status") == "completed":
                return self._payload(
                    status="completed",
                    reason="liveMemoryUnitPreorganized",
                    live_preorganization=preorganization,
                )
            if preorganization.get("status") in {"retryWait", "failed"}:
                return self._payload(
                    status=str(preorganization["status"]),
                    reason=str(preorganization.get("reason") or "liveMemoryPreorganizationFailed"),
                    live_preorganization=preorganization,
                )
            return self._payload(
                status="idle",
                reason="noEligibleCandidateExtractionJob",
                live_preorganization=preorganization,
            )

        try:
            result = self._consume_current_lease(lease)
        except AsyncEffectLeaseCancelled:
            result = self._payload(
                status="cancelled",
                reason="candidateExtractionCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            result = self._payload(
                status="lost",
                reason="candidateExtractionLeaseLost",
                lease=lease,
            )
        except Exception as error:
            result = self._release_retryable_or_terminalize(
                lease,
                failure=_classify_candidate_extraction_failure(error),
            )
        self._record_attempt(lease=lease, result=result, started_at=started_at)
        if preorganization.get("status") != "idle":
            result["livePreorganization"] = preorganization
        return result

    def _preorganize_live_unit_once(self) -> dict[str, Any]:
        if self._live_preorganizer is None:
            return {"status": "idle", "reason": "livePreorganizationNotConfigured"}
        try:
            return self._live_preorganizer.preorganize_once(
                worker_id=f"{self._worker_id}:live",
                lease_seconds=self._lease_seconds,
            )
        except Exception as error:
            failure = _classify_candidate_extraction_failure(error)
            return {
                "status": (
                    "retryWait"
                    if failure.retryable or failure.contract_retry_eligible
                    else "failed"
                ),
                "reason": failure.code,
            }

    def _make_metric_recorder(self) -> OperationMetricRecorder:
        sink = getattr(self._store, "append_evidence_event", None)
        return OperationMetricRecorder(
            environment=self._settings.environment,
            build="backend-owner-truth-candidate-worker",
            event_sink=sink if callable(sink) else None,
            retention_days=self._settings.evidence_rollout_retention_days,
            identifier_hmac_key=self._settings.operations_evidence_hmac_key,
        )

    def _record_attempt(
        self,
        *,
        lease: AsyncEffectJobLease,
        result: dict[str, Any],
        started_at: float,
    ) -> None:
        # Shadow observability must never alter a private extraction outcome.
        try:
            status = str(result.get("status") or "").strip()
            outcome = {
                "completed": "succeeded",
                "blocked": "cancelled",
                "cancelled": "cancelled",
                "lost": "unknown",
                "retryWait": "failed",
                "failed": "failed",
            }.get(status, "unknown")
            self._operation_metric_recorder.record_attempt(
                request_key=lease.job_id,
                operation_key=lease.operation_id,
                attempt=lease.attempt,
                component_kind="worker",
                component_id=_WORKER_METRIC_COMPONENT_ID,
                operation="ownerTruthCandidateExtraction",
                outcome=outcome,
                feedback_state="notApplicable",
                latency_ms=max(0, int((perf_counter() - started_at) * 1000)),
                correlation_key=f"ownerTruthCandidateExtraction:{lease.operation_id}",
            )
        except Exception:
            return

    def _consume_current_lease(self, lease: AsyncEffectJobLease) -> dict[str, Any]:
        # Read the immutable Source in a short transaction, then release the
        # database connection before any model/provider network wait begins.
        with self._unit_of_work(
            correlation_id=f"owner-truth-candidate-extraction-worker-read-{lease.job_id}",
            command_id=f"ownerTruthCandidateExtractionWorkerRead:{lease.operation_id}",
        ):
            lease_repository = self._store.async_effect_lease_repository()
            intent = lease_repository.load_intent(lease)
            self._assert_typed_intent(intent)
            admission = (
                self._store.owner_truth_source_target_admission_repository()
                .admit_owner_truth_source(intent)
            )
            consumer_repository = self._store.async_effect_consumer_repository()
            if not admission.allowed:
                receipt = consumer_repository.consume(
                    OwnerTruthSourceBlockedConsumerCommand(
                        intent=intent,
                        consumer_name="ownerTruth.source.blocked",
                        business_target_key=intent.business_target_key,
                        outcome="blocked",
                        reason_code=admission.reason_code,
                        result_ref_hash=_result_hash(intent.stable_key, admission.reason_code),
                        admission=admission,
                    )
                )
                completion = lease_repository.complete(
                    lease,
                    outcome="blocked",
                    error_code=admission.reason_code,
                )
                return self._payload(
                    status="blocked",
                    reason=admission.reason_code,
                    lease=lease,
                    intent=intent,
                    completion=completion,
                    receipt=receipt,
                )

            source = (
                self._store.owner_truth_candidate_extraction_input_repository()
                .read_for_candidate_extraction(intent)
            )
            is_live_source = self._is_live_source(source)
            if is_live_source and lease.attempt > int(intent.max_attempts):
                raise contract_failure(
                    "workerExecution", "budgetExhausted", category="budget"
                )
            stored_retry_context = (
                lease_repository.load_contract_retry_context(lease)
                if is_live_source
                and hasattr(lease_repository, "load_contract_retry_context")
                else None
            )

        # DeepSeek is used only here, outside the database transaction. Lease
        # heartbeat remains active so another worker cannot persist a duplicate.
        command = self._extract_with_lease_heartbeat(
            lease=lease,
            intent=intent,
            source=source,
            retry_context=(
                LiveMemoryContractRetryContext(
                    attempt=stored_retry_context.attempt,
                    stage=stored_retry_context.stage,
                    reason=stored_retry_context.reason,
                )
                if stored_retry_context is not None
                else None
            ),
            stage_reporter=self._stage_reporter(lease),
        )

        # Revalidate authority and the current lease immediately before the
        # Candidate result, consumer receipt, and lease completion commit.
        with self._unit_of_work(
            correlation_id=f"owner-truth-candidate-extraction-worker-write-{lease.job_id}",
            command_id=f"ownerTruthCandidateExtractionWorkerWrite:{lease.operation_id}",
        ):
            lease_repository = self._store.async_effect_lease_repository()
            current_intent = lease_repository.load_intent(lease)
            self._assert_typed_intent(current_intent)
            if current_intent.stable_key != intent.stable_key:
                raise AsyncEffectLeaseLost("candidate extraction intent changed during organization")
            self._record_stage(lease, "candidateCommitStarted")
            if is_live_source:
                try:
                    result = OwnerTruthCandidateExtractionService(
                        self._store
                    ).record_in_unit_of_work(command)
                except (TypeError, ValueError) as error:
                    raise contract_failure(
                        "candidateCommit", "contractInvalid", category="domain"
                    ) from error
            else:
                result = OwnerTruthCandidateExtractionService(self._store).record_in_unit_of_work(command)
            self._record_stage(
                lease,
                "candidateCommitSucceeded",
                candidateCount=len(result.candidate_ids),
            )
            if result.outcome == "blocked":
                completion = lease_repository.complete(
                    lease,
                    outcome="blocked",
                    error_code=result.reason_code,
                )
                return self._payload(
                    status="blocked",
                    reason=result.reason_code,
                    lease=lease,
                    intent=intent,
                    completion=completion,
                    receipt=result.consumer,
                )
            if result.status is None or result.extraction_id is None:
                raise OwnerTruthCandidateExtractionWorkerError(
                    "candidate extraction completed without a terminal result"
                )

            publication_binding = getattr(
                self._extractor,
                "publication_binding",
                None,
            )
            if (
                is_live_source
                and self._settings.owner_truth_live_long_memory_pipeline_enabled
                and result.status is ExtractionResultStatus.SUCCEEDED
                and callable(publication_binding)
            ):
                binding = publication_binding(intent=intent, source=source)
                if binding is None:
                    raise contract_failure(
                        "candidateCommit", "publicationManifestUnavailable"
                    )
                run_id, manifest_hash = binding
                self._store.owner_truth_live_long_memory_repository().mark_published(
                    run_id=run_id,
                    manifest_hash=manifest_hash,
                    extraction_id=result.extraction_id,
                    candidate_ids=result.candidate_ids,
                )

            completion = lease_repository.complete(lease, outcome="succeeded")
            extraction_completed_without_candidates = (
                result.status is ExtractionResultStatus.SUCCEEDED
                and not result.candidate_ids
            )
            reason = {
                ExtractionResultStatus.SUCCEEDED: (
                    "candidateExtractionCompletedNoChange"
                    if extraction_completed_without_candidates
                    else "candidateExtractionProposalsPersisted"
                ),
                ExtractionResultStatus.QUARANTINED: "candidateExtractionQuarantined",
                ExtractionResultStatus.FAILED: "candidateExtractionFailed",
            }[result.status]

        # Candidate persistence and job completion are authoritative. Message
        # projection is auxiliary and runs in its own transaction so an inbox
        # routing outage cannot erase a reviewable Candidate.
        message_projection = None
        message_projection_failure_reason = None
        if result.status is ExtractionResultStatus.SUCCEEDED and result.candidate_ids:
            try:
                with self._unit_of_work(
                    correlation_id=(
                        f"owner-truth-candidate-extraction-worker-message-{lease.job_id}"
                    ),
                    command_id=(
                        f"ownerTruthCandidateExtractionWorkerMessage:{lease.operation_id}"
                    ),
                ):
                    message_projection = enqueue_owner_business_message(
                        self._store,
                        intent=intent,
                        completion=result.consumer,
                        kind=InAppMessageKind.CANDIDATE_READY,
                    )
            except Exception:
                message_projection_failure_reason = (
                    "ownerBusinessMessageProjectionUnavailable"
                )
        return self._payload(
            status="completed",
            reason=reason,
            lease=lease,
            intent=intent,
            completion=completion,
            receipt=result.consumer,
            extraction_result=result,
            message_projection=message_projection,
            message_projection_failure_reason=message_projection_failure_reason,
        )

    @staticmethod
    def _assert_typed_intent(intent: AsyncEffectIntent) -> None:
        target = intent.target
        if (
            intent.job_type != _SOURCE_CANDIDATE_EXTRACTION_JOB_TYPE
            or intent.operation_type != "ownerTruth.source.created"
            or target.resource_type != "source"
            or target.purpose != "candidateExtraction"
        ):
            raise OwnerTruthCandidateExtractionWorkerError(
                "claimed job does not match candidate extraction worker type"
            )

    @staticmethod
    def _is_live_source(source: OwnerTruthCandidateExtractionInput) -> bool:
        metadata = source.source_metadata or {}
        return (
            metadata.get("captureMode") == "live"
            and metadata.get("sourcePolicy") == "userEvidenceOnly"
        )

    def _claim_next(self) -> AsyncEffectJobLease | None:
        with self._unit_of_work(
            correlation_id="owner-truth-candidate-extraction-worker-claim",
            command_id="ownerTruthCandidateExtractionWorkerClaim",
        ):
            return self._store.async_effect_lease_repository().claim_next(
                worker_id=self._worker_id,
                lease_seconds=self._lease_seconds,
                supported_job_types=[_SOURCE_CANDIDATE_EXTRACTION_JOB_TYPE],
                require_live_run_ready=(
                    self._settings.owner_truth_live_long_memory_pipeline_enabled
                ),
            )

    def _extract_with_lease_heartbeat(
        self,
        *,
        lease: AsyncEffectJobLease,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
        retry_context: LiveMemoryContractRetryContext | None = None,
        stage_reporter: Callable[[str, Mapping[str, int]], None] | None = None,
    ) -> SyntheticCandidateExtractionCommand:
        """Renew a claimed lease while an extractor performs external work.

        Source read and result persistence use separate short transactions.
        The potentially slow extractor runs without holding either one.
        Renewal uses an independent Unit of Work; if it fails, the extraction
        result is discarded rather than committing after ownership is unclear.
        """

        heartbeat = self._start_lease_heartbeat(lease)
        try:
            if isinstance(
                self._extractor,
                (
                    ModelAssistedOwnerTruthLiveConversationExtractor,
                    ModelAssistedOwnerTruthSourceExtractor,
                ),
            ):
                command = self._extractor.extract(
                    intent=intent,
                    source=source,
                    retry_context=retry_context,
                    stage_reporter=stage_reporter,
                )
            else:
                command = self._extractor.extract(intent=intent, source=source)
        except Exception:
            self._stop_and_verify_lease_heartbeat(heartbeat)
            raise
        self._stop_and_verify_lease_heartbeat(heartbeat)
        return command

    def _stage_reporter(
        self,
        lease: AsyncEffectJobLease,
    ) -> Callable[[str, Mapping[str, int]], None] | None:
        if self._stage_diagnostic_recorder is None:
            return None

        def report(stage: str, counts: Mapping[str, int]) -> None:
            self._record_stage(lease, stage, **dict(counts))

        return report

    def _record_stage(
        self,
        lease: AsyncEffectJobLease,
        stage: str,
        **counts: int,
    ) -> None:
        recorder = self._stage_diagnostic_recorder
        if recorder is None or stage not in _SAFE_STAGE_DIAGNOSTIC_NAMES:
            return
        safe_counts = {
            key: int(value)
            for key, value in counts.items()
            if key in _SAFE_STAGE_DIAGNOSTIC_COUNTS
            and isinstance(value, int)
            and not isinstance(value, bool)
        }
        event: dict[str, Any] = {
            "stage": stage,
            "attempt": lease.attempt,
            "correlation": _result_hash(
                "candidateExtractionStage",
                lease.job_id,
                lease.operation_id,
            )[:24],
        }
        event.update(safe_counts)
        try:
            recorder(event)
        except Exception:
            return

    def _start_lease_heartbeat(self, lease: AsyncEffectJobLease) -> WorkerLeaseHeartbeat:
        heartbeat = WorkerLeaseHeartbeat(
            heartbeat=lambda: self._renew_lease(lease),
            interval_seconds=self._heartbeat_interval_seconds,
        )
        heartbeat.start()
        return heartbeat

    def _stop_and_verify_lease_heartbeat(self, heartbeat: WorkerLeaseHeartbeat) -> None:
        heartbeat.stop()
        try:
            heartbeat.raise_if_failed()
        except AsyncEffectLeaseCancelled:
            raise
        except AsyncEffectLeaseLost:
            raise
        except Exception as exc:
            # A renewal error means the worker cannot prove ownership of the
            # current lease. Do not persist an extractor result or receipt.
            raise AsyncEffectLeaseLost("candidate extraction lease heartbeat failed") from exc

    def _renew_lease(self, lease: AsyncEffectJobLease) -> None:
        with self._unit_of_work(
            correlation_id=f"owner-truth-candidate-extraction-worker-heartbeat-{lease.job_id}",
            command_id=f"ownerTruthCandidateExtractionWorkerHeartbeat:{lease.operation_id}",
        ):
            self._store.async_effect_lease_repository().heartbeat(
                lease,
                lease_seconds=self._lease_seconds,
            )

    def _release_retryable_or_terminalize(
        self,
        lease: AsyncEffectJobLease,
        *,
        failure: CandidateExtractionFailure,
    ) -> dict[str, Any]:
        try:
            with self._unit_of_work(
                correlation_id=f"owner-truth-candidate-extraction-worker-retry-{lease.job_id}",
                command_id=f"ownerTruthCandidateExtractionWorkerRetry:{lease.operation_id}",
            ):
                lease_repository = self._store.async_effect_lease_repository()
                intent = lease_repository.load_intent(lease)
                self._assert_typed_intent(intent)
                can_retry = failure.retryable or (
                    failure.contract_retry_eligible and lease.attempt == 1
                )
                if can_retry and lease.attempt < int(intent.max_attempts):
                    retry_seconds = self._retry_delay_seconds(lease)
                    preview = lease_repository.release_retryable(
                        lease,
                        retry_seconds=retry_seconds,
                        error_code=failure.code,
                    )
                    return self._payload(
                        status="retryWait",
                        reason=failure.code,
                        lease=lease,
                        intent=intent,
                        retry_available_at=preview.available_at,
                        failure=failure,
                    )

                admission = (
                    self._store.owner_truth_source_target_admission_repository()
                    .admit_owner_truth_source(intent)
                )
                consumer_repository = self._store.async_effect_consumer_repository()
                if not admission.allowed:
                    receipt = consumer_repository.consume(
                        OwnerTruthSourceBlockedConsumerCommand(
                            intent=intent,
                            consumer_name="ownerTruth.source.blocked",
                            business_target_key=intent.business_target_key,
                            outcome="blocked",
                            reason_code=admission.reason_code,
                            result_ref_hash=_result_hash(
                                intent.stable_key,
                                admission.reason_code,
                                str(lease.attempt),
                            ),
                            admission=admission,
                        )
                    )
                    completion = lease_repository.complete(
                        lease,
                        outcome="blocked",
                        error_code=admission.reason_code,
                    )
                    return self._payload(
                        status="blocked",
                        reason=admission.reason_code,
                        lease=lease,
                        intent=intent,
                        completion=completion,
                        receipt=receipt,
                    )

                source = (
                    self._store.owner_truth_candidate_extraction_input_repository()
                    .read_for_candidate_extraction(intent)
                )
                command = self._terminal_failure_command(
                    intent=intent,
                    source=source,
                    failure_code=failure.code,
                )
                extraction_result = OwnerTruthCandidateExtractionService(
                    self._store
                ).record_in_unit_of_work(command)
                if extraction_result.outcome == "blocked":
                    completion = lease_repository.complete(
                        lease,
                        outcome="blocked",
                        error_code=extraction_result.reason_code,
                    )
                    return self._payload(
                        status="blocked",
                        reason=extraction_result.reason_code,
                        lease=lease,
                        intent=intent,
                        completion=completion,
                        receipt=extraction_result.consumer,
                        extraction_result=extraction_result,
                    )

                completion = lease_repository.complete(
                    lease,
                    outcome="failed",
                    error_code=failure.code,
                    terminal_reason_code=_TERMINAL_FAILURE_REASON,
                )
                admission_record = admit_dead_letter(
                    intent=intent,
                    job_state=AsyncEffectJobState.FAILED,
                    attempt=lease.attempt,
                    max_attempts=int(intent.max_attempts),
                    cause=failure.dead_letter_cause,
                    failure_hash=_result_hash(
                        intent.stable_key,
                        _TERMINAL_FAILURE_REASON,
                        str(lease.attempt),
                    ),
                    last_receipt_hash=_result_hash(
                        extraction_result.consumer.business_receipt_id,
                        extraction_result.consumer.business_target_key,
                        extraction_result.consumer.business_outcome,
                    ),
                )
                dead_letter = self._store.async_effect_dead_letter_repository().record(
                    admission_record
                )
                return self._payload(
                    status="failed",
                    reason=_TERMINAL_FAILURE_REASON,
                    lease=lease,
                    intent=intent,
                    completion=completion,
                    receipt=extraction_result.consumer,
                    extraction_result=extraction_result,
                    dead_letter=dead_letter,
                    failure=failure,
                )
        except AsyncEffectLeaseCancelled:
            return self._payload(
                status="cancelled",
                reason="candidateExtractionCancelled",
                lease=lease,
            )
        except AsyncEffectLeaseLost:
            return self._payload(
                status="lost",
                reason="candidateExtractionLeaseLost",
                lease=lease,
            )
        except Exception:
            return self._payload(
                status="failed",
                reason="candidateExtractionRetryReleaseFailed",
                lease=lease,
            )

    @staticmethod
    def _terminal_failure_command(
        *,
        intent: AsyncEffectIntent,
        source: OwnerTruthCandidateExtractionInput,
        failure_code: str,
    ) -> SyntheticCandidateExtractionCommand:
        """Persist a worker failure without representing it as model output."""

        return SyntheticCandidateExtractionCommand(
            intent=intent,
            extractor_id=_TERMINAL_FAILURE_EXTRACTOR_ID,
            model_id=_TERMINAL_FAILURE_MODEL_ID,
            prompt_version=_TERMINAL_FAILURE_PROMPT_VERSION,
            policy_version=OWNER_TRUTH_SCHEMA_VERSION,
            source_content_hash=source.source_content_hash,
            status=ExtractionResultStatus.FAILED,
            proposals=(),
            failure_code=failure_code,
            retryable=False,
        )

    def _retry_delay_seconds(self, lease: AsyncEffectJobLease) -> int:
        if self._retry_seconds != _DEFAULT_RETRY_SECONDS:
            return self._retry_seconds
        base = 5 if lease.attempt == 1 else 20
        jitter = int(lease.job_id.replace("-", "")[-2:], 16) % 3
        return base + jitter

    def _runtime_block_reason(self) -> str | None:
        readiness = self._readiness()
        runtime = resolve_async_effect_runtime_status(
            async_effect_v1_enabled=self._settings.async_effect_v1_enabled,
            worker_enabled=self._settings.async_effect_worker_enabled,
            schema_ready=readiness,
        )
        if not runtime.allowed:
            return runtime.reason
        if not self._settings.owner_truth_candidate_extraction_worker_enabled:
            return "ownerTruthCandidateExtractionWorkerDisabled"
        return None

    def _readiness(self) -> bool:
        probe = getattr(self._store, "readiness_probe", None)
        if not callable(probe):
            return False
        return is_async_effect_store_ready(probe())

    def _worker_store_block_reason(self) -> str | None:
        required = [
            "request_unit_of_work",
            "async_effect_lease_repository",
            "async_effect_consumer_repository",
            "async_effect_dead_letter_repository",
            "owner_truth_source_target_admission_repository",
            "owner_truth_candidate_extraction_input_repository",
            "owner_truth_candidate_extraction_repository",
        ]
        if self._settings.owner_truth_live_long_memory_pipeline_enabled:
            required.append("owner_truth_live_long_memory_repository")
        if not all(callable(getattr(self._store, name, None)) for name in required):
            return "ownerTruthCandidateExtractionWorkerStoreUnsupported"
        return None

    def _unit_of_work(self, *, correlation_id: str, command_id: str):
        return self._store.request_unit_of_work(
            correlation_id=correlation_id,
            command_id=command_id,
        )

    def _payload(
        self,
        *,
        status: str,
        reason: str,
        lease: AsyncEffectJobLease | None = None,
        intent: AsyncEffectIntent | None = None,
        completion: Any | None = None,
        receipt: Any | None = None,
        extraction_result: OwnerTruthCandidateExtractionResult | None = None,
        message_projection: Any | None = None,
        message_projection_failure_reason: str | None = None,
        retry_available_at: str | None = None,
        dead_letter: Any | None = None,
        failure: CandidateExtractionFailure | None = None,
        live_preorganization: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mode": "run",
            "status": status,
            "reason": reason,
            "workerId": self._worker_id,
        }
        if lease is not None:
            payload.update(
                {
                    "jobId": lease.job_id,
                    "operationId": lease.operation_id,
                    "attempt": lease.attempt,
                }
            )
        if intent is not None:
            payload["jobType"] = intent.job_type
            payload["targetStableKey"] = intent.stable_key
        if completion is not None:
            payload.update(
                {
                    "jobState": completion.job_state,
                    "operationState": completion.operation_state,
                    "outboxState": completion.outbox_state,
                }
            )
        if receipt is not None:
            payload.update(
                {
                    "consumerOutcome": receipt.outcome,
                    "businessOutcome": receipt.business_outcome,
                    "consumerInboxState": receipt.inbox_state,
                }
            )
        if extraction_result is not None:
            candidate_count = len(extraction_result.candidate_ids)
            payload.update(
                {
                    "candidateCount": candidate_count,
                    "candidateOutcome": (
                        "completedNoChange"
                        if (
                            extraction_result.status is ExtractionResultStatus.SUCCEEDED
                            and candidate_count == 0
                        )
                        else "pendingReview"
                        if extraction_result.status is ExtractionResultStatus.SUCCEEDED
                        else None
                    ),
                    "extractionId": extraction_result.extraction_id,
                    "extractionStatus": extraction_result.status.value
                    if extraction_result.status is not None
                    else None,
                }
            )
        if message_projection is not None:
            payload.update(
                {
                    "messageProjectionKind": message_projection.kind.value,
                    "messageProjectionOutcome": message_projection.outcome,
                    "messageProjectionInputOutcome": message_projection.input_outcome,
                }
            )
        if message_projection_failure_reason is not None:
            payload.update(
                {
                    "messageProjectionKind": InAppMessageKind.CANDIDATE_READY.value,
                    "messageProjectionOutcome": "unavailable",
                    "messageProjectionFailureReason": message_projection_failure_reason,
                }
            )
        if retry_available_at is not None:
            payload["retryAvailableAt"] = retry_available_at
        if failure is not None:
            payload.update(
                {
                    "failureCode": failure.code,
                    "failureStage": failure.stage,
                    "failureType": failure.error_type,
                    "retryable": failure.retryable,
                }
            )
            if failure.provider_status is not None:
                payload["providerStatus"] = failure.provider_status
        if live_preorganization is not None:
            payload["livePreorganization"] = dict(live_preorganization)
        if dead_letter is not None:
            admission = dead_letter.admission
            payload.update(
                {
                    "deadLetterCause": admission.cause.value,
                    "deadLetterId": admission.dead_letter_id,
                    "deadLetterNextAction": admission.next_action,
                    "deadLetterOutcome": dead_letter.outcome,
                    "deadLetterState": admission.state.value,
                }
            )
        return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="DreamJourney default-disabled Owner Truth candidate extraction worker"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="claim and consume at most one typed job")
    mode.add_argument("--loop", action="store_true", help="continuously claim typed jobs")
    parser.add_argument("--worker-id", default=None, help="opaque worker identifier")
    parser.add_argument("--lease-seconds", type=int, default=_DEFAULT_LEASE_SECONDS)
    parser.add_argument("--retry-seconds", type=int, default=_DEFAULT_RETRY_SECONDS)
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=None,
        help="idle delay between loop iterations; defaults to OWNER_TRUTH_WORKER_POLL_SECONDS",
    )
    return parser


def _heartbeat_interval_seconds(*, lease_seconds: int, configured: float | None) -> float:
    """Renew well before expiry while avoiding a hot loop for short QA leases."""

    if configured is not None:
        normalized = float(configured)
        if normalized <= 0:
            raise ValueError("heartbeat interval must be positive")
        return normalized
    return max(0.1, min(30.0, float(lease_seconds) / 3.0))


def _worker_result_dedupe_key(
    payload: Mapping[str, Any],
) -> tuple[str, str] | None:
    """Only collapse repeated jobless health heartbeats in loop mode."""

    if payload.get("jobId") is not None or payload.get("attempt") is not None:
        return None
    status = str(payload.get("status") or "")
    if status not in {"idle", "blocked"}:
        return None
    return status, str(payload.get("reason") or "")


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.from_env()
    store = make_store(settings)
    open_store(store, wait=True)
    drain_controller = WorkerDrainController()
    drain_controller.install()
    try:
        worker = OwnerTruthCandidateExtractionWorkerRuntime(
            settings=settings,
            store=store,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
            retry_seconds=args.retry_seconds,
        )
        poll_seconds = max(
            0.1,
            float(
                args.poll_seconds
                if args.poll_seconds is not None
                else settings.owner_truth_worker_poll_seconds
            ),
        )
        last_result_key: tuple[str, str] | None = None
        try:
            while True:
                payload = worker.run_once()
                result_key = _worker_result_dedupe_key(payload)
                if not args.loop or result_key is None or result_key != last_result_key:
                    print(json.dumps(payload, sort_keys=True))
                last_result_key = result_key
                if not args.loop:
                    return 0
                if drain_controller.stop_requested:
                    print(
                        json.dumps(
                            {
                                "mode": "run",
                                "status": "drained",
                                "reason": "workerShutdownRequested",
                            },
                            sort_keys=True,
                        )
                    )
                    return 0
                sleep(poll_seconds)
        except KeyboardInterrupt:
            return 0
    finally:
        drain_controller.restore()
        close_store(store)


if __name__ == "__main__":  # pragma: no cover - exercised by CLI smoke
    raise SystemExit(main())
