"""Append-only persistence for C05 migration parity shadow evidence.

The service persists only a value-free parity report.  It is default-off and
does not execute legacy or V4 commands, mutate Owner Truth, copy objects, call
Providers, or authorize a cutover.  A report is fenced to the active
Owner/Vault/authority epoch by the hash-only scope material in its window.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import timezone
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Callable, ContextManager, Dict, Iterable, Mapping, Optional, Protocol, Tuple

from app.domain.owner_truth.migration_parity_shadow import (
    OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION,
    MigrationParityAllowance,
    MigrationParityComparisonWindow,
    MigrationParityMismatch,
    MigrationParityObservation,
    MigrationParityShadowReport,
    OwnerTruthMigrationParityShadowError,
    build_migration_parity_scope_hash,
    build_migration_parity_shadow_report,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_legacy_backfill import OwnerTruthLegacyBackfillAuthoritySnapshot


OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SERVICE_SCHEMA_VERSION = (
    "owner-truth-migration-parity-shadow-service-v1"
)


class OwnerTruthMigrationParityShadowAccessDenied(OwnerTruthMigrationParityShadowError):
    """The active Owner/Vault cannot write a C05 evidence record."""


class OwnerTruthMigrationParityShadowConflict(OwnerTruthMigrationParityShadowError):
    """A report replay or authority scope no longer has immutable meaning."""


class OwnerTruthMigrationParityShadowUnavailable(OwnerTruthMigrationParityShadowError):
    """The default-off C05 evidence writer is disabled."""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthMigrationParityShadowError(
            "migration parity shadow material must be serializable"
        ) from exc


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


def _timestamp(value: object) -> Optional[str]:
    if value is None:
        return None
    formatter = getattr(value, "astimezone", None)
    if not callable(formatter):
        return str(value)
    try:
        rendered = formatter(timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError, AttributeError):
        return str(value)
    return rendered.replace("+00:00", "Z")


@dataclass(frozen=True)
class OwnerTruthMigrationParityShadowRun:
    """Created or replay-deduplicated append-only C05 evidence."""

    outcome: str
    report: MigrationParityShadowReport

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise OwnerTruthMigrationParityShadowError("migration parity shadow outcome is invalid")
        if not isinstance(self.report, MigrationParityShadowReport):
            raise OwnerTruthMigrationParityShadowError("migration parity shadow report is required")

    def public_summary(self) -> dict[str, object]:
        summary = self.report.value_free_summary()
        summary.update(
            {
                "outcome": self.outcome,
                "parityShadowSchemaVersion": OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION,
                "schemaVersion": OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SERVICE_SCHEMA_VERSION,
            }
        )
        return summary


class OwnerTruthMigrationParityShadowRepository(Protocol):
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
        authority: OwnerTruthLegacyBackfillAuthoritySnapshot,
        report: MigrationParityShadowReport,
    ) -> OwnerTruthMigrationParityShadowRun:
        ...


class OwnerTruthMigrationParityShadowStore(Protocol):
    def owner_truth_migration_parity_shadow_repository(
        self,
    ) -> OwnerTruthMigrationParityShadowRepository:
        ...


class InMemoryOwnerTruthMigrationParityShadowRepository:
    """Thread-safe semantic double for C05's append-only evidence ledger."""

    def __init__(
        self,
        *,
        authority_supplier: Callable[[str, str], Optional[Mapping[str, Any]]],
    ) -> None:
        self._authority_supplier = authority_supplier
        self._lock = RLock()
        self._reports: Dict[Tuple[str, str], OwnerTruthMigrationParityShadowRun] = {}

    def read_authority(
        self,
        *,
        vault_id: str,
        owner_subject_id: str,
    ) -> OwnerTruthLegacyBackfillAuthoritySnapshot:
        vault = self._authority_supplier(vault_id, owner_subject_id)
        if not isinstance(vault, Mapping):
            raise OwnerTruthMigrationParityShadowAccessDenied("Vault is not active for this Owner")
        owner = str(vault.get("ownerSubjectId") or vault.get("owner_subject_id") or "").strip()
        status = str(vault.get("status") or "active").strip()
        if owner != owner_subject_id or status != "active":
            raise OwnerTruthMigrationParityShadowAccessDenied("Vault is not active for this Owner")
        try:
            epoch = int(vault.get("authorityEpoch", vault.get("authority_epoch", 0)))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthMigrationParityShadowConflict("Vault authority epoch is invalid") from exc
        return OwnerTruthLegacyBackfillAuthoritySnapshot(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=epoch,
        )

    def persist(
        self,
        *,
        authority: OwnerTruthLegacyBackfillAuthoritySnapshot,
        report: MigrationParityShadowReport,
    ) -> OwnerTruthMigrationParityShadowRun:
        _assert_report_scope(authority=authority, report=report)
        current = self.read_authority(
            vault_id=authority.vault_id,
            owner_subject_id=authority.owner_subject_id,
        )
        if current != authority:
            raise OwnerTruthMigrationParityShadowConflict(
                "Vault authority epoch changed before migration parity persistence"
            )
        key = (authority.vault_id, report.report_hash)
        with self._lock:
            existing = self._reports.get(key)
            if existing is not None:
                if existing.report != report:
                    raise OwnerTruthMigrationParityShadowConflict(
                        "identical migration parity report key has incompatible immutable report"
                    )
                return OwnerTruthMigrationParityShadowRun(
                    outcome="deduplicated", report=existing.report
                )
            result = OwnerTruthMigrationParityShadowRun(outcome="created", report=report)
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
                        key=lambda candidate: candidate.report.report_hash,
                    )
                ],
            }


