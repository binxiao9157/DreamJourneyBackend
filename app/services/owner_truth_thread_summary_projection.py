"""Persist a default-off, content-free Owner Truth Thread summary checkpoint.

The existing Phase 4A Thread summary builder is intentionally conservative:
only current, Owner-confirmed MemoryVersion anchors may associate independent
interview Threads.  This module makes that derived view replayable without
turning an association into a fact, title, model label, or public feature.

Every read recomputes the current source projection and compares it with the
stored checkpoint.  A changed dimension checkpoint, thread/session authority,
continuation cue, rights state, or policy input therefore fails closed until a
separate rebuild occurs.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol

from app.domain.owner_truth.candidate_decisions import OwnerTruthCandidateReviewAccessDenied
from app.domain.owner_truth.memory_projection import OwnerTruthMemoryProjectionAccessDenied
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.thread_summary import (
    OWNER_TRUTH_THREAD_SUMMARY_CHECKPOINT_SCHEMA_VERSION,
    OwnerTruthThreadSummaryError,
    OwnerTruthThreadSummaryProjection,
    ThreadSummary,
    ThreadSummaryAnchor,
    build_owner_truth_thread_summary_projection_from_summaries,
)
from app.services.owner_truth_thread_summary_read import (
    read_owner_truth_thread_summary_source,
)


class OwnerTruthThreadSummaryProjectionAccessDenied(OwnerTruthThreadSummaryError):
    """The caller cannot rebuild or read this Owner's private thread map."""


@dataclass(frozen=True)
class OwnerTruthThreadSummaryProjectionRebuildResult:
    """A value-free rebuild receipt; it never contains narrative content."""

    outcome: str
    projection: OwnerTruthThreadSummaryProjection | None

    def __post_init__(self) -> None:
        if self.outcome not in {"rebuilt", "unchanged", "sourceRebuilding"}:
            raise OwnerTruthThreadSummaryError(
                "thread summary projection rebuild outcome is not supported"
            )
        if self.outcome == "sourceRebuilding" and self.projection is not None:
            raise OwnerTruthThreadSummaryError(
                "sourceRebuilding must not retain a thread summary projection"
            )
        if self.outcome != "sourceRebuilding" and self.projection is None:
            raise OwnerTruthThreadSummaryError(
                "rebuilt thread summary projection requires a projection"
            )

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "status": self.outcome,
            "schemaVersion": OWNER_TRUTH_THREAD_SUMMARY_CHECKPOINT_SCHEMA_VERSION,
        }
        if self.projection is not None:
            summary.update(
                {
                    "authorityEpoch": self.projection.authority_epoch,
                    "checkpoint": self.projection.checkpoint,
                    "inputDigest": self.projection.input_digest,
                    "threadCount": len(self.projection.summaries),
                    "associationCount": len(self.projection.associations),
                }
            )
        return summary


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthThreadSummaryError("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthThreadSummaryProjectionAccessDenied(
            "only the Vault Owner may access a Thread summary projection"
        )


def _replay_stored_projection(
    projection: OwnerTruthThreadSummaryProjection,
) -> OwnerTruthThreadSummaryProjection | None:
    """Recompute the persisted value-free material before it is reused.

    The in-memory double must fail closed just like Postgres when a stored
    thread/anchor record no longer matches its declared digest.  Returning the
    reconstructed instance also prevents a persisted association from ever
    becoming an independent source of truth.
    """

    try:
        replayed = build_owner_truth_thread_summary_projection_from_summaries(
            owner_subject_id=projection.owner_subject_id,
            vault_id=projection.vault_id,
            authority_epoch=projection.authority_epoch,
            source_dimension_checkpoint=projection.source_dimension_checkpoint,
            policy_version=projection.policy_version,
            summaries=projection.summaries,
            filtered_stale_cue_count=projection.filtered_stale_cue_count,
        )
    except OwnerTruthThreadSummaryError:
        return None
    if (
        replayed.checkpoint != projection.checkpoint
        or replayed.input_digest != projection.input_digest
        or replayed.associations != projection.associations
    ):
        return None
    return replayed


