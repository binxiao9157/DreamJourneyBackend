"""Value-free Vault rights revisions used to fence Owner Truth projections.

This is an internal authority port, not a public consent or data-rights API.
It records only an opaque event hash and state transition.  Until a later
authorized ingress maps consent/data-rights events into this port, no public
route writes these rows.
"""

from __future__ import annotations

from contextlib import nullcontext
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol

from app.domain.owner_truth.projection_rights import (
    OwnerTruthProjectionRightsAccessDenied,
    OwnerTruthProjectionRightsRevisionCommand,
    OwnerTruthProjectionRightsRevisionConflict,
    OwnerTruthProjectionRightsRevisionResult,
    OwnerTruthProjectionRightsSnapshot,
    ProjectionRightsState,
    implicit_projection_rights_snapshot,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthProjectionRightsAccessDenied("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthProjectionRightsAccessDenied(
            "only the Vault Owner may change projection rights"
        )


class OwnerTruthProjectionRightsRepository(Protocol):
    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> OwnerTruthProjectionRightsSnapshot:
        ...

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthProjectionRightsRevisionCommand,
    ) -> OwnerTruthProjectionRightsRevisionResult:
        ...


class OwnerTruthProjectionRightsStore(Protocol):
    def owner_truth_projection_rights_repository(self) -> OwnerTruthProjectionRightsRepository:
        ...

    def effect_kernel_repository(self) -> Any:
        ...


