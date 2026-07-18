"""QA-only Answer/Citation correction-request boundary for Owner Truth.

This module deliberately stops before a correction is accepted.  A request
captures an immutable answer/citation reference, preserves the Owner's raw
correction only in a private Source, and creates a pending Candidate.  It does
not modify an existing MemoryVersion, generate a new MemoryRecord, or change a
public Echo response.  A later correction-specific resolver must revalidate
the cited version and perform the version replacement atomically.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.candidate_decisions import (
    CandidateReviewAction,
    OwnerTruthCandidateReviewAccessDenied,
    OwnerTruthCandidateReviewCommand,
    OwnerTruthCandidateReviewConflict,
    OwnerTruthCandidateReviewError,
    OwnerTruthCandidateSnapshot,
)
from app.domain.owner_truth.contracts import (
    CandidateDecision,
    EpistemicStatus,
    MemoryKind,
    PerspectiveType,
    SensitivityLevel,
)
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
    OwnerTruthSourceCommandConflict,
    OwnerTruthSourceCommandResult,
    OwnerTruthSourceVersionConflict,
)
from app.services.owner_truth_memory_projection import (
    OwnerTruthMemoryProjectionError,
    OwnerTruthMemoryProjectionInput,
)
from app.services.owner_truth_memory_projection_effects import (
    build_memory_projection_rebuild_effect_intent_for_version,
)
from app.services.owner_truth_candidate_review import (
    PostgresOwnerTruthCandidateReviewRepository,
)
from app.domain.owner_truth.memory_correction import OwnerTruthMemoryCorrectionError
from app.services.owner_truth_source import OwnerTruthSourceCommandService


OWNER_TRUTH_CORRECTION_REQUEST_SCHEMA_VERSION = "owner-truth-correction-request-v1"
OWNER_TRUTH_CORRECTION_CANDIDATE_SCHEMA_VERSION = "owner-truth-correction-candidate-v1"
OWNER_TRUTH_CORRECTION_RESOLUTION_SCHEMA_VERSION = "owner-truth-correction-resolution-v1"
_SOURCE_NAMESPACE = UUID("f0f719db-7d2d-44db-900a-b4184d0eef2d")
_REQUEST_NAMESPACE = UUID("419cda41-b9d7-405d-80da-92f24fa2ae83")
_CANDIDATE_NAMESPACE = UUID("8d690241-66c8-4ac1-8af3-d473a72cf1ed")
_RESOLUTION_NAMESPACE = UUID("1dc3d9ce-85f8-45c9-9632-9eea41852f4c")
_OUTDATED_EVENT_NAMESPACE = UUID("7630d2db-cb67-47ca-b0d8-0a4dd99bebd1")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_MAX_CORRECTION_TEXT_CHARS = 20_000


class OwnerTruthCorrectionRequestError(OwnerTruthMemoryProjectionError):
    """A correction request cannot safely enter the Owner Truth QA lane."""


class OwnerTruthCorrectionRequestConflict(OwnerTruthCorrectionRequestError):
    """A stable correction command was reused with different meaning."""


class OwnerTruthCorrectionRequestAccessDenied(OwnerTruthCorrectionRequestError):
    """The cited answer, Vault or current MemoryVersion is not Owner-readable."""


class OwnerTruthCorrectionRequestStaleCitation(OwnerTruthCorrectionRequestError):
    """The cited MemoryVersion is no longer the active correction target."""


class OwnerTruthCorrectionRequestUnavailable(OwnerTruthCorrectionRequestError):
    """The default-off correction request surface is disabled."""


class OwnerTruthCorrectionResolutionConflict(OwnerTruthCorrectionRequestError):
    """A correction resolver command was reused or lost its target race."""


class OwnerTruthCorrectionResolutionStale(OwnerTruthCorrectionRequestError):
    """The cited MemoryVersion is no longer the resolvable current version."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthCorrectionRequestError("correction request values must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank_text(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthCorrectionRequestError(f"{field} must be nonblank")
    return normalized


def _identifier(value: object, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise OwnerTruthCorrectionRequestError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthCorrectionRequestError(f"{field} must be a UUID") from exc


def _hash(value: object, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field).lower()
    if not _HASH_PATTERN.fullmatch(normalized):
        raise OwnerTruthCorrectionRequestError(f"{field} must be a sha256 digest")
    return normalized


def _authority_epoch_matches(value: object, expected: int) -> bool:
    """Compare authority epochs without treating the initial epoch ``0`` as absent."""

    if value is None:
        return False
    try:
        return int(value) == expected
    except (TypeError, ValueError):
        return False


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthCorrectionRequestError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthCorrectionRequestAccessDenied(
            "only the Vault Owner may request a correction"
        )


@dataclass(frozen=True)
class OwnerTruthCorrectionRequestCommand:
    """One idempotent correction request bound to an immutable citation."""

    command_id: str
    answer_id: str
    citation_id: str
    memory_id: str
    expected_memory_version_id: str
    correction_text: str
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _identifier(self.command_id, field="command_id"))
        object.__setattr__(self, "answer_id", _uuid(self.answer_id, field="answer_id"))
        object.__setattr__(self, "citation_id", _uuid(self.citation_id, field="citation_id"))
        object.__setattr__(self, "memory_id", _uuid(self.memory_id, field="memory_id"))
        object.__setattr__(
            self,
            "expected_memory_version_id",
            _uuid(self.expected_memory_version_id, field="expected_memory_version_id"),
        )
        correction_text = _nonblank_text(self.correction_text, field="correction_text")
        if len(correction_text) > _MAX_CORRECTION_TEXT_CHARS:
            raise OwnerTruthCorrectionRequestError("correction_text exceeds the QA evidence limit")
        object.__setattr__(self, "correction_text", correction_text)
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()

    @property
    def correction_text_hash(self) -> str:
        return sha256(self.correction_text.encode("utf-8")).hexdigest()

    @property
    def correction_text_length(self) -> int:
        return len(self.correction_text)

    @property
    def reason_code_hash(self) -> str:
        return sha256(self.reason_code.encode("utf-8")).hexdigest()

    @property
    def source_id(self) -> str:
        return str(uuid5(_SOURCE_NAMESPACE, f"{self.command_id_hash}:source"))

    @property
    def correction_request_id(self) -> str:
        return str(uuid5(_REQUEST_NAMESPACE, f"{self.command_id_hash}:request"))

    @property
    def candidate_id(self) -> str:
        return str(uuid5(_CANDIDATE_NAMESPACE, f"{self.command_id_hash}:candidate"))

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "answerId": self.answer_id,
                "citationId": self.citation_id,
                "correctionTextHash": self.correction_text_hash,
                "correctionTextLength": self.correction_text_length,
                "expectedMemoryVersionId": self.expected_memory_version_id,
                "memoryId": self.memory_id,
                "reasonCode": self.reason_code,
            }
        )