class InMemoryOwnerTruthThreadSummaryProjectionRepository:
    """Semantic double for a checkpoint-bound Thread summary read model."""

    def __init__(
        self,
        *,
        memory_projection_repository: Any,
        confirmation_repository: Any,
        conversation_repository: Any,
        continuation_cue_repository: Any,
    ) -> None:
        self._memory_projection_repository = memory_projection_repository
        self._confirmation_repository = confirmation_repository
        self._conversation_repository = conversation_repository
        self._continuation_cue_repository = continuation_cue_repository
        self._lock = RLock()
        self._projections: dict[
            tuple[str, int],
            OwnerTruthThreadSummaryProjection,
        ] = {}

    def rebuild(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthThreadSummaryProjectionRebuildResult:
        _assert_owner_context(context)
        with self._lock:
            projection = self._current_projection(context=context)
            if projection is None:
                return OwnerTruthThreadSummaryProjectionRebuildResult(
                    outcome="sourceRebuilding",
                    projection=None,
                )
            key = (projection.vault_id, projection.authority_epoch)
            existing = self._projections.get(key)
            outcome = (
                "unchanged"
                if existing is not None
                and existing.checkpoint == projection.checkpoint
                and existing.input_digest == projection.input_digest
                and existing.source_dimension_checkpoint
                == projection.source_dimension_checkpoint
                else "rebuilt"
            )
            self._projections[key] = projection
        return OwnerTruthThreadSummaryProjectionRebuildResult(
            outcome=outcome,
            projection=projection,
        )

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthThreadSummaryProjection | None:
        _assert_owner_context(context)
        current = self._current_projection(context=context)
        if current is None:
            return None
        with self._lock:
            stored = self._projections.get((current.vault_id, current.authority_epoch))
        replayed = _replay_stored_projection(stored) if stored is not None else None
        if (
            replayed is None
            or replayed.checkpoint != current.checkpoint
            or replayed.input_digest != current.input_digest
            or replayed.source_dimension_checkpoint != current.source_dimension_checkpoint
            or replayed.policy_version != current.policy_version
        ):
            return None
        return replayed

    def _current_projection(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthThreadSummaryProjection | None:
        result = read_owner_truth_thread_summary_source(
            context=context,
            memory_projection_repository=self._memory_projection_repository,
            confirmation_repository=self._confirmation_repository,
            conversation_repository=self._conversation_repository,
            continuation_cue_repository=self._continuation_cue_repository,
        )
        return result.projection


class PostgresOwnerTruthThreadSummaryProjectionRepository:
    """Postgres Thread summary projector bound to one active Unit of Work."""

    def __init__(
        self,
        connection: Any,
        *,
        memory_projection_repository: Any,
        confirmation_repository: Any,
        conversation_repository: Any,
        continuation_cue_repository: Any,
    ) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection
        self._memory_projection_repository = memory_projection_repository
        self._confirmation_repository = confirmation_repository
        self._conversation_repository = conversation_repository
        self._continuation_cue_repository = continuation_cue_repository

    def rebuild(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthThreadSummaryProjectionRebuildResult:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            # Serialize all checkpoint writers for a Vault before the source
            # read. The source can still advance in another transaction, but
            # any resulting checkpoint is verified on every read and never
            # silently reused once its source digest changes.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-thread-summary-projection:{context.vault_id}",),
            )
            projection = self._current_projection(context=context)
            if projection is None:
                return OwnerTruthThreadSummaryProjectionRebuildResult(
                    outcome="sourceRebuilding",
                    projection=None,
                )
            self._assert_active_vault(
                cursor,
                context=context,
                authority_epoch=projection.authority_epoch,
                lock=True,
            )
            cursor.execute(
                """
                SELECT vault_id, authority_epoch, owner_subject_id, state, source_dimension_checkpoint,
                    input_digest, projection_hash, policy_version, thread_count,
                    association_count, filtered_stale_cue_count, schema_version
                FROM owner_truth.thread_summary_projection_checkpoints
                WHERE vault_id = %s AND authority_epoch = %s
                FOR UPDATE
                """,
                (projection.vault_id, projection.authority_epoch),
            )
            checkpoint = cursor.fetchone()
            stored = (
                self._stored_projection(cursor, checkpoint=checkpoint)
                if checkpoint is not None
                else None
            )
            outcome = (
                "unchanged"
                if self._checkpoint_matches_projection(checkpoint, projection)
                and stored is not None
                and stored.checkpoint == projection.checkpoint
                and stored.input_digest == projection.input_digest
                else "rebuilt"
            )
            if outcome == "unchanged":
                return OwnerTruthThreadSummaryProjectionRebuildResult(
                    outcome=outcome,
                    projection=projection,
                )

            cursor.execute(
                """
                INSERT INTO owner_truth.thread_summary_projection_checkpoints (
                    vault_id, authority_epoch, owner_subject_id, state,
                    source_dimension_checkpoint, input_digest, projection_hash,
                    policy_version, thread_count, association_count,
                    filtered_stale_cue_count, schema_version, updated_at
                ) VALUES (%s, %s, %s, 'rebuilding', %s, %s, %s, %s, 0, 0, 0, %s, NOW())
                ON CONFLICT (vault_id, authority_epoch) DO UPDATE SET
                    owner_subject_id = EXCLUDED.owner_subject_id,
                    state = EXCLUDED.state,
                    source_dimension_checkpoint = EXCLUDED.source_dimension_checkpoint,
                    input_digest = EXCLUDED.input_digest,
                    projection_hash = EXCLUDED.projection_hash,
                    policy_version = EXCLUDED.policy_version,
                    thread_count = EXCLUDED.thread_count,
                    association_count = EXCLUDED.association_count,
                    filtered_stale_cue_count = EXCLUDED.filtered_stale_cue_count,
                    schema_version = EXCLUDED.schema_version,
                    updated_at = NOW()
                """,
                (
                    projection.vault_id,
                    projection.authority_epoch,
                    projection.owner_subject_id,
                    projection.source_dimension_checkpoint,
                    projection.input_digest,
                    projection.checkpoint,
                    projection.policy_version,
                    OWNER_TRUTH_THREAD_SUMMARY_CHECKPOINT_SCHEMA_VERSION,
                ),
            )
            cursor.execute(
                """
                DELETE FROM owner_truth.thread_summary_projection_threads
                WHERE vault_id = %s AND authority_epoch = %s
                """,
                (projection.vault_id, projection.authority_epoch),
            )
            for summary in projection.summaries:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.thread_summary_projection_threads (
                        vault_id, authority_epoch, thread_id, session_id,
                        thread_state, session_state, session_boundary
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        projection.vault_id,
                        projection.authority_epoch,
                        summary.thread_id,
                        summary.session_id,
                        summary.thread_state,
                        summary.session_state,
                        summary.session_boundary,
                    ),
                )
                for anchor in summary.anchors:
                    cursor.execute(
                        """
                        INSERT INTO owner_truth.thread_summary_projection_anchors (
                            vault_id, authority_epoch, thread_id,
                            memory_version_id, target_dimension, missing_facet
                        ) VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            projection.vault_id,
                            projection.authority_epoch,
                            summary.thread_id,
                            anchor.memory_version_id,
                            anchor.target_dimension.value,
                            anchor.missing_facet,
                        ),
                    )
            cursor.execute(
                """
                UPDATE owner_truth.thread_summary_projection_checkpoints
                SET state = 'ready',
                    thread_count = %s,
                    association_count = %s,
                    filtered_stale_cue_count = %s,
                    updated_at = NOW()
                WHERE vault_id = %s AND authority_epoch = %s
                """,
                (
                    len(projection.summaries),
                    len(projection.associations),
                    projection.filtered_stale_cue_count,
                    projection.vault_id,
                    projection.authority_epoch,
                ),
            )
        return OwnerTruthThreadSummaryProjectionRebuildResult(
            outcome=outcome,
            projection=projection,
        )

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthThreadSummaryProjection | None:
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
                SELECT vault_id, authority_epoch, owner_subject_id, state, source_dimension_checkpoint,
                    input_digest, projection_hash, policy_version, thread_count,
                    association_count, filtered_stale_cue_count, schema_version
                FROM owner_truth.thread_summary_projection_checkpoints
                WHERE vault_id = %s AND authority_epoch = %s
                """,
                (current.vault_id, current.authority_epoch),
            )
            checkpoint = cursor.fetchone()
            if not self._checkpoint_matches_projection(checkpoint, current):
                return None
            stored = self._stored_projection(cursor, checkpoint=checkpoint)
        if (
            stored is None
            or stored.checkpoint != current.checkpoint
            or stored.input_digest != current.input_digest
            or stored.source_dimension_checkpoint != current.source_dimension_checkpoint
            or stored.policy_version != current.policy_version
        ):
            return None
        return stored

    def _current_projection(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthThreadSummaryProjection | None:
        try:
            result = read_owner_truth_thread_summary_source(
                context=context,
                memory_projection_repository=self._memory_projection_repository,
                confirmation_repository=self._confirmation_repository,
                conversation_repository=self._conversation_repository,
                continuation_cue_repository=self._continuation_cue_repository,
            )
        except OwnerTruthCandidateReviewAccessDenied as error:
            raise OwnerTruthThreadSummaryProjectionAccessDenied(str(error)) from error
        return result.projection

    @staticmethod
    def _checkpoint_matches_projection(
        checkpoint: Mapping[str, Any] | None,
        projection: OwnerTruthThreadSummaryProjection,
    ) -> bool:
        return bool(
            checkpoint is not None
            and str(checkpoint.get("owner_subject_id") or "") == projection.owner_subject_id
            and str(checkpoint.get("state") or "") == "ready"
            and str(checkpoint.get("source_dimension_checkpoint") or "")
            == projection.source_dimension_checkpoint
            and str(checkpoint.get("input_digest") or "") == projection.input_digest
            and str(checkpoint.get("projection_hash") or "") == projection.checkpoint
            and str(checkpoint.get("policy_version") or "") == projection.policy_version
            and int(checkpoint.get("thread_count", -1)) == len(projection.summaries)
            and int(checkpoint.get("association_count", -1)) == len(projection.associations)
            and int(checkpoint.get("filtered_stale_cue_count", -1))
            == projection.filtered_stale_cue_count
            and str(checkpoint.get("schema_version") or "")
            == OWNER_TRUTH_THREAD_SUMMARY_CHECKPOINT_SCHEMA_VERSION
        )

    def _stored_projection(
        self,
        cursor: Any,
        *,
        checkpoint: Mapping[str, Any],
    ) -> OwnerTruthThreadSummaryProjection | None:
        if str(checkpoint.get("state") or "") != "ready":
            return None
        vault_id = str(checkpoint.get("vault_id") or "")
        authority_epoch = checkpoint.get("authority_epoch")
        if not vault_id or not isinstance(authority_epoch, int):
            return None
        cursor.execute(
            """
            SELECT thread.thread_id, thread.session_id, thread.thread_state,
                thread.session_state, thread.session_boundary,
                anchor.memory_version_id, anchor.target_dimension, anchor.missing_facet
            FROM owner_truth.thread_summary_projection_threads AS thread
            LEFT JOIN owner_truth.thread_summary_projection_anchors AS anchor
              ON anchor.vault_id = thread.vault_id
             AND anchor.authority_epoch = thread.authority_epoch
             AND anchor.thread_id = thread.thread_id
            WHERE thread.vault_id = %s AND thread.authority_epoch = %s
            ORDER BY thread.thread_id ASC, anchor.memory_version_id ASC,
                anchor.target_dimension ASC, anchor.missing_facet ASC
            """,
            (vault_id, authority_epoch),
        )
        grouped: dict[str, dict[str, Any]] = {}
        try:
            for row in cursor.fetchall():
                thread_id = str(row["thread_id"])
                item = grouped.setdefault(
                    thread_id,
                    {
                        "sessionId": str(row["session_id"]),
                        "threadState": str(row["thread_state"]),
                        "sessionState": str(row["session_state"]),
                        "sessionBoundary": str(row["session_boundary"]),
                        "anchors": [],
                    },
                )
                if row["memory_version_id"] is not None:
                    item["anchors"].append(
                        ThreadSummaryAnchor(
                            memory_version_id=str(row["memory_version_id"]),
                            target_dimension=str(row["target_dimension"]),
                            missing_facet=str(row["missing_facet"]),
                        )
                    )
            summaries = tuple(
                ThreadSummary(
                    thread_id=thread_id,
                    session_id=item["sessionId"],
                    thread_state=item["threadState"],
                    session_state=item["sessionState"],
                    session_boundary=item["sessionBoundary"],
                    anchors=tuple(item["anchors"]),
                )
                for thread_id, item in grouped.items()
            )
            projection = build_owner_truth_thread_summary_projection_from_summaries(
                owner_subject_id=str(checkpoint["owner_subject_id"]),
                vault_id=vault_id,
                authority_epoch=authority_epoch,
                policy_version=str(checkpoint["policy_version"]),
                source_dimension_checkpoint=str(checkpoint["source_dimension_checkpoint"]),
                summaries=summaries,
                filtered_stale_cue_count=int(checkpoint["filtered_stale_cue_count"]),
            )
            if (
                projection.checkpoint != str(checkpoint["projection_hash"])
                or projection.input_digest != str(checkpoint["input_digest"])
                or len(projection.summaries) != int(checkpoint["thread_count"])
                or len(projection.associations) != int(checkpoint["association_count"])
            ):
                return None
            return projection
        except (KeyError, TypeError, ValueError, OwnerTruthThreadSummaryError):
            return None

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
            raise OwnerTruthThreadSummaryProjectionAccessDenied(
                "Vault is not active for this Owner Thread summary projection"
            )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthThreadSummaryProjectionStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def owner_truth_thread_summary_projection_repository(self) -> Any:
        ...


class OwnerTruthThreadSummaryProjectionService:
    """Transaction boundary for the default-off Thread summary checkpoint."""

    def __init__(self, store: OwnerTruthThreadSummaryProjectionStore) -> None:
        self._store = store

    def rebuild(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthThreadSummaryProjectionRebuildResult:
        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-thread-summary-projection-rebuild-{context.vault_id}",
            command_id=f"ownerTruthThreadSummaryProjectionRebuild:{context.vault_id}",
        ):
            return self._store.owner_truth_thread_summary_projection_repository().rebuild(
                context=context
            )

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthThreadSummaryProjection | None:
        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-thread-summary-projection-read-{context.vault_id}",
            command_id=f"ownerTruthThreadSummaryProjectionRead:{context.vault_id}",
        ):
            return self._store.owner_truth_thread_summary_projection_repository().read(
                context=context
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


__all__ = [
    "InMemoryOwnerTruthThreadSummaryProjectionRepository",
    "OwnerTruthThreadSummaryProjectionAccessDenied",
    "OwnerTruthThreadSummaryProjectionRebuildResult",
    "OwnerTruthThreadSummaryProjectionService",
    "PostgresOwnerTruthThreadSummaryProjectionRepository",
]
