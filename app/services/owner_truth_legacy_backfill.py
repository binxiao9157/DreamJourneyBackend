"""Default-off C03 legacy backfill admission planning.

This service persists a value-free, deterministic plan over a previously
collected legacy inventory.  The plan is intentionally not a migration writer:
it cannot create Owner Truth authority, change an authority epoch, retire a
legacy writer, or expose a public route.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Callable, ContextManager, Mapping, Protocol

from app.domain.owner_truth.legacy_backfill import (
    LegacyBackfillAdmissionPlan,
    OwnerTruthLegacyBackfillPlanError,
    build_legacy_backfill_admission_plan,
)
from app.domain.owner_truth.legacy_migration import OwnerTruthLegacyMigrationError
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_legacy_migration import (
    OwnerTruthLegacyMigrationAccessDenied,
    OwnerTruthLegacyMigrationConflict,
    OwnerTruthLegacyMigrationInventoryService,
    OwnerTruthLegacyMigrationRun,
    OwnerTruthLegacyMigrationUnavailable,
)


OWNER_TRUTH_LEGACY_BACKFILL_PLAN_SERVICE_SCHEMA_VERSION = (
    "owner-truth-legacy-backfill-plan-service-v1"
)


class OwnerTruthLegacyBackfillAccessDenied(OwnerTruthLegacyMigrationAccessDenied):
    """The requested Vault is not active for this Owner."""


class OwnerTruthLegacyBackfillConflict(OwnerTruthLegacyMigrationConflict):
    """An immutable inventory or authority epoch no longer matches the plan."""


class OwnerTruthLegacyBackfillUnavailable(OwnerTruthLegacyMigrationUnavailable):
    """The default-off C03 planner is not enabled for QA."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonblank(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthLegacyBackfillPlanError(f"{field} is required")
    return normalized


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, Mapping) else {}
    return {}


@dataclass(frozen=True)
class OwnerTruthLegacyBackfillAuthoritySnapshot:
    """Current Owner/Vault epoch used to fence a C03 plan."""

    vault_id: str
    owner_subject_id: str
    authority_epoch: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int):
            raise OwnerTruthLegacyBackfillPlanError("authority_epoch must be an integer")
        if self.authority_epoch < 0:
            raise OwnerTruthLegacyBackfillPlanError("authority_epoch must be non-negative")


@dataclass(frozen=True)
class OwnerTruthLegacyBackfillPlanRun:
    """Persisted or deduplicated C03 plan result."""

    outcome: str
    inventory_run_id: str
    plan: LegacyBackfillAdmissionPlan

    def public_summary(self) -> dict[str, object]:
        summary = self.plan.summary()
        summary.update(
            {
                "outcome": self.outcome,
                "planSchemaVersion": summary["schemaVersion"],
                "schemaVersion": OWNER_TRUTH_LEGACY_BACKFILL_PLAN_SERVICE_SCHEMA_VERSION,
            }
        )
        return summary


class OwnerTruthLegacyBackfillRepository(Protocol):
    def read_authority(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
    ) -> OwnerTruthLegacyBackfillAuthoritySnapshot:
        ...

    def persist(
        self,
        *,
        owner_subject_id: str,
        inventory_run: OwnerTruthLegacyMigrationRun,
        plan: LegacyBackfillAdmissionPlan,
    ) -> OwnerTruthLegacyBackfillPlanRun:
        ...


class OwnerTruthLegacyBackfillStore(Protocol):
    def owner_truth_legacy_migration_repository(self) -> Any:
        ...

    def owner_truth_legacy_backfill_repository(self) -> OwnerTruthLegacyBackfillRepository:
        ...


