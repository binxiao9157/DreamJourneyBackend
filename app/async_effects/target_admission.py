"""Execution-time target admission for typed async-effect consumers.

The Consumer Inbox kernel deliberately does not know product aggregates. This
module adds the first typed read boundary for the existing Owner Truth Source
effect: before a future worker can write a result, it must re-check the live
vault owner, authority epoch, source version, and active state in its current
Unit of Work. It does not execute a handler or create a completion receipt.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Mapping
from uuid import UUID

from app.async_effects.contracts import AsyncEffectIntent
from app.services.owner_truth_memory_projection_effects import (
    MEMORY_PROJECTION_REBUILD_EVENT_TYPE,
    MEMORY_PROJECTION_REBUILD_JOB_TYPE,
    MEMORY_PROJECTION_REBUILD_OPERATION_TYPE,
)


_SOURCE_CREATED_OPERATION = "ownerTruth.source.created"
_SOURCE_RESOURCE_TYPE = "source"
_SOURCE_EXTRACTION_PURPOSE = "candidateExtraction"
_MEMORY_PROJECTION_RESOURCE_TYPE = "memoryVersion"
_MEMORY_PROJECTION_PURPOSE = "compatibilityProjection"


class AsyncEffectTargetAdmissionError(RuntimeError):
    """A typed target-admission caller did not supply an effect intent."""


@dataclass(frozen=True)
class AsyncEffectTargetAdmission:
    """Value-free result of an execution-time target authority recheck."""

    outcome: str
    reason_code: str
    operation_id: str
    target_stable_key: str
    authority_epoch: int | None = None
    resource_version: int | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome == "admitted"


@dataclass(frozen=True)
class _VaultSnapshot:
    owner_subject_id: str
    authority_epoch: int
    status: str


@dataclass(frozen=True)
class _SourceSnapshot:
    owner_subject_id: str
    authority_epoch: int
    source_version: int
    state: str


@dataclass(frozen=True)
class _MemoryProjectionTargetSnapshot:
    owner_subject_id: str
    authority_epoch: int
    state: str
    source_version: int
    version_number: int
    is_current: bool
    content_hash: str
    source_owner_subject_id: str | None
    source_authority_epoch: int | None
    source_state: str | None
    source_version_current: int | None


def _blocked(intent: AsyncEffectIntent, reason_code: str) -> AsyncEffectTargetAdmission:
    return AsyncEffectTargetAdmission(
        outcome="blocked",
        reason_code=reason_code,
        operation_id=intent.operation_id,
        target_stable_key=intent.stable_key,
    )


def _admitted(
    intent: AsyncEffectIntent,
    *,
    authority_epoch: int,
    resource_version: int,
) -> AsyncEffectTargetAdmission:
    return AsyncEffectTargetAdmission(
        outcome="admitted",
        reason_code="targetAuthorized",
        operation_id=intent.operation_id,
        target_stable_key=intent.stable_key,
        authority_epoch=authority_epoch,
        resource_version=resource_version,
    )


def _source_target_precondition(intent: AsyncEffectIntent) -> str | None:
    if intent.operation_type != _SOURCE_CREATED_OPERATION:
        return "unsupportedOperation"
    target = intent.target
    if target.resource_type != _SOURCE_RESOURCE_TYPE or target.purpose != _SOURCE_EXTRACTION_PURPOSE:
        return "unsupportedTarget"
    try:
        UUID(target.resource_id)
    except (TypeError, ValueError, AttributeError):
        return "invalidSourceTarget"
    return None


def _memory_projection_target_precondition(intent: AsyncEffectIntent) -> str | None:
    if intent.operation_type != MEMORY_PROJECTION_REBUILD_OPERATION_TYPE:
        return "unsupportedOperation"
    if (
        intent.event_type != MEMORY_PROJECTION_REBUILD_EVENT_TYPE
        or intent.job_type != MEMORY_PROJECTION_REBUILD_JOB_TYPE
    ):
        return "unsupportedEffectContract"
    target = intent.target
    if (
        target.resource_type != _MEMORY_PROJECTION_RESOURCE_TYPE
        or target.purpose != _MEMORY_PROJECTION_PURPOSE
    ):
        return "unsupportedTarget"
    try:
        UUID(target.resource_id)
    except (TypeError, ValueError, AttributeError):
        return "invalidMemoryVersionTarget"
    return None


def _evaluate_owner_truth_source(
    *,
    intent: AsyncEffectIntent,
    vault: _VaultSnapshot | None,
    source: _SourceSnapshot | None,
) -> AsyncEffectTargetAdmission:
    precondition = _source_target_precondition(intent)
    if precondition is not None:
        return _blocked(intent, precondition)
    if vault is None:
        return _blocked(intent, "vaultMissing")
    target = intent.target
    if vault.status != "active":
        return _blocked(intent, "vaultInactive")
    if vault.owner_subject_id != target.owner_subject_id:
        return _blocked(intent, "vaultOwnerMismatch")
    if vault.authority_epoch != target.authority_epoch:
        return _blocked(intent, "authorityEpochChanged")
    if source is None:
        return _blocked(intent, "sourceMissing")
    if source.state != "active":
        return _blocked(intent, "sourceInactive")
    if source.owner_subject_id != target.owner_subject_id:
        return _blocked(intent, "sourceOwnerMismatch")
    if source.authority_epoch != vault.authority_epoch:
        return _blocked(intent, "sourceAuthorityEpochChanged")
    if source.source_version != target.resource_version:
        return _blocked(intent, "sourceVersionChanged")
    return _admitted(
        intent,
        authority_epoch=vault.authority_epoch,
        resource_version=source.source_version,
    )


def _evaluate_owner_truth_memory_projection(
    *,
    intent: AsyncEffectIntent,
    vault: _VaultSnapshot | None,
    memory_version: _MemoryProjectionTargetSnapshot | None,
) -> AsyncEffectTargetAdmission:
    precondition = _memory_projection_target_precondition(intent)
    if precondition is not None:
        return _blocked(intent, precondition)
    if vault is None:
        return _blocked(intent, "vaultMissing")
    target = intent.target
    if vault.status != "active":
        return _blocked(intent, "vaultInactive")
    if vault.owner_subject_id != target.owner_subject_id:
        return _blocked(intent, "vaultOwnerMismatch")
    if vault.authority_epoch != target.authority_epoch:
        return _blocked(intent, "authorityEpochChanged")
    if memory_version is None:
        return _blocked(intent, "memoryVersionMissing")
    if memory_version.state != "active":
        return _blocked(intent, "memoryInactive")
    if memory_version.owner_subject_id != target.owner_subject_id:
        return _blocked(intent, "memoryOwnerMismatch")
    if memory_version.authority_epoch != vault.authority_epoch:
        return _blocked(intent, "memoryAuthorityEpochChanged")
    if not memory_version.is_current:
        return _blocked(intent, "memoryVersionNotCurrent")
    if memory_version.version_number != target.resource_version:
        return _blocked(intent, "memoryVersionChanged")
    if memory_version.content_hash != intent.payload_hash:
        return _blocked(intent, "memoryContentHashChanged")
    if memory_version.source_state is None:
        return _blocked(intent, "sourceMissing")
    if memory_version.source_state != "active":
        return _blocked(intent, "sourceInactive")
    if memory_version.source_owner_subject_id != target.owner_subject_id:
        return _blocked(intent, "sourceOwnerMismatch")
    if memory_version.source_authority_epoch != vault.authority_epoch:
        return _blocked(intent, "sourceAuthorityEpochChanged")
    if memory_version.source_version_current != memory_version.source_version:
        return _blocked(intent, "sourceVersionChanged")
    return _admitted(
        intent,
        authority_epoch=vault.authority_epoch,
        resource_version=memory_version.version_number,
    )


class InMemoryOwnerTruthSourceTargetAdmissionRepository:
    """G0 semantic double for Owner Truth Source execution-time admission."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._vaults: dict[str, _VaultSnapshot] = {}
        self._sources: dict[tuple[str, str], _SourceSnapshot] = {}

    def seed_vault(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int,
        status: str,
    ) -> None:
        with self._lock:
            self._vaults[str(vault_id)] = _VaultSnapshot(
                owner_subject_id=str(owner_subject_id),
                authority_epoch=int(authority_epoch),
                status=str(status),
            )

    def seed_source(
        self,
        *,
        vault_id: str,
        source_id: str,
        owner_subject_id: str,
        authority_epoch: int,
        source_version: int,
        state: str,
    ) -> None:
        with self._lock:
            self._sources[(str(vault_id), str(source_id))] = _SourceSnapshot(
                owner_subject_id=str(owner_subject_id),
                authority_epoch=int(authority_epoch),
                source_version=int(source_version),
                state=str(state),
            )

    def admit_owner_truth_source(self, intent: AsyncEffectIntent) -> AsyncEffectTargetAdmission:
        if not isinstance(intent, AsyncEffectIntent):
            raise AsyncEffectTargetAdmissionError("async effect intent is required")
        with self._lock:
            vault = self._vaults.get(intent.target.vault_id)
            source = self._sources.get((intent.target.vault_id, intent.target.resource_id))
        return _evaluate_owner_truth_source(intent=intent, vault=vault, source=source)


class InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository:
    """G0 semantic double for active MemoryVersion effect admission."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._vaults: dict[str, _VaultSnapshot] = {}
        self._memory_versions: dict[tuple[str, str], _MemoryProjectionTargetSnapshot] = {}

    def seed_vault(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int,
        status: str,
    ) -> None:
        with self._lock:
            self._vaults[str(vault_id)] = _VaultSnapshot(
                owner_subject_id=str(owner_subject_id),
                authority_epoch=int(authority_epoch),
                status=str(status),
            )

    def seed_memory_version(
        self,
        *,
        vault_id: str,
        memory_version_id: str,
        owner_subject_id: str,
        authority_epoch: int,
        state: str,
        source_version: int,
        version_number: int,
        is_current: bool,
        content_hash: str,
        source_owner_subject_id: str | None,
        source_authority_epoch: int | None,
        source_state: str | None,
        source_version_current: int | None,
    ) -> None:
        with self._lock:
            self._memory_versions[(str(vault_id), str(memory_version_id))] = (
                _MemoryProjectionTargetSnapshot(
                    owner_subject_id=str(owner_subject_id),
                    authority_epoch=int(authority_epoch),
                    state=str(state),
                    source_version=int(source_version),
                    version_number=int(version_number),
                    is_current=bool(is_current),
                    content_hash=str(content_hash),
                    source_owner_subject_id=(
                        None if source_owner_subject_id is None else str(source_owner_subject_id)
                    ),
                    source_authority_epoch=(
                        None if source_authority_epoch is None else int(source_authority_epoch)
                    ),
                    source_state=None if source_state is None else str(source_state),
                    source_version_current=(
                        None if source_version_current is None else int(source_version_current)
                    ),
                )
            )

    def admit_owner_truth_memory_projection(
        self,
        intent: AsyncEffectIntent,
    ) -> AsyncEffectTargetAdmission:
        if not isinstance(intent, AsyncEffectIntent):
            raise AsyncEffectTargetAdmissionError("async effect intent is required")
        with self._lock:
            vault = self._vaults.get(intent.target.vault_id)
            memory_version = self._memory_versions.get(
                (intent.target.vault_id, intent.target.resource_id)
            )
        return _evaluate_owner_truth_memory_projection(
            intent=intent,
            vault=vault,
            memory_version=memory_version,
        )

class PostgresOwnerTruthSourceTargetAdmissionRepository:
    """Owner Truth Source target admission bound to the active Postgres UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def admit_owner_truth_source(self, intent: AsyncEffectIntent) -> AsyncEffectTargetAdmission:
        if not isinstance(intent, AsyncEffectIntent):
            raise AsyncEffectTargetAdmissionError("async effect intent is required")
        precondition = _source_target_precondition(intent)
        if precondition is not None:
            return _blocked(intent, precondition)
        source_id = str(UUID(intent.target.resource_id))
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT owner_subject_id, authority_epoch, status
                FROM owner_truth.vaults
                WHERE vault_id = %s
                FOR SHARE
                """,
                (intent.target.vault_id,),
            )
            vault_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT owner_subject_id, authority_epoch, source_version, state
                FROM owner_truth.sources
                WHERE vault_id = %s AND id = %s
                FOR SHARE
                """,
                (intent.target.vault_id, source_id),
            )
            source_row = cursor.fetchone()
        vault = None
        if vault_row is not None:
            vault = _VaultSnapshot(
                owner_subject_id=str(vault_row["owner_subject_id"]),
                authority_epoch=int(vault_row["authority_epoch"]),
                status=str(vault_row["status"]),
            )
        source = None
        if source_row is not None:
            source = _SourceSnapshot(
                owner_subject_id=str(source_row["owner_subject_id"]),
                authority_epoch=int(source_row["authority_epoch"]),
                source_version=int(source_row["source_version"]),
                state=str(source_row["state"]),
            )
        return _evaluate_owner_truth_source(intent=intent, vault=vault, source=source)

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class PostgresOwnerTruthMemoryProjectionTargetAdmissionRepository:
    """Live MemoryVersion admission bound to the active Postgres Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def admit_owner_truth_memory_projection(
        self,
        intent: AsyncEffectIntent,
    ) -> AsyncEffectTargetAdmission:
        if not isinstance(intent, AsyncEffectIntent):
            raise AsyncEffectTargetAdmissionError("async effect intent is required")
        precondition = _memory_projection_target_precondition(intent)
        if precondition is not None:
            return _blocked(intent, precondition)
        memory_version_id = str(UUID(intent.target.resource_id))
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT owner_subject_id, authority_epoch, status
                FROM owner_truth.vaults
                WHERE vault_id = %s
                FOR SHARE
                """,
                (intent.target.vault_id,),
            )
            vault_row = cursor.fetchone()
            cursor.execute(
                """
                SELECT
                    memory.owner_subject_id AS memory_owner_subject_id,
                    memory.authority_epoch AS memory_authority_epoch,
                    memory.status AS memory_status,
                    version.source_id AS version_source_id,
                    version.source_version AS version_source_version,
                    version.version_number AS version_number,
                    version.is_current AS is_current,
                    version.content_hash AS content_hash
                FROM owner_truth.memory_versions AS version
                JOIN owner_truth.memories AS memory
                  ON memory.vault_id = version.vault_id
                 AND memory.id = version.memory_id
                WHERE version.vault_id = %s AND version.id = %s
                FOR SHARE OF version, memory
                """,
                (intent.target.vault_id, memory_version_id),
            )
            memory_row = cursor.fetchone()
            source_row = None
            if memory_row is not None and memory_row["version_source_id"] is not None:
                cursor.execute(
                    """
                    SELECT owner_subject_id, authority_epoch, state, source_version
                    FROM owner_truth.sources
                    WHERE vault_id = %s AND id = %s
                    FOR SHARE
                    """,
                    (intent.target.vault_id, memory_row["version_source_id"]),
                )
                source_row = cursor.fetchone()
        vault = None
        if vault_row is not None:
            vault = _VaultSnapshot(
                owner_subject_id=str(vault_row["owner_subject_id"]),
                authority_epoch=int(vault_row["authority_epoch"]),
                status=str(vault_row["status"]),
            )
        memory_version = None
        if memory_row is not None:
            memory_version = _MemoryProjectionTargetSnapshot(
                owner_subject_id=str(memory_row["memory_owner_subject_id"]),
                authority_epoch=int(memory_row["memory_authority_epoch"]),
                state=str(memory_row["memory_status"]),
                source_version=int(memory_row["version_source_version"]),
                version_number=int(memory_row["version_number"]),
                is_current=bool(memory_row["is_current"]),
                content_hash=str(memory_row["content_hash"]),
                source_owner_subject_id=(
                    None
                    if source_row is None
                    else str(source_row["owner_subject_id"])
                ),
                source_authority_epoch=(
                    None
                    if source_row is None
                    else int(source_row["authority_epoch"])
                ),
                source_state=(
                    None if source_row is None else str(source_row["state"])
                ),
                source_version_current=(
                    None
                    if source_row is None
                    else int(source_row["source_version"])
                ),
            )
        return _evaluate_owner_truth_memory_projection(
            intent=intent,
            vault=vault,
            memory_version=memory_version,
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "AsyncEffectTargetAdmission",
    "AsyncEffectTargetAdmissionError",
    "InMemoryOwnerTruthMemoryProjectionTargetAdmissionRepository",
    "InMemoryOwnerTruthSourceTargetAdmissionRepository",
    "PostgresOwnerTruthMemoryProjectionTargetAdmissionRepository",
    "PostgresOwnerTruthSourceTargetAdmissionRepository",
]
