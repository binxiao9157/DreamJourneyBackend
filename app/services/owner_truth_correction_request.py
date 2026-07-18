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
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.candidate_decisions import (
    OwnerTruthCandidateReviewAccessDenied,
    OwnerTruthCandidateReviewConflict,
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
from app.services.owner_truth_source import OwnerTruthSourceCommandService


OWNER_TRUTH_CORRECTION_REQUEST_SCHEMA_VERSION = "owner-truth-correction-request-v1"
OWNER_TRUTH_CORRECTION_CANDIDATE_SCHEMA_VERSION = "owner-truth-correction-candidate-v1"
_SOURCE_NAMESPACE = UUID("f0f719db-7d2d-44db-900a-b4184d0eef2d")
_REQUEST_NAMESPACE = UUID("419cda41-b9d7-405d-80da-92f24fa2ae83")
_CANDIDATE_NAMESPACE = UUID("8d690241-66c8-4ac1-8af3-d473a72cf1ed")
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
            }
            self._records[key] = record
            return _result_from_record(record, outcome="created")

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
                memory.source_id AS memory_source_id,
                memory.source_version AS memory_source_version, memory.memory_kind,
                memory.perspective_type, memory.epistemic_status, memory.sensitivity,
                memory.status AS memory_status, memory.authority_epoch AS memory_authority_epoch,
                version.id AS memory_version_id, version.version_number,
                version.is_current, version.schema_version, version.content_hash,
                version.payload,
                source.owner_subject_id AS source_owner_subject_id,
                source.source_version AS source_row_version, source.state AS source_state,
                source.authority_epoch AS source_authority_epoch
            FROM owner_truth.memories AS memory
            JOIN owner_truth.memory_versions AS version
              ON version.vault_id = memory.vault_id AND version.memory_id = memory.id
            JOIN owner_truth.sources AS source
              ON source.vault_id = memory.vault_id AND source.id = memory.source_id
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
            or int(row["memory_source_version"]) != int(row["source_row_version"])
            or bool(row["is_current"]) is not True
            or int(row["version_number"]) != int(citation["memory_version"])
            or str(row["content_hash"]) != str(citation["cited_content_hash"])
            or str(row["memory_source_id"]) != str(citation["cited_source_id"])
            or int(row["memory_source_version"]) != int(citation["cited_source_version"])
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
            source_id=str(row["memory_source_id"]),
            source_version=int(row["memory_source_version"]),
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


__all__ = [
    "InMemoryOwnerTruthCorrectionRequestRepository",
    "OWNER_TRUTH_CORRECTION_REQUEST_SCHEMA_VERSION",
    "OwnerTruthCorrectionRequestAccessDenied",
    "OwnerTruthCorrectionRequestCommand",
    "OwnerTruthCorrectionRequestConflict",
    "OwnerTruthCorrectionRequestError",
    "OwnerTruthCorrectionRequestResult",
    "OwnerTruthCorrectionRequestService",
    "OwnerTruthCorrectionRequestStaleCitation",
    "OwnerTruthCorrectionRequestUnavailable",
    "PostgresOwnerTruthCorrectionRequestRepository",
    "correction_request_summary",
]
