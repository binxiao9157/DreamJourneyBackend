"""Rebuildable private SearchDocument persistence for Phase 4C.

This module stores a derived index of the current Owner-confirmed
MemoryVersion projection. It is intentionally default-off and separate from
any public search surface, vector index, KBLite authority, provider call, or
MemoryVersion writer. A read verifies the source checkpoint and exact derived
document digest; any mismatch returns no index so callers fail closed.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
import json
from threading import RLock
from typing import Any, Mapping, Protocol

from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateReviewAccessDenied
from app.domain.owner_truth.search_documents import (
    OwnerTruthSearchDocument,
    OwnerTruthSearchDocumentProjection,
    OwnerTruthSearchDocumentProjectionError,
    OwnerTruthSearchDocumentProjectionRebuildResult,
    build_owner_truth_search_document_projection,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


class OwnerTruthMemorySearchProjectionAccessDenied(OwnerTruthSearchDocumentProjectionError):
    """The requester cannot rebuild or read this Owner's private index."""


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthSearchDocumentProjectionError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthMemorySearchProjectionAccessDenied(
            "only the Vault Owner may access a SearchDocument projection"
        )


class InMemoryOwnerTruthMemorySearchDocumentProjectionRepository:
    """Semantic double for a checkpoint-bound, rebuildable private index."""

    def __init__(self, memory_projection_repository: Any) -> None:
        self._memory_projection_repository = memory_projection_repository
        self._lock = RLock()
        self._projections: dict[
            tuple[str, int],
            tuple[OwnerTruthSearchDocumentProjection, str],
        ] = {}

    def rebuild(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthSearchDocumentProjectionRebuildResult:
        _assert_owner_context(context)
        projection = self._current_projection(context=context)
        if projection is None:
            return OwnerTruthSearchDocumentProjectionRebuildResult(
                outcome="sourceRebuilding",
                projection=None,
            )
        key = (projection.vault_id, projection.authority_epoch)
        digest = projection.document_digest()
        with self._lock:
            existing = self._projections.get(key)
            outcome = (
                "unchanged"
                if existing is not None
                and existing[0].checkpoint == projection.checkpoint
                and existing[1] == digest
                else "rebuilt"
            )
            self._projections[key] = (projection, digest)
        return OwnerTruthSearchDocumentProjectionRebuildResult(
            outcome=outcome,
            projection=projection,
        )

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthSearchDocumentProjection | None:
        _assert_owner_context(context)
        current = self._current_projection(context=context)
        if current is None:
            return None
        key = (current.vault_id, current.authority_epoch)
        with self._lock:
            stored = self._projections.get(key)
        if (
            stored is None
            or stored[0].checkpoint != current.checkpoint
            or stored[1] != stored[0].document_digest()
            or stored[1] != current.document_digest()
        ):
            return None
        return stored[0]

    def _current_projection(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthSearchDocumentProjection | None:
        source = self._memory_projection_repository.read(context=context)
        return build_owner_truth_search_document_projection(memory_projection=source)


class PostgresOwnerTruthMemorySearchDocumentProjectionRepository:
    """Postgres SearchDocument projector bound to an active Unit of Work."""

    def __init__(self, connection: Any, memory_projection_repository: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection
        self._memory_projection_repository = memory_projection_repository

    def rebuild(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthSearchDocumentProjectionRebuildResult:
        _assert_owner_context(context)
        projection = self._current_projection(context=context)
        if projection is None:
            return OwnerTruthSearchDocumentProjectionRebuildResult(
                outcome="sourceRebuilding",
                projection=None,
            )
        digest = projection.document_digest()
        with self._cursor() as cursor:
            self._assert_active_vault(
                cursor,
                context=context,
                authority_epoch=projection.authority_epoch,
                lock=True,
            )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-memory-search-projection:"
                    f"{context.vault_id}:{projection.authority_epoch}",
                ),
            )
            cursor.execute(
                """
                SELECT state, source_projection_checkpoint, document_count, document_hash,
                    schema_version
                FROM owner_truth.search_document_checkpoints
                WHERE vault_id = %s AND authority_epoch = %s
                FOR UPDATE
                """,
                (context.vault_id, projection.authority_epoch),
            )
            existing = cursor.fetchone()
            stored = self._stored_projection(cursor, projection=projection)
            outcome = (
                "unchanged"
                if existing is not None
                and str(existing["state"]) == "ready"
                and str(existing["source_projection_checkpoint"]) == projection.checkpoint
                and int(existing["document_count"]) == len(projection.documents)
                and str(existing["document_hash"]) == digest
                and str(existing["schema_version"])
                == "owner-truth-search-document-projection-v1"
                and stored is not None
                and stored.document_digest() == digest
                else "rebuilt"
            )
            # The transaction exposes either the old complete index or the
            # new one. The transient rebuilding checkpoint lets row triggers
            # bind every insert to the intended current source checkpoint.
            cursor.execute(
                """
                INSERT INTO owner_truth.search_document_checkpoints (
                    vault_id, authority_epoch, owner_subject_id, state,
                    source_projection_checkpoint, document_count, document_hash,
                    schema_version, updated_at
                ) VALUES (%s, %s, %s, 'rebuilding', %s, 0, %s, %s, NOW())
                ON CONFLICT (vault_id, authority_epoch) DO UPDATE SET
                    owner_subject_id = EXCLUDED.owner_subject_id,
                    state = EXCLUDED.state,
                    source_projection_checkpoint = EXCLUDED.source_projection_checkpoint,
                    document_count = EXCLUDED.document_count,
                    document_hash = EXCLUDED.document_hash,
                    schema_version = EXCLUDED.schema_version,
                    updated_at = NOW()
                """,
                (
                    projection.vault_id,
                    projection.authority_epoch,
                    projection.owner_subject_id,
                    projection.checkpoint,
                    digest,
                    "owner-truth-search-document-projection-v1",
                ),
            )
            cursor.execute(
                """
                DELETE FROM owner_truth.search_documents
                WHERE vault_id = %s AND authority_epoch = %s
                """,
                (projection.vault_id, projection.authority_epoch),
            )
            for document in projection.documents:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.search_documents (
                        vault_id, authority_epoch, memory_id, memory_version_id,
                        content_hash, memory_kind, perspective_type, sensitivity,
                        search_text, structured_terms, text_was_truncated
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    self._adapt_params(
                        (
                            document.vault_id,
                            document.authority_epoch,
                            document.memory_id,
                            document.memory_version_id,
                            document.content_hash,
                            document.memory_kind,
                            document.perspective_type,
                            document.sensitivity,
                            document.search_text,
                            list(document.structured_terms),
                            document.text_was_truncated,
                        )
                    ),
                )
            cursor.execute(
                """
                UPDATE owner_truth.search_document_checkpoints
                SET state = 'ready',
                    document_count = %s,
                    document_hash = %s,
                    updated_at = NOW()
                WHERE vault_id = %s AND authority_epoch = %s
                """,
                (
                    len(projection.documents),
                    digest,
                    projection.vault_id,
                    projection.authority_epoch,
                ),
            )
        return OwnerTruthSearchDocumentProjectionRebuildResult(
            outcome=outcome,
            projection=projection,
        )

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthSearchDocumentProjection | None:
        _assert_owner_context(context)
        current = self._current_projection(context=context)
        if current is None:
            return None
        with self._cursor() as cursor:
            self._assert_active_vault(
                cursor,
                context=context,
                authority_epoch=current.authority_epoch,
                lock=False,
            )
            cursor.execute(
                """
                SELECT owner_subject_id, state, source_projection_checkpoint,
                    document_count, document_hash, schema_version
                FROM owner_truth.search_document_checkpoints
                WHERE vault_id = %s AND authority_epoch = %s
                """,
                (current.vault_id, current.authority_epoch),
            )
            checkpoint = cursor.fetchone()
            if (
                checkpoint is None
                or str(checkpoint["owner_subject_id"]) != current.owner_subject_id
                or str(checkpoint["state"]) != "ready"
                or str(checkpoint["source_projection_checkpoint"]) != current.checkpoint
                or int(checkpoint["document_count"]) != len(current.documents)
                or str(checkpoint["document_hash"]) != current.document_digest()
                or str(checkpoint["schema_version"])
                != "owner-truth-search-document-projection-v1"
            ):
                return None
            stored = self._stored_projection(cursor, projection=current)
        if stored is None:
            return None
        if stored.document_digest() != current.document_digest():
            return None
        return stored

    def _stored_projection(
        self,
        cursor: Any,
        *,
        projection: OwnerTruthSearchDocumentProjection,
    ) -> OwnerTruthSearchDocumentProjection | None:
        cursor.execute(
            """
            SELECT memory_id, memory_version_id, content_hash, memory_kind,
                perspective_type, sensitivity, search_text, structured_terms,
                text_was_truncated
            FROM owner_truth.search_documents
            WHERE vault_id = %s AND authority_epoch = %s
            ORDER BY memory_version_id ASC
            """,
            (projection.vault_id, projection.authority_epoch),
        )
        try:
            return OwnerTruthSearchDocumentProjection(
                vault_id=projection.vault_id,
                owner_subject_id=projection.owner_subject_id,
                authority_epoch=projection.authority_epoch,
                checkpoint=projection.checkpoint,
                documents=tuple(
                    self._document_from_row(
                        row,
                        vault_id=projection.vault_id,
                        owner_subject_id=projection.owner_subject_id,
                        authority_epoch=projection.authority_epoch,
                    )
                    for row in cursor.fetchall()
                ),
            )
        except OwnerTruthSearchDocumentProjectionError:
            return None

    def _current_projection(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthSearchDocumentProjection | None:
        try:
            source = self._memory_projection_repository.read(context=context)
        except OwnerTruthCandidateReviewAccessDenied as error:
            raise OwnerTruthMemorySearchProjectionAccessDenied(str(error)) from error
        return build_owner_truth_search_document_projection(memory_projection=source)

    @staticmethod
    def _document_from_row(
        row: Mapping[str, Any],
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int,
    ) -> OwnerTruthSearchDocument:
        return OwnerTruthSearchDocument(
            memory_id=str(row["memory_id"]),
            memory_version_id=str(row["memory_version_id"]),
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=authority_epoch,
            content_hash=str(row["content_hash"]),
            memory_kind=str(row["memory_kind"]),
            perspective_type=str(row["perspective_type"]),
            sensitivity=str(row["sensitivity"]),
            search_text=str(row["search_text"]),
            structured_terms=PostgresOwnerTruthMemorySearchDocumentProjectionRepository._json_terms(
                row["structured_terms"]
            ),
            text_was_truncated=bool(row["text_was_truncated"]),
        )

    def _assert_active_vault(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
        lock: bool,
    ) -> None:
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, status
            FROM owner_truth.vaults
            WHERE vault_id = %s
            """ + ("FOR SHARE" if lock else ""),
            (context.vault_id,),
        )
        vault = cursor.fetchone()
        if (
            vault is None
            or str(vault["owner_subject_id"]) != context.owner_subject_id
            or int(vault["authority_epoch"]) != authority_epoch
            or str(vault["status"]) != "active"
        ):
            raise OwnerTruthMemorySearchProjectionAccessDenied(
                "Vault is not active for this Owner SearchDocument projection"
            )

    @staticmethod
    def _json_terms(value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise OwnerTruthSearchDocumentProjectionError(
                    "search document structured_terms is not valid JSON"
                ) from exc
        if not isinstance(value, list):
            raise OwnerTruthSearchDocumentProjectionError(
                "search document structured_terms must be a JSON array"
            )
        return tuple(value)

    @staticmethod
    def _adapt_params(values: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            return tuple(
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                if isinstance(value, (Mapping, list))
                else value
                for value in values
            )
        return tuple(
            Jsonb(value) if isinstance(value, (Mapping, list)) else value
            for value in values
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthMemorySearchDocumentProjectionStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def owner_truth_memory_search_document_projection_repository(self) -> Any:
        ...


class OwnerTruthMemorySearchDocumentProjectionService:
    """Transactional service for the explicit QA SearchDocument rebuild."""

    def __init__(self, store: OwnerTruthMemorySearchDocumentProjectionStore) -> None:
        self._store = store

    def rebuild(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthSearchDocumentProjectionRebuildResult:
        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-memory-search-projection-rebuild-{context.vault_id}",
            command_id=f"ownerTruthMemorySearchProjectionRebuild:{context.vault_id}",
        ):
            return self._store.owner_truth_memory_search_document_projection_repository().rebuild(
                context=context
            )

    def _request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


__all__ = [
    "InMemoryOwnerTruthMemorySearchDocumentProjectionRepository",
    "OwnerTruthMemorySearchProjectionAccessDenied",
    "OwnerTruthMemorySearchDocumentProjectionService",
    "PostgresOwnerTruthMemorySearchDocumentProjectionRepository",
]
