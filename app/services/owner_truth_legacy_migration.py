"""Read-only legacy inventory and checkpoint boundary for Owner Truth.

This is deliberately the first migration slice only.  It may read legacy
Archive, KBLite and ``/memories`` rows, but persists only hashes, evidence
states and counts.  It never creates a V4 Source, Candidate, MemoryVersion or
public Context entry.  That promotion requires a later, separately gated
backfill command.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Callable, ContextManager, Mapping, Protocol, Sequence
from uuid import UUID, uuid5

from app.domain.owner_truth.legacy_migration import (
    LegacyMigrationDomain,
    LegacyMigrationEntry,
    LegacyMigrationInventory,
    LegacyMigrationRecord,
    OwnerTruthLegacyMigrationError,
    build_legacy_migration_inventory,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


OWNER_TRUTH_LEGACY_MIGRATION_SCHEMA_VERSION = "owner-truth-legacy-migration-inventory-v1"
OWNER_TRUTH_LEGACY_MIGRATION_CLASSIFIER_VERSION = "owner-truth-legacy-classifier-v1"
_RUN_NAMESPACE = UUID("2637dd3c-a1d2-4a72-8e5e-93841cd86913")
_AVAILABILITY_AVAILABLE = "available"
_AVAILABILITY_UNAVAILABLE = "unavailable"


class OwnerTruthLegacyMigrationAccessDenied(OwnerTruthLegacyMigrationError):
    """The caller is not the Owner of the requested legacy inventory."""


class OwnerTruthLegacyMigrationConflict(OwnerTruthLegacyMigrationError):
    """A stable run identity was observed with incompatible immutable rows."""


class OwnerTruthLegacyMigrationUnavailable(OwnerTruthLegacyMigrationError):
    """The default-off legacy inventory contract is not enabled for QA."""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            _json_safe(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise OwnerTruthLegacyMigrationError(
            "legacy inventory values must be JSON serializable"
        ) from exc


def _json_safe(value: object) -> object:
    """Normalize DB-native values before hashing, without retaining them."""

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthLegacyMigrationError(f"{field} is required")
    return normalized


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _row_value(row: Mapping[str, Any], *keys: str) -> object:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return value
    return None


@dataclass(frozen=True)
class LegacyMigrationCheckpoint:
    """One latest per-domain checkpoint, containing no raw legacy values."""

    domain: LegacyMigrationDomain
    availability: str
    entry_count: int
    classification_counts: Mapping[str, int]
    inventory_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", LegacyMigrationDomain(self.domain))
        availability = str(self.availability or "").strip()
        if availability not in {_AVAILABILITY_AVAILABLE, _AVAILABILITY_UNAVAILABLE}:
            raise OwnerTruthLegacyMigrationError("checkpoint availability is invalid")
        object.__setattr__(self, "availability", availability)
        if not isinstance(self.entry_count, int) or self.entry_count < 0:
            raise OwnerTruthLegacyMigrationError("checkpoint entry_count must be non-negative")
        counts = {
            str(key): int(value)
            for key, value in dict(self.classification_counts).items()
            if int(value) > 0
        }
        object.__setattr__(self, "classification_counts", dict(sorted(counts.items())))
        inventory_hash = str(self.inventory_hash or "").strip().lower()
        if len(inventory_hash) != 64 or any(
            character not in "0123456789abcdef" for character in inventory_hash
        ):
            raise OwnerTruthLegacyMigrationError("checkpoint inventory_hash must be sha256")
        object.__setattr__(self, "inventory_hash", inventory_hash)

    def summary(self) -> dict[str, object]:
        return {
            "availability": self.availability,
            "classificationCounts": dict(self.classification_counts),
            "domain": self.domain.value,
            "entryCount": self.entry_count,
            "inventoryHash": self.inventory_hash,
        }


@dataclass(frozen=True)
class OwnerTruthLegacyMigrationRun:
    outcome: str
    run_id: str
    inventory: LegacyMigrationInventory
    checkpoints: tuple[LegacyMigrationCheckpoint, ...]

    def public_summary(self) -> dict[str, object]:
        """QA-safe run summary.  Raw legacy identifiers and bodies never escape."""

        return {
            "checkpoints": [checkpoint.summary() for checkpoint in self.checkpoints],
            "inventory": self.inventory.summary(),
            "outcome": self.outcome,
            "runId": self.run_id,
            "schemaVersion": OWNER_TRUTH_LEGACY_MIGRATION_SCHEMA_VERSION,
        }


@dataclass(frozen=True)
class LegacyMigrationLegacyRows:
    """Transient raw database rows used only while calculating their hashes."""

    archive_items: Sequence[Mapping[str, Any]] = ()
    memories: Sequence[Mapping[str, Any]] = ()
    kb_snapshots: Sequence[Mapping[str, Any]] = ()
    kb_changes: Sequence[Mapping[str, Any]] = ()
    kb_receipts: Sequence[Mapping[str, Any]] = ()
    unavailable_domains: Sequence[LegacyMigrationDomain] = (
        LegacyMigrationDomain.CONVERSATION_CACHE,
    )


def _legacy_record(
    *,
    domain: LegacyMigrationDomain,
    row: Mapping[str, Any],
    canonical_owner_subject_id: str,
    fallback_identity: str,
    observed_only: bool,
) -> LegacyMigrationRecord:
    payload = _mapping(row.get("payload"))
    legacy_id = _row_value(
        row,
        "legacyId",
        "legacy_id",
        "id",
        "operationId",
        "operation_id",
        "revision",
    )
    identity = str(legacy_id or fallback_identity).strip()
    if not identity:
        raise OwnerTruthLegacyMigrationError(f"{domain.value} row is missing an identity")
    observed_owner = _row_value(
        row,
        "observedOwnerSubjectId",
        "observed_owner_subject_id",
        "ownerSubjectId",
        "owner_subject_id",
        "userId",
        "user_id",
    )
    authority_state = _row_value(row, "authorityState", "authority_state") or "active"
    # The canonical hash deliberately includes the actual row only in-process.
    # It is never serialized into the run, checkpoint, route response or logs.
    record_hash = _digest(
        {
            "domain": domain.value,
            "identity": identity,
            "payload": payload,
            # KBLite graph/mutation/receipt bodies are intentionally included
            # in the digest, but never retained after this calculation.
            "row": dict(row),
        }
    )
    return LegacyMigrationRecord(
        domain=domain,
        legacy_id=identity,
        record_hash=record_hash,
        canonical_owner_subject_id=canonical_owner_subject_id,
        observed_owner_subject_id=(None if observed_owner is None else str(observed_owner)),
        authority_state=str(authority_state),
        # Existing KBLite operation receipts demonstrate only that some legacy
        # operation ran. They are not an Owner Truth terminal decision receipt.
        source_evidence_id=None,
        decision_receipt_id=None,
        decision_is_terminal=False,
        revision_evidence_id=None,
        observed_only=observed_only,
    )


def build_inventory_from_legacy_rows(
    *,
    vault_id: str,
    owner_subject_id: str,
    classifier_version: str,
    rows: LegacyMigrationLegacyRows,
) -> LegacyMigrationInventory:
    """Hash and classify legacy rows without retaining their bodies.

    The caller provides only rows already scoped to the canonical owner.  The
    classifier still checks row-level ownership and legacy authority state so a
    quarantined/mismatched record cannot silently join the candidate stream.
    """

    canonical_owner = _nonblank(owner_subject_id, field="owner_subject_id")
    records: list[LegacyMigrationRecord] = []
    records.extend(
        _legacy_record(
            domain=LegacyMigrationDomain.ARCHIVE_ITEM,
            row=row,
            canonical_owner_subject_id=canonical_owner,
            fallback_identity=f"archive-row-{index}",
            observed_only=True,
        )
        for index, row in enumerate(rows.archive_items, start=1)
    )
    records.extend(
        _legacy_record(
            domain=LegacyMigrationDomain.MEMORY,
            row=row,
            canonical_owner_subject_id=canonical_owner,
            fallback_identity=f"memory-row-{index}",
            observed_only=False,
        )
        for index, row in enumerate(rows.memories, start=1)
    )
    records.extend(
        _legacy_record(
            domain=LegacyMigrationDomain.KB_SNAPSHOT,
            row=row,
            canonical_owner_subject_id=canonical_owner,
            fallback_identity=f"kb-snapshot-row-{index}",
            observed_only=True,
        )
        for index, row in enumerate(rows.kb_snapshots, start=1)
    )
    records.extend(
        _legacy_record(
            domain=LegacyMigrationDomain.KB_CHANGE,
            row=row,
            canonical_owner_subject_id=canonical_owner,
            fallback_identity=f"kb-change-row-{index}",
            observed_only=True,
        )
        for index, row in enumerate(rows.kb_changes, start=1)
    )
    records.extend(
        _legacy_record(
            domain=LegacyMigrationDomain.KB_RECEIPT,
            row=row,
            canonical_owner_subject_id=canonical_owner,
            fallback_identity=f"kb-receipt-row-{index}",
            observed_only=True,
        )
        for index, row in enumerate(rows.kb_receipts, start=1)
    )
    return build_legacy_migration_inventory(
        vault_id=vault_id,
        classifier_version=classifier_version,
        records=records,
        unavailable_domains=rows.unavailable_domains,
    )


def _checkpoints_for(inventory: LegacyMigrationInventory) -> tuple[LegacyMigrationCheckpoint, ...]:
    unavailable = set(inventory.unavailable_domains)
    by_domain: dict[LegacyMigrationDomain, list[LegacyMigrationEntry]] = {
        domain: [] for domain in LegacyMigrationDomain
    }
    for entry in inventory.entries:
        by_domain[entry.domain].append(entry)
    checkpoints = []
    for domain in LegacyMigrationDomain:
        entries = by_domain[domain]
        counts: dict[str, int] = {}
        for entry in entries:
            classification = entry.classification.value
            counts[classification] = counts.get(classification, 0) + 1
        checkpoints.append(
            LegacyMigrationCheckpoint(
                domain=domain,
                availability=(
                    _AVAILABILITY_UNAVAILABLE if domain in unavailable else _AVAILABILITY_AVAILABLE
                ),
                entry_count=len(entries),
                classification_counts=counts,
                inventory_hash=inventory.inventory_hash,
            )
        )
    return tuple(checkpoints)


class OwnerTruthLegacyMigrationRepository(Protocol):
    def collect(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        classifier_version: str,
    ) -> LegacyMigrationInventory:
        ...

    def persist(
        self,
        *,
        owner_subject_id: str,
        inventory: LegacyMigrationInventory,
    ) -> OwnerTruthLegacyMigrationRun:
        ...


class OwnerTruthLegacyMigrationStore(Protocol):
    def owner_truth_legacy_migration_repository(self) -> OwnerTruthLegacyMigrationRepository:
        ...


class InMemoryOwnerTruthLegacyMigrationRepository:
    """Semantic double for deterministic inventory/replay/checkpoint behavior."""

    def __init__(
        self,
        *,
        row_supplier: Callable[[str], LegacyMigrationLegacyRows],
    ) -> None:
        self._row_supplier = row_supplier
        self._lock = RLock()
        self._runs: dict[tuple[str, str, str], OwnerTruthLegacyMigrationRun] = {}
        self._checkpoints: dict[tuple[str, str, LegacyMigrationDomain], LegacyMigrationCheckpoint] = {}

    def collect(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        classifier_version: str,
    ) -> LegacyMigrationInventory:
        return build_inventory_from_legacy_rows(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            classifier_version=classifier_version,
            rows=self._row_supplier(owner_subject_id),
        )

    def persist(
        self,
        *,
        owner_subject_id: str,
        inventory: LegacyMigrationInventory,
    ) -> OwnerTruthLegacyMigrationRun:
        _nonblank(owner_subject_id, field="owner_subject_id")
        key = (inventory.vault_id, inventory.classifier_version, inventory.inventory_hash)
        checkpoints = _checkpoints_for(inventory)
        run_id = str(
            uuid5(
                _RUN_NAMESPACE,
                f"{inventory.vault_id}:{inventory.classifier_version}:{inventory.inventory_hash}",
            )
        )
        with self._lock:
            existing = self._runs.get(key)
            if existing is not None:
                if existing.inventory != inventory:
                    raise OwnerTruthLegacyMigrationConflict(
                        "identical legacy inventory hash has different immutable content"
                    )
                outcome = "deduplicated"
            else:
                existing = OwnerTruthLegacyMigrationRun(
                    outcome="created",
                    run_id=run_id,
                    inventory=inventory,
                    checkpoints=checkpoints,
                )
                self._runs[key] = existing
                outcome = "created"
            for checkpoint in checkpoints:
                self._checkpoints[(inventory.vault_id, inventory.classifier_version, checkpoint.domain)] = checkpoint
            return OwnerTruthLegacyMigrationRun(
                outcome=outcome,
                run_id=existing.run_id,
                inventory=existing.inventory,
                checkpoints=checkpoints,
            )

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "checkpointCount": len(self._checkpoints),
                "runCount": len(self._runs),
                "runs": [
                    run.public_summary()
                    for run in sorted(self._runs.values(), key=lambda item: item.run_id)
                ],
            }


class PostgresOwnerTruthLegacyMigrationRepository:
    """Read legacy rows and persist a hash-only audit run in the active UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def collect(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
        classifier_version: str,
    ) -> LegacyMigrationInventory:
        owner = _nonblank(owner_subject_id, field="owner_subject_id")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT id, user_id, owner_subject_id, authority_state, payload
                FROM archive_items
                WHERE user_id = %s
                ORDER BY id ASC
                """,
                (owner,),
            )
            archive_items = cursor.fetchall()
            cursor.execute(
                """
                SELECT id, user_id, owner_subject_id, authority_state, payload
                FROM memories
                WHERE user_id = %s
                ORDER BY id ASC
                """,
                (owner,),
            )
            memories = cursor.fetchall()
            cursor.execute(
                """
                SELECT user_id, revision, graph, updated_at
                FROM kb_snapshots
                WHERE user_id = %s
                ORDER BY revision ASC
                """,
                (owner,),
            )
            kb_snapshots = cursor.fetchall()
            cursor.execute(
                """
                SELECT user_id, revision, operation_id, graph, mutation, created_at
                FROM kb_changes
                WHERE user_id = %s
                ORDER BY revision ASC
                """,
                (owner,),
            )
            kb_changes = cursor.fetchall()
            cursor.execute(
                """
                SELECT user_id, operation_id, operation_kind, schema_version,
                    payload_hash, result, created_at
                FROM kb_operation_receipts
                WHERE user_id = %s
                ORDER BY operation_id ASC
                """,
                (owner,),
            )
            kb_receipts = cursor.fetchall()
        return build_inventory_from_legacy_rows(
            vault_id=vault_id,
            owner_subject_id=owner,
            classifier_version=classifier_version,
            rows=LegacyMigrationLegacyRows(
                archive_items=tuple(dict(row) for row in archive_items),
                memories=tuple(dict(row) for row in memories),
                kb_snapshots=tuple(dict(row) for row in kb_snapshots),
                kb_changes=tuple(dict(row) for row in kb_changes),
                kb_receipts=tuple(dict(row) for row in kb_receipts),
            ),
        )

    def persist(
        self,
        *,
        owner_subject_id: str,
        inventory: LegacyMigrationInventory,
    ) -> OwnerTruthLegacyMigrationRun:
        owner = _nonblank(owner_subject_id, field="owner_subject_id")
        checkpoints = _checkpoints_for(inventory)
        run_id = str(
            uuid5(
                _RUN_NAMESPACE,
                f"{inventory.vault_id}:{inventory.classifier_version}:{inventory.inventory_hash}",
            )
        )
        summary = inventory.summary()
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-legacy-migration:"
                    f"{inventory.vault_id}:{inventory.classifier_version}",
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.legacy_migration_runs (
                    id, vault_id, owner_subject_id, classifier_version,
                    inventory_hash, entry_count, summary
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (vault_id, classifier_version, inventory_hash) DO NOTHING
                RETURNING id
                """,
                self._adapt_params(
                    (
                        run_id,
                        inventory.vault_id,
                        owner,
                        inventory.classifier_version,
                        inventory.inventory_hash,
                        len(inventory.entries),
                        summary,
                    )
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                cursor.execute(
                    """
                    SELECT id, owner_subject_id, entry_count, summary
                    FROM owner_truth.legacy_migration_runs
                    WHERE vault_id = %s AND classifier_version = %s AND inventory_hash = %s
                    FOR SHARE
                    """,
                    (
                        inventory.vault_id,
                        inventory.classifier_version,
                        inventory.inventory_hash,
                    ),
                )
                existing = cursor.fetchone()
                if (
                    existing is None
                    or str(existing["owner_subject_id"]) != owner
                    or int(existing["entry_count"]) != len(inventory.entries)
                    or dict(existing["summary"] or {}) != summary
                ):
                    raise OwnerTruthLegacyMigrationConflict(
                        "legacy migration run replay conflicts with immutable inventory"
                    )
                persisted_run_id = str(existing["id"])
                outcome = "deduplicated"
            else:
                persisted_run_id = str(inserted["id"])
                outcome = "created"

            for entry in inventory.entries:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.legacy_migration_entries (
                        run_id, domain, legacy_id_hash, record_hash, classification,
                        disposition, owner_evidence_state, source_evidence_state,
                        decision_evidence_state, reason_code, target_state
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'notCreated')
                    ON CONFLICT (run_id, domain, legacy_id_hash) DO NOTHING
                    """,
                    (
                        persisted_run_id,
                        entry.domain.value,
                        entry.legacy_id_hash,
                        entry.record_hash,
                        entry.classification.value,
                        entry.disposition.value,
                        entry.owner_evidence_state.value,
                        entry.source_evidence_state.value,
                        entry.decision_evidence_state.value,
                        entry.reason_code,
                    ),
                )
            self._assert_entries(cursor, run_id=persisted_run_id, inventory=inventory)
            for checkpoint in checkpoints:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.legacy_migration_checkpoints (
                        vault_id, classifier_version, domain, run_id, inventory_hash,
                        availability, entry_count, classification_counts
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (vault_id, classifier_version, domain) DO UPDATE SET
                        run_id = EXCLUDED.run_id,
                        inventory_hash = EXCLUDED.inventory_hash,
                        availability = EXCLUDED.availability,
                        entry_count = EXCLUDED.entry_count,
                        classification_counts = EXCLUDED.classification_counts,
                        updated_at = NOW()
                    """,
                    self._adapt_params(
                        (
                            inventory.vault_id,
                            inventory.classifier_version,
                            checkpoint.domain.value,
                            persisted_run_id,
                            checkpoint.inventory_hash,
                            checkpoint.availability,
                            checkpoint.entry_count,
                            dict(checkpoint.classification_counts),
                        )
                    ),
                )
        return OwnerTruthLegacyMigrationRun(
            outcome=outcome,
            run_id=persisted_run_id,
            inventory=inventory,
            checkpoints=checkpoints,
        )

    @staticmethod
    def _assert_entries(
        cursor: Any,
        *,
        run_id: str,
        inventory: LegacyMigrationInventory,
    ) -> None:
        cursor.execute(
            """
            SELECT domain, legacy_id_hash, record_hash, classification, disposition,
                owner_evidence_state, source_evidence_state, decision_evidence_state,
                reason_code, target_state
            FROM owner_truth.legacy_migration_entries
            WHERE run_id = %s
            ORDER BY domain ASC, legacy_id_hash ASC
            """,
            (run_id,),
        )
        expected = [
            {
                "domain": entry.domain.value,
                "legacy_id_hash": entry.legacy_id_hash,
                "record_hash": entry.record_hash,
                "classification": entry.classification.value,
                "disposition": entry.disposition.value,
                "owner_evidence_state": entry.owner_evidence_state.value,
                "source_evidence_state": entry.source_evidence_state.value,
                "decision_evidence_state": entry.decision_evidence_state.value,
                "reason_code": entry.reason_code,
                "target_state": "notCreated",
            }
            for entry in inventory.entries
        ]
        actual = [dict(row) for row in cursor.fetchall()]
        if actual != expected:
            raise OwnerTruthLegacyMigrationConflict(
                "legacy migration entry replay is incomplete or conflicts with immutable rows"
            )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)

    @staticmethod
    def _adapt_params(values: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            return tuple(
                json.dumps(value, ensure_ascii=True, sort_keys=True)
                if isinstance(value, Mapping)
                else value
                for value in values
            )
        return tuple(Jsonb(dict(value)) if isinstance(value, Mapping) else value for value in values)


class OwnerTruthLegacyMigrationInventoryService:
    """Default-off Owner-only entry point for read-only legacy classification."""

    def __init__(self, store: OwnerTruthLegacyMigrationStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def inventory(
        self,
        *,
        context: OwnerTruthCommandContext,
        classifier_version: str = OWNER_TRUTH_LEGACY_MIGRATION_CLASSIFIER_VERSION,
    ) -> OwnerTruthLegacyMigrationRun:
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthLegacyMigrationAccessDenied(
                "only the Vault Owner may inventory legacy evidence"
            )
        if not self._enabled:
            raise OwnerTruthLegacyMigrationUnavailable("legacy migration inventory is disabled")
        normalized_classifier = _nonblank(classifier_version, field="classifier_version")
        command_hash = _digest(
            {
                "classifierVersion": normalized_classifier,
                "ownerSubjectId": context.owner_subject_id,
                "vaultId": context.vault_id,
            }
        )
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-legacy-inventory-{command_hash[:16]}",
            command_id=command_hash,
        ):
            repository = self._store.owner_truth_legacy_migration_repository()
            inventory = repository.collect(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                classifier_version=normalized_classifier,
            )
            return repository.persist(
                owner_subject_id=context.owner_subject_id,
                inventory=inventory,
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


def legacy_migration_summary(run: OwnerTruthLegacyMigrationRun) -> dict[str, object]:
    if not isinstance(run, OwnerTruthLegacyMigrationRun):
        raise OwnerTruthLegacyMigrationError("legacy migration run is required")
    return run.public_summary()


__all__ = [
    "InMemoryOwnerTruthLegacyMigrationRepository",
    "LegacyMigrationCheckpoint",
    "LegacyMigrationLegacyRows",
    "OWNER_TRUTH_LEGACY_MIGRATION_CLASSIFIER_VERSION",
    "OWNER_TRUTH_LEGACY_MIGRATION_SCHEMA_VERSION",
    "OwnerTruthLegacyMigrationAccessDenied",
    "OwnerTruthLegacyMigrationConflict",
    "OwnerTruthLegacyMigrationInventoryService",
    "OwnerTruthLegacyMigrationRun",
    "OwnerTruthLegacyMigrationUnavailable",
    "PostgresOwnerTruthLegacyMigrationRepository",
    "build_inventory_from_legacy_rows",
    "legacy_migration_summary",
]
