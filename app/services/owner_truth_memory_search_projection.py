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
from app.domain.owner_truth.memory_projection import (
    OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION,
    OWNER_TRUTH_MEMORY_PROJECTION_SOURCE,
)
from app.domain.owner_truth.search_documents import (
    OWNER_TRUTH_SEARCH_DOCUMENT_PROJECTION_SCHEMA_VERSION,
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
                == OWNER_TRUTH_SEARCH_DOCUMENT_PROJECTION_SCHEMA_VERSION
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
                    OWNER_TRUTH_SEARCH_DOCUMENT_PROJECTION_SCHEMA_VERSION,
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
                        content_hash, content_schema_version, memory_kind,
                        perspective_type, sensitivity, search_text,
                        structured_terms, text_was_truncated
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    self._adapt_params(
                        (
                            document.vault_id,
                            document.authority_epoch,
                            document.memory_id,
                            document.memory_version_id,
                            document.content_hash,
                            document.content_schema_version,
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
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    vault.authority_epoch,
                    search_checkpoint.owner_subject_id,
                    search_checkpoint.state,
                    search_checkpoint.source_projection_checkpoint,
                    search_checkpoint.document_count,
                    search_checkpoint.document_hash,
                    search_checkpoint.schema_version,
                    memory_checkpoint.owner_subject_id AS memory_owner_subject_id,
                    memory_checkpoint.projection_source,
                    memory_checkpoint.state AS memory_state,
                    memory_checkpoint.projection_hash,
                    memory_checkpoint.schema_version AS memory_schema_version,
                    memory_checkpoint.memory_revision,
                    memory_revision.revision AS current_memory_revision
                FROM owner_truth.vaults AS vault
                JOIN owner_truth.memory_projection_checkpoints AS memory_checkpoint
                  ON memory_checkpoint.vault_id = vault.vault_id
                 AND memory_checkpoint.authority_epoch = vault.authority_epoch
                JOIN owner_truth.memory_revisions AS memory_revision
                  ON memory_revision.vault_id = vault.vault_id
                JOIN owner_truth.search_document_checkpoints AS search_checkpoint
                  ON search_checkpoint.vault_id = vault.vault_id
                 AND search_checkpoint.authority_epoch = vault.authority_epoch
                WHERE vault.vault_id = %s
                  AND vault.owner_subject_id = %s
                  AND vault.status = 'active'
                FOR SHARE OF vault, memory_checkpoint, memory_revision, search_checkpoint
                """,
                (context.vault_id, context.owner_subject_id),
            )
            checkpoint = cursor.fetchone()
            if (
                checkpoint is None
                or str(checkpoint["owner_subject_id"]) != context.owner_subject_id
                or str(checkpoint["state"]) != "ready"
                or str(checkpoint["memory_owner_subject_id"]) != context.owner_subject_id
                or str(checkpoint["projection_source"])
                != OWNER_TRUTH_MEMORY_PROJECTION_SOURCE
                or str(checkpoint["memory_state"]) != "ready"
                or str(checkpoint["source_projection_checkpoint"])
                != str(checkpoint["projection_hash"])
                or int(checkpoint["memory_revision"])
                != int(checkpoint["current_memory_revision"])
                or str(checkpoint["schema_version"])
                != OWNER_TRUTH_SEARCH_DOCUMENT_PROJECTION_SCHEMA_VERSION
                or str(checkpoint["memory_schema_version"])
                != OWNER_TRUTH_MEMORY_PROJECTION_SCHEMA_VERSION
            ):
                return None
            persisted = OwnerTruthSearchDocumentProjection(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=int(checkpoint["authority_epoch"]),
                checkpoint=str(checkpoint["projection_hash"]),
                documents=(),
            )
            stored = self._stored_projection(cursor, projection=persisted)
        if stored is None:
            return None
        if (
            len(stored.documents) != int(checkpoint["document_count"])
            or stored.document_digest() != str(checkpoint["document_hash"])
        ):
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
            SELECT memory_id, memory_version_id, content_hash,
                content_schema_version, memory_kind, perspective_type,
                sensitivity, search_text, structured_terms, text_was_truncated
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
            content_schema_version=str(row["content_schema_version"]),
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
