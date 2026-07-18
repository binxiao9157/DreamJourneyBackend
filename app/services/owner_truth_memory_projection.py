"""Owner Truth MemoryVersion projection rebuild/read service.

This is intentionally a default-off shadow read model.  It does not replace
legacy KBLite writes, `/context/build`, or any public route in this slice.  A
checkpoint is served only when it matches the current Owner Vault authority
epoch and the complete active MemoryVersion input set; otherwise reads fail
closed as ``rebuilding``.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
import json
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol

from app.domain.owner_truth.memory_projection import (
    OWNER_TRUTH_MEMORY_PROJECTION_SOURCE,
    OwnerTruthMemoryProjectionAccessDenied,
    OwnerTruthMemoryProjectionError,
    OwnerTruthMemoryProjectionInput,
    OwnerTruthMemoryProjectionResult,
    build_ready_memory_projection,
    build_rebuilding_memory_projection,
)
from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateReviewAccessDenied
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthMemoryProjectionError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthMemoryProjectionAccessDenied(
            "only the Vault Owner may read a memory projection"
        )


def _copy_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(snapshot), ensure_ascii=False, sort_keys=True))


def _payload_from_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "content": deepcopy(entry["content"]),
        "evidenceRefs": deepcopy(entry["evidenceRefs"]),
    }


class OwnerTruthMemoryProjectionStore(Protocol):
    def owner_truth_memory_projection_repository(self) -> Any:
        ...


class InMemoryOwnerTruthMemoryProjectionRepository:
    """Semantic double backed by the Candidate review repository's memory data."""

    def __init__(self, source_repository: Any) -> None:
        self._source_repository = source_repository
        self._lock = RLock()
        self._snapshots: dict[tuple[str, int], dict[str, Any]] = {}

    def rebuild(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryProjectionResult:
        _assert_owner_context(context)
        authority_epoch, inputs = self._projection_inputs(context=context)
        snapshot = build_ready_memory_projection(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=authority_epoch,
            inputs=inputs,
        )
        key = (context.vault_id, authority_epoch)
        with self._lock:
            existing = self._snapshots.get(key)
            outcome = (
                "unchanged"
                if existing is not None
                and existing.get("checkpoint") == snapshot.get("checkpoint")
                and existing.get("sourceHash") == snapshot.get("sourceHash")
                else "rebuilt"
            )
            self._snapshots[key] = _copy_snapshot(snapshot)
        return OwnerTruthMemoryProjectionResult(outcome=outcome, snapshot=snapshot)

    def read(self, *, context: OwnerTruthCommandContext) -> dict[str, Any]:
        _assert_owner_context(context)
        authority_epoch, inputs = self._projection_inputs(context=context)
        expected = build_ready_memory_projection(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=authority_epoch,
            inputs=inputs,
        )
        with self._lock:
            snapshot = self._snapshots.get((context.vault_id, authority_epoch))
            if (
                snapshot is None
                or snapshot.get("state") != "ready"
                or snapshot.get("sourceHash") != expected["sourceHash"]
                or snapshot.get("checkpoint") != expected["checkpoint"]
            ):
                return build_rebuilding_memory_projection(
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    authority_epoch=authority_epoch,
                )
            return _copy_snapshot(snapshot)

    def _projection_inputs(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[int, tuple[OwnerTruthMemoryProjectionInput, ...]]:
        supplier = getattr(self._source_repository, "list_memory_projection_inputs", None)
        if not callable(supplier):
            raise OwnerTruthMemoryProjectionError(
                "in-memory source repository does not expose MemoryVersion projection inputs"
            )
        return supplier(context=context)


class PostgresOwnerTruthMemoryProjectionRepository:
    """Postgres projection port bound to one active request/job Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def rebuild(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryProjectionResult:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            vault = self._active_vault(cursor, context=context, lock=True)
            authority_epoch = int(vault["authority_epoch"])
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-memory-projection:{context.vault_id}:{authority_epoch}",),
            )
            inputs = self._load_current_inputs(
                cursor,
                context=context,
                authority_epoch=authority_epoch,
            )
            snapshot = build_ready_memory_projection(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=authority_epoch,
                inputs=inputs,
            )
            cursor.execute(
                """
                SELECT source_hash, projection_hash
                FROM owner_truth.memory_projection_checkpoints
                WHERE vault_id = %s AND authority_epoch = %s
                FOR UPDATE
                """,
                (context.vault_id, authority_epoch),
            )
            existing = cursor.fetchone()
            outcome = (
                "unchanged"
                if existing is not None
                and str(existing["source_hash"]) == snapshot["sourceHash"]
                and str(existing["projection_hash"]) == snapshot["checkpoint"]
                else "rebuilt"
            )
            cursor.execute(
                """
                DELETE FROM owner_truth.memory_projection_entries
                WHERE vault_id = %s AND authority_epoch = %s
                """,
                (context.vault_id, authority_epoch),
            )
            for entry in snapshot["entries"]:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.memory_projection_entries (
                        vault_id, authority_epoch, memory_id, memory_version_id,
                        version_number, source_id, source_version, memory_kind,
                        perspective_type, epistemic_status, sensitivity, visibility,
                        content_schema_version, content_hash, payload
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s
                    )
                    """,
                    self._adapt_params(
                        (
                            context.vault_id,
                            authority_epoch,
                            entry["memoryId"],
                            entry["memoryVersionId"],
                            entry["memoryVersion"],
                            entry["sourceId"],
                            entry["sourceVersion"],
                            entry["memoryKind"],
                            entry["perspectiveType"],
                            entry["epistemicStatus"],
                            entry["sensitivity"],
                            entry["visibility"],
                            entry["contentSchemaVersion"],
                            entry["contentHash"],
                            _payload_from_entry(entry),
                        )
                    ),
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_projection_checkpoints (
                    vault_id, authority_epoch, owner_subject_id, projection_source,
                    state, entry_count, source_hash, projection_hash, schema_version,
                    updated_at
                ) VALUES (%s, %s, %s, %s, 'ready', %s, %s, %s, %s, NOW())
                ON CONFLICT (vault_id, authority_epoch) DO UPDATE SET
                    owner_subject_id = EXCLUDED.owner_subject_id,
                    projection_source = EXCLUDED.projection_source,
                    state = EXCLUDED.state,
                    entry_count = EXCLUDED.entry_count,
                    source_hash = EXCLUDED.source_hash,
                    projection_hash = EXCLUDED.projection_hash,
                    schema_version = EXCLUDED.schema_version,
                    updated_at = NOW()
                """,
                (
                    context.vault_id,
                    authority_epoch,
                    context.owner_subject_id,
                    OWNER_TRUTH_MEMORY_PROJECTION_SOURCE,
                    snapshot["entryCount"],
                    snapshot["sourceHash"],
                    snapshot["checkpoint"],
                    snapshot["schemaVersion"],
                ),
            )
        return OwnerTruthMemoryProjectionResult(outcome=outcome, snapshot=snapshot)

    def read(self, *, context: OwnerTruthCommandContext) -> dict[str, Any]:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            vault = self._active_vault(cursor, context=context, lock=False)
            authority_epoch = int(vault["authority_epoch"])
            cursor.execute(
                """
                SELECT owner_subject_id, projection_source, state, entry_count,
                    source_hash, projection_hash, schema_version
                FROM owner_truth.memory_projection_checkpoints
                WHERE vault_id = %s AND authority_epoch = %s
                """,
                (context.vault_id, authority_epoch),
            )
            checkpoint = cursor.fetchone()
            if (
                checkpoint is None
                or str(checkpoint["owner_subject_id"]) != context.owner_subject_id
                or str(checkpoint["projection_source"]) != OWNER_TRUTH_MEMORY_PROJECTION_SOURCE
                or str(checkpoint["state"]) != "ready"
            ):
                return build_rebuilding_memory_projection(
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    authority_epoch=authority_epoch,
                )
            inputs = self._load_current_inputs(
                cursor,
                context=context,
                authority_epoch=authority_epoch,
            )
            expected = build_ready_memory_projection(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=authority_epoch,
                inputs=inputs,
            )
            if (
                str(checkpoint["source_hash"]) != expected["sourceHash"]
                or str(checkpoint["projection_hash"]) != expected["checkpoint"]
                or int(checkpoint["entry_count"]) != expected["entryCount"]
                or str(checkpoint["schema_version"]) != expected["schemaVersion"]
            ):
                return build_rebuilding_memory_projection(
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    authority_epoch=authority_epoch,
                )
            stored_inputs = self._load_stored_inputs(
                cursor,
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=authority_epoch,
            )
            stored = build_ready_memory_projection(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=authority_epoch,
                inputs=stored_inputs,
            )
            if (
                stored["sourceHash"] != expected["sourceHash"]
                or stored["checkpoint"] != expected["checkpoint"]
                or stored["entryCount"] != expected["entryCount"]
            ):
                return build_rebuilding_memory_projection(
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    authority_epoch=authority_epoch,
                )
            return stored

    def _active_vault(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        lock: bool,
    ) -> Mapping[str, Any]:
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
            or str(vault["status"]) != "active"
        ):
            raise OwnerTruthMemoryProjectionAccessDenied("Vault is not active for this Owner")
        return vault

    def _load_current_inputs(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> tuple[OwnerTruthMemoryProjectionInput, ...]:
        cursor.execute(
            """
            SELECT
                memory.id AS memory_id,
                version.id AS memory_version_id,
                version.version_number,
                version.source_id,
                version.source_version,
                memory.memory_kind,
                memory.perspective_type,
                memory.epistemic_status,
                memory.sensitivity,
                version.schema_version AS memory_version_schema_version,
                version.content_hash,
                version.payload
            FROM owner_truth.memories AS memory
            JOIN owner_truth.memory_versions AS version
              ON version.vault_id = memory.vault_id
             AND version.memory_id = memory.id
             AND version.is_current = TRUE
            JOIN owner_truth.sources AS source
              ON source.vault_id = version.vault_id
             AND source.id = version.source_id
            WHERE memory.vault_id = %s
              AND memory.owner_subject_id = %s
              AND memory.status = 'active'
              AND memory.authority_epoch = %s
              AND source.owner_subject_id = %s
              AND source.state = 'active'
              AND source.authority_epoch = %s
              AND source.source_version = version.source_version
            ORDER BY memory.id ASC, version.version_number ASC, version.id ASC
            """,
            (
                context.vault_id,
                context.owner_subject_id,
                authority_epoch,
                context.owner_subject_id,
                authority_epoch,
            ),
        )
        return tuple(
            self._projection_input_from_memory_row(
                row,
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=authority_epoch,
            )
            for row in cursor.fetchall()
        )

    def _load_stored_inputs(
        self,
        cursor: Any,
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int,
    ) -> tuple[OwnerTruthMemoryProjectionInput, ...]:
        cursor.execute(
            """
            SELECT memory_id, memory_version_id, version_number, source_id,
                source_version, memory_kind, perspective_type, epistemic_status,
                sensitivity, content_schema_version, content_hash, payload
            FROM owner_truth.memory_projection_entries
            WHERE vault_id = %s AND authority_epoch = %s
            ORDER BY memory_id ASC, version_number ASC, memory_version_id ASC
            """,
            (vault_id, authority_epoch),
        )
        return tuple(
            self._projection_input_from_entry_row(
                row,
                vault_id=vault_id,
                owner_subject_id=owner_subject_id,
                authority_epoch=authority_epoch,
            )
            for row in cursor.fetchall()
        )

    @staticmethod
    def _projection_input_from_memory_row(
        row: Mapping[str, Any],
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int,
    ) -> OwnerTruthMemoryProjectionInput:
        payload = PostgresOwnerTruthMemoryProjectionRepository._json_object(
            row.get("payload"),
            field="MemoryVersion payload",
        )
        return OwnerTruthMemoryProjectionInput(
            memory_id=str(row["memory_id"]),
            memory_version_id=str(row["memory_version_id"]),
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=authority_epoch,
            version_number=int(row["version_number"]),
            source_id=str(row["source_id"]),
            source_version=int(row["source_version"]),
            memory_kind=str(row["memory_kind"]),
            perspective_type=str(row["perspective_type"]),
            epistemic_status=str(row["epistemic_status"]),
            sensitivity=str(row["sensitivity"]),
            content_schema_version=str(payload.get("contentSchemaVersion") or ""),
            content_hash=str(row["content_hash"]),
            content=payload.get("content"),
            evidence_refs=tuple(payload.get("evidenceRefs") or ()),
        )

    @staticmethod
    def _projection_input_from_entry_row(
        row: Mapping[str, Any],
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int,
    ) -> OwnerTruthMemoryProjectionInput:
        payload = PostgresOwnerTruthMemoryProjectionRepository._json_object(
            row.get("payload"),
            field="projection entry payload",
        )
        return OwnerTruthMemoryProjectionInput(
            memory_id=str(row["memory_id"]),
            memory_version_id=str(row["memory_version_id"]),
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=authority_epoch,
            version_number=int(row["version_number"]),
            source_id=str(row["source_id"]),
            source_version=int(row["source_version"]),
            memory_kind=str(row["memory_kind"]),
            perspective_type=str(row["perspective_type"]),
            epistemic_status=str(row["epistemic_status"]),
            sensitivity=str(row["sensitivity"]),
            content_schema_version=str(row["content_schema_version"]),
            content_hash=str(row["content_hash"]),
            content=payload.get("content"),
            evidence_refs=tuple(payload.get("evidenceRefs") or ()),
        )

    @staticmethod
    def _json_object(value: Any, *, field: str) -> dict[str, Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise OwnerTruthMemoryProjectionError(f"{field} is not valid JSON") from exc
        if not isinstance(value, Mapping):
            raise OwnerTruthMemoryProjectionError(f"{field} must be an object")
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


class OwnerTruthMemoryProjectionService:
    def __init__(self, store: OwnerTruthMemoryProjectionStore) -> None:
        self._store = store

    def rebuild(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthMemoryProjectionResult:
        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-memory-projection-rebuild-{context.vault_id}",
            command_id=f"ownerTruthMemoryProjectionRebuild:{context.vault_id}",
        ):
            try:
                return self._store.owner_truth_memory_projection_repository().rebuild(context=context)
            except OwnerTruthCandidateReviewAccessDenied as error:
                raise OwnerTruthMemoryProjectionAccessDenied(str(error)) from error

    def read(self, *, context: OwnerTruthCommandContext) -> dict[str, Any]:
        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-memory-projection-read-{context.vault_id}",
            command_id=f"ownerTruthMemoryProjectionRead:{context.vault_id}",
        ):
            try:
                return self._store.owner_truth_memory_projection_repository().read(context=context)
            except OwnerTruthCandidateReviewAccessDenied as error:
                raise OwnerTruthMemoryProjectionAccessDenied(str(error)) from error

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


__all__ = [
    "InMemoryOwnerTruthMemoryProjectionRepository",
    "OwnerTruthMemoryProjectionService",
    "PostgresOwnerTruthMemoryProjectionRepository",
]