class InMemoryOwnerTruthProjectionRightsRepository:
    """Thread-safe semantic double for immutable per-epoch rights revisions."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshots: dict[tuple[str, int], OwnerTruthProjectionRightsSnapshot] = {}
        self._commands: dict[
            tuple[str, int, str],
            tuple[OwnerTruthProjectionRightsRevisionCommand, OwnerTruthProjectionRightsRevisionResult],
        ] = {}

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> OwnerTruthProjectionRightsSnapshot:
        _assert_owner_context(context)
        if authority_epoch < 0:
            raise ValueError("authority_epoch must not be negative")
        key = (context.vault_id, authority_epoch)
        with self._lock:
            snapshot = self._snapshots.get(key)
            if snapshot is None:
                snapshot = implicit_projection_rights_snapshot(
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    authority_epoch=authority_epoch,
                )
                self._snapshots[key] = snapshot
            if snapshot.owner_subject_id != context.owner_subject_id:
                raise OwnerTruthProjectionRightsAccessDenied(
                    "projection rights do not belong to this Vault Owner"
                )
            return snapshot

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthProjectionRightsRevisionCommand,
    ) -> OwnerTruthProjectionRightsRevisionResult:
        _assert_owner_context(context)
        if not isinstance(command, OwnerTruthProjectionRightsRevisionCommand):
            raise TypeError("projection rights revision command is required")
        key = (context.vault_id, command.authority_epoch)
        command_key = (context.vault_id, command.authority_epoch, command.command_id)
        with self._lock:
            current = self._snapshots.get(key)
            if current is None:
                current = implicit_projection_rights_snapshot(
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    authority_epoch=command.authority_epoch,
                )
                self._snapshots[key] = current
            if current.owner_subject_id != context.owner_subject_id:
                raise OwnerTruthProjectionRightsAccessDenied(
                    "projection rights do not belong to this Vault Owner"
                )
            existing = self._commands.get(command_key)
            if existing is not None:
                existing_command, existing_result = existing
                if existing_command != command:
                    raise OwnerTruthProjectionRightsRevisionConflict(
                        "projection rights command id was reused with different meaning"
                    )
                return OwnerTruthProjectionRightsRevisionResult(
                    outcome="deduplicated",
                    snapshot=existing_result.snapshot,
                )
            if current.revision != command.expected_revision:
                raise OwnerTruthProjectionRightsRevisionConflict(
                    "projection rights revision does not match expectedRevision"
                )
            if current.state is ProjectionRightsState.REVOKED:
                raise OwnerTruthProjectionRightsRevisionConflict(
                    "revoked projection rights require a future explicit reconsent flow"
                )
            snapshot = OwnerTruthProjectionRightsSnapshot(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=command.authority_epoch,
                revision=current.revision + 1,
                state=command.state,
                event_hash=command.event_hash,
            )
            result = OwnerTruthProjectionRightsRevisionResult(
                outcome="recorded",
                snapshot=snapshot,
            )
            self._snapshots[key] = snapshot
            self._commands[command_key] = (command, result)
            return result


class PostgresOwnerTruthProjectionRightsRepository:
    """Postgres implementation bound to one active Owner Truth Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        authority_epoch: int,
    ) -> OwnerTruthProjectionRightsSnapshot:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            vault = self._active_vault(cursor, context=context, lock=False)
            if int(vault["authority_epoch"]) != authority_epoch:
                raise OwnerTruthProjectionRightsAccessDenied(
                    "projection rights authority epoch is stale"
                )
            return self._read_current(cursor, context=context, vault=vault, lock=False)

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthProjectionRightsRevisionCommand,
    ) -> OwnerTruthProjectionRightsRevisionResult:
        _assert_owner_context(context)
        if not isinstance(command, OwnerTruthProjectionRightsRevisionCommand):
            raise TypeError("projection rights revision command is required")
        with self._cursor() as cursor:
            vault = self._active_vault(cursor, context=context, lock=True)
            authority_epoch = int(vault["authority_epoch"])
            if command.authority_epoch != authority_epoch:
                raise OwnerTruthProjectionRightsRevisionConflict(
                    "projection rights authority epoch does not match current Vault"
                )
            command_hash = command.command_id_hash
            cursor.execute(
                """
                SELECT revision, rights_state, event_hash
                FROM owner_truth.projection_rights_events
                WHERE vault_id = %s
                  AND authority_epoch = %s
                  AND command_id_hash = %s
                """,
                (context.vault_id, authority_epoch, command_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    int(existing["revision"]) != command.expected_revision + 1
                    or str(existing["rights_state"]) != command.state.value
                    or str(existing["event_hash"]) != command.event_hash
                ):
                    raise OwnerTruthProjectionRightsRevisionConflict(
                        "projection rights command id was reused with different meaning"
                    )
                return OwnerTruthProjectionRightsRevisionResult(
                    outcome="deduplicated",
                    snapshot=OwnerTruthProjectionRightsSnapshot(
                        vault_id=context.vault_id,
                        owner_subject_id=context.owner_subject_id,
                        authority_epoch=authority_epoch,
                        revision=int(existing["revision"]),
                        state=ProjectionRightsState(str(existing["rights_state"])),
                        event_hash=str(existing["event_hash"]),
                    ),
                )
            current = self._read_current(cursor, context=context, vault=vault, lock=True)
            if current.revision != command.expected_revision:
                raise OwnerTruthProjectionRightsRevisionConflict(
                    "projection rights revision does not match expectedRevision"
                )
            if current.state is ProjectionRightsState.REVOKED:
                raise OwnerTruthProjectionRightsRevisionConflict(
                    "revoked projection rights require a future explicit reconsent flow"
                )
            snapshot = OwnerTruthProjectionRightsSnapshot(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=authority_epoch,
                revision=current.revision + 1,
                state=command.state,
                event_hash=command.event_hash,
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.projection_rights_events (
                    vault_id, authority_epoch, revision, owner_subject_id,
                    rights_state, event_hash, command_id_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    snapshot.vault_id,
                    snapshot.authority_epoch,
                    snapshot.revision,
                    snapshot.owner_subject_id,
                    snapshot.state.value,
                    snapshot.event_hash,
                    command_hash,
                ),
            )
            return OwnerTruthProjectionRightsRevisionResult(
                outcome="recorded",
                snapshot=snapshot,
            )

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
            """ + ("FOR UPDATE" if lock else ""),
            (context.vault_id,),
        )
        vault = cursor.fetchone()
        if (
            vault is None
            or str(vault["owner_subject_id"]) != context.owner_subject_id
            or str(vault["status"]) != "active"
        ):
            raise OwnerTruthProjectionRightsAccessDenied("Vault is not active for this Owner")
        return vault

    def _read_current(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        vault: Mapping[str, Any],
        lock: bool,
    ) -> OwnerTruthProjectionRightsSnapshot:
        authority_epoch = int(vault["authority_epoch"])
        cursor.execute(
            """
            SELECT revision, rights_state, event_hash, owner_subject_id
            FROM owner_truth.projection_rights_events
            WHERE vault_id = %s AND authority_epoch = %s
            ORDER BY revision DESC
            LIMIT 1
            """ + ("FOR SHARE" if lock else ""),
            (context.vault_id, authority_epoch),
        )
        row = cursor.fetchone()
        if row is None:
            return implicit_projection_rights_snapshot(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=authority_epoch,
            )
        if str(row["owner_subject_id"]) != context.owner_subject_id:
            raise OwnerTruthProjectionRightsAccessDenied(
                "projection rights do not belong to this Vault Owner"
            )
        return OwnerTruthProjectionRightsSnapshot(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            authority_epoch=authority_epoch,
            revision=int(row["revision"]),
            state=ProjectionRightsState(str(row["rights_state"])),
            event_hash=str(row["event_hash"]),
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthProjectionRightsService:
    """Application boundary for the internal rights-event ingress."""

    def __init__(self, store: OwnerTruthProjectionRightsStore) -> None:
        self._store = store

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthProjectionRightsRevisionCommand,
    ) -> OwnerTruthProjectionRightsRevisionResult:
        _assert_owner_context(context)
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-projection-rights-{context.vault_id}",
            command_id=command.command_id_hash,
        ):
            result = self._store.owner_truth_projection_rights_repository().record(
                context=context,
                command=command,
            )
            # The intent contains only immutable rights-revision coordinates.
            # It shares this UoW so a persisted fence can never silently miss
            # the Projection rebuild request that makes it current again.
            # Import lazily: the effect kernel imports typed admission modules
            # which also consume the Projection service at application startup.
            from app.services.owner_truth_memory_projection_effects import (
                build_memory_projection_rebuild_effect_intent_for_rights_revision,
            )

            self._store.effect_kernel_repository().accept(
                build_memory_projection_rebuild_effect_intent_for_rights_revision(
                    context=context,
                    rights=result.snapshot,
                )
            )
            return result

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
    "InMemoryOwnerTruthProjectionRightsRepository",
    "OwnerTruthProjectionRightsRepository",
    "OwnerTruthProjectionRightsService",
    "OwnerTruthProjectionRightsStore",
    "PostgresOwnerTruthProjectionRightsRepository",
]
