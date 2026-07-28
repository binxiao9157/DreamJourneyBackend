"""Append-only persistence for C04 legacy tail would-run reports.

This module persists only C04's hash-only shadow evidence.  It deliberately
does not enqueue an async effect, create a job or object reference, invoke a
Provider, process a callback, mutate legacy data, or change Owner Authority.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Callable, ContextManager, Dict, Iterable, Mapping, Optional, Protocol, Tuple

from app.async_effects.provider_effects import ProviderEffectCatalogEntry
from app.domain.owner_truth.legacy_backfill import LegacyBackfillAdmissionPlan
from app.domain.owner_truth.legacy_tail_shadow import (
    OWNER_TRUTH_LEGACY_TAIL_SHADOW_SCHEMA_VERSION,
    LegacyTailShadowMapping,
    LegacyTailShadowOperation,
    LegacyTailShadowReport,
    OwnerTruthLegacyTailShadowError,
    build_legacy_tail_shadow_report,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_legacy_backfill import OwnerTruthLegacyBackfillAuthoritySnapshot


OWNER_TRUTH_LEGACY_TAIL_SHADOW_SERVICE_SCHEMA_VERSION = "owner-truth-legacy-tail-shadow-service-v1"


class OwnerTruthLegacyTailShadowAccessDenied(OwnerTruthLegacyTailShadowError):
    """The active Owner/Vault epoch does not authorize C04 shadow persistence."""


class OwnerTruthLegacyTailShadowConflict(OwnerTruthLegacyTailShadowError):
    """A persisted C04 report replayed with incompatible immutable meaning."""


class OwnerTruthLegacyTailShadowUnavailable(OwnerTruthLegacyTailShadowError):
    """The QA-only C04 shadow writer is disabled."""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthLegacyTailShadowError("legacy tail shadow material must be serializable") from exc


def _digest(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


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
class OwnerTruthLegacyTailShadowRun:
    """Created or deduplicated C04 shadow persistence result."""

    outcome: str
    report: LegacyTailShadowReport

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise OwnerTruthLegacyTailShadowError("tail shadow outcome is invalid")
        if not isinstance(self.report, LegacyTailShadowReport):
            raise OwnerTruthLegacyTailShadowError("tail shadow report is required")

    def public_summary(self) -> dict[str, object]:
        summary = self.report.value_free_summary()
        summary.update(
            {
                "outcome": self.outcome,
                "schemaVersion": OWNER_TRUTH_LEGACY_TAIL_SHADOW_SERVICE_SCHEMA_VERSION,
                "tailShadowSchemaVersion": OWNER_TRUTH_LEGACY_TAIL_SHADOW_SCHEMA_VERSION,
            }
        )
        return summary


class OwnerTruthLegacyTailShadowRepository(Protocol):
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
        plan: LegacyBackfillAdmissionPlan,
        report: LegacyTailShadowReport,
    ) -> OwnerTruthLegacyTailShadowRun:
        ...


class OwnerTruthLegacyTailShadowStore(Protocol):
    def owner_truth_legacy_tail_shadow_repository(self) -> OwnerTruthLegacyTailShadowRepository:
        ...


class InMemoryOwnerTruthLegacyTailShadowRepository:
    """Thread-safe semantic double for C04's append-only report ledger."""

    def __init__(
        self,
        *,
        authority_supplier: Callable[[str, str], Optional[Mapping[str, Any]]],
    ) -> None:
        self._authority_supplier = authority_supplier
        self._lock = RLock()
        self._reports: Dict[Tuple[str, str], OwnerTruthLegacyTailShadowRun] = {}

    def read_authority(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
    ) -> OwnerTruthLegacyBackfillAuthoritySnapshot:
        vault = self._authority_supplier(vault_id, owner_subject_id)
        if not isinstance(vault, Mapping):
            raise OwnerTruthLegacyTailShadowAccessDenied("Vault is not active for this Owner")
        owner = str(vault.get("ownerSubjectId") or vault.get("owner_subject_id") or "").strip()
        status = str(vault.get("status") or "active").strip()
        if owner != owner_subject_id or status != "active":
            raise OwnerTruthLegacyTailShadowAccessDenied("Vault is not active for this Owner")
        try:
            epoch = int(vault.get("authorityEpoch", vault.get("authority_epoch", 0)))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthLegacyTailShadowConflict("Vault authority epoch is invalid") from exc
        return OwnerTruthLegacyBackfillAuthoritySnapshot(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=epoch,
        )

    def persist(
        self,
        *,
        owner_subject_id: str,
        plan: LegacyBackfillAdmissionPlan,
        report: LegacyTailShadowReport,
    ) -> OwnerTruthLegacyTailShadowRun:
        _assert_report_matches_plan(owner_subject_id=owner_subject_id, plan=plan, report=report)
        authority = self.read_authority(
            vault_id=plan.vault_id,
            owner_subject_id=owner_subject_id,
        )
        if authority.authority_epoch != plan.authority_epoch:
            raise OwnerTruthLegacyTailShadowConflict(
                "Vault authority epoch changed before tail shadow persistence"
            )
        key = (report.plan_id, report.report_hash)
        with self._lock:
            existing = self._reports.get(key)
            if existing is not None:
                if existing.report != report:
                    raise OwnerTruthLegacyTailShadowConflict(
                        "identical tail shadow report key has incompatible immutable report"
                    )
                return OwnerTruthLegacyTailShadowRun(outcome="deduplicated", report=existing.report)
            result = OwnerTruthLegacyTailShadowRun(outcome="created", report=report)
            self._reports[key] = result
            return result

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "reportCount": len(self._reports),
                "reports": [
                    item.public_summary()
                    for item in sorted(
                        self._reports.values(),
                        key=lambda candidate: (candidate.report.plan_id, candidate.report.report_hash),
                    )
                ],
            }