@dataclass(frozen=True)
class OwnerTruthCorrectionRequestResult:
    outcome: str
    correction_request_id: str
    candidate_id: str
    answer_id: str
    citation_id: str
    memory_id: str
    expected_memory_version_id: str
    correction_source_id: str
    correction_text_hash: str
    correction_text_length: int
    candidate_row_version: int


@dataclass(frozen=True)
class OwnerTruthCorrectionResolutionCommand:
    """Owner's terminal decision over one pending correction request.

    ``corrected_value`` is supplied only for the apply path and is persisted in
    the existing private ``candidate_decision_values`` table.  The resolver
    response and all new ledger rows retain hashes and typed IDs only.
    """

    command_id: str
    expected_candidate_version: int
    expected_memory_version_id: str
    action: CandidateReviewAction
    corrected_value: Mapping[str, Any] | None
    corrected_value_schema_version: str
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _identifier(self.command_id, field="command_id"))
        if isinstance(self.expected_candidate_version, bool) or self.expected_candidate_version < 1:
            raise OwnerTruthCorrectionResolutionConflict(
                "expected_candidate_version must be positive"
            )
        object.__setattr__(
            self,
            "expected_memory_version_id",
            _uuid(self.expected_memory_version_id, field="expected_memory_version_id"),
        )
        try:
            action = CandidateReviewAction(self.action)
        except ValueError as exc:
            raise OwnerTruthCorrectionResolutionConflict(
                "correction resolution action is unsupported"
            ) from exc
        if action not in {CandidateReviewAction.CORRECT, CandidateReviewAction.REJECT}:
            raise OwnerTruthCorrectionResolutionConflict(
                "correction resolution action must be correct or reject"
            )
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))
        schema_version = _nonblank_text(
            self.corrected_value_schema_version,
            field="corrected_value_schema_version",
        )
        object.__setattr__(self, "corrected_value_schema_version", schema_version)
        if action is CandidateReviewAction.CORRECT:
            if not isinstance(self.corrected_value, Mapping):
                raise OwnerTruthCorrectionResolutionConflict(
                    "correct action requires corrected_value"
                )
            object.__setattr__(
                self,
                "corrected_value",
                json.loads(_canonical_json(dict(self.corrected_value))),
            )
        elif self.corrected_value is not None:
            raise OwnerTruthCorrectionResolutionConflict(
                "reject action must not include corrected_value"
            )

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "action": self.action.value,
                "correctedValue": self.corrected_value,
                "correctedValueSchemaVersion": self.corrected_value_schema_version,
                "expectedCandidateVersion": self.expected_candidate_version,
                "expectedMemoryVersionId": self.expected_memory_version_id,
                "reasonCode": self.reason_code,
            }
        )

    def review_command(self, *, candidate_id: str) -> OwnerTruthCandidateReviewCommand:
        return OwnerTruthCandidateReviewCommand(
            command_id=self.command_id,
            candidate_id=candidate_id,
            expected_candidate_version=self.expected_candidate_version,
            action=self.action,
            corrected_value=self.corrected_value,
            corrected_value_schema_version=self.corrected_value_schema_version,
            reason_code=self.reason_code,
        )

    def resolution_id(self, *, vault_id: str, correction_request_id: str) -> str:
        return str(
            uuid5(
                _RESOLUTION_NAMESPACE,
                f"correction-resolution:{vault_id}:{correction_request_id}:{self.command_id_hash}",
            )
        )

    def outdated_event_id(self, *, vault_id: str, correction_request_id: str) -> str:
        return str(
            uuid5(
                _OUTDATED_EVENT_NAMESPACE,
                f"answer-outdated:{vault_id}:{correction_request_id}:{self.command_id_hash}",
            )
        )


@dataclass(frozen=True)
class OwnerTruthCorrectionResolutionResult:
    outcome: str
    correction_request_id: str
    candidate_id: str
    receipt_id: str
    decision: CandidateDecision
    candidate_row_version: int
    superseded_memory_version_id: str | None
    replacement_memory_version_id: str | None
    replacement_memory_version: int | None
    answer_outdated_event_id: str | None
    authority_epoch: int | None
    content_hash: str | None
    projection_effect: Any | None = None


@dataclass(frozen=True)
class _CorrectionTarget:
    answer_id: str
    citation_id: str
    memory: OwnerTruthMemoryProjectionInput


class OwnerTruthCorrectionRequestStore(Protocol):
    def create_owner_truth_source(self, record: Any) -> OwnerTruthSourceCommandResult:
        ...

    def owner_truth_correction_request_repository(self) -> Any:
        ...


def _correction_source_metadata(
    *,
    command: OwnerTruthCorrectionRequestCommand,
) -> dict[str, Any]:
    return {
        "answerId": command.answer_id,
        "citationId": command.citation_id,
        "correctionTextHash": command.correction_text_hash,
        "correctionTextLength": command.correction_text_length,
        "expectedMemoryVersionId": command.expected_memory_version_id,
        "memoryId": command.memory_id,
        "origin": "ownerTruthAnswerCitationCorrection",
        "reasonCodeHash": command.reason_code_hash,
        "schemaVersion": OWNER_TRUTH_CORRECTION_REQUEST_SCHEMA_VERSION,
    }


def _candidate_snapshot(
    *,
    context: OwnerTruthCommandContext,
    command: OwnerTruthCorrectionRequestCommand,
    target: _CorrectionTarget,
    source: OwnerTruthSourceCommandResult,
) -> OwnerTruthCandidateSnapshot:
    memory = target.memory
    content = deepcopy(dict(memory.content))
    candidate_body = {
        "answerId": command.answer_id,
        "candidateKind": memory.memory_kind,
        "citationId": command.citation_id,
        "content": content,
        "contentSchemaVersion": memory.content_schema_version,
        "correctionRequestId": command.correction_request_id,
        "correctionTextHash": command.correction_text_hash,
        "correctionTextLength": command.correction_text_length,
        "expectedMemoryVersionId": command.expected_memory_version_id,
        "memoryId": command.memory_id,
        "reasonCodeHash": command.reason_code_hash,
        "sourceId": source.source_id,
        "sourceVersion": source.source_version,
    }
    proposal_hash = _digest(candidate_body)
    payload = {
        "schemaVersion": OWNER_TRUTH_CORRECTION_CANDIDATE_SCHEMA_VERSION,
        "candidateKind": memory.memory_kind,
        "perspectiveType": memory.perspective_type,
        "epistemicStatus": memory.epistemic_status,
        "sensitivity": memory.sensitivity,
        "content": content,
        "contentSchemaVersion": memory.content_schema_version,
        "confidence": 1.0,
        "reviewMode": "correction",
        "evidenceRefs": [
            {
                "sourceId": source.source_id,
                "sourceVersion": source.source_version,
                "span": {"start": 0, "end": command.correction_text_length},
            }
        ],
        "correctionRequest": {
            "answerId": command.answer_id,
            "citationId": command.citation_id,
            "correctionRequestId": command.correction_request_id,
            "correctionTextHash": command.correction_text_hash,
            "correctionTextLength": command.correction_text_length,
            "expectedMemoryVersionId": command.expected_memory_version_id,
            "memoryId": command.memory_id,
            "reasonCodeHash": command.reason_code_hash,
        },
        "proposalHash": proposal_hash,
    }
    return OwnerTruthCandidateSnapshot(
        candidate_id=command.candidate_id,
        vault_id=context.vault_id,
        owner_subject_id=context.owner_subject_id,
        source_id=source.source_id,
        memory_kind=MemoryKind(memory.memory_kind),
        perspective_type=PerspectiveType(memory.perspective_type),
        epistemic_status=EpistemicStatus(memory.epistemic_status),
        sensitivity=SensitivityLevel(memory.sensitivity),
        decision=CandidateDecision.PENDING,
        policy_version=context.policy_version,
        authority_epoch=source.authority_epoch,
        row_version=1,
        content_hash=_hash(memory.content_hash, field="memory.contentHash"),
        content_schema_version=memory.content_schema_version,
        payload=payload,
    )


