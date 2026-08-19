"""Owner-only formal-memory library and explicit correction command.

The product surface reads only current MemoryVersions. Editing never mutates a
MemoryVersion in place: a confirmed command creates an immutable correction
Source, Candidate, DecisionReceipt and successor MemoryVersion in one request
unit of work. Historical rows remain an internal audit ledger while the Owner
surface exposes the current version plus at most three prior snapshots.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import base64
import json
import re
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
from app.domain.owner_truth.memory_correction import (
    OwnerTruthMemoryCorrectionActivationResult,
    OwnerTruthMemoryCorrectionError,
)
from app.domain.owner_truth.ontology import (
    OWNER_TRUTH_FACET_NAMES,
    validate_memory_payload,
)
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
    OwnerTruthSourceCommandResult,
)
from app.services.owner_truth_candidate_review import (
    InMemoryOwnerTruthCandidateReviewRepository,
    PostgresOwnerTruthCandidateReviewRepository,
)
from app.services.owner_truth_memory_projection_effects import (
    build_memory_projection_rebuild_effect_intent_for_version,
)
from app.services.owner_truth_source import OwnerTruthSourceCommandService


FORMAL_MEMORY_LIST_SCHEMA_VERSION = "owner-truth-formal-memory-list-v1"
FORMAL_MEMORY_DETAIL_SCHEMA_VERSION = "owner-truth-formal-memory-detail-v1"
FORMAL_MEMORY_CORRECTION_SCHEMA_VERSION = "owner-truth-formal-memory-correction-v1"
FORMAL_MEMORY_HISTORY_LIMIT = 3
_MAX_PAGE_LIMIT = 100
_MAX_QUERY_LENGTH = 256
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SOURCE_NAMESPACE = UUID("35b60f8e-edc4-47a4-9527-6ae98fe42ded")
_CANDIDATE_NAMESPACE = UUID("29a3c84f-23da-479d-a33b-38b5b29241b7")
_CORRECTION_LINK_NAMESPACE = UUID("f8a53262-1d68-4dbb-aad0-ddf2ad08a210")


class OwnerTruthFormalMemoryError(OwnerTruthCandidateReviewError):
    """A formal-memory request is malformed or cannot be completed."""


class OwnerTruthFormalMemoryConflict(OwnerTruthFormalMemoryError):
    """A command is stale or conflicts with an immutable prior command."""


class OwnerTruthFormalMemoryAccessDenied(OwnerTruthFormalMemoryError):
    """The requested memory does not belong to this active Owner Vault."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthFormalMemoryError("formal memory content must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _postgres_ilike_literal(value: str) -> str:
    """Treat Owner search text as text, not as SQL LIKE wildcard syntax."""

    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _nonblank(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthFormalMemoryError(f"{field} is required")
    return normalized


def _uuid(value: Any, *, field: str) -> str:
    try:
        return str(UUID(_nonblank(value, field=field)))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthFormalMemoryError(f"{field} must be a UUID") from exc


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthFormalMemoryError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthFormalMemoryAccessDenied("only the Vault Owner may read or edit formal memory")


@dataclass(frozen=True)
class OwnerTruthFormalMemoryFacetFilter:
    name: str
    value: str

    def __post_init__(self) -> None:
        name = _nonblank(self.name, field="facet.name")
        value = _nonblank(self.value, field="facet.value")
        if name not in OWNER_TRUTH_FACET_NAMES:
            raise OwnerTruthFormalMemoryError("facet name is unsupported")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class OwnerTruthFormalMemoryCursor:
    created_at: str
    memory_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _nonblank(self.created_at, field="cursor.createdAt"))
        object.__setattr__(self, "memory_id", _uuid(self.memory_id, field="cursor.memoryId"))

    def encode(self) -> str:
        payload = _canonical_json({"createdAt": self.created_at, "memoryId": self.memory_id})
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, value: str | None) -> "OwnerTruthFormalMemoryCursor | None":
        normalized = str(value or "").strip()
        if not normalized:
            return None
        try:
            padded = normalized + "=" * (-len(normalized) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            return cls(created_at=payload["createdAt"], memory_id=payload["memoryId"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OwnerTruthFormalMemoryError("cursor is invalid") from exc


@dataclass(frozen=True)
class OwnerTruthFormalMemoryQuery:
    kind: str | None = None
    query: str | None = None
    facets: tuple[OwnerTruthFormalMemoryFacetFilter, ...] = ()
    cursor: OwnerTruthFormalMemoryCursor | None = None
    limit: int = 20

    def __post_init__(self) -> None:
        normalized_kind = str(self.kind or "").strip() or None
        if normalized_kind is not None:
            try:
                normalized_kind = MemoryKind(normalized_kind).value
            except ValueError as exc:
                raise OwnerTruthFormalMemoryError("kind is unsupported") from exc
        normalized_query = str(self.query or "").strip() or None
        if normalized_query is not None and len(normalized_query) > _MAX_QUERY_LENGTH:
            raise OwnerTruthFormalMemoryError("query exceeds maximum length")
        if type(self.limit) is not int or not 1 <= self.limit <= _MAX_PAGE_LIMIT:
            raise OwnerTruthFormalMemoryError("limit must be between 1 and 100")
        object.__setattr__(self, "kind", normalized_kind)
        object.__setattr__(self, "query", normalized_query)
        object.__setattr__(self, "facets", tuple(self.facets))


@dataclass(frozen=True)
class OwnerTruthFormalMemoryVersion:
    version_id: str
    version_number: int
    status: str
    decision: str
    content_schema_version: str
    content_hash: str
    content: Mapping[str, Any]
    source_count: int
    created_at: str

    def public_contract(self) -> dict[str, Any]:
        return {
            "versionId": self.version_id,
            "versionNumber": self.version_number,
            "status": self.status,
            "decision": self.decision,
            "contentSchemaVersion": self.content_schema_version,
            "contentHash": self.content_hash,
            "content": deepcopy(dict(self.content)),
            "sourceCount": self.source_count,
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class OwnerTruthFormalMemory:
    memory_id: str
    memory_kind: str
    perspective_type: str
    epistemic_status: str
    sensitivity: str
    current_version: OwnerTruthFormalMemoryVersion
    versions: tuple[OwnerTruthFormalMemoryVersion, ...] = ()
    history_truncated: bool = False

    def list_contract(self) -> dict[str, Any]:
        return {
            "memoryId": self.memory_id,
            "memoryKind": self.memory_kind,
            "perspectiveType": self.perspective_type,
            "epistemicStatus": self.epistemic_status,
            "sensitivity": self.sensitivity,
            "currentVersion": self.current_version.public_contract(),
        }

    def detail_contract(self) -> dict[str, Any]:
        return {
            **self.list_contract(),
            "historyLimit": FORMAL_MEMORY_HISTORY_LIMIT,
            "historyTruncated": self.history_truncated,
            "versions": [item.public_contract() for item in self.versions],
        }


@dataclass(frozen=True)
class OwnerTruthFormalMemoryPage:
    items: tuple[OwnerTruthFormalMemory, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class OwnerTruthFormalMemoryCorrectionCommand:
    command_id: str
    expected_version: int
    expected_content_hash: str
    expected_content_schema_version: str
    content_schema_version: str
    corrected_content: Mapping[str, Any]
    second_confirmation: bool
    reason_code: str = "ownerConfirmedFormalMemoryCorrection"

    def __post_init__(self) -> None:
        command_id = _nonblank(self.command_id, field="commandId")
        if not _IDENTIFIER_PATTERN.fullmatch(command_id):
            raise OwnerTruthFormalMemoryError("commandId must be an opaque identifier")
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise OwnerTruthFormalMemoryError("expectedVersion must be positive")
        expected_hash = str(self.expected_content_hash or "").strip().lower()
        if not _HASH_PATTERN.fullmatch(expected_hash):
            raise OwnerTruthFormalMemoryError("expectedContentHash must be a SHA-256 digest")
        if self.second_confirmation is not True:
            raise OwnerTruthFormalMemoryError("secondConfirmation must be true")
        corrected = json.loads(_canonical_json(dict(self.corrected_content)))
        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "expected_content_hash", expected_hash)
        object.__setattr__(self, "expected_content_schema_version", _nonblank(
            self.expected_content_schema_version, field="expectedContentSchemaVersion"
        ))
        object.__setattr__(self, "content_schema_version", _nonblank(
            self.content_schema_version, field="contentSchemaVersion"
        ))
        object.__setattr__(self, "corrected_content", corrected)
        object.__setattr__(self, "reason_code", _nonblank(self.reason_code, field="reasonCode"))

    @property
    def payload_hash(self) -> str:
        return _digest({
            "commandId": self.command_id,
            "expectedVersion": self.expected_version,
            "expectedContentHash": self.expected_content_hash,
            "expectedContentSchemaVersion": self.expected_content_schema_version,
            "contentSchemaVersion": self.content_schema_version,
            "correctedContent": self.corrected_content,
            "secondConfirmation": self.second_confirmation,
            "reasonCode": self.reason_code,
        })


@dataclass(frozen=True)
class OwnerTruthFormalMemoryCorrectionTarget:
    memory_id: str
    memory_kind: str
    perspective_type: str
    epistemic_status: str
    sensitivity: str
    authority_epoch: int
    version_id: str
    version_number: int
    content_schema_version: str
    content_hash: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class OwnerTruthFormalMemoryCorrectionResult:
    outcome: str
    memory_id: str
    superseded_version_id: str
    replacement_version_id: str
    replacement_version: int
    content_hash: str
    receipt_id: str
    correction_link_id: str
    projection_effect: Any | None = None

    def public_contract(self) -> dict[str, Any]:
        return {
            "schemaVersion": FORMAL_MEMORY_CORRECTION_SCHEMA_VERSION,
            "status": self.outcome,
            "memoryId": self.memory_id,
            "supersededVersionId": self.superseded_version_id,
            "replacementVersionId": self.replacement_version_id,
            "replacementVersion": self.replacement_version,
            "contentHash": self.content_hash,
            "receiptId": self.receipt_id,
            "correctionLinkId": self.correction_link_id,
        }


class OwnerTruthFormalMemoryRepository(Protocol):
    def list_current(
        self, *, context: OwnerTruthCommandContext, query: OwnerTruthFormalMemoryQuery
    ) -> tuple[tuple[OwnerTruthFormalMemory, ...], bool]: ...

    def read_detail(
        self, *, context: OwnerTruthCommandContext, memory_id: str
    ) -> OwnerTruthFormalMemory: ...

    def prepare_correction(
        self,
        *,
        context: OwnerTruthCommandContext,
        memory_id: str,
        command: OwnerTruthFormalMemoryCorrectionCommand,
        review_command: OwnerTruthCandidateReviewCommand,
    ) -> OwnerTruthFormalMemoryCorrectionTarget | OwnerTruthFormalMemoryCorrectionResult: ...

    def ensure_correction_candidate(
        self,
        *,
        context: OwnerTruthCommandContext,
        target: OwnerTruthFormalMemoryCorrectionTarget,
        command: OwnerTruthFormalMemoryCorrectionCommand,
        candidate: OwnerTruthCandidateSnapshot,
    ) -> None: ...


def _version_from_activation(
    activation: Mapping[str, Any], candidate: Mapping[str, Any]
) -> OwnerTruthFormalMemoryVersion:
    payload = activation.get("payload")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("content"), Mapping):
        raise OwnerTruthFormalMemoryConflict("MemoryVersion payload is unavailable")
    refs = payload.get("evidenceRefs")
    source_count = len({
        (str(item.get("sourceId") or ""), int(item.get("sourceVersion") or 0))
        for item in refs if isinstance(item, Mapping)
    }) if isinstance(refs, list) else 0
    if source_count < 1:
        raise OwnerTruthFormalMemoryConflict("MemoryVersion provenance is unavailable")
    return OwnerTruthFormalMemoryVersion(
        version_id=_uuid(activation.get("memoryVersionId"), field="memoryVersionId"),
        version_number=int(activation.get("memoryVersion") or 0),
        status="current" if activation.get("isCurrent") is True else "superseded",
        decision=str(candidate.get("decision") or ""),
        content_schema_version=_nonblank(payload.get("contentSchemaVersion"), field="contentSchemaVersion"),
        content_hash=_nonblank(activation.get("contentHash"), field="contentHash"),
        content=deepcopy(dict(payload["content"])),
        source_count=source_count,
        created_at=_nonblank(activation.get("createdAt"), field="createdAt"),
    )


class InMemoryOwnerTruthFormalMemoryRepository:
    def __init__(self, review_repository: InMemoryOwnerTruthCandidateReviewRepository) -> None:
        self._review = review_repository

    def _memories(self, *, context: OwnerTruthCommandContext) -> tuple[OwnerTruthFormalMemory, ...]:
        _assert_owner_context(context)
        snapshot = self._review.snapshot()
        candidates = snapshot["candidates"]
        grouped: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = {}
        for activation in snapshot["memoryActivations"].values():
            candidate = candidates.get(str(activation.get("candidateId") or ""))
            if not isinstance(candidate, Mapping):
                continue
            if candidate.get("vaultId") != context.vault_id or candidate.get("ownerSubjectId") != context.owner_subject_id:
                continue
            grouped.setdefault(str(activation.get("memoryId") or ""), []).append((activation, candidate))
        values: list[OwnerTruthFormalMemory] = []
        for memory_id, rows in grouped.items():
            versions = tuple(sorted(
                (_version_from_activation(activation, candidate) for activation, candidate in rows),
                key=lambda item: item.version_number,
                reverse=True,
            ))
            current = next((item for item in versions if item.status == "current"), None)
            initial_candidate = min(rows, key=lambda item: int(item[0].get("memoryVersion") or 0))[1]
            if current is None:
                raise OwnerTruthFormalMemoryConflict("formal memory has no current version")
            values.append(OwnerTruthFormalMemory(
                memory_id=_uuid(memory_id, field="memoryId"),
                memory_kind=str(initial_candidate["memoryKind"]),
                perspective_type=str(initial_candidate["perspectiveType"]),
                epistemic_status=str(initial_candidate["epistemicStatus"]),
                sensitivity=str(initial_candidate["sensitivity"]),
                current_version=current,
                versions=versions[: FORMAL_MEMORY_HISTORY_LIMIT + 1],
                history_truncated=len(versions) > FORMAL_MEMORY_HISTORY_LIMIT + 1,
            ))
        return tuple(values)

    def list_current(
        self, *, context: OwnerTruthCommandContext, query: OwnerTruthFormalMemoryQuery
    ) -> tuple[tuple[OwnerTruthFormalMemory, ...], bool]:
        values = list(self._memories(context=context))
        if query.kind:
            values = [item for item in values if item.memory_kind == query.kind]
        if query.query:
            needle = query.query.casefold()
            values = [item for item in values if needle in _canonical_json(item.current_version.content).casefold()]
        for facet in query.facets:
            values = [item for item in values if any(
                isinstance(entry, Mapping) and str(entry.get("value") or "") == facet.value
                for entry in ((item.current_version.content.get("facets") or {}).get(facet.name) or [])
            )]
        values.sort(key=lambda item: (item.current_version.created_at, item.memory_id), reverse=True)
        if query.cursor:
            marker = (query.cursor.created_at, query.cursor.memory_id)
            values = [item for item in values if (item.current_version.created_at, item.memory_id) < marker]
        has_more = len(values) > query.limit
        return tuple(values[: query.limit]), has_more

    def read_detail(self, *, context: OwnerTruthCommandContext, memory_id: str) -> OwnerTruthFormalMemory:
        normalized = _uuid(memory_id, field="memoryId")
        item = next((value for value in self._memories(context=context) if value.memory_id == normalized), None)
        if item is None:
            raise OwnerTruthFormalMemoryAccessDenied("Memory does not exist in this Owner Vault")
        return item

    def prepare_correction(
        self,
        *,
        context: OwnerTruthCommandContext,
        memory_id: str,
        command: OwnerTruthFormalMemoryCorrectionCommand,
        review_command: OwnerTruthCandidateReviewCommand,
    ) -> OwnerTruthFormalMemoryCorrectionTarget | OwnerTruthFormalMemoryCorrectionResult:
        snapshot = self._review.snapshot()
        receipt = snapshot["receipts"].get(review_command.command_id_hash)
        if receipt is not None:
            if receipt.get("candidateId") != review_command.candidate_id or receipt.get("payloadHash") != review_command.payload_hash:
                raise OwnerTruthFormalMemoryConflict("commandId was reused with different correction content")
            activation = snapshot["memoryActivations"].get(str(receipt.get("id") or ""))
            if not isinstance(activation, Mapping) or str(activation.get("memoryId") or "") != memory_id:
                raise OwnerTruthFormalMemoryConflict("correction receipt has no matching MemoryVersion")
            return OwnerTruthFormalMemoryCorrectionResult(
                outcome="deduplicated",
                memory_id=memory_id,
                superseded_version_id=_uuid((activation.get("payload") or {}).get("supersedesVersionId"), field="supersededVersionId"),
                replacement_version_id=_uuid(activation.get("memoryVersionId"), field="replacementVersionId"),
                replacement_version=int(activation.get("memoryVersion") or 0),
                content_hash=str(activation.get("contentHash") or ""),
                receipt_id=str(receipt["id"]),
                correction_link_id=str(uuid5(_CORRECTION_LINK_NAMESPACE, f"{context.vault_id}:{receipt['id']}")),
            )
        detail = self.read_detail(context=context, memory_id=memory_id)
        current = detail.current_version
        return OwnerTruthFormalMemoryCorrectionTarget(
            memory_id=detail.memory_id,
            memory_kind=detail.memory_kind,
            perspective_type=detail.perspective_type,
            epistemic_status=detail.epistemic_status,
            sensitivity=detail.sensitivity,
            authority_epoch=0,
            version_id=current.version_id,
            version_number=current.version_number,
            content_schema_version=current.content_schema_version,
            content_hash=current.content_hash,
            payload={"content": deepcopy(dict(current.content))},
        )

    def ensure_correction_candidate(
        self,
        *,
        context: OwnerTruthCommandContext,
        target: OwnerTruthFormalMemoryCorrectionTarget,
        command: OwnerTruthFormalMemoryCorrectionCommand,
        candidate: OwnerTruthCandidateSnapshot,
    ) -> None:
        existing = self._review.candidate_snapshot(candidate.candidate_id)
        if existing is not None:
            if existing != candidate:
                raise OwnerTruthFormalMemoryConflict("correction Candidate id was reused")
            return
        self._review.seed(candidate, created_at=datetime.now(timezone.utc).isoformat())


class PostgresOwnerTruthFormalMemoryRepository:
    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def list_current(
        self, *, context: OwnerTruthCommandContext, query: OwnerTruthFormalMemoryQuery
    ) -> tuple[tuple[OwnerTruthFormalMemory, ...], bool]:
        _assert_owner_context(context)
        conditions = [
            "memory.vault_id = %s",
            "memory.owner_subject_id = %s",
            "memory.status = 'active'",
            "version.is_current = TRUE",
        ]
        params: list[Any] = [context.vault_id, context.owner_subject_id]
        if query.kind:
            conditions.append("memory.memory_kind = %s")
            params.append(query.kind)
        if query.query:
            conditions.append(
                "CAST(version.payload -> 'content' AS TEXT) ILIKE %s ESCAPE '\\\\'"
            )
            params.append(f"%{_postgres_ilike_literal(query.query)}%")
        for facet in query.facets:
            conditions.append("(version.payload -> 'content' -> 'facets' -> %s) @> %s::jsonb")
            params.extend((facet.name, json.dumps([{"value": facet.value}], ensure_ascii=False)))
        if query.cursor:
            conditions.append("(version.created_at, memory.id) < (%s::timestamptz, %s::uuid)")
            params.extend((query.cursor.created_at, query.cursor.memory_id))
        params.append(query.limit + 1)
        sql = f"""
            SELECT memory.id AS memory_id, memory.memory_kind,
                memory.perspective_type, memory.epistemic_status,
                memory.sensitivity, version.id AS version_id,
                version.version_number, version.schema_version,
                version.content_hash, version.payload, version.created_at,
                receipt.decision
            FROM owner_truth.memories AS memory
            JOIN owner_truth.memory_versions AS version
              ON version.vault_id = memory.vault_id AND version.memory_id = memory.id
            JOIN owner_truth.decision_receipts AS receipt
              ON receipt.vault_id = version.vault_id AND receipt.id = version.decision_receipt_id
            WHERE {' AND '.join(conditions)}
            ORDER BY version.created_at DESC, memory.id DESC
            LIMIT %s
        """
        with self._cursor() as cursor:
            self._assert_active_vault(cursor, context=context)
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
        values = tuple(self._memory_from_current_row(row) for row in rows[: query.limit])
        return values, len(rows) > query.limit

    def read_detail(self, *, context: OwnerTruthCommandContext, memory_id: str) -> OwnerTruthFormalMemory:
        _assert_owner_context(context)
        normalized = _uuid(memory_id, field="memoryId")
        with self._cursor() as cursor:
            self._assert_active_vault(cursor, context=context)
            cursor.execute(
                """
                SELECT memory.id AS memory_id, memory.memory_kind,
                    memory.perspective_type, memory.epistemic_status,
                    memory.sensitivity, version.id AS version_id,
                    version.version_number, version.is_current,
                    version.schema_version, version.content_hash,
                    version.payload, version.created_at, receipt.decision
                FROM owner_truth.memories AS memory
                JOIN owner_truth.memory_versions AS version
                  ON version.vault_id = memory.vault_id AND version.memory_id = memory.id
                JOIN owner_truth.decision_receipts AS receipt
                  ON receipt.vault_id = version.vault_id AND receipt.id = version.decision_receipt_id
                WHERE memory.vault_id = %s AND memory.id = %s
                  AND memory.owner_subject_id = %s AND memory.status = 'active'
                ORDER BY version.version_number DESC
                LIMIT %s
                """,
                (context.vault_id, normalized, context.owner_subject_id, FORMAL_MEMORY_HISTORY_LIMIT + 2),
            )
            rows = cursor.fetchall()
        if not rows:
            raise OwnerTruthFormalMemoryAccessDenied("Memory does not exist in this Owner Vault")
        versions = tuple(self._version_from_row(row) for row in rows[: FORMAL_MEMORY_HISTORY_LIMIT + 1])
        current = next((item for item in versions if item.status == "current"), None)
        if current is None:
            raise OwnerTruthFormalMemoryConflict("formal memory has no current version")
        first = rows[0]
        return OwnerTruthFormalMemory(
            memory_id=str(first["memory_id"]),
            memory_kind=str(first["memory_kind"]),
            perspective_type=str(first["perspective_type"]),
            epistemic_status=str(first["epistemic_status"]),
            sensitivity=str(first["sensitivity"]),
            current_version=current,
            versions=versions,
            history_truncated=len(rows) > FORMAL_MEMORY_HISTORY_LIMIT + 1,
        )

    def prepare_correction(
        self,
        *,
        context: OwnerTruthCommandContext,
        memory_id: str,
        command: OwnerTruthFormalMemoryCorrectionCommand,
        review_command: OwnerTruthCandidateReviewCommand,
    ) -> OwnerTruthFormalMemoryCorrectionTarget | OwnerTruthFormalMemoryCorrectionResult:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-formal-memory-correction:{context.vault_id}:{review_command.command_id_hash}",),
            )
            cursor.execute(
                """
                SELECT receipt.id, receipt.candidate_id, receipt.payload_hash,
                    version.id AS version_id, version.memory_id,
                    version.version_number, version.content_hash,
                    version.supersedes_version_id
                FROM owner_truth.decision_receipts AS receipt
                LEFT JOIN owner_truth.memory_versions AS version
                  ON version.vault_id = receipt.vault_id
                 AND version.decision_receipt_id = receipt.id
                WHERE receipt.vault_id = %s AND receipt.command_id_hash = %s
                FOR UPDATE OF receipt
                """,
                (context.vault_id, review_command.command_id_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    str(existing["candidate_id"]) != review_command.candidate_id
                    or str(existing["payload_hash"]) != review_command.payload_hash
                    or existing["version_id"] is None
                    or str(existing["memory_id"]) != memory_id
                    or existing["supersedes_version_id"] is None
                ):
                    raise OwnerTruthFormalMemoryConflict("commandId was reused with different correction content")
                return OwnerTruthFormalMemoryCorrectionResult(
                    outcome="deduplicated",
                    memory_id=memory_id,
                    superseded_version_id=str(existing["supersedes_version_id"]),
                    replacement_version_id=str(existing["version_id"]),
                    replacement_version=int(existing["version_number"]),
                    content_hash=str(existing["content_hash"]),
                    receipt_id=str(existing["id"]),
                    correction_link_id=str(uuid5(_CORRECTION_LINK_NAMESPACE, f"{context.vault_id}:{existing['id']}")),
                )
            self._assert_active_vault(cursor, context=context)
            cursor.execute(
                """
                SELECT memory.memory_kind, memory.perspective_type,
                    memory.epistemic_status, memory.sensitivity,
                    memory.authority_epoch, version.id AS version_id,
                    version.version_number, version.schema_version,
                    version.content_hash, version.payload
                FROM owner_truth.memories AS memory
                JOIN owner_truth.memory_versions AS version
                  ON version.vault_id = memory.vault_id AND version.memory_id = memory.id
                WHERE memory.vault_id = %s AND memory.id = %s
                  AND memory.owner_subject_id = %s AND memory.status = 'active'
                  AND version.is_current = TRUE
                FOR UPDATE OF memory, version
                """,
                (context.vault_id, memory_id, context.owner_subject_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise OwnerTruthFormalMemoryAccessDenied("Memory does not exist in this Owner Vault")
        payload = self._json_object(row["payload"])
        return OwnerTruthFormalMemoryCorrectionTarget(
            memory_id=memory_id,
            memory_kind=str(row["memory_kind"]),
            perspective_type=str(row["perspective_type"]),
            epistemic_status=str(row["epistemic_status"]),
            sensitivity=str(row["sensitivity"]),
            authority_epoch=int(row["authority_epoch"]),
            version_id=str(row["version_id"]),
            version_number=int(row["version_number"]),
            content_schema_version=str(row["schema_version"]),
            content_hash=str(row["content_hash"]),
            payload=payload,
        )

    def ensure_correction_candidate(
        self,
        *,
        context: OwnerTruthCommandContext,
        target: OwnerTruthFormalMemoryCorrectionTarget,
        command: OwnerTruthFormalMemoryCorrectionCommand,
        candidate: OwnerTruthCandidateSnapshot,
    ) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT owner_subject_id, source_id, candidate_kind,
                    perspective_type, epistemic_status, sensitivity,
                    decision_status, policy_version, authority_epoch,
                    row_version, content_hash, payload_schema_version, payload
                FROM owner_truth.memory_candidates
                WHERE vault_id = %s AND id = %s
                FOR UPDATE
                """,
                (context.vault_id, candidate.candidate_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing["content_hash"]) != candidate.content_hash or self._json_object(existing["payload"]) != dict(candidate.payload):
                    raise OwnerTruthFormalMemoryConflict("correction Candidate id was reused")
                return
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_candidates (
                    id, vault_id, owner_subject_id, source_id, extraction_result_id,
                    candidate_kind, perspective_type, epistemic_status, sensitivity,
                    decision_status, quarantine_code, policy_version, authority_epoch,
                    content_hash, payload_schema_version, payload
                ) VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s,
                    'pending', NULL, %s, %s, %s, %s, %s)
                """,
                self._adapt_params((
                    candidate.candidate_id, context.vault_id, context.owner_subject_id,
                    candidate.source_id, candidate.memory_kind.value,
                    candidate.perspective_type.value, candidate.epistemic_status.value,
                    candidate.sensitivity.value, candidate.policy_version,
                    candidate.authority_epoch, candidate.content_hash,
                    candidate.content_schema_version, dict(candidate.payload),
                )),
            )

    @staticmethod
    def _memory_from_current_row(row: Mapping[str, Any]) -> OwnerTruthFormalMemory:
        version = PostgresOwnerTruthFormalMemoryRepository._version_from_row({**row, "is_current": True})
        return OwnerTruthFormalMemory(
            memory_id=str(row["memory_id"]),
            memory_kind=str(row["memory_kind"]),
            perspective_type=str(row["perspective_type"]),
            epistemic_status=str(row["epistemic_status"]),
            sensitivity=str(row["sensitivity"]),
            current_version=version,
        )

    @staticmethod
    def _version_from_row(row: Mapping[str, Any]) -> OwnerTruthFormalMemoryVersion:
        payload = PostgresOwnerTruthFormalMemoryRepository._json_object(row["payload"])
        content = payload.get("content")
        refs = payload.get("evidenceRefs")
        if not isinstance(content, Mapping) or not isinstance(refs, list):
            raise OwnerTruthFormalMemoryConflict("MemoryVersion payload is unavailable")
        source_count = len({
            (str(item.get("sourceId") or ""), int(item.get("sourceVersion") or 0))
            for item in refs if isinstance(item, Mapping)
        })
        return OwnerTruthFormalMemoryVersion(
            version_id=str(row["version_id"]),
            version_number=int(row["version_number"]),
            status="current" if bool(row.get("is_current")) else "superseded",
            decision=str(row["decision"]),
            content_schema_version=str(payload.get("contentSchemaVersion") or row["schema_version"]),
            content_hash=str(row["content_hash"]),
            content=deepcopy(dict(content)),
            source_count=source_count,
            created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
        )

    @staticmethod
    def _assert_active_vault(cursor: Any, *, context: OwnerTruthCommandContext) -> None:
        cursor.execute(
            "SELECT owner_subject_id, status FROM owner_truth.vaults WHERE vault_id = %s",
            (context.vault_id,),
        )
        vault = cursor.fetchone()
        if vault is None or str(vault["owner_subject_id"]) != context.owner_subject_id or str(vault["status"]) != "active":
            raise OwnerTruthFormalMemoryAccessDenied("Vault is not active for this Owner")

    @staticmethod
    def _json_object(value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, Mapping):
            raise OwnerTruthFormalMemoryConflict("stored JSON object is invalid")
        return dict(value)

    @staticmethod
    def _adapt_params(values: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover
            return tuple(_canonical_json(value) if isinstance(value, Mapping) else value for value in values)
        return tuple(Jsonb(dict(value)) if isinstance(value, Mapping) else value for value in values)

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthFormalMemoryStore(Protocol):
    def owner_truth_formal_memory_repository(self) -> OwnerTruthFormalMemoryRepository: ...
    def owner_truth_candidate_review_repository(self) -> Any: ...


class OwnerTruthFormalMemoryService:
    def __init__(self, store: OwnerTruthFormalMemoryStore) -> None:
        self._store = store

    def list(self, *, context: OwnerTruthCommandContext, query: OwnerTruthFormalMemoryQuery) -> OwnerTruthFormalMemoryPage:
        _assert_owner_context(context)
        with self._uow(correlation_id=f"formal-memory-list:{context.vault_id}", command_id="formalMemoryList"):
            items, has_more = self._store.owner_truth_formal_memory_repository().list_current(context=context, query=query)
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = OwnerTruthFormalMemoryCursor(
                created_at=last.current_version.created_at,
                memory_id=last.memory_id,
            ).encode()
        return OwnerTruthFormalMemoryPage(items=items, next_cursor=next_cursor)

    def detail(self, *, context: OwnerTruthCommandContext, memory_id: str) -> OwnerTruthFormalMemory:
        _assert_owner_context(context)
        with self._uow(correlation_id=f"formal-memory-detail:{context.vault_id}:{memory_id}", command_id="formalMemoryDetail"):
            return self._store.owner_truth_formal_memory_repository().read_detail(
                context=context, memory_id=_uuid(memory_id, field="memoryId")
            )

    def correct(
        self,
        *,
        context: OwnerTruthCommandContext,
        memory_id: str,
        command: OwnerTruthFormalMemoryCorrectionCommand,
    ) -> OwnerTruthFormalMemoryCorrectionResult:
        _assert_owner_context(context)
        normalized_memory_id = _uuid(memory_id, field="memoryId")
        candidate_id = str(uuid5(_CANDIDATE_NAMESPACE, f"{context.vault_id}:{normalized_memory_id}:{command.payload_hash}"))
        review_command = OwnerTruthCandidateReviewCommand(
            command_id=f"formalMemoryCorrection:{command.command_id}",
            candidate_id=candidate_id,
            expected_candidate_version=1,
            action=CandidateReviewAction.CORRECT,
            corrected_value=command.corrected_content,
            corrected_value_schema_version=command.content_schema_version,
            reason_code=command.reason_code,
        )
        with self._uow(
            correlation_id=f"formal-memory-correction:{context.vault_id}:{review_command.command_id_hash}",
            command_id=review_command.command_id_hash,
        ):
            repository = self._store.owner_truth_formal_memory_repository()
            prepared = repository.prepare_correction(
                context=context,
                memory_id=normalized_memory_id,
                command=command,
                review_command=review_command,
            )
            if isinstance(prepared, OwnerTruthFormalMemoryCorrectionResult):
                return prepared
            self._assert_expected_target(command=command, target=prepared)
            validation = validate_memory_payload(
                kind=MemoryKind(prepared.memory_kind),
                payload=command.corrected_content,
                schema_version=command.content_schema_version,
            )
            if not validation.accepted:
                raise OwnerTruthFormalMemoryError(f"correctedContent is not admitted: {validation.code}")
            source_id = str(uuid5(_SOURCE_NAMESPACE, f"{context.vault_id}:{normalized_memory_id}:{command.payload_hash}"))
            source = OwnerTruthSourceCommandService(self._store).create_text_source(
                command=CreateTextSourceCommand(
                    command_id=f"formal-memory-correction-source:{command.command_id}",
                    source_id=source_id,
                    expected_version=0,
                    text=_canonical_json(command.corrected_content),
                    metadata={
                        "origin": "formalMemoryEditor",
                        "memoryId": normalized_memory_id,
                        "expectedVersionId": prepared.version_id,
                        "expectedVersion": prepared.version_number,
                        "commandPayloadHash": command.payload_hash,
                        "schemaVersion": FORMAL_MEMORY_CORRECTION_SCHEMA_VERSION,
                    },
                    expected_authority_epoch=prepared.authority_epoch,
                ),
                context=context,
            )
            candidate = self._correction_candidate(
                context=context,
                target=prepared,
                command=command,
                candidate_id=candidate_id,
                source=source,
            )
            repository.ensure_correction_candidate(
                context=context,
                target=prepared,
                command=command,
                candidate=candidate,
            )
            review_repository = self._store.owner_truth_candidate_review_repository()
            review = review_repository.decide(
                command=review_command,
                context=context,
                allow_correction=True,
            )
            correction_link_id = str(uuid5(_CORRECTION_LINK_NAMESPACE, f"{context.vault_id}:{review.receipt_id}"))
            activation = review_repository.activate_correction_memory_version(
                receipt_id=review.receipt_id,
                correction_request_id=correction_link_id,
                memory_id=normalized_memory_id,
                expected_memory_version_id=prepared.version_id,
                reason_code_hash=sha256(command.reason_code.encode("utf-8")).hexdigest(),
                context=context,
            )
            effect = self._projection_effect(context=context, activation=activation)
            return OwnerTruthFormalMemoryCorrectionResult(
                outcome="created" if review.outcome == "created" else "deduplicated",
                memory_id=activation.memory_id,
                superseded_version_id=activation.superseded_memory_version_id,
                replacement_version_id=activation.replacement_memory_version_id,
                replacement_version=activation.replacement_memory_version,
                content_hash=activation.content_hash,
                receipt_id=activation.receipt_id,
                correction_link_id=correction_link_id,
                projection_effect=effect,
            )

    @staticmethod
    def _assert_expected_target(
        *, command: OwnerTruthFormalMemoryCorrectionCommand, target: OwnerTruthFormalMemoryCorrectionTarget
    ) -> None:
        if (
            target.version_number != command.expected_version
            or target.content_hash != command.expected_content_hash
            or target.content_schema_version != command.expected_content_schema_version
        ):
            raise OwnerTruthFormalMemoryConflict("formal memory correction targets a stale current version")

    @staticmethod
    def _correction_candidate(
        *,
        context: OwnerTruthCommandContext,
        target: OwnerTruthFormalMemoryCorrectionTarget,
        command: OwnerTruthFormalMemoryCorrectionCommand,
        candidate_id: str,
        source: OwnerTruthSourceCommandResult,
    ) -> OwnerTruthCandidateSnapshot:
        content = deepcopy(dict(command.corrected_content))
        payload = {
            "schemaVersion": FORMAL_MEMORY_CORRECTION_SCHEMA_VERSION,
            "candidateKind": target.memory_kind,
            "perspectiveType": target.perspective_type,
            "epistemicStatus": target.epistemic_status,
            "sensitivity": target.sensitivity,
            "content": content,
            "contentSchemaVersion": command.content_schema_version,
            "confidence": 1.0,
            "reviewMode": "correction",
            "correctionOrigin": "formalMemoryEditor",
            "evidenceRefs": [{"sourceId": source.source_id, "sourceVersion": source.source_version}],
            "correctionTarget": {
                "memoryId": target.memory_id,
                "expectedVersionId": target.version_id,
                "expectedVersion": target.version_number,
                "expectedContentHash": target.content_hash,
            },
            "proposalHash": command.payload_hash,
        }
        return OwnerTruthCandidateSnapshot(
            candidate_id=candidate_id,
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            source_id=source.source_id,
            memory_kind=MemoryKind(target.memory_kind),
            perspective_type=PerspectiveType(target.perspective_type),
            epistemic_status=EpistemicStatus(target.epistemic_status),
            sensitivity=SensitivityLevel(target.sensitivity),
            decision=CandidateDecision.PENDING,
            policy_version=context.policy_version,
            authority_epoch=source.authority_epoch,
            row_version=1,
            content_hash=_digest(content),
            content_schema_version=command.content_schema_version,
            payload=payload,
        )

    def _projection_effect(
        self,
        *,
        context: OwnerTruthCommandContext,
        activation: OwnerTruthMemoryCorrectionActivationResult,
    ) -> Any | None:
        factory = getattr(self._store, "effect_kernel_repository", None)
        if not callable(factory):
            return None
        return factory().accept(build_memory_projection_rebuild_effect_intent_for_version(
            context=context,
            memory_version_id=activation.replacement_memory_version_id,
            memory_version=activation.replacement_memory_version,
            authority_epoch=activation.authority_epoch,
            content_hash=activation.content_hash,
        ))

    def _uow(self, *, correlation_id: str, command_id: str) -> ContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        return factory(correlation_id=correlation_id, command_id=command_id) if callable(factory) else nullcontext()


__all__ = [
    "FORMAL_MEMORY_CORRECTION_SCHEMA_VERSION",
    "FORMAL_MEMORY_DETAIL_SCHEMA_VERSION",
    "FORMAL_MEMORY_HISTORY_LIMIT",
    "FORMAL_MEMORY_LIST_SCHEMA_VERSION",
    "InMemoryOwnerTruthFormalMemoryRepository",
    "OwnerTruthFormalMemoryAccessDenied",
    "OwnerTruthFormalMemoryConflict",
    "OwnerTruthFormalMemoryCorrectionCommand",
    "OwnerTruthFormalMemoryCorrectionResult",
    "OwnerTruthFormalMemoryCursor",
    "OwnerTruthFormalMemoryError",
    "OwnerTruthFormalMemoryFacetFilter",
    "OwnerTruthFormalMemoryPage",
    "OwnerTruthFormalMemoryQuery",
    "OwnerTruthFormalMemoryService",
    "PostgresOwnerTruthFormalMemoryRepository",
]
