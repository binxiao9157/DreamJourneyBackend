"""QA-only immutable Answer/Citation evidence over Owner Truth Context V4.

This is deliberately a shadow persistence boundary.  It records only hashes,
typed citations, and policy metadata after the Context shadow has selected
current confirmed MemoryVersions.  It neither stores raw question/answer text
nor changes the legacy public Echo route.
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

from app.domain.owner_truth.memory_projection import (
    OwnerTruthMemoryProjectionAccessDenied,
    OwnerTruthMemoryProjectionError,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_context_shadow_build import OwnerTruthContextShadowBuildService


OWNER_TRUTH_ANSWER_CITATION_SCHEMA_VERSION = "owner-truth-answer-citation-v1"
_ANSWER_NAMESPACE = UUID("a55c6a58-3cff-4be1-8a9e-8c2cf9afbc7b")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_MAX_ANSWER_CHARS = 32768


class OwnerTruthAnswerCitationError(OwnerTruthMemoryProjectionError):
    """An Answer/Citation evidence record cannot be safely persisted."""


class OwnerTruthAnswerCitationConflict(OwnerTruthAnswerCitationError):
    """A command id was reused with different Answer/Citation meaning."""


class OwnerTruthAnswerCitationUnavailable(OwnerTruthAnswerCitationError):
    """The QA-only persistence boundary is disabled."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthAnswerCitationError("answer citation values must be JSON serializable") from exc


