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


_SOURCE_CREATED_OPERATION = "ownerTruth.source.created"
_SOURCE_RESOURCE_TYPE = "source"
_SOURCE_EXTRACTION_PURPOSE = "candidateExtraction"


class AsyncEffectTargetAdmissionError(RuntimeError):
    """A typed target-admission caller did not supply an effect intent."""


@dataclass(frozen=True)
class AsyncEffectTargetAdmission:
    """Value-free result of an execution-time target authority recheck."""

    outcome: str
    reason_code: str
    operation_id: str
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


def _blocked(intent: AsyncEffectIntent, reason_code: str) -> AsyncEffectTargetAdmission:
    return AsyncEffectTargetAdmission(
        outcome="blocked",
        reason_code=reason_code,
        operation_id=intent.operation_id,
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


__all__ = [
    "AsyncEffectTargetAdmission",
    "AsyncEffectTargetAdmissionError",
    "InMemoryOwnerTruthSourceTargetAdmissionRepository",
    "PostgresOwnerTruthSourceTargetAdmissionRepository",
]
