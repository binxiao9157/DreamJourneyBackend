from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Tuple

from app.db.recovery import RecoveryContractError, validate_recovery_target


OWNER_ORPHAN_QUARANTINE_SCHEMA_VERSION = 1
_IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_SCHEMA_HEAD_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _redaction_key(value: Any) -> bytes:
    if not isinstance(value, bytes) or len(value) < 16:
        raise RecoveryContractError("invalidRecoveryOrphanRedactionKey")
    return value


def _sensitive_digest(payload: Any, *, redaction_key: bytes) -> str:
    """Return a deterministic private-manifest locator without persisting raw values."""

    return hmac.new(
        _redaction_key(redaction_key),
        b"dreamjourney-recovery-owner-orphan-v1\0" + _canonical_bytes(payload),
        hashlib.sha256,
    ).hexdigest()


def _identifier(value: Any, *, code: str) -> str:
    normalized = str(value or "").strip()
    if _IDENTIFIER_PATTERN.fullmatch(normalized) is None:
        raise RecoveryContractError(code)
    return normalized


def _schema_head(value: Any) -> str:
    normalized = str(value or "").strip()
    if _SCHEMA_HEAD_PATTERN.fullmatch(normalized) is None:
        raise RecoveryContractError("invalidRecoverySchemaHead")
    return normalized


@dataclass(frozen=True)
class RecoveryOwnerOrphanCandidate:
    """One in-memory orphan locator whose persisted form never exposes values."""

    schema_name: str
    table_name: str
    primary_key_columns: Tuple[str, ...]
    primary_key_values: Tuple[Any, ...] = field(repr=False)
    owner_id: Any = field(repr=False)

    def __post_init__(self) -> None:
        schema = _identifier(self.schema_name, code="invalidRecoveryOrphanSchema")
        table = _identifier(self.table_name, code="invalidRecoveryOrphanTable")
        columns = tuple(
            _identifier(column, code="invalidRecoveryOrphanPrimaryKeyColumn")
            for column in self.primary_key_columns
        )
        if not columns or len(set(columns)) != len(columns):
            raise RecoveryContractError("invalidRecoveryOrphanPrimaryKeyColumns")
        values = tuple(self.primary_key_values)
        if len(columns) != len(values):
            raise RecoveryContractError("recoveryOrphanPrimaryKeyValueMismatch")
        if self.owner_id is None:
            raise RecoveryContractError("invalidRecoveryOrphanOwner")
        object.__setattr__(self, "schema_name", schema)
        object.__setattr__(self, "table_name", table)
        object.__setattr__(self, "primary_key_columns", columns)
        object.__setattr__(self, "primary_key_values", values)

    @property
    def qualified_table_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    def value_free_summary(self, *, redaction_key: bytes) -> Dict[str, Any]:
        primary_key = {
            column: str(value)
            for column, value in zip(self.primary_key_columns, self.primary_key_values)
        }
        return {
            "primaryKeyColumns": list(self.primary_key_columns),
            "locatorDigest": _sensitive_digest(
                {
                    "schema": self.schema_name,
                    "table": self.table_name,
                    "primaryKey": primary_key,
                },
                redaction_key=redaction_key,
            ),
            "ownerDigest": _sensitive_digest(
                {"ownerId": str(self.owner_id)},
                redaction_key=redaction_key,
            ),
        }