class PostgresOwnerTruthLegacyTailShadowRepository:
    """Append-only C04 report persistence in an active Postgres UoW.

    This repository writes only the two C04 shadow ledger tables.  The schema
    and this class intentionally have no dependency on an async-effect writer,
    object-storage client, Provider adapter, callback processor or Authority
    mutation service.
    """

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
        plan: LegacyBackfillAdmissionPlan,
        report: LegacyTailShadowReport,
    ) -> OwnerTruthLegacyTailShadowRun:
        _assert_report_matches_plan(owner_subject_id=owner_subject_id, plan=plan, report=report)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-legacy-tail-shadow:"
                    f"{plan.vault_id}:{plan.authority_epoch}:{report.report_hash}",
                ),
            )
            current = self._active_authority(
                cursor,
                vault_id=plan.vault_id,
                owner_subject_id=owner_subject_id,
                lock=True,
            )
            if current.authority_epoch != plan.authority_epoch:
                raise OwnerTruthLegacyTailShadowConflict(
                    "Vault authority epoch changed before tail shadow persistence"
                )
            self._assert_persisted_plan(cursor, plan=plan)
            cursor.execute(
                """
                INSERT INTO owner_truth.legacy_migration_tail_shadow_reports (
                    plan_id, report_hash, plan_hash, vault_id, owner_subject_id,
                    authority_epoch, input_operation_count, duplicate_input_count,
                    required_outbox_entry_count, missing_outbox_mapping_count,
                    archive_object_evidence_gap_count, unmapped_provider_catalog_keys,
                    mapping_count, tail_checkpoint_hash, schema_version,
                    shadow_only, effect_execution_count, outbox_write_count,
                    job_write_count, object_storage_operation_count, provider_call_count,
                    provider_callback_processed_count, callback_accepted_count,
                    cutover_allowed, legacy_writer_retired
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    TRUE, 0, 0,
                    0, 0, 0,
                    0, 0,
                    FALSE, FALSE
                )
                ON CONFLICT (plan_id, report_hash) DO NOTHING
                RETURNING plan_id
                """,
                self._adapt_params(self._report_params(plan=plan, report=report)),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                self._assert_existing_report(cursor, plan=plan, report=report)
                outcome = "deduplicated"
            else:
                outcome = "created"
            for mapping in report.mappings:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.legacy_migration_tail_shadow_mappings (
                        plan_id, report_hash, mapping_hash, channel, source_domain,
                        source_legacy_id_hash, source_record_hash, action,
                        operation_stable_key, provider_catalog_key,
                        provider_query_reconcile_support, object_reference_hash,
                        callback_fixture_hash
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s
                    )
                    ON CONFLICT (plan_id, report_hash, mapping_hash) DO NOTHING
                    """,
                    self._mapping_params(report=report, mapping=mapping),
                )
            self._assert_mappings(cursor, report=report)
        return OwnerTruthLegacyTailShadowRun(outcome=outcome, report=report)

    @staticmethod
    def _report_params(
        *,
        plan: LegacyBackfillAdmissionPlan,
        report: LegacyTailShadowReport,
    ) -> Tuple[object, ...]:
        return (
            report.plan_id,
            report.report_hash,
            report.plan_hash,
            plan.vault_id,
            plan.owner_subject_id,
            report.authority_epoch,
            report.input_operation_count,
            report.duplicate_input_count,
            report.required_outbox_entry_count,
            report.missing_outbox_mapping_count,
            report.archive_object_evidence_gap_count,
            list(report.unmapped_provider_catalog_keys),
            len(report.mappings),
            report.tail_checkpoint_hash,
            OWNER_TRUTH_LEGACY_TAIL_SHADOW_SCHEMA_VERSION,
        )

    @staticmethod
    def _mapping_params(
        *,
        report: LegacyTailShadowReport,
        mapping: LegacyTailShadowMapping,
    ) -> Tuple[object, ...]:
        return (
            report.plan_id,
            report.report_hash,
            mapping.mapping_hash,
            mapping.channel.value,
            mapping.source_domain.value,
            mapping.source_legacy_id_hash,
            mapping.source_record_hash,
            mapping.action.value,
            mapping.operation_stable_key,
            mapping.provider_catalog_key,
            mapping.provider_query_reconcile_support,
            mapping.object_reference_hash,
            mapping.callback_fixture_hash,
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
            raise OwnerTruthLegacyTailShadowAccessDenied("Vault is not active for this Owner")
        return OwnerTruthLegacyBackfillAuthoritySnapshot(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=int(vault["authority_epoch"]),
        )

    @staticmethod
    def _assert_persisted_plan(cursor: Any, *, plan: LegacyBackfillAdmissionPlan) -> None:
        cursor.execute(
            """
            SELECT id, vault_id, owner_subject_id, authority_epoch, plan_hash
            FROM owner_truth.legacy_migration_backfill_plans
            WHERE id = %s
            FOR SHARE
            """,
            (plan.plan_id,),
        )
        row = cursor.fetchone()
        expected = {
            "id": plan.plan_id,
            "vault_id": plan.vault_id,
            "owner_subject_id": plan.owner_subject_id,
            "authority_epoch": plan.authority_epoch,
            "plan_hash": plan.plan_hash,
        }
        if row is None:
            raise OwnerTruthLegacyTailShadowConflict(
                "C03 legacy admission plan is not persisted before C04 shadow persistence"
            )
        actual = dict(row)
        for field in ("id", "vault_id", "owner_subject_id", "plan_hash"):
            actual[field] = str(actual.get(field) or "")
        actual["authority_epoch"] = int(actual.get("authority_epoch") or 0)
        if actual != expected:
            raise OwnerTruthLegacyTailShadowConflict(
                "persisted C03 legacy admission plan conflicts with C04 report"
            )

    @staticmethod
    def _assert_existing_report(
        cursor: Any,
        *,
        plan: LegacyBackfillAdmissionPlan,
        report: LegacyTailShadowReport,
    ) -> None:
        cursor.execute(
            """
            SELECT plan_hash, vault_id, owner_subject_id, authority_epoch,
                input_operation_count, duplicate_input_count,
                required_outbox_entry_count, missing_outbox_mapping_count,
                archive_object_evidence_gap_count, unmapped_provider_catalog_keys,
                mapping_count, tail_checkpoint_hash, schema_version, shadow_only,
                effect_execution_count, outbox_write_count, job_write_count,
                object_storage_operation_count, provider_call_count,
                provider_callback_processed_count, callback_accepted_count,
                cutover_allowed, legacy_writer_retired
            FROM owner_truth.legacy_migration_tail_shadow_reports
            WHERE plan_id = %s AND report_hash = %s
            FOR SHARE
            """,
            (report.plan_id, report.report_hash),
        )
        expected = PostgresOwnerTruthLegacyTailShadowRepository._expected_report_row(
            plan=plan,
            report=report,
        )
        actual = PostgresOwnerTruthLegacyTailShadowRepository._normalized_report_row(
            cursor.fetchone(),
        )
        if actual != expected:
            raise OwnerTruthLegacyTailShadowConflict(
                "legacy tail shadow report replay conflicts with immutable report"
            )

    @staticmethod
    def _assert_mappings(cursor: Any, *, report: LegacyTailShadowReport) -> None:
        cursor.execute(
            """
            SELECT mapping_hash, channel, source_domain, source_legacy_id_hash,
                source_record_hash, action, operation_stable_key,
                provider_catalog_key, provider_query_reconcile_support,
                object_reference_hash, callback_fixture_hash
            FROM owner_truth.legacy_migration_tail_shadow_mappings
            WHERE plan_id = %s AND report_hash = %s
            ORDER BY channel ASC, mapping_hash ASC
            """,
            (report.plan_id, report.report_hash),
        )
        actual = [
            PostgresOwnerTruthLegacyTailShadowRepository._normalized_mapping_row(row)
            for row in cursor.fetchall()
        ]
        expected = [
            PostgresOwnerTruthLegacyTailShadowRepository._expected_mapping_row(mapping)
            for mapping in report.mappings
        ]
        if actual != expected:
            raise OwnerTruthLegacyTailShadowConflict(
                "legacy tail shadow mappings are incomplete or conflict with immutable report"
            )

    @staticmethod
    def _expected_report_row(
        *,
        plan: LegacyBackfillAdmissionPlan,
        report: LegacyTailShadowReport,
    ) -> dict[str, object]:
        return {
            "plan_hash": report.plan_hash,
            "vault_id": plan.vault_id,
            "owner_subject_id": plan.owner_subject_id,
            "authority_epoch": report.authority_epoch,
            "input_operation_count": report.input_operation_count,
            "duplicate_input_count": report.duplicate_input_count,
            "required_outbox_entry_count": report.required_outbox_entry_count,
            "missing_outbox_mapping_count": report.missing_outbox_mapping_count,
            "archive_object_evidence_gap_count": report.archive_object_evidence_gap_count,
            "unmapped_provider_catalog_keys": list(report.unmapped_provider_catalog_keys),
            "mapping_count": len(report.mappings),
            "tail_checkpoint_hash": report.tail_checkpoint_hash,
            "schema_version": OWNER_TRUTH_LEGACY_TAIL_SHADOW_SCHEMA_VERSION,
            "shadow_only": True,
            "effect_execution_count": 0,
            "outbox_write_count": 0,
            "job_write_count": 0,
            "object_storage_operation_count": 0,
            "provider_call_count": 0,
            "provider_callback_processed_count": 0,
            "callback_accepted_count": 0,
            "cutover_allowed": False,
            "legacy_writer_retired": False,
        }

    @staticmethod
    def _normalized_report_row(row: object) -> dict[str, object]:
        actual = dict(_mapping(row))
        if not actual:
            return {}
        for field in ("plan_hash", "vault_id", "owner_subject_id", "tail_checkpoint_hash", "schema_version"):
            actual[field] = str(actual.get(field) or "")
        for field in (
            "authority_epoch",
            "input_operation_count",
            "duplicate_input_count",
            "required_outbox_entry_count",
            "missing_outbox_mapping_count",
            "archive_object_evidence_gap_count",
            "mapping_count",
            "effect_execution_count",
            "outbox_write_count",
            "job_write_count",
            "object_storage_operation_count",
            "provider_call_count",
            "provider_callback_processed_count",
            "callback_accepted_count",
        ):
            actual[field] = int(actual.get(field) or 0)
        provider_keys = actual.get("unmapped_provider_catalog_keys")
        if isinstance(provider_keys, str):
            try:
                provider_keys = json.loads(provider_keys)
            except json.JSONDecodeError:
                provider_keys = None
        actual["unmapped_provider_catalog_keys"] = (
            [str(item) for item in provider_keys] if isinstance(provider_keys, list) else []
        )
        for field in ("shadow_only", "cutover_allowed", "legacy_writer_retired"):
            actual[field] = bool(actual.get(field))
        return actual

    @staticmethod
    def _expected_mapping_row(mapping: LegacyTailShadowMapping) -> dict[str, object]:
        return {
            "mapping_hash": mapping.mapping_hash,
            "channel": mapping.channel.value,
            "source_domain": mapping.source_domain.value,
            "source_legacy_id_hash": mapping.source_legacy_id_hash,
            "source_record_hash": mapping.source_record_hash,
            "action": mapping.action.value,
            "operation_stable_key": mapping.operation_stable_key,
            "provider_catalog_key": mapping.provider_catalog_key,
            "provider_query_reconcile_support": mapping.provider_query_reconcile_support,
            "object_reference_hash": mapping.object_reference_hash,
            "callback_fixture_hash": mapping.callback_fixture_hash,
        }

    @staticmethod
    def _normalized_mapping_row(row: object) -> dict[str, object]:
        actual = dict(_mapping(row))
        if not actual:
            return {}
        for field in (
            "mapping_hash",
            "channel",
            "source_domain",
            "source_legacy_id_hash",
            "source_record_hash",
            "action",
            "operation_stable_key",
        ):
            actual[field] = str(actual.get(field) or "")
        for field in (
            "provider_catalog_key",
            "provider_query_reconcile_support",
            "object_reference_hash",
            "callback_fixture_hash",
        ):
            value = actual.get(field)
            actual[field] = None if value is None else str(value)
        return actual

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)

    @staticmethod
    def _adapt_params(values: Tuple[object, ...]) -> Tuple[object, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            return tuple(
                json.dumps(value, ensure_ascii=True, sort_keys=True)
                if isinstance(value, (Mapping, list, tuple))
                else value
                for value in values
            )
        return tuple(
            Jsonb(value) if isinstance(value, (Mapping, list, tuple)) else value
            for value in values
        )


class OwnerTruthLegacyTailShadowService:
    """Owner-only, default-off entry point for C04 shadow persistence."""

    def __init__(self, store: OwnerTruthLegacyTailShadowStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def shadow(
        self,
        *,
        context: OwnerTruthCommandContext,
        plan: LegacyBackfillAdmissionPlan,
        operations: Iterable[LegacyTailShadowOperation],
        catalog_entries: Optional[Iterable[ProviderEffectCatalogEntry]] = None,
    ) -> OwnerTruthLegacyTailShadowRun:
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthLegacyTailShadowAccessDenied(
                "only the Vault Owner may persist a legacy tail shadow report"
            )
        if not self._enabled:
            raise OwnerTruthLegacyTailShadowUnavailable("legacy tail shadow persistence is disabled")
        if not isinstance(plan, LegacyBackfillAdmissionPlan):
            raise OwnerTruthLegacyTailShadowConflict("C03 legacy admission plan is required")
        if plan.vault_id != context.vault_id or plan.owner_subject_id != context.owner_subject_id:
            raise OwnerTruthLegacyTailShadowAccessDenied(
                "C03 legacy admission plan must belong to the active Owner/Vault"
            )
        normalized_operations = tuple(operations)
        command_hash = _digest(
            {
                "planHash": plan.plan_hash,
                "planId": plan.plan_id,
                "operationFingerprints": [
                    operation.immutable_fingerprint
                    for operation in normalized_operations
                    if isinstance(operation, LegacyTailShadowOperation)
                ],
                "workflow": OWNER_TRUTH_LEGACY_TAIL_SHADOW_SERVICE_SCHEMA_VERSION,
            }
        )
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-legacy-tail-shadow-{command_hash[:16]}",
            command_id=command_hash,
        ):
            repository = self._store.owner_truth_legacy_tail_shadow_repository()
            authority = repository.read_authority(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
            )
            if authority.authority_epoch != plan.authority_epoch:
                raise OwnerTruthLegacyTailShadowConflict(
                    "Vault authority epoch changed before tail shadow planning"
                )
            report = build_legacy_tail_shadow_report(
                plan=plan,
                operations=normalized_operations,
                catalog_entries=catalog_entries,
            )
            return repository.persist(
                owner_subject_id=context.owner_subject_id,
                plan=plan,
                report=report,
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


def legacy_tail_shadow_summary(run: OwnerTruthLegacyTailShadowRun) -> dict[str, object]:
    if not isinstance(run, OwnerTruthLegacyTailShadowRun):
        raise OwnerTruthLegacyTailShadowError("legacy tail shadow run is required")
    return run.public_summary()


def _assert_report_matches_plan(
    *,
    owner_subject_id: str,
    plan: LegacyBackfillAdmissionPlan,
    report: LegacyTailShadowReport,
) -> None:
    if not isinstance(plan, LegacyBackfillAdmissionPlan):
        raise OwnerTruthLegacyTailShadowConflict("C03 legacy admission plan is required")
    if not isinstance(report, LegacyTailShadowReport):
        raise OwnerTruthLegacyTailShadowConflict("legacy tail shadow report is required")
    if (
        plan.owner_subject_id != owner_subject_id
        or report.plan_id != plan.plan_id
        or report.plan_hash != plan.plan_hash
        or report.authority_epoch != plan.authority_epoch
    ):
        raise OwnerTruthLegacyTailShadowConflict(
            "legacy tail shadow report does not match its immutable C03 plan"
        )


__all__ = [
    "InMemoryOwnerTruthLegacyTailShadowRepository",
    "OWNER_TRUTH_LEGACY_TAIL_SHADOW_SERVICE_SCHEMA_VERSION",
    "OwnerTruthLegacyTailShadowAccessDenied",
    "OwnerTruthLegacyTailShadowConflict",
    "OwnerTruthLegacyTailShadowRepository",
    "OwnerTruthLegacyTailShadowRun",
    "OwnerTruthLegacyTailShadowService",
    "OwnerTruthLegacyTailShadowUnavailable",
    "PostgresOwnerTruthLegacyTailShadowRepository",
    "legacy_tail_shadow_summary",
]