class PostgresOwnerTruthMigrationParityShadowRepository:
    """Append-only C05 evidence persistence in an active Postgres UoW."""

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
        authority: OwnerTruthLegacyBackfillAuthoritySnapshot,
        report: MigrationParityShadowReport,
    ) -> OwnerTruthMigrationParityShadowRun:
        if not isinstance(report, MigrationParityShadowReport):
            raise OwnerTruthMigrationParityShadowConflict("migration parity shadow report is required")
        _assert_report_scope(authority=authority, report=report)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-migration-parity-shadow:"
                    f"{authority.vault_id}:{authority.authority_epoch}:{report.report_hash}",
                ),
            )
            current = self._active_authority(
                cursor,
                vault_id=authority.vault_id,
                owner_subject_id=authority.owner_subject_id,
                lock=True,
            )
            if current != authority:
                raise OwnerTruthMigrationParityShadowConflict(
                    "Vault authority epoch changed before migration parity persistence"
                )
            _assert_report_scope(authority=current, report=report)
            cursor.execute(
                """
                INSERT INTO owner_truth.migration_parity_shadow_reports (
                    report_hash, vault_id, owner_subject_id, authority_epoch,
                    window_reference_hash, scope_hash, denominator_source_hash,
                    threshold_source_hash, expected_sample_count, observed_sample_count,
                    comparison_count, duplicate_input_count, match_count, mismatch_count,
                    blocking_mismatch_count, approved_m08_difference_count,
                    unresolved_m08_difference_count, schema_version, shadow_only,
                    command_effect_execution_count, object_copy_execution_count,
                    provider_call_count, provider_cost_charged, write_operation_count,
                    cutover_allowed, legacy_writer_retired
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, TRUE,
                    0, 0,
                    0, FALSE, 0,
                    FALSE, FALSE
                )
                ON CONFLICT (report_hash) DO NOTHING
                RETURNING report_hash
                """,
                self._report_params(authority=current, report=report),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                self._assert_existing_report(cursor, authority=current, report=report)
                outcome = "deduplicated"
            else:
                outcome = "created"
            for mismatch in report.mismatches:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.migration_parity_shadow_mismatches (
                        report_hash, observation_hash, sample_id_hash, surface, dimension,
                        mismatch_code, severity, legacy_value_hash, v4_value_hash,
                        allowance_status, allowance_reason_code,
                        approval_reference_hash, allowance_expires_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s
                    )
                    ON CONFLICT (report_hash, observation_hash) DO NOTHING
                    """,
                    self._mismatch_params(report=report, mismatch=mismatch),
                )
            self._assert_mismatches(cursor, report=report)
        return OwnerTruthMigrationParityShadowRun(outcome=outcome, report=report)

    @staticmethod
    def _active_authority(
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
            raise OwnerTruthMigrationParityShadowAccessDenied("Vault is not active for this Owner")
        return OwnerTruthLegacyBackfillAuthoritySnapshot(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            authority_epoch=int(vault["authority_epoch"]),
        )

    @staticmethod
    def _report_params(
        *,
        authority: OwnerTruthLegacyBackfillAuthoritySnapshot,
        report: MigrationParityShadowReport,
    ) -> Tuple[object, ...]:
        return (
            report.report_hash,
            authority.vault_id,
            authority.owner_subject_id,
            authority.authority_epoch,
            report.window.window_reference_hash,
            report.window.scope_hash,
            report.window.denominator_source_hash,
            report.window.threshold_source_hash,
            report.window.expected_sample_count,
            report.observed_sample_count,
            len(report.observations),
            report.duplicate_input_count,
            len(report.observations) - len(report.mismatches),
            len(report.mismatches),
            report.blocking_mismatch_count,
            report.approved_m08_difference_count,
            report.unresolved_m08_difference_count,
            OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION,
        )

    @staticmethod
    def _mismatch_params(
        *,
        report: MigrationParityShadowReport,
        mismatch: MigrationParityMismatch,
    ) -> Tuple[object, ...]:
        allowance = mismatch.allowance
        return (
            report.report_hash,
            mismatch.observation.observation_hash,
            mismatch.observation.sample_id_hash,
            mismatch.observation.surface.value,
            mismatch.observation.dimension.value,
            mismatch.observation.mismatch_code.value,
            mismatch.observation.severity.value,
            mismatch.observation.legacy_value_hash,
            mismatch.observation.v4_value_hash,
            mismatch.allowance_status,
            None if allowance is None else allowance.reason_code,
            None if allowance is None else allowance.approval_reference_hash,
            None if allowance is None else allowance.expires_at,
        )

    @staticmethod
    def _expected_report_row(
        *,
        authority: OwnerTruthLegacyBackfillAuthoritySnapshot,
        report: MigrationParityShadowReport,
    ) -> dict[str, object]:
        return {
            "vault_id": authority.vault_id,
            "owner_subject_id": authority.owner_subject_id,
            "authority_epoch": authority.authority_epoch,
            "window_reference_hash": report.window.window_reference_hash,
            "scope_hash": report.window.scope_hash,
            "denominator_source_hash": report.window.denominator_source_hash,
            "threshold_source_hash": report.window.threshold_source_hash,
            "expected_sample_count": report.window.expected_sample_count,
            "observed_sample_count": report.observed_sample_count,
            "comparison_count": len(report.observations),
            "duplicate_input_count": report.duplicate_input_count,
            "match_count": len(report.observations) - len(report.mismatches),
            "mismatch_count": len(report.mismatches),
            "blocking_mismatch_count": report.blocking_mismatch_count,
            "approved_m08_difference_count": report.approved_m08_difference_count,
            "unresolved_m08_difference_count": report.unresolved_m08_difference_count,
            "schema_version": OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SCHEMA_VERSION,
            "shadow_only": True,
            "command_effect_execution_count": 0,
            "object_copy_execution_count": 0,
            "provider_call_count": 0,
            "provider_cost_charged": False,
            "write_operation_count": 0,
            "cutover_allowed": False,
            "legacy_writer_retired": False,
        }

    def _assert_existing_report(
        self,
        cursor: Any,
        *,
        authority: OwnerTruthLegacyBackfillAuthoritySnapshot,
        report: MigrationParityShadowReport,
    ) -> None:
        cursor.execute(
            """
            SELECT vault_id, owner_subject_id, authority_epoch, window_reference_hash,
                scope_hash, denominator_source_hash, threshold_source_hash,
                expected_sample_count, observed_sample_count, comparison_count,
                duplicate_input_count, match_count, mismatch_count,
                blocking_mismatch_count, approved_m08_difference_count,
                unresolved_m08_difference_count, schema_version, shadow_only,
                command_effect_execution_count, object_copy_execution_count,
                provider_call_count, provider_cost_charged, write_operation_count,
                cutover_allowed, legacy_writer_retired
            FROM owner_truth.migration_parity_shadow_reports
            WHERE report_hash = %s
            FOR SHARE
            """,
            (report.report_hash,),
        )
        actual = self._normalized_report_row(cursor.fetchone())
        expected = self._expected_report_row(authority=authority, report=report)
        if actual != expected:
            raise OwnerTruthMigrationParityShadowConflict(
                "migration parity shadow report replay conflicts with immutable report"
            )

    def _assert_mismatches(self, cursor: Any, *, report: MigrationParityShadowReport) -> None:
        cursor.execute(
            """
            SELECT observation_hash, sample_id_hash, surface, dimension, mismatch_code,
                severity, legacy_value_hash, v4_value_hash, allowance_status,
                allowance_reason_code, approval_reference_hash, allowance_expires_at
            FROM owner_truth.migration_parity_shadow_mismatches
            WHERE report_hash = %s
            ORDER BY observation_hash ASC
            """,
            (report.report_hash,),
        )
        actual = [self._normalized_mismatch_row(row) for row in cursor.fetchall()]
        expected = [
            self._expected_mismatch_row(report=report, mismatch=mismatch)
            for mismatch in sorted(
                report.mismatches, key=lambda item: item.observation.observation_hash
            )
        ]
        if actual != expected:
            raise OwnerTruthMigrationParityShadowConflict(
                "migration parity shadow mismatches are incomplete or conflict with immutable report"
            )

    @staticmethod
    def _expected_mismatch_row(
        *,
        report: MigrationParityShadowReport,
        mismatch: MigrationParityMismatch,
    ) -> dict[str, object]:
        del report
        allowance = mismatch.allowance
        return {
            "observation_hash": mismatch.observation.observation_hash,
            "sample_id_hash": mismatch.observation.sample_id_hash,
            "surface": mismatch.observation.surface.value,
            "dimension": mismatch.observation.dimension.value,
            "mismatch_code": mismatch.observation.mismatch_code.value,
            "severity": mismatch.observation.severity.value,
            "legacy_value_hash": mismatch.observation.legacy_value_hash,
            "v4_value_hash": mismatch.observation.v4_value_hash,
            "allowance_status": mismatch.allowance_status,
            "allowance_reason_code": None if allowance is None else allowance.reason_code,
            "approval_reference_hash": None if allowance is None else allowance.approval_reference_hash,
            "allowance_expires_at": None if allowance is None else _timestamp(allowance.expires_at),
        }

    @staticmethod
    def _normalized_report_row(row: object) -> dict[str, object]:
        actual = dict(_mapping(row))
        if not actual:
            return {}
        for field in (
            "vault_id",
            "owner_subject_id",
            "window_reference_hash",
            "scope_hash",
            "denominator_source_hash",
            "threshold_source_hash",
            "schema_version",
        ):
            actual[field] = str(actual.get(field) or "")
        for field in (
            "authority_epoch",
            "expected_sample_count",
            "observed_sample_count",
            "comparison_count",
            "duplicate_input_count",
            "match_count",
            "mismatch_count",
            "blocking_mismatch_count",
            "approved_m08_difference_count",
            "unresolved_m08_difference_count",
            "command_effect_execution_count",
            "object_copy_execution_count",
            "provider_call_count",
            "write_operation_count",
        ):
            actual[field] = int(actual.get(field) or 0)
        for field in (
            "shadow_only",
            "provider_cost_charged",
            "cutover_allowed",
            "legacy_writer_retired",
        ):
            actual[field] = bool(actual.get(field))
        return actual

    @staticmethod
    def _normalized_mismatch_row(row: object) -> dict[str, object]:
        actual = dict(_mapping(row))
        if not actual:
            return {}
        for field in (
            "observation_hash",
            "sample_id_hash",
            "surface",
            "dimension",
            "mismatch_code",
            "severity",
            "allowance_status",
        ):
            actual[field] = str(actual.get(field) or "")
        for field in (
            "legacy_value_hash",
            "v4_value_hash",
            "allowance_reason_code",
            "approval_reference_hash",
        ):
            value = actual.get(field)
            actual[field] = None if value is None else str(value)
        actual["allowance_expires_at"] = _timestamp(actual.get("allowance_expires_at"))
        return actual

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthMigrationParityShadowService:
    """Owner-only, default-off entry point for C05 evidence persistence."""

    def __init__(self, store: OwnerTruthMigrationParityShadowStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def shadow(
        self,
        *,
        context: OwnerTruthCommandContext,
        window: MigrationParityComparisonWindow,
        observations: Iterable[MigrationParityObservation],
        allowances: Iterable[MigrationParityAllowance] = (),
        as_of: Any,
    ) -> OwnerTruthMigrationParityShadowRun:
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthMigrationParityShadowAccessDenied(
                "only the Vault Owner may persist a migration parity report"
            )
        if not self._enabled:
            raise OwnerTruthMigrationParityShadowUnavailable(
                "migration parity shadow persistence is disabled"
            )
        normalized_observations = tuple(observations)
        normalized_allowances = tuple(allowances)
        report = build_migration_parity_shadow_report(
            window=window,
            observations=normalized_observations,
            allowances=normalized_allowances,
            as_of=as_of,
        )
        command_hash = _digest(
            {
                "allowanceHashes": [
                    item.observation_hash
                    for item in normalized_allowances
                    if isinstance(item, MigrationParityAllowance)
                ],
                "observationHashes": [
                    item.observation_hash
                    for item in normalized_observations
                    if isinstance(item, MigrationParityObservation)
                ],
                "reportHash": report.report_hash,
                "workflow": OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SERVICE_SCHEMA_VERSION,
            }
        )
        with self._request_unit_of_work(
            correlation_id="owner-truth-migration-parity-shadow-" + command_hash[:16],
            command_id=command_hash,
        ):
            repository = self._store.owner_truth_migration_parity_shadow_repository()
            authority = repository.read_authority(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
            )
            _assert_report_scope(authority=authority, report=report)
            return repository.persist(authority=authority, report=report)

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


def _assert_report_scope(
    *,
    authority: OwnerTruthLegacyBackfillAuthoritySnapshot,
    report: MigrationParityShadowReport,
) -> None:
    if not isinstance(authority, OwnerTruthLegacyBackfillAuthoritySnapshot):
        raise OwnerTruthMigrationParityShadowConflict("active Vault authority is required")
    if not isinstance(report, MigrationParityShadowReport):
        raise OwnerTruthMigrationParityShadowConflict("migration parity shadow report is required")
    expected_scope_hash = build_migration_parity_scope_hash(
        vault_id=authority.vault_id,
        owner_subject_id=authority.owner_subject_id,
        authority_epoch=authority.authority_epoch,
    )
    if report.window.scope_hash != expected_scope_hash:
        raise OwnerTruthMigrationParityShadowConflict(
            "migration parity report does not match the active Owner/Vault authority scope"
        )


def migration_parity_shadow_summary(
    run: OwnerTruthMigrationParityShadowRun,
) -> dict[str, object]:
    if not isinstance(run, OwnerTruthMigrationParityShadowRun):
        raise OwnerTruthMigrationParityShadowError("migration parity shadow run is required")
    return run.public_summary()


__all__ = [
    "InMemoryOwnerTruthMigrationParityShadowRepository",
    "OWNER_TRUTH_MIGRATION_PARITY_SHADOW_SERVICE_SCHEMA_VERSION",
    "OwnerTruthMigrationParityShadowAccessDenied",
    "OwnerTruthMigrationParityShadowConflict",
    "OwnerTruthMigrationParityShadowRepository",
    "OwnerTruthMigrationParityShadowRun",
    "OwnerTruthMigrationParityShadowService",
    "OwnerTruthMigrationParityShadowUnavailable",
    "PostgresOwnerTruthMigrationParityShadowRepository",
    "migration_parity_shadow_summary",
]
