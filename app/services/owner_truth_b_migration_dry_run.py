"""QA-gated orchestration for the no-write B migration dry run."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import json
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol

from app.domain.owner_truth.b_migration_dry_run import (
    OWNER_TRUTH_B_MIGRATION_DRY_RUN_SCHEMA_VERSION,
    OwnerTruthBMigrationDisposition,
    OwnerTruthBMigrationDryRunEntry,
    OwnerTruthBMigrationDryRunError,
    OwnerTruthBMigrationDryRunReport,
    build_owner_truth_b_migration_dry_run_report,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_legacy_backfill import (
    OwnerTruthLegacyBackfillPlanService,
    OwnerTruthLegacyBackfillPlanRun,
    OwnerTruthLegacyBackfillUnavailable,
)


class OwnerTruthBMigrationDryRunUnavailable(OwnerTruthLegacyBackfillUnavailable):
    """The B dry-run route remains disabled outside an explicit QA decision."""


class OwnerTruthBMigrationDryRunConflict(OwnerTruthBMigrationDryRunError):
    """A persisted report identity was reused with different immutable content."""


@dataclass(frozen=True)
class OwnerTruthBMigrationDryRunResult:
    outcome: str
    plan_outcome: str
    report: OwnerTruthBMigrationDryRunReport

    def public_summary(self) -> dict[str, object]:
        summary = self.report.summary()
        summary.update(
            {
                "outcome": self.outcome,
                "planOutcome": self.plan_outcome,
                "schemaVersion": OWNER_TRUTH_B_MIGRATION_DRY_RUN_SCHEMA_VERSION,
            }
        )
        return summary


@dataclass(frozen=True)
class OwnerTruthBMigrationDryRunReceipt:
    outcome: str
    report: OwnerTruthBMigrationDryRunReport


class OwnerTruthBMigrationDryRunRepository(Protocol):
    def read(
        self,
        *,
        owner_subject_id: str,
        report_id: str,
    ) -> OwnerTruthBMigrationDryRunReport:
        ...

    def persist(
        self,
        *,
        owner_subject_id: str,
        plan_run: OwnerTruthLegacyBackfillPlanRun,
        report: OwnerTruthBMigrationDryRunReport,
    ) -> OwnerTruthBMigrationDryRunReceipt:
        ...


class OwnerTruthBMigrationDryRunStore(Protocol):
    def owner_truth_b_migration_dry_run_repository(
        self,
    ) -> OwnerTruthBMigrationDryRunRepository:
        ...


def _assert_report_matches_plan_run(
    *,
    owner_subject_id: str,
    plan_run: OwnerTruthLegacyBackfillPlanRun,
    report: OwnerTruthBMigrationDryRunReport,
) -> None:
    if not isinstance(plan_run, OwnerTruthLegacyBackfillPlanRun):
        raise OwnerTruthBMigrationDryRunError("legacy admission plan result is required")
    if not isinstance(report, OwnerTruthBMigrationDryRunReport):
        raise OwnerTruthBMigrationDryRunError("B migration dry run report is required")
    plan = plan_run.plan
    if (
        owner_subject_id != plan.owner_subject_id
        or report.inventory_run_id != plan_run.inventory_run_id
        or report.vault_id != plan.vault_id
        or report.owner_subject_id != plan.owner_subject_id
        or report.authority_epoch != plan.authority_epoch
        or report.inventory_hash != plan.inventory_hash
        or report.plan_id != plan.plan_id
        or report.plan_hash != plan.plan_hash
        or report.scope_hash != plan.scope_hash
    ):
        raise OwnerTruthBMigrationDryRunConflict(
            "B migration dry run report does not match immutable admission plan"
        )


def _persisted_plan_matches_report(
    plan_row: object,
    *,
    report: OwnerTruthBMigrationDryRunReport,
) -> bool:
    if not isinstance(plan_row, Mapping):
        return False
    authority_epoch = plan_row.get("authority_epoch")
    try:
        persisted_authority_epoch = int(authority_epoch)
    except (TypeError, ValueError):
        return False
    return (
        str(plan_row.get("inventory_run_id") or "") == report.inventory_run_id
        and str(plan_row.get("vault_id") or "") == report.vault_id
        and str(plan_row.get("owner_subject_id") or "") == report.owner_subject_id
        and persisted_authority_epoch == report.authority_epoch
        and str(plan_row.get("inventory_hash") or "") == report.inventory_hash
        and str(plan_row.get("plan_hash") or "") == report.plan_hash
        and str(plan_row.get("scope_hash") or "") == report.scope_hash
    )


class InMemoryOwnerTruthBMigrationDryRunRepository:
    """Semantic double with append-only, idempotent value-free report storage."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._reports: dict[str, OwnerTruthBMigrationDryRunReport] = {}

    def persist(
        self,
        *,
        owner_subject_id: str,
        plan_run: OwnerTruthLegacyBackfillPlanRun,
        report: OwnerTruthBMigrationDryRunReport,
    ) -> OwnerTruthBMigrationDryRunReceipt:
        _assert_report_matches_plan_run(
            owner_subject_id=owner_subject_id,
            plan_run=plan_run,
            report=report,
        )
        with self._lock:
            existing = self._reports.get(report.report_id)
            if existing is not None:
                if existing != report:
                    raise OwnerTruthBMigrationDryRunConflict(
                        "B migration dry run id has incompatible immutable report"
                    )
                return OwnerTruthBMigrationDryRunReceipt(
                    outcome="deduplicated",
                    report=existing,
                )
            self._reports[report.report_id] = report
            return OwnerTruthBMigrationDryRunReceipt(outcome="created", report=report)

    def read(
        self,
        *,
        owner_subject_id: str,
        report_id: str,
    ) -> OwnerTruthBMigrationDryRunReport:
        with self._lock:
            report = self._reports.get(str(report_id))
            if report is None or report.owner_subject_id != str(owner_subject_id):
                raise OwnerTruthBMigrationDryRunConflict(
                    "B migration dry-run report is unavailable for this Owner"
                )
            return report

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "reportCount": len(self._reports),
                "reports": [
                    report.summary()
                    for report in sorted(self._reports.values(), key=lambda item: item.report_id)
                ],
            }