class InMemoryOwnerTruthLegacyBackfillRepository:
    """Semantic double for immutable plan persistence and authority fencing."""

    def __init__(
        self,
        *,
        authority_supplier: Callable[[str, str], Mapping[str, Any] | None],
    ) -> None:
        self._authority_supplier = authority_supplier
        self._lock = RLock()
        self._plans: dict[tuple[str, int, str], OwnerTruthLegacyBackfillPlanRun] = {}

    def read_authority(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
    ) -> OwnerTruthLegacyBackfillAuthoritySnapshot:
        vault = self._authority_supplier(vault_id, owner_subject_id)
        if not isinstance(vault, Mapping):
            raise OwnerTruthLegacyBackfillAccessDenied("Vault is not active for this Owner")
        owner = str(vault.get("ownerSubjectId") or vault.get("owner_subject_id") or "").strip()
        status = str(vault.get("status") or "active").strip()
        if owner != owner_subject_id or status != "active":
            raise OwnerTruthLegacyBackfillAccessDenied("Vault is not active for this Owner")
        try:
            epoch = int(vault.get("authorityEpoch", vault.get("authority_epoch", 0)))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthLegacyBackfillConflict("Vault authority epoch is invalid") from exc
        return OwnerTruthLegacyBackfillAuthoritySnapshot(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=epoch,
        )

    def persist(
        self,
        *,
        owner_subject_id: str,
        inventory_run: OwnerTruthLegacyMigrationRun,
        plan: LegacyBackfillAdmissionPlan,
    ) -> OwnerTruthLegacyBackfillPlanRun:
        _assert_plan_matches_run(
            owner_subject_id=owner_subject_id,
            inventory_run=inventory_run,
            plan=plan,
        )
        current = self.read_authority(
            vault_id=plan.vault_id,
            owner_subject_id=owner_subject_id,
        )
        if current.authority_epoch != plan.authority_epoch:
            raise OwnerTruthLegacyBackfillConflict("Vault authority epoch changed before plan persistence")
        key = (plan.inventory_run_id, plan.authority_epoch, plan.plan_hash)
        with self._lock:
            existing = self._plans.get(key)
            if existing is not None:
                if existing.plan != plan:
                    raise OwnerTruthLegacyBackfillConflict(
                        "identical legacy admission key has incompatible immutable plan"
                    )
                return OwnerTruthLegacyBackfillPlanRun(
                    outcome="deduplicated",
                    inventory_run_id=existing.inventory_run_id,
                    plan=existing.plan,
                )
            result = OwnerTruthLegacyBackfillPlanRun(
                outcome="created",
                inventory_run_id=inventory_run.run_id,
                plan=plan,
            )
            self._plans[key] = result
            return result

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "planCount": len(self._plans),
                "plans": [
                    item.public_summary()
                    for item in sorted(self._plans.values(), key=lambda candidate: candidate.plan.plan_id)
                ],
            }