class InMemoryOwnerTruthCorrectionRequestRepository:
    """Semantic double sharing Answer/Citation and Candidate review state."""

    def __init__(self, *, answer_repository: Any, review_repository: Any) -> None:
        self._answer_repository = answer_repository
        self._review_repository = review_repository
        self._lock = RLock()
        self._records: dict[tuple[str, str], dict[str, Any]] = {}
        self._resolutions: dict[tuple[str, str], dict[str, Any]] = {}
        self._outdated_events: dict[tuple[str, str], dict[str, Any]] = {}

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthCorrectionRequestCommand,
        source: OwnerTruthSourceCommandResult,
    ) -> OwnerTruthCorrectionRequestResult:
        _assert_owner_context(context)
        key = (context.vault_id, command.command_id_hash)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing["payloadHash"] != command.payload_hash:
                    raise OwnerTruthCorrectionRequestConflict(
                        "commandId cannot be reused with different correction evidence"
                    )
                return _result_from_record(existing, outcome="deduplicated")

            target = self._resolve_target(context=context, command=command)
            snapshot = _candidate_snapshot(
                context=context,
                command=command,
                target=target,
                source=source,
            )
            self._review_repository.seed(snapshot, source_state="active")
            record = {
                "answerId": command.answer_id,
                "candidateId": snapshot.candidate_id,
                "candidateRowVersion": snapshot.row_version,
                "citationId": command.citation_id,
                "correctionRequestId": command.correction_request_id,
                "correctionSourceId": source.source_id,
                "correctionTextHash": command.correction_text_hash,
                "correctionTextLength": command.correction_text_length,
                "expectedMemoryVersionId": command.expected_memory_version_id,
                "memoryId": command.memory_id,
                "payloadHash": command.payload_hash,
                "reasonCodeHash": command.reason_code_hash,
                "status": "pending",
            }
            self._records[key] = record
            return _result_from_record(record, outcome="created")

    def resolve(
        self,
        *,
        context: OwnerTruthCommandContext,
        correction_request_id: str,
        command: OwnerTruthCorrectionResolutionCommand,
    ) -> OwnerTruthCorrectionResolutionResult:
        _assert_owner_context(context)
        normalized_request_id = _uuid(correction_request_id, field="correction_request_id")
        with self._lock:
            existing = self._resolutions.get((context.vault_id, command.command_id_hash))
            if existing is not None:
                if (
                    existing["correctionRequestId"] != normalized_request_id
                    or existing["payloadHash"] != command.payload_hash
                ):
                    raise OwnerTruthCorrectionResolutionConflict(
                        "commandId cannot be reused with a different correction resolution"
                    )
                return _resolution_result_from_record(existing, outcome="deduplicated")
            record = self._record_by_id(
                vault_id=context.vault_id,
                correction_request_id=normalized_request_id,
            )
            if record is None:
                raise OwnerTruthCorrectionRequestAccessDenied(
                    "correction request does not exist in this Owner Vault"
                )
            if record.get("status") != "pending":
                raise OwnerTruthCorrectionResolutionConflict(
                    "correction request already has a terminal resolution"
                )
            if command.expected_memory_version_id != record["expectedMemoryVersionId"]:
                raise OwnerTruthCorrectionResolutionStale(
                    "correction resolver expectedMemoryVersionId does not match the request"
                )
            self._assert_current_target(
                context=context,
                memory_id=str(record["memoryId"]),
                expected_memory_version_id=str(record["expectedMemoryVersionId"]),
            )
            review = self._review_repository.decide(
                command=command.review_command(candidate_id=str(record["candidateId"])),
                context=context,
                allow_correction=True,
            )
            if command.action is CandidateReviewAction.CORRECT:
                if review.decision is not CandidateDecision.CORRECTED:
                    raise OwnerTruthCorrectionResolutionConflict(
                        "correction Candidate did not receive the corrected decision"
                    )
                activation = self._review_repository.activate_correction_memory_version(
                    receipt_id=review.receipt_id,
                    correction_request_id=normalized_request_id,
                    memory_id=str(record["memoryId"]),
                    expected_memory_version_id=str(record["expectedMemoryVersionId"]),
                    reason_code_hash=str(record["reasonCodeHash"]),
                    context=context,
                )
                outdated_event_id = command.outdated_event_id(
                    vault_id=context.vault_id,
                    correction_request_id=normalized_request_id,
                )
                self._outdated_events[(context.vault_id, outdated_event_id)] = {
                    "answerId": record["answerId"],
                    "citationId": record["citationId"],
                    "correctionRequestId": normalized_request_id,
                    "memoryId": record["memoryId"],
                    "replacementMemoryVersionId": activation.replacement_memory_version_id,
                    "supersededMemoryVersionId": activation.superseded_memory_version_id,
                }
                status = "accepted"
            else:
                if review.decision is not CandidateDecision.REJECTED:
                    raise OwnerTruthCorrectionResolutionConflict(
                        "correction Candidate did not receive the rejected decision"
                    )
                activation = None
                outdated_event_id = None
                status = "rejected"
            resolution = {
                "answerOutdatedEventId": outdated_event_id,
                "candidateId": record["candidateId"],
                "candidateRowVersion": review.candidate_row_version,
                "correctionRequestId": normalized_request_id,
                "decision": review.decision.value,
                "payloadHash": command.payload_hash,
                "receiptId": review.receipt_id,
                "replacementMemoryVersion": (
                    None if activation is None else activation.replacement_memory_version
                ),
                "replacementMemoryVersionId": (
                    None if activation is None else activation.replacement_memory_version_id
                ),
                "supersededMemoryVersionId": (
                    None if activation is None else activation.superseded_memory_version_id
                ),
                "authorityEpoch": None if activation is None else activation.authority_epoch,
                "contentHash": None if activation is None else activation.content_hash,
            }
            record["status"] = status
            self._resolutions[(context.vault_id, command.command_id_hash)] = resolution
            return _resolution_result_from_record(resolution, outcome="created")

    def _record_by_id(
        self,
        *,
        vault_id: str,
        correction_request_id: str,
    ) -> dict[str, Any] | None:
        for (record_vault_id, _command_hash), record in self._records.items():
            if record_vault_id == vault_id and record["correctionRequestId"] == correction_request_id:
                return record
        return None

    def _assert_current_target(
        self,
        *,
        context: OwnerTruthCommandContext,
        memory_id: str,
        expected_memory_version_id: str,
    ) -> None:
        """Fail before writing a terminal Candidate if its cited version is stale.

        The production implementation is additionally protected by a single
        database unit of work.  The semantic double has no rollback layer, so
        this early check keeps a stale concurrent correction from consuming a
        Candidate decision receipt that cannot produce a successor version.
        """

        supplier = getattr(self._review_repository, "list_memory_projection_inputs", None)
        if not callable(supplier):
            raise OwnerTruthCorrectionRequestError(
                "in-memory MemoryVersion lookup is unavailable"
            )
        _epoch, inputs = supplier(context=context)
        if not any(
            item.memory_id == memory_id
            and item.memory_version_id == expected_memory_version_id
            for item in inputs
        ):
            raise OwnerTruthCorrectionResolutionStale(
                "cited MemoryVersion is no longer current and cannot be resolved"
            )

    def _resolve_target(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthCorrectionRequestCommand,
    ) -> _CorrectionTarget:
        finder = getattr(self._answer_repository, "find_citation", None)
        if not callable(finder):
            raise OwnerTruthCorrectionRequestError("in-memory Answer/Citation lookup is unavailable")
        citation = finder(
            context=context,
            answer_id=command.answer_id,
            citation_id=command.citation_id,
        )
        if citation is None:
            raise OwnerTruthCorrectionRequestAccessDenied(
                "Answer citation does not exist in this Owner Vault"
            )
        fields = citation.get("citation") if isinstance(citation, Mapping) else None
        if not isinstance(fields, Mapping):
            raise OwnerTruthCorrectionRequestError("persisted Answer citation is malformed")
        if (
            str(fields.get("memoryId") or "") != command.memory_id
            or str(fields.get("memoryVersionId") or "") != command.expected_memory_version_id
        ):
            raise OwnerTruthCorrectionRequestStaleCitation(
                "Answer citation does not match the requested MemoryVersion"
            )
        supplier = getattr(self._review_repository, "list_memory_projection_inputs", None)
        if not callable(supplier):
            raise OwnerTruthCorrectionRequestError("in-memory MemoryVersion lookup is unavailable")
        _epoch, inputs = supplier(context=context)
        matches = [
            item
            for item in inputs
            if item.memory_id == command.memory_id
            and item.memory_version_id == command.expected_memory_version_id
        ]
        if len(matches) != 1:
            raise OwnerTruthCorrectionRequestStaleCitation(
                "cited MemoryVersion is no longer current and active"
            )
        return _CorrectionTarget(
            answer_id=command.answer_id,
            citation_id=command.citation_id,
            memory=matches[0],
        )