def _digest(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise OwnerTruthAnswerCitationError(f"{field} must be text")
    normalized = value.strip()
    if not normalized:
        raise OwnerTruthAnswerCitationError(f"{field} must be nonblank")
    return normalized


def _opaque_identifier(value: Any, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    if _IDENTIFIER_PATTERN.fullmatch(normalized) is None:
        raise OwnerTruthAnswerCitationError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: Any, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthAnswerCitationError(f"{field} must be a UUID") from exc


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OwnerTruthAnswerCitationError(f"{field} must be positive")
    return value


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OwnerTruthAnswerCitationError(f"{field} must be non-negative")
    return value


def _hash(value: Any, *, field: str) -> str:
    normalized = _nonblank_text(value, field=field)
    if _HASH_PATTERN.fullmatch(normalized) is None:
        raise OwnerTruthAnswerCitationError(f"{field} must be a sha256 digest")
    return normalized


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthAnswerCitationError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthMemoryProjectionAccessDenied(
            "only the Vault Owner may persist Answer/Citation evidence"
        )


def _citation_record(
    item: Mapping[str, Any],
    *,
    context: OwnerTruthCommandContext,
    position: int,
) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise OwnerTruthAnswerCitationError("selected Context item must be an object")
    citation = item.get("citation")
    source_ref = item.get("sourceRef")
    if not isinstance(citation, Mapping) or not isinstance(source_ref, Mapping):
        raise OwnerTruthAnswerCitationError("selected Context item must carry typed citation and sourceRef")

    vault_id = _nonblank_text(citation.get("vaultId"), field="citation.vaultId")
    if vault_id != context.vault_id:
        raise OwnerTruthMemoryProjectionAccessDenied("typed citation belongs to another Vault")
    source_vault_id = _nonblank_text(source_ref.get("vaultId"), field="sourceRef.vaultId")
    if source_vault_id != context.vault_id:
        raise OwnerTruthMemoryProjectionAccessDenied("citation source belongs to another Vault")

    source_id = _uuid(citation.get("sourceId"), field="citation.sourceId")
    source_version = _positive_int(citation.get("sourceVersion"), field="citation.sourceVersion")
    if _uuid(source_ref.get("sourceId"), field="sourceRef.sourceId") != source_id:
        raise OwnerTruthAnswerCitationError("citation sourceRef must match citation sourceId")
    if _positive_int(source_ref.get("sourceVersion"), field="sourceRef.sourceVersion") != source_version:
        raise OwnerTruthAnswerCitationError("citation sourceRef must match citation sourceVersion")

    return {
        "position": position,
        "resolved": True,
        "resolution": "current_confirmed_projection_entry",
        "citation": {
            "vaultId": vault_id,
            "memoryId": _uuid(citation.get("memoryId"), field="citation.memoryId"),
            "memoryVersionId": _uuid(
                citation.get("memoryVersionId"), field="citation.memoryVersionId"
            ),
            "memoryVersion": _positive_int(citation.get("memoryVersion"), field="citation.memoryVersion"),
            "sourceId": source_id,
            "sourceVersion": source_version,
            "contentHash": _hash(citation.get("contentHash"), field="citation.contentHash"),
        },
    }


@dataclass(frozen=True)
class OwnerTruthAnswerCitationCommand:
    """One idempotent, QA-only answer evidence write.

    ``answer_text`` exists only while hashing this command.  Repositories are
    forbidden from retaining it; persisted records carry a digest and length.
    """

    command_id: str
    answer_text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _opaque_identifier(self.command_id, field="command_id"))
        answer_text = _nonblank_text(self.answer_text, field="answer_text")
        if len(answer_text) > _MAX_ANSWER_CHARS:
            raise OwnerTruthAnswerCitationError("answer_text exceeds the QA evidence limit")
        object.__setattr__(self, "answer_text", answer_text)

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()

    @property
    def answer_hash(self) -> str:
        return sha256(self.answer_text.encode("utf-8")).hexdigest()

    @property
    def answer_length(self) -> int:
        return len(self.answer_text)


@dataclass(frozen=True)
class OwnerTruthAnswerCitationResult:
    outcome: str
    answer_id: str
    command_id_hash: str
    context_hash: str
    context_version: str
    query_hash: str | None
    answer_hash: str
    answer_length: int
    authority_epoch: int | None
    projection_checkpoint: str | None
    citation_count: int
    citations: tuple[Mapping[str, Any], ...]
    fallbacks: tuple[str, ...]


class OwnerTruthAnswerCitationStore(Protocol):
    def owner_truth_memory_projection_repository(self) -> Any:
        ...

    def owner_truth_answer_citation_repository(self) -> Any:
        ...


def _record_input(
    *,
    context: OwnerTruthCommandContext,
    command: OwnerTruthAnswerCitationCommand,
    context_build: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(context_build, Mapping):
        raise OwnerTruthAnswerCitationError("Context shadow build result must be an object")
    context_hash = _hash(context_build.get("contextHash"), field="contextHash")
    context_version = _nonblank_text(context_build.get("contextVersion"), field="contextVersion")
    request = context_build.get("request")
    authority = context_build.get("authority")
    selected_context = context_build.get("selectedContext")
    fallbacks = context_build.get("fallbacks")
    if not isinstance(request, Mapping) or not isinstance(authority, Mapping):
        raise OwnerTruthAnswerCitationError("Context shadow build metadata is invalid")
    if not isinstance(selected_context, list) or not isinstance(fallbacks, list):
        raise OwnerTruthAnswerCitationError("Context shadow build lists are invalid")

    raw_query_hash = request.get("queryHash")
    query_hash = None if raw_query_hash is None else _hash(raw_query_hash, field="request.queryHash")
    query_length = _nonnegative_int(request.get("queryLength"), field="request.queryLength")
    authority_epoch = authority.get("authorityEpoch")
    if authority_epoch is not None:
        authority_epoch = _nonnegative_int(authority_epoch, field="authority.authorityEpoch")
    checkpoint = authority.get("projectionCheckpoint")
    if checkpoint is not None:
        checkpoint = _hash(checkpoint, field="authority.projectionCheckpoint")
    if _nonblank_text(authority.get("vaultId"), field="authority.vaultId") != context.vault_id:
        raise OwnerTruthMemoryProjectionAccessDenied("Context shadow build belongs to another Vault")

    citations = [
        _citation_record(item, context=context, position=index)
        for index, item in enumerate(selected_context, start=1)
    ]
    normalized_fallbacks = tuple(_nonblank_text(item, field="fallback") for item in fallbacks)
    return {
        "answerId": str(uuid5(_ANSWER_NAMESPACE, f"{context.vault_id}:{command.command_id_hash}")),
        "vaultId": context.vault_id,
        "ownerSubjectId": context.owner_subject_id,
        "commandIdHash": command.command_id_hash,
        "contextHash": context_hash,
        "contextVersion": context_version,
        "queryHash": query_hash,
        "queryLength": query_length,
        "answerHash": command.answer_hash,
        "answerLength": command.answer_length,
        "authorityEpoch": authority_epoch,
        "projectionCheckpoint": checkpoint,
        "citations": citations,
        "fallbacks": normalized_fallbacks,
    }


def _payload_hash(record: Mapping[str, Any]) -> str:
    return _digest(
        {
            "contextHash": record["contextHash"],
            "contextVersion": record["contextVersion"],
            "queryHash": record["queryHash"],
            "queryLength": record["queryLength"],
            "answerHash": record["answerHash"],
            "answerLength": record["answerLength"],
            "authorityEpoch": record["authorityEpoch"],
            "projectionCheckpoint": record["projectionCheckpoint"],
            "citations": record["citations"],
            "fallbacks": record["fallbacks"],
        }
    )


def _result_from_record(record: Mapping[str, Any], *, outcome: str) -> OwnerTruthAnswerCitationResult:
    citations = record.get("citations")
    fallbacks = record.get("fallbacks")
    if not isinstance(citations, list) or not isinstance(fallbacks, (list, tuple)):
        raise OwnerTruthAnswerCitationError("persisted Answer/Citation record is malformed")
    return OwnerTruthAnswerCitationResult(
        outcome=outcome,
        answer_id=_uuid(record.get("answerId"), field="answerId"),
        command_id_hash=_hash(record.get("commandIdHash"), field="commandIdHash"),
        context_hash=_hash(record.get("contextHash"), field="contextHash"),
        context_version=_nonblank_text(record.get("contextVersion"), field="contextVersion"),
        query_hash=None
        if record.get("queryHash") is None
        else _hash(record.get("queryHash"), field="queryHash"),
        answer_hash=_hash(record.get("answerHash"), field="answerHash"),
        answer_length=_nonnegative_int(record.get("answerLength"), field="answerLength"),
        authority_epoch=None
        if record.get("authorityEpoch") is None
        else _nonnegative_int(record.get("authorityEpoch"), field="authorityEpoch"),
        projection_checkpoint=None
        if record.get("projectionCheckpoint") is None
        else _hash(record.get("projectionCheckpoint"), field="projectionCheckpoint"),
        citation_count=len(citations),
        citations=tuple(deepcopy(citations)),
        fallbacks=tuple(str(item) for item in fallbacks),
    )


class InMemoryOwnerTruthAnswerCitationRepository:
    """Semantic double for immutable, command-idempotent Answer/Citation records."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[tuple[str, str], dict[str, Any]] = {}

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthAnswerCitationResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        key = (context.vault_id, str(normalized["commandIdHash"]))
        payload_hash = _payload_hash(normalized)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing["payloadHash"] != payload_hash:
                    raise OwnerTruthAnswerCitationConflict(
                        "commandId cannot be reused with different Answer/Citation evidence"
                    )
                return _result_from_record(existing, outcome="deduplicated")
            normalized["payloadHash"] = payload_hash
            self._records[key] = normalized
            return _result_from_record(normalized, outcome="created")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy({
                "records": list(self._records.values()),
            })


class PostgresOwnerTruthAnswerCitationRepository:
    """Postgres writer bound to one request Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthAnswerCitationResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        payload_hash = _payload_hash(normalized)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-answer-citation:{context.vault_id}:{normalized['commandIdHash']}",),
            )
            cursor.execute(
                """
                SELECT id, command_id_hash, command_payload_hash, context_hash, context_version,
                    query_hash, query_length, answer_hash, answer_length,
                    authority_epoch, projection_checkpoint, fallbacks
                FROM owner_truth.answers
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, normalized["commandIdHash"]),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing["command_payload_hash"]) != payload_hash:
                    raise OwnerTruthAnswerCitationConflict(
                        "commandId cannot be reused with different Answer/Citation evidence"
                    )
                return self._existing_result(cursor, context=context, row=existing, outcome="deduplicated")

            self._assert_active_vault(cursor, context=context, authority_epoch=normalized["authorityEpoch"])
            cursor.execute(
                """
                INSERT INTO owner_truth.answers (
                    id, vault_id, owner_subject_id, command_id_hash,
                    command_payload_hash, context_hash, context_version,
                    query_hash, query_length, answer_hash, answer_length,
                    authority_epoch, projection_checkpoint, fallbacks
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                self._adapt_params(
                    (
                        normalized["answerId"],
                        normalized["vaultId"],
                        normalized["ownerSubjectId"],
                        normalized["commandIdHash"],
                        payload_hash,
                        normalized["contextHash"],
                        normalized["contextVersion"],
                        normalized["queryHash"],
                        normalized["queryLength"],
                        normalized["answerHash"],
                        normalized["answerLength"],
                        normalized["authorityEpoch"],
                        normalized["projectionCheckpoint"],
                        list(normalized["fallbacks"]),
                    )
                ),
            )
            for citation in normalized["citations"]:
                fields = citation["citation"]
                cursor.execute(
                    """
                    INSERT INTO owner_truth.answer_citations (
                        id, vault_id, answer_id, citation_position,
                        memory_id, memory_version_id, memory_version,
                        source_id, source_version, content_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(
                            uuid5(
                                _ANSWER_NAMESPACE,
                                f"{normalized['answerId']}:{citation['position']}",
                            )
                        ),
                        context.vault_id,
                        normalized["answerId"],
                        citation["position"],
                        fields["memoryId"],
                        fields["memoryVersionId"],
                        fields["memoryVersion"],
                        fields["sourceId"],
                        fields["sourceVersion"],
                        fields["contentHash"],
                    ),
                )

            stored = dict(normalized)
            stored["payloadHash"] = payload_hash
            return _result_from_record(stored, outcome="created")

    def _existing_result(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        row: Mapping[str, Any],
        outcome: str,
    ) -> OwnerTruthAnswerCitationResult:
        answer_id = str(row["id"])
        cursor.execute(
            """
            SELECT citation_position, memory_id, memory_version_id, memory_version,
                source_id, source_version, content_hash
            FROM owner_truth.answer_citations
            WHERE vault_id = %s AND answer_id = %s
            ORDER BY citation_position ASC
            """,
            (context.vault_id, answer_id),
        )
        citations = [
            {
                "position": int(item["citation_position"]),
                "resolved": True,
                "resolution": "current_confirmed_projection_entry",
                "citation": {
                    "vaultId": context.vault_id,
                    "memoryId": str(item["memory_id"]),
                    "memoryVersionId": str(item["memory_version_id"]),
                    "memoryVersion": int(item["memory_version"]),
                    "sourceId": str(item["source_id"]),
                    "sourceVersion": int(item["source_version"]),
                    "contentHash": str(item["content_hash"]),
                },
            }
            for item in cursor.fetchall()
        ]
        fallbacks = row["fallbacks"]
        if isinstance(fallbacks, str):
            fallbacks = json.loads(fallbacks)
        record = {
            "answerId": answer_id,
            "commandIdHash": str(row.get("command_id_hash") or ""),
            "contextHash": str(row["context_hash"]),
            "contextVersion": str(row["context_version"]),
            "queryHash": row["query_hash"],
            "answerHash": str(row["answer_hash"]),
            "answerLength": int(row["answer_length"]),
            "authorityEpoch": row["authority_epoch"],
            "projectionCheckpoint": row["projection_checkpoint"],
            "citations": citations,
            "fallbacks": list(fallbacks or []),
        }
        return _result_from_record(record, outcome=outcome)

    @staticmethod
    def _assert_active_vault(
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int | None,
    ) -> None:
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
            raise OwnerTruthMemoryProjectionAccessDenied("Vault is not active for this Owner")
        if authority_epoch is not None and int(vault["authority_epoch"]) != authority_epoch:
            raise OwnerTruthAnswerCitationConflict("Context authority epoch is stale")

    @staticmethod
    def _adapt_params(values: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            return tuple(
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (Mapping, list, tuple))
                else value
                for value in values
            )
        return tuple(
            Jsonb(value) if isinstance(value, (Mapping, list, tuple)) else value
            for value in values
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthAnswerCitationService:
    """Build Context V4 shadow and persist its immutable citation evidence."""

    def __init__(self, store: OwnerTruthAnswerCitationStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthAnswerCitationCommand,
        context_payload: Mapping[str, Any] | None,
    ) -> OwnerTruthAnswerCitationResult:
        _assert_owner_context(context)
        if not self._enabled:
            raise OwnerTruthAnswerCitationUnavailable("Answer/Citation shadow persistence is disabled")
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-answer-citation-{command.command_id_hash}",
            command_id=command.command_id_hash,
        ):
            context_build = OwnerTruthContextShadowBuildService(
                self._store,
                enabled=True,
            ).build(
                context=context,
                payload=context_payload,
            )
            record = _record_input(
                context=context,
                command=command,
                context_build=context_build,
            )
            return self._store.owner_truth_answer_citation_repository().record(
                context=context,
                record=record,
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


def answer_citation_summary(result: OwnerTruthAnswerCitationResult) -> dict[str, Any]:
    """Return QA-safe evidence without raw user question, answer, or memory text."""

    if not isinstance(result, OwnerTruthAnswerCitationResult):
        raise OwnerTruthAnswerCitationError("answer citation result is required")
    return {
        "schemaVersion": OWNER_TRUTH_ANSWER_CITATION_SCHEMA_VERSION,
        "outcome": result.outcome,
        "answerId": result.answer_id,
        "contextHash": result.context_hash,
        "contextVersion": result.context_version,
        "queryHash": result.query_hash,
        "answerHash": result.answer_hash,
        "answerLength": result.answer_length,
        "authorityEpoch": result.authority_epoch,
        "projectionCheckpoint": result.projection_checkpoint,
        "citationCount": result.citation_count,
        "citations": [deepcopy(dict(item)) for item in result.citations],
        "fallbacks": list(result.fallbacks),
    }


__all__ = [
    "InMemoryOwnerTruthAnswerCitationRepository",
    "OWNER_TRUTH_ANSWER_CITATION_SCHEMA_VERSION",
    "OwnerTruthAnswerCitationCommand",
    "OwnerTruthAnswerCitationConflict",
    "OwnerTruthAnswerCitationError",
    "OwnerTruthAnswerCitationResult",
    "OwnerTruthAnswerCitationService",
    "OwnerTruthAnswerCitationUnavailable",
    "PostgresOwnerTruthAnswerCitationRepository",
    "answer_citation_summary",
]