@dataclass(frozen=True)
class RecoveryOwnerOrphanTableInventory:
    """Read-only, table-level inventory for manual quarantine/reconciliation."""

    schema_name: str
    table_name: str
    primary_key_columns: Tuple[str, ...]
    orphan_count: int
    candidates: Tuple[RecoveryOwnerOrphanCandidate, ...] = ()
    candidate_limit: int = 100

    def __post_init__(self) -> None:
        schema = _identifier(self.schema_name, code="invalidRecoveryOrphanSchema")
        table = _identifier(self.table_name, code="invalidRecoveryOrphanTable")
        columns = tuple(
            _identifier(column, code="invalidRecoveryOrphanPrimaryKeyColumn")
            for column in self.primary_key_columns
        )
        if len(set(columns)) != len(columns):
            raise RecoveryContractError("invalidRecoveryOrphanPrimaryKeyColumns")
        if isinstance(self.orphan_count, bool):
            raise RecoveryContractError("invalidRecoveryOrphanCount")
        try:
            orphan_count = int(self.orphan_count)
        except (TypeError, ValueError) as exc:
            raise RecoveryContractError("invalidRecoveryOrphanCount") from exc
        if orphan_count < 0:
            raise RecoveryContractError("invalidRecoveryOrphanCount")
        if isinstance(self.candidate_limit, bool):
            raise RecoveryContractError("invalidRecoveryOrphanCandidateLimit")
        try:
            candidate_limit = int(self.candidate_limit)
        except (TypeError, ValueError) as exc:
            raise RecoveryContractError("invalidRecoveryOrphanCandidateLimit") from exc
        if candidate_limit < 1 or candidate_limit > 1000:
            raise RecoveryContractError("invalidRecoveryOrphanCandidateLimit")
        candidates = tuple(self.candidates)
        if not columns and candidates:
            raise RecoveryContractError("recoveryOrphanCandidatesRequirePrimaryKey")
        if len(candidates) > min(orphan_count, candidate_limit):
            raise RecoveryContractError("recoveryOrphanCandidateLimitExceeded")
        for candidate in candidates:
            if (
                candidate.schema_name != schema
                or candidate.table_name != table
                or candidate.primary_key_columns != columns
            ):
                raise RecoveryContractError("recoveryOrphanCandidateTableMismatch")
        object.__setattr__(self, "schema_name", schema)
        object.__setattr__(self, "table_name", table)
        object.__setattr__(self, "primary_key_columns", columns)
        object.__setattr__(self, "orphan_count", orphan_count)
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "candidate_limit", candidate_limit)

    @property
    def qualified_table_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    @property
    def candidate_collection_status(self) -> str:
        if self.orphan_count == 0:
            return "clear"
        if not self.primary_key_columns:
            return "unlocatable"
        if len(self.candidates) < self.orphan_count:
            return "truncated"
        return "complete"

    def value_free_summary(self, *, redaction_key: bytes) -> Dict[str, Any]:
        return {
            "table": self.qualified_table_name,
            "primaryKeyColumns": list(self.primary_key_columns),
            "orphanCount": self.orphan_count,
            "candidateLimit": self.candidate_limit,
            "candidateCount": len(self.candidates),
            "candidateCollectionStatus": self.candidate_collection_status,
            "candidates": [
                candidate.value_free_summary(redaction_key=redaction_key)
                for candidate in self.candidates
            ],
        }


def build_owner_orphan_quarantine_manifest(
    *,
    target_database: str,
    production_database: str,
    schema_head: str,
    table_inventories: Iterable[RecoveryOwnerOrphanTableInventory],
    redaction_key: bytes,
) -> Dict[str, Any]:
    """Build value-free evidence for manual orphan quarantine planning.

    This is intentionally not a reconciliation engine. It rejects non-isolated
    targets and states that no automatic owner claim, deletion, or mutation is
    authorized by the manifest.
    """

    target = validate_recovery_target(target_database, production_database)
    redaction_key = _redaction_key(redaction_key)
    inventories = tuple(
        sorted(table_inventories, key=lambda inventory: inventory.qualified_table_name)
    )
    qualified_names = [inventory.qualified_table_name for inventory in inventories]
    if len(set(qualified_names)) != len(qualified_names):
        raise RecoveryContractError("duplicateRecoveryOrphanInventoryTable")
    if any(inventory.schema_name != "public" for inventory in inventories):
        raise RecoveryContractError("invalidRecoveryOrphanInventorySchema")

    orphan_count = sum(inventory.orphan_count for inventory in inventories)
    unlocatable_tables = [
        inventory.qualified_table_name
        for inventory in inventories
        if inventory.orphan_count and not inventory.primary_key_columns
    ]
    blockers = []
    if orphan_count:
        blockers.extend(("ownerOrphansPresent", "ownerOrphanQuarantineRequired"))
    if unlocatable_tables:
        blockers.append("ownerOrphanLocatorUnavailable")

    payload: Dict[str, Any] = {
        "schemaVersion": OWNER_ORPHAN_QUARANTINE_SCHEMA_VERSION,
        "status": "clear" if orphan_count == 0 else "quarantineRequired",
        "mode": "readOnlyInventory",
        "targetIsolation": "ephemeralDatabase",
        "targetDatabaseHash": hashlib.sha256(target.encode("utf-8")).hexdigest(),
        "schemaHead": _schema_head(schema_head),
        "orphanOwnerCount": orphan_count,
        "automaticMutation": False,
        "automaticOwnerClaim": False,
        "automaticDelete": False,
        "redactionMode": "hmacSha256KeyNotPersisted",
        "operatorActionMap": "notIncluded",
        "tableInventories": [
            inventory.value_free_summary(redaction_key=redaction_key)
            for inventory in inventories
        ],
        "unlocatableTables": unlocatable_tables,
        "blockers": blockers,
    }
    payload["manifestDigest"] = _canonical_hash(payload)
    return payload