class PostgresOwnerTruthCorrectionRequestRepository:
    """Write one immutable correction request and pending Candidate in a UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthCorrectionRequestCommand,
        source: OwnerTruthSourceCommandResult,
    ) -> OwnerTruthCorrectionRequestResult:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-correction-request:{context.vault_id}:{command.command_id_hash}",),
            )
            existing = self._existing_request(cursor, context=context, command=command)
            if existing is not None:
                return _result_from_record(existing, outcome="deduplicated")

            target = self._resolve_target(cursor, context=context, command=command)
            self._assert_correction_source(cursor, context=context, source=source)
            snapshot = _candidate_snapshot(
                context=context,
                command=command,
                target=target,
                source=source,
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_candidates (
                    id, vault_id, owner_subject_id, source_id, extraction_result_id,
                    candidate_kind, perspective_type, epistemic_status, sensitivity,
                    decision_status, quarantine_code, policy_version, authority_epoch,
                    content_hash, payload_schema_version, payload
                ) VALUES (
                    %s, %s, %s, %s, NULL, %s, %s, %s, %s,
                    'pending', NULL, %s, %s, %s, %s, %s
                )
                """,
                self._adapt_params(
                    (
                        snapshot.candidate_id,
                        context.vault_id,
                        context.owner_subject_id,
                        source.source_id,
                        snapshot.memory_kind.value,
                        snapshot.perspective_type.value,
                        snapshot.epistemic_status.value,
                        snapshot.sensitivity.value,
                        snapshot.policy_version,
                        snapshot.authority_epoch,
                        snapshot.content_hash,
                        snapshot.content_schema_version,
                        dict(snapshot.payload),
                    )
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.correction_requests (
                    id, vault_id, owner_subject_id, command_id_hash, command_payload_hash,
                    answer_id, citation_id, memory_id, expected_memory_version_id,
                    correction_source_id, correction_text_hash, correction_text_length,
                    reason_code_hash, candidate_id, status
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, 'pending'
                )
                """,
                (
                    command.correction_request_id,
                    context.vault_id,
                    context.owner_subject_id,
                    command.command_id_hash,
                    command.payload_hash,
                    command.answer_id,
                    command.citation_id,
                    command.memory_id,
                    command.expected_memory_version_id,
                    source.source_id,
                    command.correction_text_hash,
                    command.correction_text_length,
                    command.reason_code_hash,
                    snapshot.candidate_id,
                ),
            )
        return OwnerTruthCorrectionRequestResult(
            outcome="created",
            correction_request_id=command.correction_request_id,
            candidate_id=command.candidate_id,
            answer_id=command.answer_id,
            citation_id=command.citation_id,
            memory_id=command.memory_id,
            expected_memory_version_id=command.expected_memory_version_id,
            correction_source_id=source.source_id,
            correction_text_hash=command.correction_text_hash,
            correction_text_length=command.correction_text_length,
            candidate_row_version=1,
        )

    def resolve(
        self,
        *,
        context: OwnerTruthCommandContext,
        correction_request_id: str,
        command: OwnerTruthCorrectionResolutionCommand,
    ) -> OwnerTruthCorrectionResolutionResult:
        """Resolve a pending correction without creating a second MemoryRecord."""

        _assert_owner_context(context)
        normalized_request_id = _uuid(correction_request_id, field="correction_request_id")
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-correction-resolution-command:"
                    f"{context.vault_id}:{command.command_id_hash}",
                ),
            )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-correction-resolution-request:"
                    f"{context.vault_id}:{normalized_request_id}",
                ),
            )
            existing = self._existing_resolution(
                cursor,
                context=context,
                correction_request_id=normalized_request_id,
                command=command,
            )
            if existing is not None:
                return _resolution_result_from_record(existing, outcome="deduplicated")
            request_record = self._locked_pending_request(
                cursor,
                context=context,
                correction_request_id=normalized_request_id,
            )
            if command.expected_memory_version_id != request_record["expectedMemoryVersionId"]:
                raise OwnerTruthCorrectionResolutionStale(
                    "correction resolver expectedMemoryVersionId does not match the request"
                )
            self._assert_current_target(
                cursor,
                context=context,
                memory_id=request_record["memoryId"],
                expected_memory_version_id=request_record["expectedMemoryVersionId"],
            )
            review_repository = PostgresOwnerTruthCandidateReviewRepository(self._connection)
            review = review_repository.decide(
                command=command.review_command(candidate_id=request_record["candidateId"]),
                context=context,
                allow_correction=True,
            )
            if command.action is CandidateReviewAction.CORRECT:
                if review.decision is not CandidateDecision.CORRECTED:
                    raise OwnerTruthCorrectionResolutionConflict(
                        "correction Candidate did not receive the corrected decision"
                    )
                activation = review_repository.activate_correction_memory_version(
                    receipt_id=review.receipt_id,
                    correction_request_id=normalized_request_id,
                    memory_id=request_record["memoryId"],
                    expected_memory_version_id=request_record["expectedMemoryVersionId"],
                    reason_code_hash=request_record["reasonCodeHash"],
                    context=context,
                )
                decision = CandidateDecision.CORRECTED
                status = "accepted"
                replacement_version_id = activation.replacement_memory_version_id
                answer_outdated_event_id = command.outdated_event_id(
                    vault_id=context.vault_id,
                    correction_request_id=normalized_request_id,
                )
            else:
                if review.decision is not CandidateDecision.REJECTED:
                    raise OwnerTruthCorrectionResolutionConflict(
                        "correction Candidate did not receive the rejected decision"
                    )
                activation = None
                decision = CandidateDecision.REJECTED
                status = "rejected"
                replacement_version_id = None
                answer_outdated_event_id = None
            resolution_id = command.resolution_id(
                vault_id=context.vault_id,
                correction_request_id=normalized_request_id,
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.correction_resolutions (
                    id, vault_id, correction_request_id, candidate_id,
                    decision_receipt_id, command_id_hash, command_payload_hash,
                    expected_memory_version_id, decision,
                    replacement_memory_version_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    resolution_id,
                    context.vault_id,
                    normalized_request_id,
                    request_record["candidateId"],
                    review.receipt_id,
                    command.command_id_hash,
                    command.payload_hash,
                    request_record["expectedMemoryVersionId"],
                    decision.value,
                    replacement_version_id,
                ),
            )
            if activation is not None:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.answer_outdated_events (
                        id, vault_id, correction_resolution_id, answer_id,
                        citation_id, memory_id, superseded_memory_version_id,
                        replacement_memory_version_id, authority_epoch
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        answer_outdated_event_id,
                        context.vault_id,
                        resolution_id,
                        request_record["answerId"],
                        request_record["citationId"],
                        request_record["memoryId"],
                        activation.superseded_memory_version_id,
                        activation.replacement_memory_version_id,
                        activation.authority_epoch,
                    ),
                )
            cursor.execute(
                """
                UPDATE owner_truth.correction_requests
                SET status = %s
                WHERE vault_id = %s AND id = %s AND status = 'pending'
                RETURNING id
                """,
                (status, context.vault_id, normalized_request_id),
            )
            if cursor.fetchone() is None:
                raise OwnerTruthCorrectionResolutionConflict(
                    "correction request lost its pending state before resolution"
                )
        return OwnerTruthCorrectionResolutionResult(
            outcome="created",
            correction_request_id=normalized_request_id,
            candidate_id=request_record["candidateId"],
            receipt_id=review.receipt_id,
            decision=decision,
            candidate_row_version=review.candidate_row_version,
            superseded_memory_version_id=(
                None if activation is None else activation.superseded_memory_version_id
            ),
            replacement_memory_version_id=(
                None if activation is None else activation.replacement_memory_version_id
            ),
            replacement_memory_version=(
                None if activation is None else activation.replacement_memory_version
            ),
            answer_outdated_event_id=answer_outdated_event_id,
            authority_epoch=None if activation is None else activation.authority_epoch,
            content_hash=None if activation is None else activation.content_hash,
        )

    def _existing_resolution(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        correction_request_id: str,
        command: OwnerTruthCorrectionResolutionCommand,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT resolution.correction_request_id, resolution.candidate_id,
                resolution.decision_receipt_id, resolution.command_payload_hash,
                resolution.decision, resolution.replacement_memory_version_id,
                version.version_number AS replacement_memory_version,
                version.content_hash AS replacement_content_hash,
                outdated.id AS answer_outdated_event_id,
                outdated.superseded_memory_version_id,
                outdated.authority_epoch,
                candidate.row_version AS candidate_row_version
            FROM owner_truth.correction_resolutions AS resolution
            JOIN owner_truth.memory_candidates AS candidate
              ON candidate.vault_id = resolution.vault_id
             AND candidate.id = resolution.candidate_id
            LEFT JOIN owner_truth.memory_versions AS version
              ON version.vault_id = resolution.vault_id
             AND version.id = resolution.replacement_memory_version_id
            LEFT JOIN owner_truth.answer_outdated_events AS outdated
              ON outdated.vault_id = resolution.vault_id
             AND outdated.correction_resolution_id = resolution.id
            WHERE resolution.vault_id = %s
              AND resolution.command_id_hash = %s
            FOR UPDATE OF resolution, candidate
            """,
            (context.vault_id, command.command_id_hash),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        if (
            str(row["correction_request_id"]) != correction_request_id
            or str(row["command_payload_hash"]) != command.payload_hash
        ):
            raise OwnerTruthCorrectionResolutionConflict(
                "commandId cannot be reused with a different correction resolution"
            )
        return {
            "answerOutdatedEventId": (
                None
                if row["answer_outdated_event_id"] is None
                else str(row["answer_outdated_event_id"])
            ),
            "candidateId": str(row["candidate_id"]),
            "candidateRowVersion": int(row["candidate_row_version"]),
            "correctionRequestId": str(row["correction_request_id"]),
            "decision": str(row["decision"]),
            "receiptId": str(row["decision_receipt_id"]),
            "replacementMemoryVersion": (
                None
                if row["replacement_memory_version"] is None
                else int(row["replacement_memory_version"])
            ),
            "replacementMemoryVersionId": (
                None
                if row["replacement_memory_version_id"] is None
                else str(row["replacement_memory_version_id"])
            ),
            "supersededMemoryVersionId": (
                None
                if row["superseded_memory_version_id"] is None
                else str(row["superseded_memory_version_id"])
            ),
            "authorityEpoch": (
                None if row["authority_epoch"] is None else int(row["authority_epoch"])
            ),
            "contentHash": (
                None
                if row["replacement_content_hash"] is None
                else str(row["replacement_content_hash"])
            ),
        }

    def _locked_pending_request(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        correction_request_id: str,
    ) -> dict[str, str]:
        cursor.execute(
            """
            SELECT id, owner_subject_id, answer_id, citation_id, memory_id,
                expected_memory_version_id, correction_source_id, reason_code_hash,
                candidate_id, status
            FROM owner_truth.correction_requests
            WHERE vault_id = %s AND id = %s
            FOR UPDATE
            """,
            (context.vault_id, correction_request_id),
        )
        row = cursor.fetchone()
        if (
            row is None
            or str(row["owner_subject_id"]) != context.owner_subject_id
        ):
            raise OwnerTruthCorrectionRequestAccessDenied(
                "correction request does not exist in this Owner Vault"
            )
        if str(row["status"]) != "pending":
            raise OwnerTruthCorrectionResolutionConflict(
                "correction request already has a terminal resolution"
            )
        return {
            "answerId": str(row["answer_id"]),
            "candidateId": str(row["candidate_id"]),
            "citationId": str(row["citation_id"]),
            "correctionSourceId": str(row["correction_source_id"]),
            "expectedMemoryVersionId": str(row["expected_memory_version_id"]),
            "memoryId": str(row["memory_id"]),
            "reasonCodeHash": str(row["reason_code_hash"]),
        }

    @staticmethod
    def _assert_current_target(
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        memory_id: str,
        expected_memory_version_id: str,
    ) -> None:
        """Confirm the correction still targets this Owner's current version.

        The later activation holds a stronger per-version advisory lock, which
        closes the race with another resolver.  This early assertion preserves
        the same stale behavior as the in-memory semantic double and avoids
        creating a terminal decision for an already superseded target.
        """

        cursor.execute(
            """
            SELECT memory.id
            FROM owner_truth.memories AS memory
            JOIN owner_truth.memory_versions AS version
              ON version.vault_id = memory.vault_id
             AND version.memory_id = memory.id
            JOIN owner_truth.vaults AS vault
              ON vault.vault_id = memory.vault_id
            WHERE memory.vault_id = %s
              AND memory.id = %s
              AND version.id = %s
              AND memory.owner_subject_id = %s
              AND memory.status = 'active'
              AND memory.authority_epoch = vault.authority_epoch
              AND vault.owner_subject_id = %s
              AND vault.status = 'active'
              AND version.is_current = TRUE
            FOR SHARE OF memory, version, vault
            """,
            (
                context.vault_id,
                memory_id,
                expected_memory_version_id,
                context.owner_subject_id,
                context.owner_subject_id,
            ),
        )
        if cursor.fetchone() is None:
            raise OwnerTruthCorrectionResolutionStale(
                "cited MemoryVersion is no longer current and cannot be resolved"
            )

    def _existing_request(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthCorrectionRequestCommand,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT id, command_payload_hash, answer_id, citation_id, memory_id,
                expected_memory_version_id, correction_source_id,
                correction_text_hash, correction_text_length, candidate_id
            FROM owner_truth.correction_requests
            WHERE vault_id = %s AND command_id_hash = %s
            FOR UPDATE
            """,
            (context.vault_id, command.command_id_hash),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        if str(row["command_payload_hash"]) != command.payload_hash:
            raise OwnerTruthCorrectionRequestConflict(
                "commandId cannot be reused with different correction evidence"
            )
        cursor.execute(
            """
            SELECT row_version
            FROM owner_truth.memory_candidates
            WHERE vault_id = %s AND id = %s
            """,
            (context.vault_id, row["candidate_id"]),
        )
        candidate = cursor.fetchone()
        if candidate is None:
            raise OwnerTruthCorrectionRequestConflict(
                "correction request references a missing Candidate"
            )
        return {
            "answerId": str(row["answer_id"]),
            "candidateId": str(row["candidate_id"]),
            "candidateRowVersion": int(candidate["row_version"]),
            "citationId": str(row["citation_id"]),
            "correctionRequestId": str(row["id"]),
            "correctionSourceId": str(row["correction_source_id"]),
            "correctionTextHash": str(row["correction_text_hash"]),
            "correctionTextLength": int(row["correction_text_length"]),
            "expectedMemoryVersionId": str(row["expected_memory_version_id"]),
            "memoryId": str(row["memory_id"]),
            "payloadHash": str(row["command_payload_hash"]),
        }

    def _resolve_target(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthCorrectionRequestCommand,
    ) -> _CorrectionTarget:
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, status
            FROM owner_truth.vaults
            WHERE vault_id = %s
            FOR SHARE
            """,
            (context.vault_id,),
        )
        vault = cursor.fetchone()
        if (
            vault is None
            or str(vault["owner_subject_id"]) != context.owner_subject_id
            or str(vault["status"]) != "active"
        ):
            raise OwnerTruthCorrectionRequestAccessDenied("Vault is not active for this Owner")
        authority_epoch = int(vault["authority_epoch"])

        cursor.execute(
            """
            SELECT answer.id AS answer_id, answer.owner_subject_id AS answer_owner_subject_id,
                answer.authority_epoch AS answer_authority_epoch,
                citation.id AS citation_id, citation.memory_id,
                citation.memory_version_id, citation.memory_version,
                citation.source_id AS cited_source_id,
                citation.source_version AS cited_source_version,
                citation.content_hash AS cited_content_hash
            FROM owner_truth.answers AS answer
            JOIN owner_truth.answer_citations AS citation
              ON citation.vault_id = answer.vault_id AND citation.answer_id = answer.id
            WHERE answer.vault_id = %s
              AND answer.id = %s
              AND citation.id = %s
            FOR SHARE
            """,
            (context.vault_id, command.answer_id, command.citation_id),
        )
        citation = cursor.fetchone()
        if (
            citation is None
            or str(citation["answer_owner_subject_id"]) != context.owner_subject_id
            or not _authority_epoch_matches(
                citation["answer_authority_epoch"], authority_epoch
            )
        ):
            raise OwnerTruthCorrectionRequestAccessDenied(
                "Answer citation does not exist in this active Owner Vault"
            )
        if (
            str(citation["memory_id"]) != command.memory_id
            or str(citation["memory_version_id"]) != command.expected_memory_version_id
        ):
            raise OwnerTruthCorrectionRequestStaleCitation(
                "Answer citation does not match the requested MemoryVersion"
            )

        cursor.execute(
            """
            SELECT memory.id AS memory_id, memory.owner_subject_id,
                memory.memory_kind,
                memory.perspective_type, memory.epistemic_status, memory.sensitivity,
                memory.status AS memory_status, memory.authority_epoch AS memory_authority_epoch,
                version.id AS memory_version_id, version.version_number,
                version.is_current, version.schema_version, version.content_hash,
                version.source_id AS version_source_id,
                version.source_version AS version_source_version,
                version.payload,
                source.owner_subject_id AS source_owner_subject_id,
                source.source_version AS source_row_version, source.state AS source_state,
                source.authority_epoch AS source_authority_epoch
            FROM owner_truth.memories AS memory
            JOIN owner_truth.memory_versions AS version
              ON version.vault_id = memory.vault_id AND version.memory_id = memory.id
            JOIN owner_truth.sources AS source
              ON source.vault_id = version.vault_id AND source.id = version.source_id
            WHERE memory.vault_id = %s
              AND memory.id = %s
              AND version.id = %s
            FOR SHARE
            """,
            (context.vault_id, command.memory_id, command.expected_memory_version_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise OwnerTruthCorrectionRequestStaleCitation(
                "cited MemoryVersion no longer exists"
            )
        if (
            str(row["owner_subject_id"]) != context.owner_subject_id
            or str(row["memory_status"]) != "active"
            or int(row["memory_authority_epoch"]) != authority_epoch
            or str(row["source_owner_subject_id"]) != context.owner_subject_id
            or str(row["source_state"]) != "active"
            or int(row["source_authority_epoch"]) != authority_epoch
            or int(row["version_source_version"]) != int(row["source_row_version"])
            or bool(row["is_current"]) is not True
            or int(row["version_number"]) != int(citation["memory_version"])
            or str(row["content_hash"]) != str(citation["cited_content_hash"])
            or str(row["version_source_id"]) != str(citation["cited_source_id"])
            or int(row["version_source_version"]) != int(citation["cited_source_version"])
        ):
            raise OwnerTruthCorrectionRequestStaleCitation(
                "cited MemoryVersion is no longer current and active"
            )
        payload = self._json_object(row["payload"], field="MemoryVersion payload")
        content_schema_version = str(payload.get("contentSchemaVersion") or "").strip()
        content = payload.get("content")
        if not content_schema_version or not isinstance(content, Mapping):
            raise OwnerTruthCorrectionRequestError("cited MemoryVersion payload is malformed")
        memory = OwnerTruthMemoryProjectionInput(
            memory_id=str(row["memory_id"]),
            memory_version_id=str(row["memory_version_id"]),
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=authority_epoch,
            version_number=int(row["version_number"]),
            source_id=str(row["version_source_id"]),
            source_version=int(row["version_source_version"]),
            memory_kind=str(row["memory_kind"]),
            perspective_type=str(row["perspective_type"]),
            epistemic_status=str(row["epistemic_status"]),
            sensitivity=str(row["sensitivity"]),
            content_schema_version=content_schema_version,
            content=content,
            content_hash=str(row["content_hash"]),
            evidence_refs=tuple(payload.get("evidenceRefs") or ()),
        )
        return _CorrectionTarget(
            answer_id=str(citation["answer_id"]),
            citation_id=str(citation["citation_id"]),
            memory=memory,
        )

    @staticmethod
    def _assert_correction_source(
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        source: OwnerTruthSourceCommandResult,
    ) -> None:
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, source_version, state
            FROM owner_truth.sources
            WHERE vault_id = %s AND id = %s
            FOR SHARE
            """,
            (context.vault_id, source.source_id),
        )
        row = cursor.fetchone()
        if (
            row is None
            or str(row["owner_subject_id"]) != context.owner_subject_id
            or str(row["state"]) != "active"
            or int(row["authority_epoch"]) != source.authority_epoch
            or int(row["source_version"]) != source.source_version
        ):
            raise OwnerTruthCorrectionRequestConflict(
                "correction Source changed before request persistence"
            )

    @staticmethod
    def _json_object(value: Any, *, field: str) -> dict[str, Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise OwnerTruthCorrectionRequestError(f"{field} is not valid JSON") from exc
        if not isinstance(value, Mapping):
            raise OwnerTruthCorrectionRequestError(f"{field} must be an object")
        return dict(value)

    @staticmethod
    def _adapt_params(values: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            return tuple(
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, Mapping)
                else value
                for value in values
            )
        return tuple(Jsonb(dict(value)) if isinstance(value, Mapping) else value for value in values)

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthCorrectionRequestService:
    """Default-off command facade for an Owner's answer correction request."""

    def __init__(self, store: OwnerTruthCorrectionRequestStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def request(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthCorrectionRequestCommand,
    ) -> OwnerTruthCorrectionRequestResult:
        _assert_owner_context(context)
        if not self._enabled:
            raise OwnerTruthCorrectionRequestUnavailable("correction request shadow is disabled")
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-correction-request-{command.command_id_hash}",
            command_id=command.command_id_hash,
        ):
            try:
                source = OwnerTruthSourceCommandService(self._store).create_text_source(
                    command=CreateTextSourceCommand(
                        command_id=f"correction-source:{command.command_id}",
                        source_id=command.source_id,
                        expected_version=0,
                        text=command.correction_text,
                        metadata=_correction_source_metadata(command=command),
                    ),
                    context=context,
                )
            except OwnerTruthSourceCommandConflict as error:
                if "vault owner" in str(error):
                    raise OwnerTruthCorrectionRequestAccessDenied(
                        "Vault is not active for this Owner"
                    ) from error
                raise OwnerTruthCorrectionRequestConflict(
                    "commandId cannot be reused with different correction evidence"
                ) from error
            except OwnerTruthSourceVersionConflict as error:
                raise OwnerTruthCorrectionRequestConflict(
                    "correction Source version conflicts with the request command"
                ) from error
            return self._store.owner_truth_correction_request_repository().record(
                context=context,
                command=command,
                source=source,
            )

    def resolve(
        self,
        *,
        context: OwnerTruthCommandContext,
        correction_request_id: str,
        command: OwnerTruthCorrectionResolutionCommand,
    ) -> OwnerTruthCorrectionResolutionResult:
        """Resolve one pending correction and enqueue a derived rebuild intent.

        This remains QA-only and is intentionally separate from the ordinary
        Candidate review route.  A corrected request creates v(N+1) of the
        same MemoryRecord; a rejected request keeps the authoritative version
        untouched.  The effect write shares the request UoW so an effect
        persistence failure rolls the resolution back rather than leaving a
        stale projection silently visible.
        """

        _assert_owner_context(context)
        if not self._enabled:
            raise OwnerTruthCorrectionRequestUnavailable("correction request shadow is disabled")
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-correction-resolution-{command.command_id_hash}",
            command_id=command.command_id_hash,
        ):
            try:
                result = self._store.owner_truth_correction_request_repository().resolve(
                    context=context,
                    correction_request_id=correction_request_id,
                    command=command,
                )
                if result.replacement_memory_version_id is None:
                    return result
                effect_factory = getattr(self._store, "effect_kernel_repository", None)
                if not callable(effect_factory):
                    return result
                if (
                    result.replacement_memory_version is None
                    or result.authority_epoch is None
                    or result.content_hash is None
                ):
                    raise OwnerTruthCorrectionResolutionConflict(
                        "corrected resolution is missing replacement version evidence"
                    )
                effect = effect_factory().accept(
                    build_memory_projection_rebuild_effect_intent_for_version(
                        context=context,
                        memory_version_id=result.replacement_memory_version_id,
                        memory_version=result.replacement_memory_version,
                        authority_epoch=result.authority_epoch,
                        content_hash=result.content_hash,
                    )
                )
                return replace(result, projection_effect=effect)
            except OwnerTruthMemoryCorrectionError as error:
                raise OwnerTruthCorrectionResolutionStale(str(error)) from error
            except OwnerTruthCandidateReviewAccessDenied as error:
                raise OwnerTruthCorrectionRequestAccessDenied(str(error)) from error
            except OwnerTruthCandidateReviewConflict as error:
                raise OwnerTruthCorrectionResolutionConflict(str(error)) from error
            except OwnerTruthCandidateReviewError as error:
                raise OwnerTruthCorrectionResolutionConflict(str(error)) from error

    def _request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


def _result_from_record(
    record: Mapping[str, Any],
    *,
    outcome: str,
) -> OwnerTruthCorrectionRequestResult:
    return OwnerTruthCorrectionRequestResult(
        outcome=outcome,
        correction_request_id=_uuid(record.get("correctionRequestId"), field="correctionRequestId"),
        candidate_id=_uuid(record.get("candidateId"), field="candidateId"),
        answer_id=_uuid(record.get("answerId"), field="answerId"),
        citation_id=_uuid(record.get("citationId"), field="citationId"),
        memory_id=_uuid(record.get("memoryId"), field="memoryId"),
        expected_memory_version_id=_uuid(
            record.get("expectedMemoryVersionId"), field="expectedMemoryVersionId"
        ),
        correction_source_id=_uuid(record.get("correctionSourceId"), field="correctionSourceId"),
        correction_text_hash=_hash(record.get("correctionTextHash"), field="correctionTextHash"),
        correction_text_length=int(record.get("correctionTextLength") or 0),
        candidate_row_version=int(record.get("candidateRowVersion") or 0),
    )


def _resolution_result_from_record(
    record: Mapping[str, Any],
    *,
    outcome: str,
) -> OwnerTruthCorrectionResolutionResult:
    try:
        decision = CandidateDecision(str(record.get("decision") or ""))
    except ValueError as exc:
        raise OwnerTruthCorrectionResolutionConflict(
            "persisted correction resolution has an unsupported decision"
        ) from exc
    if decision not in {CandidateDecision.CORRECTED, CandidateDecision.REJECTED}:
        raise OwnerTruthCorrectionResolutionConflict(
            "persisted correction resolution is not terminal"
        )
    replacement_memory_version_id = record.get("replacementMemoryVersionId")
    superseded_memory_version_id = record.get("supersededMemoryVersionId")
    answer_outdated_event_id = record.get("answerOutdatedEventId")
    content_hash = record.get("contentHash")
    authority_epoch = record.get("authorityEpoch")
    if decision is CandidateDecision.CORRECTED:
        if (
            replacement_memory_version_id is None
            or superseded_memory_version_id is None
            or answer_outdated_event_id is None
            or content_hash is None
            or authority_epoch is None
        ):
            raise OwnerTruthCorrectionResolutionConflict(
                "corrected resolution is missing version or Answer-outdated evidence"
            )
    return OwnerTruthCorrectionResolutionResult(
        outcome=outcome,
        correction_request_id=_uuid(
            record.get("correctionRequestId"), field="correctionRequestId"
        ),
        candidate_id=_uuid(record.get("candidateId"), field="candidateId"),
        receipt_id=_uuid(record.get("receiptId"), field="receiptId"),
        decision=decision,
        candidate_row_version=int(record.get("candidateRowVersion") or 0),
        superseded_memory_version_id=(
            None
            if superseded_memory_version_id is None
            else _uuid(superseded_memory_version_id, field="supersededMemoryVersionId")
        ),
        replacement_memory_version_id=(
            None
            if replacement_memory_version_id is None
            else _uuid(replacement_memory_version_id, field="replacementMemoryVersionId")
        ),
        replacement_memory_version=(
            None
            if record.get("replacementMemoryVersion") is None
            else int(record.get("replacementMemoryVersion"))
        ),
        answer_outdated_event_id=(
            None
            if answer_outdated_event_id is None
            else _uuid(answer_outdated_event_id, field="answerOutdatedEventId")
        ),
        authority_epoch=None if authority_epoch is None else int(authority_epoch),
        content_hash=None if content_hash is None else _hash(content_hash, field="contentHash"),
    )


def correction_request_summary(result: OwnerTruthCorrectionRequestResult) -> dict[str, Any]:
    """Return a value-free QA receipt; raw correction text remains in Source."""

    if not isinstance(result, OwnerTruthCorrectionRequestResult):
        raise OwnerTruthCorrectionRequestError("correction request result is required")
    return {
        "schemaVersion": OWNER_TRUTH_CORRECTION_REQUEST_SCHEMA_VERSION,
        "outcome": result.outcome,
        "correctionRequestId": result.correction_request_id,
        "candidateId": result.candidate_id,
        "candidateVersion": result.candidate_row_version,
        "answerId": result.answer_id,
        "citationId": result.citation_id,
        "memoryId": result.memory_id,
        "expectedMemoryVersionId": result.expected_memory_version_id,
        "correctionSourceId": result.correction_source_id,
        "correctionTextHash": result.correction_text_hash,
        "correctionTextLength": result.correction_text_length,
        "status": "pendingReview",
    }


def correction_resolution_summary(result: OwnerTruthCorrectionResolutionResult) -> dict[str, Any]:
    """Return a value-free record of one correction terminal decision."""

    if not isinstance(result, OwnerTruthCorrectionResolutionResult):
        raise OwnerTruthCorrectionRequestError("correction resolution result is required")
    return {
        "schemaVersion": OWNER_TRUTH_CORRECTION_RESOLUTION_SCHEMA_VERSION,
        "outcome": result.outcome,
        "correctionRequestId": result.correction_request_id,
        "candidateId": result.candidate_id,
        "candidateVersion": result.candidate_row_version,
        "receiptId": result.receipt_id,
        "decision": result.decision.value,
        "supersededMemoryVersionId": result.superseded_memory_version_id,
        "replacementMemoryVersionId": result.replacement_memory_version_id,
        "replacementMemoryVersion": result.replacement_memory_version,
        "answerOutdatedEventId": result.answer_outdated_event_id,
        "authorityEpoch": result.authority_epoch,
        "contentHash": result.content_hash,
        "projectionEffect": (
            None
            if result.projection_effect is None
            else result.projection_effect.public_contract()
        ),
    }


__all__ = [
    "InMemoryOwnerTruthCorrectionRequestRepository",
    "OWNER_TRUTH_CORRECTION_REQUEST_SCHEMA_VERSION",
    "OWNER_TRUTH_CORRECTION_RESOLUTION_SCHEMA_VERSION",
    "OwnerTruthCorrectionRequestAccessDenied",
    "OwnerTruthCorrectionRequestCommand",
    "OwnerTruthCorrectionRequestConflict",
    "OwnerTruthCorrectionRequestError",
    "OwnerTruthCorrectionRequestResult",
    "OwnerTruthCorrectionRequestService",
    "OwnerTruthCorrectionRequestStaleCitation",
    "OwnerTruthCorrectionRequestUnavailable",
    "OwnerTruthCorrectionResolutionCommand",
    "OwnerTruthCorrectionResolutionConflict",
    "OwnerTruthCorrectionResolutionResult",
    "OwnerTruthCorrectionResolutionStale",
    "PostgresOwnerTruthCorrectionRequestRepository",
    "correction_resolution_summary",
    "correction_request_summary",
]
