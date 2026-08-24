"""Owner-only read model for immutable Source submission records.

Formal Memory is the normalized, confirmed knowledge surface. This module is
the separate provenance surface: it lets the Owner inspect each original text
submission and its organization/review status without treating Source text as
a formal memory.
"""

from __future__ import annotations

import base64
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Any, ContextManager, Mapping, MutableMapping, Protocol
from uuid import UUID

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


SOURCE_RECORD_LIST_SCHEMA_VERSION = "owner-truth-source-record-list-v1"
SOURCE_RECORD_DETAIL_SCHEMA_VERSION = "owner-truth-source-record-detail-v1"
_MAX_PAGE_LIMIT = 100
_MAX_PREVIEW_CHARACTERS = 180
_HIDDEN_ORIGINS = frozenset({"formalMemoryEditor"})


class OwnerTruthSourceRecordError(ValueError):
    pass


class OwnerTruthSourceRecordAccessDenied(OwnerTruthSourceRecordError):
    pass


class OwnerTruthSourceRecordNotFound(OwnerTruthSourceRecordError):
    pass


def _nonblank(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthSourceRecordError(f"{field} is required")
    return normalized


def _uuid(value: Any, *, field: str) -> str:
    try:
        return str(UUID(_nonblank(value, field=field)))
    except (TypeError, ValueError) as error:
        raise OwnerTruthSourceRecordError(f"{field} must be a UUID") from error


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthSourceRecordError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthSourceRecordAccessDenied(
            "only the Vault Owner may read Source submission records"
        )


def _source_text(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    return str(value.get("text") or "").strip()


def _source_origin(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    normalized = str(value.get("origin") or "").strip()
    return normalized or None


def _organization_status(
    *,
    extraction_status: str | None,
    candidate_count: int,
    pending_count: int,
    confirmed_count: int,
    rejected_count: int,
) -> str:
    if pending_count > 0:
        return "awaitingReview"
    if confirmed_count > 0:
        return "confirmed"
    if candidate_count > 0 and rejected_count >= candidate_count:
        return "reviewed"
    if extraction_status in {"failed", "quarantined"}:
        return "failed"
    if extraction_status == "succeeded":
        return "noMemoryFound"
    return "organizing"


@dataclass(frozen=True)
class OwnerTruthSourceRecordCursor:
    created_at: str
    source_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _nonblank(self.created_at, field="cursor.createdAt"))
        object.__setattr__(self, "source_id", _uuid(self.source_id, field="cursor.sourceId"))

    def encode(self) -> str:
        payload = json.dumps(
            {"createdAt": self.created_at, "sourceId": self.source_id},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, value: str | None) -> "OwnerTruthSourceRecordCursor | None":
        normalized = str(value or "").strip()
        if not normalized:
            return None
        try:
            padded = normalized + "=" * (-len(normalized) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            return cls(created_at=payload["createdAt"], source_id=payload["sourceId"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OwnerTruthSourceRecordError("cursor is invalid") from error


@dataclass(frozen=True)
class OwnerTruthSourceRecordQuery:
    cursor: OwnerTruthSourceRecordCursor | None = None
    limit: int = 20

    def __post_init__(self) -> None:
        if type(self.limit) is not int or not 1 <= self.limit <= _MAX_PAGE_LIMIT:
            raise OwnerTruthSourceRecordError("limit must be between 1 and 100")


@dataclass(frozen=True)
class OwnerTruthSourceRecord:
    source_id: str
    source_kind: str
    source_version: int
    state: str
    text: str
    origin: str | None
    created_at: str
    updated_at: str
    extraction_status: str | None
    failure_code: str | None
    candidate_count: int
    pending_count: int
    confirmed_count: int
    rejected_count: int

    @property
    def organization_status(self) -> str:
        return _organization_status(
            extraction_status=self.extraction_status,
            candidate_count=self.candidate_count,
            pending_count=self.pending_count,
            confirmed_count=self.confirmed_count,
            rejected_count=self.rejected_count,
        )

    def list_contract(self) -> dict[str, Any]:
        preview = self.text
        if len(preview) > _MAX_PREVIEW_CHARACTERS:
            preview = preview[:_MAX_PREVIEW_CHARACTERS].rstrip() + "…"
        return {
            "sourceId": self.source_id,
            "sourceKind": self.source_kind,
            "sourceVersion": self.source_version,
            "state": self.state,
            "textPreview": preview,
            "origin": self.origin,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "organizationStatus": self.organization_status,
            "extractionStatus": self.extraction_status,
            "failureCode": self.failure_code,
            "candidateCount": self.candidate_count,
            "pendingCount": self.pending_count,
            "confirmedCount": self.confirmed_count,
            "rejectedCount": self.rejected_count,
        }

    def detail_contract(self) -> dict[str, Any]:
        return {**self.list_contract(), "text": self.text}


@dataclass(frozen=True)
class OwnerTruthSourceRecordPage:
    items: tuple[OwnerTruthSourceRecord, ...]
    next_cursor: str | None


class OwnerTruthSourceRecordRepository(Protocol):
    def list_records(
        self,
        *,
        context: OwnerTruthCommandContext,
        query: OwnerTruthSourceRecordQuery,
    ) -> tuple[tuple[OwnerTruthSourceRecord, ...], bool]: ...

    def read_record(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_id: str,
    ) -> OwnerTruthSourceRecord: ...


class InMemoryOwnerTruthSourceRecordRepository:
    def __init__(
        self,
        *,
        sources: MutableMapping[tuple[str, str], dict[str, Any]],
        lock: Any,
        candidate_review_repository: Any,
    ) -> None:
        self._sources = sources
        self._lock = lock
        self._candidate_review_repository = candidate_review_repository

    def list_records(
        self,
        *,
        context: OwnerTruthCommandContext,
        query: OwnerTruthSourceRecordQuery,
    ) -> tuple[tuple[OwnerTruthSourceRecord, ...], bool]:
        _assert_owner_context(context)
        with self._lock:
            rows = [
                deepcopy(source)
                for (vault_id, _source_id), source in self._sources.items()
                if vault_id == context.vault_id
                and source.get("ownerSubjectId") == context.owner_subject_id
                and source.get("state") != "deleted"
                and _source_origin(source.get("metadata")) not in _HIDDEN_ORIGINS
            ]
        rows.sort(
            key=lambda row: (str(row.get("createdAt") or ""), str(row.get("id") or "")),
            reverse=True,
        )
        if query.cursor is not None:
            cursor_key = (query.cursor.created_at, query.cursor.source_id)
            rows = [
                row
                for row in rows
                if (str(row.get("createdAt") or ""), str(row.get("id") or "")) < cursor_key
            ]
        page_rows = rows[: query.limit + 1]
        has_more = len(page_rows) > query.limit
        return (
            tuple(self._record(row, context=context) for row in page_rows[: query.limit]),
            has_more,
        )

    def read_record(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_id: str,
    ) -> OwnerTruthSourceRecord:
        _assert_owner_context(context)
        normalized_source_id = _uuid(source_id, field="sourceId")
        with self._lock:
            source = deepcopy(self._sources.get((context.vault_id, normalized_source_id)))
        if (
            not isinstance(source, Mapping)
            or source.get("ownerSubjectId") != context.owner_subject_id
            or source.get("state") == "deleted"
            or _source_origin(source.get("metadata")) in _HIDDEN_ORIGINS
        ):
            raise OwnerTruthSourceRecordNotFound("Source submission record was not found")
        return self._record(source, context=context)

    def _record(
        self,
        source: Mapping[str, Any],
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthSourceRecord:
        snapshot = self._candidate_review_repository.snapshot()
        candidates = [
            candidate
            for candidate in snapshot.get("candidates", {}).values()
            if isinstance(candidate, Mapping)
            and candidate.get("vaultId") == context.vault_id
            and candidate.get("sourceId") == source.get("id")
        ]
        decisions = [str(candidate.get("decision") or "") for candidate in candidates]
        return _record_from_values(
            source=source,
            extraction_status=None,
            failure_code=None,
            candidate_count=len(candidates),
            pending_count=decisions.count("pending"),
            confirmed_count=sum(value in {"accepted", "corrected"} for value in decisions),
            rejected_count=sum(value in {"rejected", "invalidated"} for value in decisions),
        )


class PostgresOwnerTruthSourceRecordRepository:
    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def list_records(
        self,
        *,
        context: OwnerTruthCommandContext,
        query: OwnerTruthSourceRecordQuery,
    ) -> tuple[tuple[OwnerTruthSourceRecord, ...], bool]:
        _assert_owner_context(context)
        params: list[Any] = [context.vault_id, context.owner_subject_id]
        cursor_clause = ""
        if query.cursor is not None:
            cursor_clause = "AND (source.created_at, source.id) < (%s::timestamptz, %s::uuid)"
            params.extend([query.cursor.created_at, query.cursor.source_id])
        params.append(query.limit + 1)
        with self._cursor() as cursor:
            cursor.execute(
                f"""
                SELECT source.id, source.source_kind, source.state,
                    source.source_version, source.content_payload, source.metadata,
                    source.created_at, source.updated_at,
                    extraction.status AS extraction_status,
                    extraction.failure_code,
                    COALESCE(counts.candidate_count, 0) AS candidate_count,
                    COALESCE(counts.pending_count, 0) AS pending_count,
                    COALESCE(counts.confirmed_count, 0) AS confirmed_count,
                    COALESCE(counts.rejected_count, 0) AS rejected_count
                FROM owner_truth.sources AS source
                JOIN owner_truth.vaults AS vault
                  ON vault.vault_id = source.vault_id
                LEFT JOIN LATERAL (
                    SELECT result.status, result.failure_code
                    FROM owner_truth.extraction_results AS result
                    WHERE result.vault_id = source.vault_id
                      AND result.source_id = source.id
                      AND result.source_version = source.source_version
                    ORDER BY result.created_at DESC, result.id DESC
                    LIMIT 1
                ) AS extraction ON TRUE
                LEFT JOIN LATERAL (
                    SELECT COUNT(*)::int AS candidate_count,
                        COUNT(*) FILTER (WHERE candidate.decision_status = 'pending')::int AS pending_count,
                        COUNT(*) FILTER (WHERE candidate.decision_status IN ('accepted', 'corrected'))::int AS confirmed_count,
                        COUNT(*) FILTER (WHERE candidate.decision_status IN ('rejected', 'invalidated'))::int AS rejected_count
                    FROM owner_truth.memory_candidates AS candidate
                    WHERE candidate.vault_id = source.vault_id
                      AND candidate.source_id = source.id
                ) AS counts ON TRUE
                WHERE source.vault_id = %s
                  AND source.owner_subject_id = %s
                  AND source.state <> 'deleted'
                  AND COALESCE(source.metadata->>'origin', '') <> 'formalMemoryEditor'
                  AND vault.owner_subject_id = source.owner_subject_id
                  AND vault.status = 'active'
                  {cursor_clause}
                ORDER BY source.created_at DESC, source.id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cursor.fetchall()
        has_more = len(rows) > query.limit
        return tuple(self._record(row) for row in rows[: query.limit]), has_more

    def read_record(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_id: str,
    ) -> OwnerTruthSourceRecord:
        _assert_owner_context(context)
        normalized_source_id = _uuid(source_id, field="sourceId")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT source.id, source.source_kind, source.state,
                    source.source_version, source.content_payload, source.metadata,
                    source.created_at, source.updated_at,
                    extraction.status AS extraction_status,
                    extraction.failure_code,
                    COALESCE(counts.candidate_count, 0) AS candidate_count,
                    COALESCE(counts.pending_count, 0) AS pending_count,
                    COALESCE(counts.confirmed_count, 0) AS confirmed_count,
                    COALESCE(counts.rejected_count, 0) AS rejected_count
                FROM owner_truth.sources AS source
                JOIN owner_truth.vaults AS vault
                  ON vault.vault_id = source.vault_id
                LEFT JOIN LATERAL (
                    SELECT result.status, result.failure_code
                    FROM owner_truth.extraction_results AS result
                    WHERE result.vault_id = source.vault_id
                      AND result.source_id = source.id
                      AND result.source_version = source.source_version
                    ORDER BY result.created_at DESC, result.id DESC
                    LIMIT 1
                ) AS extraction ON TRUE
                LEFT JOIN LATERAL (
                    SELECT COUNT(*)::int AS candidate_count,
                        COUNT(*) FILTER (WHERE candidate.decision_status = 'pending')::int AS pending_count,
                        COUNT(*) FILTER (WHERE candidate.decision_status IN ('accepted', 'corrected'))::int AS confirmed_count,
                        COUNT(*) FILTER (WHERE candidate.decision_status IN ('rejected', 'invalidated'))::int AS rejected_count
                    FROM owner_truth.memory_candidates AS candidate
                    WHERE candidate.vault_id = source.vault_id
                      AND candidate.source_id = source.id
                ) AS counts ON TRUE
                WHERE source.vault_id = %s
                  AND source.id = %s::uuid
                  AND source.owner_subject_id = %s
                  AND source.state <> 'deleted'
                  AND COALESCE(source.metadata->>'origin', '') <> 'formalMemoryEditor'
                  AND vault.owner_subject_id = source.owner_subject_id
                  AND vault.status = 'active'
                """,
                (context.vault_id, normalized_source_id, context.owner_subject_id),
            )
            row = cursor.fetchone()
        if row is None:
            raise OwnerTruthSourceRecordNotFound("Source submission record was not found")
        return self._record(row)

    @staticmethod
    def _record(row: Mapping[str, Any]) -> OwnerTruthSourceRecord:
        return _record_from_values(
            source={
                "id": row.get("id"),
                "sourceKind": row.get("source_kind"),
                "state": row.get("state"),
                "sourceVersion": row.get("source_version"),
                "contentPayload": row.get("content_payload"),
                "metadata": row.get("metadata"),
                "createdAt": row.get("created_at"),
                "updatedAt": row.get("updated_at"),
            },
            extraction_status=(str(row.get("extraction_status")) if row.get("extraction_status") else None),
            failure_code=(str(row.get("failure_code")) if row.get("failure_code") else None),
            candidate_count=int(row.get("candidate_count") or 0),
            pending_count=int(row.get("pending_count") or 0),
            confirmed_count=int(row.get("confirmed_count") or 0),
            rejected_count=int(row.get("rejected_count") or 0),
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


def _record_from_values(
    *,
    source: Mapping[str, Any],
    extraction_status: str | None,
    failure_code: str | None,
    candidate_count: int,
    pending_count: int,
    confirmed_count: int,
    rejected_count: int,
) -> OwnerTruthSourceRecord:
    created_at = source.get("createdAt")
    updated_at = source.get("updatedAt")
    content_payload = source.get("contentPayload")
    return OwnerTruthSourceRecord(
        source_id=_uuid(source.get("id"), field="sourceId"),
        source_kind=_nonblank(source.get("sourceKind"), field="sourceKind"),
        source_version=int(source.get("sourceVersion") or 0),
        state=_nonblank(source.get("state"), field="state"),
        text=_source_text(content_payload),
        origin=_source_origin(source.get("metadata")),
        created_at=_nonblank(
            created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
            field="createdAt",
        ),
        updated_at=_nonblank(
            updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
            field="updatedAt",
        ),
        extraction_status=extraction_status,
        failure_code=failure_code,
        candidate_count=max(0, candidate_count),
        pending_count=max(0, pending_count),
        confirmed_count=max(0, confirmed_count),
        rejected_count=max(0, rejected_count),
    )


class OwnerTruthSourceRecordStore(Protocol):
    def owner_truth_source_record_repository(self) -> OwnerTruthSourceRecordRepository: ...


class OwnerTruthSourceRecordService:
    def __init__(self, store: OwnerTruthSourceRecordStore) -> None:
        self._store = store

    def list(
        self,
        *,
        context: OwnerTruthCommandContext,
        query: OwnerTruthSourceRecordQuery,
    ) -> OwnerTruthSourceRecordPage:
        _assert_owner_context(context)
        with self._uow(
            correlation_id=f"source-record-list:{context.vault_id}",
            command_id="sourceRecordList",
        ):
            items, has_more = self._store.owner_truth_source_record_repository().list_records(
                context=context,
                query=query,
            )
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = OwnerTruthSourceRecordCursor(
                created_at=last.created_at,
                source_id=last.source_id,
            ).encode()
        return OwnerTruthSourceRecordPage(items=items, next_cursor=next_cursor)

    def detail(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_id: str,
    ) -> OwnerTruthSourceRecord:
        _assert_owner_context(context)
        with self._uow(
            correlation_id=f"source-record-detail:{context.vault_id}:{source_id}",
            command_id="sourceRecordDetail",
        ):
            return self._store.owner_truth_source_record_repository().read_record(
                context=context,
                source_id=_uuid(source_id, field="sourceId"),
            )

    def _uow(self, *, correlation_id: str, command_id: str) -> ContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        return (
            factory(correlation_id=correlation_id, command_id=command_id)
            if callable(factory)
            else nullcontext()
        )


__all__ = [
    "SOURCE_RECORD_DETAIL_SCHEMA_VERSION",
    "SOURCE_RECORD_LIST_SCHEMA_VERSION",
    "InMemoryOwnerTruthSourceRecordRepository",
    "OwnerTruthSourceRecordAccessDenied",
    "OwnerTruthSourceRecordCursor",
    "OwnerTruthSourceRecordError",
    "OwnerTruthSourceRecordNotFound",
    "OwnerTruthSourceRecordQuery",
    "OwnerTruthSourceRecordService",
    "PostgresOwnerTruthSourceRecordRepository",
]