class PostgresOwnerTruthLegacyBackfillRepository:
    """Append-only C03 plan persistence in the active Postgres UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def read_authority(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
    ) -> OwnerTruthLegacyBackfillAuthoritySnapshot:
        with self._cursor() as cursor:
            return self._active_authority(
                cursor,
                vault_id=vault_id,
                owner_subject_id=owner_subject_id,
                lock=True,
            )

    def persist(
        self,
        *,
        owner_subject_id: str,
        inventory_run: OwnerTruthLegacyMigrationRun,
        plan: LegacyBackfillAdmissionPlan,
    ) -> OwnerTruthLegacyBackfillPlanRun:
        _assert_plan_matches_run(
            owner_subject_id=owner_subject_id,
            inventory_run=inventory_run,
            plan=plan,
        )
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-legacy-backfill-plan:"
                    f"{plan.vault_id}:{plan.authority_epoch}",
                ),
            )
            current = self._active_authority(
                cursor,
                vault_id=plan.vault_id,
                owner_subject_id=owner_subject_id,
                lock=True,
            )
            if current.authority_epoch != plan.authority_epoch:
                raise OwnerTruthLegacyBackfillConflict(
                    "Vault authority epoch changed before plan persistence"
                )
            self._assert_inventory_run(cursor, owner_subject_id=owner_subject_id, inventory_run=inventory_run)
            cursor.execute(
                """
                INSERT INTO owner_truth.legacy_migration_backfill_plans (
                    id, inventory_run_id, vault_id, owner_subject_id,
                    classifier_version, inventory_hash, authority_epoch,
                    entry_count, action_counts, scope_hash, plan_hash, schema_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (inventory_run_id, authority_epoch, plan_hash) DO NOTHING
                RETURNING id
                """,
                self._adapt_params(
                    (
                        plan.plan_id,
                        plan.inventory_run_id,
                        plan.vault_id,
                        plan.owner_subject_id,
                        plan.classifier_version,
                        plan.inventory_hash,
                        plan.authority_epoch,
                        len(plan.entries),
                        plan.action_counts,
                        plan.scope_hash,
                        plan.plan_hash,
                        plan.summary()["schemaVersion"],
                    )
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                self._assert_existing_plan(cursor, plan=plan)
                outcome = "deduplicated"
            else:
                outcome = "created"
            for entry in plan.entries:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.legacy_migration_backfill_plan_entries (
                        plan_id, domain, legacy_id_hash, record_hash, classification,
                        disposition, action, reason_code, target_state
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'notCreated')
                    ON CONFLICT (plan_id, domain, legacy_id_hash) DO NOTHING
                    """,
                    (
                        plan.plan_id,
                        entry.domain.value,
                        entry.legacy_id_hash,
                        entry.record_hash,
                        entry.classification.value,
                        entry.disposition.value,
                        entry.action.value,
                        entry.reason_code,
                    ),
                )
            self._assert_entries(cursor, plan=plan)
        return OwnerTruthLegacyBackfillPlanRun(
            outcome=outcome,
            inventory_run_id=inventory_run.run_id,
            plan=plan,
        )

    @staticmethod
    def _assert_inventory_run(
        cursor: Any,
        *,
        owner_subject_id: str,
        inventory_run: OwnerTruthLegacyMigrationRun,
    ) -> None:
        cursor.execute(
            """
            SELECT vault_id, owner_subject_id, classifier_version, inventory_hash, entry_count
            FROM owner_truth.legacy_migration_runs
            WHERE id = %s
            FOR SHARE
            """,
            (inventory_run.run_id,),
        )
        existing = cursor.fetchone()
        inventory = inventory_run.inventory
        if (
            existing is None
            or str(existing["vault_id"]) != inventory.vault_id
            or str(existing["owner_subject_id"]) != owner_subject_id
            or str(existing["classifier_version"]) != inventory.classifier_version
            or str(existing["inventory_hash"]) != inventory.inventory_hash
            or int(existing["entry_count"]) != len(inventory.entries)
        ):
            raise OwnerTruthLegacyBackfillConflict(
                "legacy inventory run is missing or no longer matches the immutable plan input"
            )

    def _active_authority(
        self,
        cursor: Any,
        *,
        vault_id: str,
        owner_subject_id: str,
        lock: bool,
    ) -> OwnerTruthLegacyBackfillAuthoritySnapshot:
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, status
            FROM owner_truth.vaults
            WHERE vault_id = %s
            """ + ("FOR SHARE" if lock else ""),
            (vault_id,),
        )
        vault = cursor.fetchone()
        if (
            vault is None
            or str(vault["owner_subject_id"]) != owner_subject_id
            or str(vault["status"]) != "active"
        ):
            raise OwnerTruthLegacyBackfillAccessDenied("Vault is not active for this Owner")
        return OwnerTruthLegacyBackfillAuthoritySnapshot(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=int(vault["authority_epoch"]),
        )

    @staticmethod
    def _assert_existing_plan(cursor: Any, *, plan: LegacyBackfillAdmissionPlan) -> None:
        cursor.execute(
            """
            SELECT id, vault_id, owner_subject_id, classifier_version, inventory_hash,
                authority_epoch, entry_count, action_counts, scope_hash, schema_version
            FROM owner_truth.legacy_migration_backfill_plans
            WHERE inventory_run_id = %s AND authority_epoch = %s AND plan_hash = %s
            FOR SHARE
            """,
            (plan.inventory_run_id, plan.authority_epoch, plan.plan_hash),
        )
        existing = cursor.fetchone()
        expected = {
            "id": plan.plan_id,
            "vault_id": plan.vault_id,
            "owner_subject_id": plan.owner_subject_id,
            "classifier_version": plan.classifier_version,
            "inventory_hash": plan.inventory_hash,
            "authority_epoch": plan.authority_epoch,
            "entry_count": len(plan.entries),
            "action_counts": plan.action_counts,
            "scope_hash": plan.scope_hash,
            "schema_version": plan.summary()["schemaVersion"],
        }
        if existing is None:
            raise OwnerTruthLegacyBackfillConflict("legacy admission plan replay was not found")
        actual = dict(existing)
        for field in (
            "id",
            "vault_id",
            "owner_subject_id",
            "classifier_version",
            "inventory_hash",
            "scope_hash",
            "schema_version",
        ):
            actual[field] = str(actual.get(field) or "")
        actual["authority_epoch"] = int(actual.get("authority_epoch") or 0)
        actual["entry_count"] = int(actual.get("entry_count") or 0)
        actual["action_counts"] = {
            str(key): int(value)
            for key, value in dict(_mapping(actual.get("action_counts"))).items()
        }
        if actual != expected:
            raise OwnerTruthLegacyBackfillConflict(
                "legacy admission plan replay conflicts with immutable plan"
            )

    @staticmethod
    def _assert_entries(cursor: Any, *, plan: LegacyBackfillAdmissionPlan) -> None:
        cursor.execute(
            """
            SELECT domain, legacy_id_hash, record_hash, classification, disposition,
                action, reason_code, target_state
            FROM owner_truth.legacy_migration_backfill_plan_entries
            WHERE plan_id = %s
            ORDER BY domain ASC, legacy_id_hash ASC
            """,
            (plan.plan_id,),
        )
        expected = [
            {
                "domain": entry.domain.value,
                "legacy_id_hash": entry.legacy_id_hash,
                "record_hash": entry.record_hash,
                "classification": entry.classification.value,
                "disposition": entry.disposition.value,
                "action": entry.action.value,
                "reason_code": entry.reason_code,
                "target_state": "notCreated",
            }
            for entry in plan.entries
        ]
        if [dict(row) for row in cursor.fetchall()] != expected:
            raise OwnerTruthLegacyBackfillConflict(
                "legacy admission plan entries are incomplete or conflict with immutable inventory"
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


class OwnerTruthLegacyBackfillPlanService:
    """Owner-only, default-off C03 admission-plan entry point."""

    def __init__(self, store: OwnerTruthLegacyBackfillStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def plan(self, *, context: OwnerTruthCommandContext) -> OwnerTruthLegacyBackfillPlanRun:
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthLegacyBackfillAccessDenied(
                "only the Vault Owner may build a legacy backfill plan"
            )
        if not self._enabled:
            raise OwnerTruthLegacyBackfillUnavailable("legacy backfill planning is disabled")
        command_hash = _digest(
            {
                "ownerSubjectId": context.owner_subject_id,
                "vaultId": context.vault_id,
                "workflow": OWNER_TRUTH_LEGACY_BACKFILL_PLAN_SERVICE_SCHEMA_VERSION,
            }
        )
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-legacy-backfill-{command_hash[:16]}",
            command_id=command_hash,
        ):
            # Fence the Vault before the legacy collector is allowed to create
            # even a hash-only run tied to its identifier.  This avoids the
            # inventory-first behaviour of the older observer and makes C03
            # fail closed for cross-owner attempts.
            repository = self._store.owner_truth_legacy_backfill_repository()
            authority = repository.read_authority(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
            )
            inventory_run = OwnerTruthLegacyMigrationInventoryService(
                self._store,
                enabled=True,
            ).inventory(context=context)
            plan = build_legacy_backfill_admission_plan(
                inventory_run_id=inventory_run.run_id,
                inventory=inventory_run.inventory,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=authority.authority_epoch,
            )
            return repository.persist(
                owner_subject_id=context.owner_subject_id,
                inventory_run=inventory_run,
                plan=plan,
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


def legacy_backfill_plan_summary(run: OwnerTruthLegacyBackfillPlanRun) -> dict[str, object]:
    if not isinstance(run, OwnerTruthLegacyBackfillPlanRun):
        raise OwnerTruthLegacyMigrationError("legacy backfill plan run is required")
    return run.public_summary()


def _assert_plan_matches_run(
    *,
    owner_subject_id: str,
    inventory_run: OwnerTruthLegacyMigrationRun,
    plan: LegacyBackfillAdmissionPlan,
) -> None:
    if not isinstance(inventory_run, OwnerTruthLegacyMigrationRun):
        raise OwnerTruthLegacyBackfillConflict("legacy inventory run is required")
    if not isinstance(plan, LegacyBackfillAdmissionPlan):
        raise OwnerTruthLegacyBackfillConflict("legacy admission plan is required")
    inventory = inventory_run.inventory
    if (
        plan.inventory_run_id != inventory_run.run_id
        or plan.vault_id != inventory.vault_id
        or plan.owner_subject_id != owner_subject_id
        or plan.classifier_version != inventory.classifier_version
        or plan.inventory_hash != inventory.inventory_hash
        or len(plan.entries) != len(inventory.entries)
    ):
        raise OwnerTruthLegacyBackfillConflict(
            "legacy admission plan does not match its immutable inventory run"
        )


__all__ = [
    "InMemoryOwnerTruthLegacyBackfillRepository",
    "OWNER_TRUTH_LEGACY_BACKFILL_PLAN_SERVICE_SCHEMA_VERSION",
    "OwnerTruthLegacyBackfillAccessDenied",
    "OwnerTruthLegacyBackfillAuthoritySnapshot",
    "OwnerTruthLegacyBackfillConflict",
    "OwnerTruthLegacyBackfillPlanRun",
    "OwnerTruthLegacyBackfillPlanService",
    "OwnerTruthLegacyBackfillUnavailable",
    "PostgresOwnerTruthLegacyBackfillRepository",
    "legacy_backfill_plan_summary",
]