class PostgresOwnerTruthBMigrationDryRunRepository:
    """Append-only B dry-run persistence; it has no formal-memory write path."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def persist(
        self,
        *,
        owner_subject_id: str,
        plan_run: OwnerTruthLegacyBackfillPlanRun,
        report: OwnerTruthBMigrationDryRunReport,
    ) -> OwnerTruthBMigrationDryRunReceipt:
        _assert_report_matches_plan_run(
            owner_subject_id=owner_subject_id,
            plan_run=plan_run,
            report=report,
        )
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT id, inventory_run_id, vault_id, owner_subject_id,
                       authority_epoch, inventory_hash, plan_hash, scope_hash
                  FROM owner_truth.legacy_migration_backfill_plans
                 WHERE id = %s
                 FOR SHARE
                """,
                (report.plan_id,),
            )
            plan_row = cursor.fetchone()
            if not _persisted_plan_matches_report(plan_row, report=report):
                raise OwnerTruthBMigrationDryRunConflict(
                    "persisted B migration plan no longer matches report checkpoint"
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.b_migration_dry_run_reports (
                    id, inventory_run_id, plan_id, vault_id, owner_subject_id,
                    authority_epoch, inventory_hash, plan_hash, scope_hash,
                    report_hash, entry_count, disposition_counts,
                    owner_review_required_count, formal_memory_write_count,
                    target_state, schema_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, 0, 'notCreated', %s
                )
                ON CONFLICT (inventory_run_id, plan_id, authority_epoch, report_hash)
                DO NOTHING
                RETURNING id
                """,
                self._adapt_params(
                    (
                        report.report_id,
                        report.inventory_run_id,
                        report.plan_id,
                        report.vault_id,
                        report.owner_subject_id,
                        report.authority_epoch,
                        report.inventory_hash,
                        report.plan_hash,
                        report.scope_hash,
                        report.report_hash,
                        len(report.entries),
                        report.disposition_counts,
                        report.requires_owner_review_count,
                        OWNER_TRUTH_B_MIGRATION_DRY_RUN_SCHEMA_VERSION,
                    )
                ),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                for entry in report.entries:
                    cursor.execute(
                        """
                        INSERT INTO owner_truth.b_migration_dry_run_entries (
                            report_id, domain, legacy_id_hash, record_hash,
                            admission_action, disposition, requires_owner_review,
                            reason_code, semantic_field_policy, target_state
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'notCreated')
                        """,
                        self._adapt_params(
                            (
                                report.report_id,
                                entry.domain,
                                entry.legacy_id_hash,
                                entry.record_hash,
                                entry.admission_action.value,
                                entry.disposition.value,
                                entry.requires_owner_review,
                                entry.reason_code,
                                {"fields": list(entry.semantic_field_policy)},
                            )
                        ),
                    )
                return OwnerTruthBMigrationDryRunReceipt(outcome="created", report=report)
            cursor.execute(
                """
                SELECT id, report_hash, entry_count, disposition_counts,
                       owner_review_required_count, formal_memory_write_count,
                       target_state, schema_version
                  FROM owner_truth.b_migration_dry_run_reports
                 WHERE inventory_run_id = %s
                   AND plan_id = %s
                   AND authority_epoch = %s
                   AND report_hash = %s
                 FOR SHARE
                """,
                (
                    report.inventory_run_id,
                    report.plan_id,
                    report.authority_epoch,
                    report.report_hash,
                ),
            )
            existing = cursor.fetchone()
            if not isinstance(existing, dict) or (
                str(existing.get("id") or "") != report.report_id
                or int(existing.get("entry_count") or -1) != len(report.entries)
                or int(existing.get("owner_review_required_count") or -1)
                != report.requires_owner_review_count
                or int(existing.get("formal_memory_write_count") or -1) != 0
                or str(existing.get("target_state") or "") != "notCreated"
                or str(existing.get("schema_version") or "")
                != OWNER_TRUTH_B_MIGRATION_DRY_RUN_SCHEMA_VERSION
            ):
                raise OwnerTruthBMigrationDryRunConflict(
                    "existing B migration dry run receipt has incompatible immutable content"
                )
            return OwnerTruthBMigrationDryRunReceipt(outcome="deduplicated", report=report)

    def read(
        self,
        *,
        owner_subject_id: str,
        report_id: str,
    ) -> OwnerTruthBMigrationDryRunReport:
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT id, inventory_run_id, plan_id, vault_id, owner_subject_id,
                       authority_epoch, inventory_hash, plan_hash, scope_hash,
                       report_hash
                  FROM owner_truth.b_migration_dry_run_reports
                 WHERE id = %s AND owner_subject_id = %s
                 FOR SHARE
                """,
                (report_id, owner_subject_id),
            )
            row = cursor.fetchone()
            if not isinstance(row, dict):
                raise OwnerTruthBMigrationDryRunConflict(
                    "B migration dry-run report is unavailable for this Owner"
                )
            cursor.execute(
                """
                SELECT domain, legacy_id_hash, record_hash, admission_action,
                       disposition, requires_owner_review, reason_code,
                       semantic_field_policy
                  FROM owner_truth.b_migration_dry_run_entries
                 WHERE report_id = %s
                 ORDER BY domain ASC, legacy_id_hash ASC
                """,
                (report_id,),
            )
            entries = tuple(
                OwnerTruthBMigrationDryRunEntry(
                    domain=str(entry["domain"]),
                    legacy_id_hash=str(entry["legacy_id_hash"]),
                    record_hash=str(entry["record_hash"]),
                    admission_action=str(entry["admission_action"]),
                    disposition=OwnerTruthBMigrationDisposition(
                        str(entry["disposition"])
                    ),
                    requires_owner_review=bool(entry["requires_owner_review"]),
                    reason_code=str(entry["reason_code"]),
                    semantic_field_policy=tuple(
                        self._json_object(entry["semantic_field_policy"]).get("fields")
                        or ()
                    ),
                )
                for entry in cursor.fetchall()
            )
        return OwnerTruthBMigrationDryRunReport(
            report_id=str(row["id"]),
            inventory_run_id=str(row["inventory_run_id"]),
            vault_id=str(row["vault_id"]),
            owner_subject_id=str(row["owner_subject_id"]),
            authority_epoch=int(row["authority_epoch"]),
            inventory_hash=str(row["inventory_hash"]),
            plan_id=str(row["plan_id"]),
            plan_hash=str(row["plan_hash"]),
            scope_hash=str(row["scope_hash"]),
            entries=entries,
            report_hash=str(row["report_hash"]),
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - psycopg is production-only here
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)

    @staticmethod
    def _adapt_params(values: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            import json

            return tuple(
                json.dumps(value, ensure_ascii=True, sort_keys=True)
                if isinstance(value, dict)
                else value
                for value in values
            )
        return tuple(Jsonb(value) if isinstance(value, dict) else value for value in values)

    @staticmethod
    def _json_object(value: object) -> dict[str, object]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except ValueError:
                return {}
            return decoded if isinstance(decoded, dict) else {}
        return {}


class OwnerTruthBMigrationDryRunService:
    """Build an immutable no-write report after the existing admission plan."""

    def __init__(self, store: Any, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def dry_run(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthBMigrationDryRunResult:
        if not self._enabled:
            raise OwnerTruthBMigrationDryRunUnavailable("B migration dry run is disabled")
        plan_run = OwnerTruthLegacyBackfillPlanService(
            self._store,
            enabled=True,
        ).plan(context=context)
        if not isinstance(plan_run, OwnerTruthLegacyBackfillPlanRun):
            raise OwnerTruthBMigrationDryRunError("legacy admission plan result is required")
        report = build_owner_truth_b_migration_dry_run_report(
            inventory_run_id=plan_run.inventory_run_id,
            plan=plan_run.plan,
        )
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-b-migration-dry-run-{report.report_id[:16]}",
            command_id=report.report_id,
        ):
            receipt = self._store.owner_truth_b_migration_dry_run_repository().persist(
                owner_subject_id=context.owner_subject_id,
                plan_run=plan_run,
                report=report,
            )
        return OwnerTruthBMigrationDryRunResult(
            outcome=receipt.outcome,
            plan_outcome=plan_run.outcome,
            report=receipt.report,
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


def owner_truth_b_migration_dry_run_summary(
    result: OwnerTruthBMigrationDryRunResult,
) -> dict[str, object]:
    if not isinstance(result, OwnerTruthBMigrationDryRunResult):
        raise OwnerTruthBMigrationDryRunError("B migration dry run result is required")
    return result.public_summary()


__all__ = [
    "OwnerTruthBMigrationDryRunResult",
    "OwnerTruthBMigrationDryRunReceipt",
    "OwnerTruthBMigrationDryRunRepository",
    "OwnerTruthBMigrationDryRunConflict",
    "OwnerTruthBMigrationDryRunService",
    "OwnerTruthBMigrationDryRunUnavailable",
    "InMemoryOwnerTruthBMigrationDryRunRepository",
    "PostgresOwnerTruthBMigrationDryRunRepository",
    "owner_truth_b_migration_dry_run_summary",
]
