"""Default-off, bounded replay of legacy text into current Candidate review.

The executor is deliberately not a formal-memory migration writer.  A legacy
row can only become an immutable ``import`` Source plus the normal durable
candidate-extraction effect.  The Owner must still review any extracted
Candidate before a MemoryVersion can exist.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, ContextManager, Mapping, Protocol
from uuid import NAMESPACE_URL, uuid5

from app.domain.owner_truth.b_migration_dry_run import (
    OwnerTruthBMigrationDisposition,
    OwnerTruthBMigrationDryRunError,
    OwnerTruthBMigrationDryRunReport,
    require_owner_truth_b_migration_restore_compatible,
)
from app.domain.owner_truth.b_migration_execution import (
    OWNER_TRUTH_B_MIGRATION_EXECUTION_SCHEMA_VERSION,
    OwnerTruthBMigrationEntryCompletion,
    OwnerTruthBMigrationExecutionClaim,
    OwnerTruthBMigrationExecutionConflict,
    OwnerTruthBMigrationExecutionDisposition,
    OwnerTruthBMigrationExecutionEntry,
    OwnerTruthBMigrationExecutionError,
    OwnerTruthBMigrationExecutionResult,
    OwnerTruthBMigrationExecutionState,
    OwnerTruthBMigrationExecutionUnavailable,
    execution_run_id,
    execution_source_id,
)
from app.domain.owner_truth.legacy_migration import LegacyMigrationDomain
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
    OwnerTruthSourceAuthorityEpochConflict,
)
from app.services.owner_truth_b_migration_dry_run import (
    OwnerTruthBMigrationDryRunService,
)
from app.services.owner_truth_legacy_backfill import OwnerTruthLegacyBackfillPlanService
from app.services.owner_truth_source import OwnerTruthSourceAsyncEffectCommandService


_MAX_BATCH_SIZE = 25
_MAX_SOURCE_TEXT_CHARACTERS = 20_000
_TERMINAL_STATES = frozenset(
    state.value for state in OwnerTruthBMigrationExecutionState if state.is_terminal
)


def _persisted_execution_run_matches(
    run: object,
    *,
    expected_run_id: str,
    expected_report_hash: str,
    expected_entry_count: int,
    expected_authority_epoch: int,
) -> bool:
    if not isinstance(run, Mapping):
        return False
    try:
        entry_count = int(run.get("entry_count"))
        authority_epoch = int(run.get("authority_epoch"))
    except (TypeError, ValueError):
        return False
    return (
        str(run.get("id") or "") == expected_run_id
        and str(run.get("report_hash") or "") == expected_report_hash
        and entry_count == expected_entry_count
        and authority_epoch == expected_authority_epoch
    )


class OwnerTruthBMigrationExecutionRepository(Protocol):
    def prepare_and_claim(
        self,
        *,
        owner_subject_id: str,
        report: OwnerTruthBMigrationDryRunReport,
        batch_size: int,
    ) -> OwnerTruthBMigrationExecutionClaim:
        ...

    def complete_entry(
        self,
        *,
        owner_subject_id: str,
        run_id: str,
        completion: OwnerTruthBMigrationEntryCompletion,
    ) -> None:
        ...

    def summarize(
        self,
        *,
        owner_subject_id: str,
        run_id: str,
        batch_processed_count: int,
    ) -> OwnerTruthBMigrationExecutionResult:
        ...


class OwnerTruthBMigrationExecutionStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def owner_truth_b_migration_execution_repository(
        self,
    ) -> OwnerTruthBMigrationExecutionRepository:
        ...

    def owner_truth_legacy_migration_repository(self) -> Any:
        ...


def _validate_batch_size(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_BATCH_SIZE:
        raise OwnerTruthBMigrationExecutionError(
            f"batch_size must be an integer between 1 and {_MAX_BATCH_SIZE}"
        )
    return value


def _validate_report_scope(
    *,
    owner_subject_id: str,
    report: OwnerTruthBMigrationDryRunReport,
) -> None:
    if report.owner_subject_id != owner_subject_id:
        raise OwnerTruthBMigrationExecutionConflict(
            "B migration report does not belong to this Owner"
        )


def _run_summary(
    *,
    run_id: str,
    report_id: str,
    report_hash: str,
    states: list[str],
    batch_processed_count: int,
) -> OwnerTruthBMigrationExecutionResult:
    counts: dict[str, int] = {}
    for state in states:
        counts[state] = counts.get(state, 0) + 1
    checkpoint = 0
    for state in states:
        if state not in _TERMINAL_STATES:
            break
        checkpoint += 1
    retryable_count = counts.get(OwnerTruthBMigrationExecutionState.FAILED_RETRYABLE.value, 0)
    blocked_count = counts.get(
        OwnerTruthBMigrationExecutionState.BLOCKED_RECORD_CHANGED.value,
        0,
    )
    nonterminal_count = sum(
        count for state, count in counts.items() if state not in _TERMINAL_STATES
    )
    status = "blocked" if blocked_count else ("completed" if nonterminal_count == 0 else "running")
    return OwnerTruthBMigrationExecutionResult(
        run_id=run_id,
        report_id=report_id,
        report_hash=report_hash,
        status=status,
        batch_processed_count=batch_processed_count,
        checkpoint_ordinal=checkpoint,
        entry_count=len(states),
        disposition_counts={
            state: count
            for state, count in counts.items()
            if state not in {
                OwnerTruthBMigrationExecutionState.PENDING.value,
                OwnerTruthBMigrationExecutionState.PROCESSING.value,
            }
        },
        retryable_count=retryable_count,
    )


class InMemoryOwnerTruthBMigrationExecutionRepository:
    """Semantic checkpoint store for unit and API contract tests."""

    def __init__(
        self,
        *,
        authority_supplier: Callable[[str, str], Mapping[str, Any] | None],
    ) -> None:
        self._authority_supplier = authority_supplier
        self._lock = RLock()
        self._runs: dict[str, dict[str, Any]] = {}

    def prepare_and_claim(
        self,
        *,
        owner_subject_id: str,
        report: OwnerTruthBMigrationDryRunReport,
        batch_size: int,
    ) -> OwnerTruthBMigrationExecutionClaim:
        _validate_report_scope(owner_subject_id=owner_subject_id, report=report)
        limit = _validate_batch_size(batch_size)
        authority = self._authority_supplier(report.vault_id, owner_subject_id)
        if not isinstance(authority, Mapping):
            raise OwnerTruthBMigrationExecutionConflict("Owner Vault is not active")
        if (
            str(authority.get("ownerSubjectId") or authority.get("owner_subject_id") or "")
            != owner_subject_id
            or str(authority.get("status") or "active") != "active"
            or int(authority.get("authorityEpoch", authority.get("authority_epoch", -1)))
            != report.authority_epoch
        ):
            raise OwnerTruthBMigrationExecutionConflict(
                "Owner Vault authority changed after the approved dry run"
            )
        run_id = execution_run_id(report)
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                run = {
                    "report": report,
                    "entries": [
                        {
                            "entry": entry,
                            "state": OwnerTruthBMigrationExecutionState.PENDING.value,
                            "attemptCount": 0,
                            "completion": None,
                        }
                        for entry in report.entries
                    ],
                }
                self._runs[run_id] = run
            elif run["report"] != report:
                raise OwnerTruthBMigrationExecutionConflict(
                    "execution run id has incompatible dry-run content"
                )
            claims: list[OwnerTruthBMigrationExecutionEntry] = []
            if any(
                stored["state"]
                == OwnerTruthBMigrationExecutionState.BLOCKED_RECORD_CHANGED.value
                for stored in run["entries"]
            ):
                return OwnerTruthBMigrationExecutionClaim(
                    run_id=run_id,
                    report=report,
                    entries=(),
                )
            for ordinal, stored in enumerate(run["entries"]):
                if stored["state"] not in {
                    OwnerTruthBMigrationExecutionState.PENDING.value,
                    OwnerTruthBMigrationExecutionState.FAILED_RETRYABLE.value,
                }:
                    continue
                stored["state"] = OwnerTruthBMigrationExecutionState.PROCESSING.value
                stored["attemptCount"] += 1
                claims.append(
                    OwnerTruthBMigrationExecutionEntry(
                        ordinal=ordinal,
                        entry=stored["entry"],
                        state=OwnerTruthBMigrationExecutionState.PROCESSING,
                        attempt_count=stored["attemptCount"],
                    )
                )
                if len(claims) >= limit:
                    break
        return OwnerTruthBMigrationExecutionClaim(
            run_id=run_id,
            report=report,
            entries=tuple(claims),
        )

    def complete_entry(
        self,
        *,
        owner_subject_id: str,
        run_id: str,
        completion: OwnerTruthBMigrationEntryCompletion,
    ) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run["report"].owner_subject_id != owner_subject_id:
                raise OwnerTruthBMigrationExecutionConflict("execution run is unavailable")
            try:
                stored = run["entries"][completion.ordinal]
            except IndexError as error:
                raise OwnerTruthBMigrationExecutionConflict(
                    "execution entry ordinal is unavailable"
                ) from error
            if stored["state"] != OwnerTruthBMigrationExecutionState.PROCESSING.value:
                existing = stored.get("completion")
                if existing == completion:
                    return
                raise OwnerTruthBMigrationExecutionConflict(
                    "execution entry is not held by this batch"
                )
            stored["state"] = completion.state.value
            stored["completion"] = completion

    def summarize(
        self,
        *,
        owner_subject_id: str,
        run_id: str,
        batch_processed_count: int,
    ) -> OwnerTruthBMigrationExecutionResult:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run["report"].owner_subject_id != owner_subject_id:
                raise OwnerTruthBMigrationExecutionConflict("execution run is unavailable")
            states = [str(entry["state"]) for entry in run["entries"]]
        return _run_summary(
            run_id=run_id,
            report_id=run["report"].report_id,
            report_hash=run["report"].report_hash,
            states=states,
            batch_processed_count=batch_processed_count,
        )

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "runCount": len(self._runs),
                "runs": {
                    run_id: {
                        "reportId": run["report"].report_id,
                        "states": [entry["state"] for entry in run["entries"]],
                    }
                    for run_id, run in self._runs.items()
                },
            }


class PostgresOwnerTruthBMigrationExecutionRepository:
    """Checkpointed executor metadata on the caller's active transaction."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def prepare_and_claim(
        self,
        *,
        owner_subject_id: str,
        report: OwnerTruthBMigrationDryRunReport,
        batch_size: int,
    ) -> OwnerTruthBMigrationExecutionClaim:
        _validate_report_scope(owner_subject_id=owner_subject_id, report=report)
        limit = _validate_batch_size(batch_size)
        run_id = execution_run_id(report)
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT report.id
                  FROM owner_truth.b_migration_dry_run_reports AS report
                  JOIN owner_truth.vaults AS vault
                    ON vault.vault_id = report.vault_id
                 WHERE report.id = %s
                   AND report.owner_subject_id = %s
                   AND report.report_hash = %s
                   AND report.authority_epoch = %s
                   AND vault.owner_subject_id = report.owner_subject_id
                   AND vault.authority_epoch = report.authority_epoch
                   AND vault.status = 'active'
                 FOR SHARE OF report, vault
                """,
                (
                    report.report_id,
                    owner_subject_id,
                    report.report_hash,
                    report.authority_epoch,
                ),
            )
            if cursor.fetchone() is None:
                raise OwnerTruthBMigrationExecutionConflict(
                    "dry-run report or Owner authority no longer matches"
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.b_migration_execution_runs (
                    id, report_id, vault_id, owner_subject_id, authority_epoch,
                    report_hash, entry_count, schema_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (report_id) DO NOTHING
                """,
                (
                    run_id,
                    report.report_id,
                    report.vault_id,
                    report.owner_subject_id,
                    report.authority_epoch,
                    report.report_hash,
                    len(report.entries),
                    OWNER_TRUTH_B_MIGRATION_EXECUTION_SCHEMA_VERSION,
                ),
            )
            cursor.execute(
                """
                SELECT id, report_hash, entry_count, authority_epoch, state
                  FROM owner_truth.b_migration_execution_runs
                 WHERE report_id = %s
                 FOR UPDATE
                """,
                (report.report_id,),
            )
            run = cursor.fetchone()
            if not _persisted_execution_run_matches(
                run,
                expected_run_id=run_id,
                expected_report_hash=report.report_hash,
                expected_entry_count=len(report.entries),
                expected_authority_epoch=report.authority_epoch,
            ):
                raise OwnerTruthBMigrationExecutionConflict(
                    "existing execution run has incompatible immutable content"
                )
            for ordinal, entry in enumerate(report.entries):
                cursor.execute(
                    """
                    INSERT INTO owner_truth.b_migration_execution_entries (
                        run_id, ordinal, domain, legacy_id_hash, record_hash,
                        dry_run_disposition, state
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                    ON CONFLICT (run_id, ordinal) DO NOTHING
                    """,
                    (
                        run_id,
                        ordinal,
                        entry.domain,
                        entry.legacy_id_hash,
                        entry.record_hash,
                        entry.disposition.value,
                    ),
                )
            cursor.execute(
                """
                SELECT ordinal, domain, legacy_id_hash, record_hash,
                       dry_run_disposition, state, attempt_count
                  FROM owner_truth.b_migration_execution_entries
                 WHERE run_id = %s
                 ORDER BY ordinal ASC
                """,
                (run_id,),
            )
            persisted = cursor.fetchall()
            if len(persisted) != len(report.entries):
                raise OwnerTruthBMigrationExecutionConflict(
                    "execution checkpoint does not contain every dry-run entry"
                )
            for ordinal, row in enumerate(persisted):
                expected = report.entries[ordinal]
                if (
                    int(row["ordinal"]) != ordinal
                    or str(row["domain"]) != expected.domain
                    or str(row["legacy_id_hash"]) != expected.legacy_id_hash
                    or str(row["record_hash"]) != expected.record_hash
                    or str(row["dry_run_disposition"]) != expected.disposition.value
                ):
                    raise OwnerTruthBMigrationExecutionConflict(
                        "execution checkpoint entry conflicts with the dry run"
                    )
            claim_rows = []
            if str(run["state"]) not in {"completed", "blocked"}:
                claim_rows = [
                    row
                    for row in persisted
                    if str(row["state"])
                    in {
                        OwnerTruthBMigrationExecutionState.PENDING.value,
                        OwnerTruthBMigrationExecutionState.FAILED_RETRYABLE.value,
                    }
                ][:limit]
            claims: list[OwnerTruthBMigrationExecutionEntry] = []
            for row in claim_rows:
                ordinal = int(row["ordinal"])
                cursor.execute(
                    """
                    UPDATE owner_truth.b_migration_execution_entries
                       SET state = 'processing',
                           attempt_count = attempt_count + 1,
                           updated_at = NOW()
                     WHERE run_id = %s AND ordinal = %s
                       AND state IN ('pending', 'failedRetryable')
                    RETURNING attempt_count
                    """,
                    (run_id, ordinal),
                )
                updated = cursor.fetchone()
                if not isinstance(updated, dict):
                    raise OwnerTruthBMigrationExecutionConflict(
                        "execution entry claim changed concurrently"
                    )
                claims.append(
                    OwnerTruthBMigrationExecutionEntry(
                        ordinal=ordinal,
                        entry=report.entries[ordinal],
                        state=OwnerTruthBMigrationExecutionState.PROCESSING,
                        attempt_count=int(updated["attempt_count"]),
                    )
                )
            cursor.execute(
                """
                UPDATE owner_truth.b_migration_execution_runs
                   SET state = 'running', updated_at = NOW()
                 WHERE id = %s AND state IN ('pending', 'running')
                """,
                (run_id,),
            )
        return OwnerTruthBMigrationExecutionClaim(
            run_id=run_id,
            report=report,
            entries=tuple(claims),
        )

    def complete_entry(
        self,
        *,
        owner_subject_id: str,
        run_id: str,
        completion: OwnerTruthBMigrationEntryCompletion,
    ) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT entry.state, entry.attempt_count, run.owner_subject_id
                  FROM owner_truth.b_migration_execution_entries AS entry
                  JOIN owner_truth.b_migration_execution_runs AS run ON run.id = entry.run_id
                 WHERE entry.run_id = %s AND entry.ordinal = %s
                 FOR UPDATE OF entry
                """,
                (run_id, completion.ordinal),
            )
            row = cursor.fetchone()
            if not isinstance(row, dict) or str(row["owner_subject_id"]) != owner_subject_id:
                raise OwnerTruthBMigrationExecutionConflict("execution entry is unavailable")
            if str(row["state"]) != OwnerTruthBMigrationExecutionState.PROCESSING.value:
                cursor.execute(
                    """
                    SELECT receipt_hash
                      FROM owner_truth.b_migration_execution_receipts
                     WHERE run_id = %s AND ordinal = %s
                     ORDER BY attempt_count DESC LIMIT 1
                    """,
                    (run_id, completion.ordinal),
                )
                existing = cursor.fetchone()
                if isinstance(existing, dict) and str(existing["receipt_hash"]) == completion.receipt_hash:
                    return
                raise OwnerTruthBMigrationExecutionConflict(
                    "execution entry is not held by this batch"
                )
            attempt_count = int(row["attempt_count"])
            receipt_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"dreamjourney-b-migration:{run_id}:{completion.ordinal}:"
                    f"{attempt_count}:{completion.receipt_hash}",
                )
            )
            cursor.execute(
                """
                UPDATE owner_truth.b_migration_execution_entries
                   SET state = %s, reason_code = %s,
                       source_id = %s, source_receipt_id = %s,
                       effect_operation_id = %s, updated_at = NOW()
                 WHERE run_id = %s AND ordinal = %s AND state = 'processing'
                """,
                (
                    completion.state.value,
                    completion.reason_code,
                    completion.source_id,
                    completion.source_receipt_id,
                    completion.effect_operation_id,
                    run_id,
                    completion.ordinal,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.b_migration_execution_receipts (
                    id, run_id, ordinal, attempt_count, disposition,
                    reason_code, receipt_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, ordinal, attempt_count) DO NOTHING
                """,
                (
                    receipt_id,
                    run_id,
                    completion.ordinal,
                    attempt_count,
                    completion.disposition.value,
                    completion.reason_code,
                    completion.receipt_hash,
                ),
            )

    def summarize(
        self,
        *,
        owner_subject_id: str,
        run_id: str,
        batch_processed_count: int,
    ) -> OwnerTruthBMigrationExecutionResult:
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT entry.state, run.report_id, run.report_hash
                  FROM owner_truth.b_migration_execution_entries AS entry
                  JOIN owner_truth.b_migration_execution_runs AS run ON run.id = entry.run_id
                 WHERE entry.run_id = %s AND run.owner_subject_id = %s
                 ORDER BY entry.ordinal ASC
                """,
                (run_id, owner_subject_id),
            )
            rows = cursor.fetchall()
            states = [str(row["state"]) for row in rows]
            if not rows:
                cursor.execute(
                    """
                    SELECT report_id, report_hash
                      FROM owner_truth.b_migration_execution_runs
                     WHERE id = %s AND owner_subject_id = %s
                    """,
                    (run_id, owner_subject_id),
                )
                run_row = cursor.fetchone()
                if not isinstance(run_row, dict):
                    raise OwnerTruthBMigrationExecutionConflict(
                        "execution run is unavailable"
                    )
            else:
                run_row = rows[0]
            result = _run_summary(
                run_id=run_id,
                report_id=str(run_row["report_id"]),
                report_hash=str(run_row["report_hash"]),
                states=states,
                batch_processed_count=batch_processed_count,
            )
            cursor.execute(
                """
                UPDATE owner_truth.b_migration_execution_runs
                   SET state = %s, checkpoint_ordinal = %s,
                       processed_count = %s, updated_at = NOW(),
                       completed_at = CASE WHEN %s IN ('completed', 'blocked')
                                           THEN COALESCE(completed_at, NOW())
                                           ELSE NULL END
                 WHERE id = %s AND owner_subject_id = %s
                   AND state NOT IN ('completed', 'blocked')
                """,
                (
                    result.status,
                    result.checkpoint_ordinal,
                    sum(1 for state in states if state in _TERMINAL_STATES),
                    result.status,
                    run_id,
                    owner_subject_id,
                ),
            )
        return result

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - psycopg is production-only here
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthBMigrationExecutionService:
    """Execute one resumable batch from an immutable, currently valid report."""

    def __init__(self, store: OwnerTruthBMigrationExecutionStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def execute_next_batch(
        self,
        *,
        context: OwnerTruthCommandContext,
        batch_size: int = 10,
        report_id: str | None = None,
    ) -> OwnerTruthBMigrationExecutionResult:
        if context.actor_subject_id != context.owner_subject_id:
            raise OwnerTruthBMigrationExecutionConflict(
                "only the Vault Owner may execute legacy review replay"
            )
        if not self._enabled:
            raise OwnerTruthBMigrationExecutionUnavailable(
                "B migration execution is disabled"
            )
        limit = _validate_batch_size(batch_size)
        report = self._resolve_report(context=context, report_id=report_id)
        command_id = f"b-migration-execute:{execution_run_id(report)}:{report.report_hash}"
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-{command_id}",
            command_id=command_id,
        ):
            repository = self._store.owner_truth_b_migration_execution_repository()
            claim = repository.prepare_and_claim(
                owner_subject_id=context.owner_subject_id,
                report=report,
                batch_size=limit,
            )
            legacy_repository = self._store.owner_truth_legacy_migration_repository()
            for claimed in claim.entries:
                completion = self._completion_for(
                    context=context,
                    claim=claim,
                    claimed=claimed,
                    legacy_repository=legacy_repository,
                )
                repository.complete_entry(
                    owner_subject_id=context.owner_subject_id,
                    run_id=claim.run_id,
                    completion=completion,
                )
            return repository.summarize(
                owner_subject_id=context.owner_subject_id,
                run_id=claim.run_id,
                batch_processed_count=len(claim.entries),
            )

    def _resolve_report(
        self,
        *,
        context: OwnerTruthCommandContext,
        report_id: str | None,
    ) -> OwnerTruthBMigrationDryRunReport:
        if report_id is None:
            return OwnerTruthBMigrationDryRunService(
                self._store,
                enabled=True,
            ).dry_run(context=context).report
        with self._request_unit_of_work(
            correlation_id=f"owner-truth-b-migration-report-{report_id}",
            command_id=f"b-migration-report:{report_id}",
        ):
            report = self._store.owner_truth_b_migration_dry_run_repository().read(
                owner_subject_id=context.owner_subject_id,
                report_id=report_id,
            )
        current_plan = OwnerTruthLegacyBackfillPlanService(
            self._store,
            enabled=True,
        ).plan(context=context).plan
        try:
            require_owner_truth_b_migration_restore_compatible(
                report=report,
                current_plan=current_plan,
            )
        except OwnerTruthBMigrationDryRunError as error:
            raise OwnerTruthBMigrationExecutionConflict(
                "legacy inventory, plan, or authority changed after the approved dry run"
            ) from error
        return report

    def _completion_for(
        self,
        *,
        context: OwnerTruthCommandContext,
        claim: OwnerTruthBMigrationExecutionClaim,
        claimed: OwnerTruthBMigrationExecutionEntry,
        legacy_repository: Any,
    ) -> OwnerTruthBMigrationEntryCompletion:
        entry = claimed.entry
        if entry.disposition is OwnerTruthBMigrationDisposition.QUARANTINED:
            return OwnerTruthBMigrationEntryCompletion(
                ordinal=claimed.ordinal,
                disposition=OwnerTruthBMigrationExecutionDisposition.QUARANTINED,
                reason_code="legacyAuthorityOrOwnerEvidenceQuarantined",
            )
        if entry.disposition is OwnerTruthBMigrationDisposition.EXCLUDED:
            return OwnerTruthBMigrationEntryCompletion(
                ordinal=claimed.ordinal,
                disposition=OwnerTruthBMigrationExecutionDisposition.EXCLUDED,
                reason_code="legacyDomainExcluded",
            )
        domain = LegacyMigrationDomain(entry.domain)
        if domain not in {LegacyMigrationDomain.ARCHIVE_ITEM, LegacyMigrationDomain.MEMORY}:
            return OwnerTruthBMigrationEntryCompletion(
                ordinal=claimed.ordinal,
                disposition=OwnerTruthBMigrationExecutionDisposition.MANUAL_EVIDENCE_REVIEW,
                reason_code="legacyGraphOrReceiptRequiresManualEvidenceReview",
            )
        try:
            material = legacy_repository.read_replay_material(
                owner_subject_id=context.owner_subject_id,
                domain=domain,
                legacy_id_hash=entry.legacy_id_hash,
                record_hash=entry.record_hash,
            )
        except OwnerTruthBMigrationExecutionConflict:
            return OwnerTruthBMigrationEntryCompletion(
                ordinal=claimed.ordinal,
                disposition=OwnerTruthBMigrationExecutionDisposition.BLOCKED_RECORD_CHANGED,
                reason_code="legacyRecordChangedAfterDryRun",
            )
        if material.material_state != "textReady" or material.text is None:
            return OwnerTruthBMigrationEntryCompletion(
                ordinal=claimed.ordinal,
                disposition=OwnerTruthBMigrationExecutionDisposition.MANUAL_EVIDENCE_REVIEW,
                reason_code="legacyRecordHasNoReviewableText",
            )
        if len(material.text) > _MAX_SOURCE_TEXT_CHARACTERS:
            return OwnerTruthBMigrationEntryCompletion(
                ordinal=claimed.ordinal,
                disposition=OwnerTruthBMigrationExecutionDisposition.MANUAL_EVIDENCE_REVIEW,
                reason_code="legacyTextRequiresLosslessChunkReview",
            )
        source_id = execution_source_id(
            report_id=claim.report.report_id,
            domain=entry.domain,
            legacy_id_hash=entry.legacy_id_hash,
            record_hash=entry.record_hash,
        )
        try:
            result = OwnerTruthSourceAsyncEffectCommandService(self._store).create_text_source(
                command=CreateTextSourceCommand(
                    command_id=(
                        f"b-migration:{claim.run_id}:{entry.domain}:{entry.legacy_id_hash}"
                    ),
                    source_id=source_id,
                    expected_version=0,
                    text=material.text,
                    metadata={
                        "origin": "legacyBMigration",
                        "migrationReportId": claim.report.report_id,
                        "legacyDomain": entry.domain,
                        "legacyIdHash": entry.legacy_id_hash,
                        "legacyRecordHash": entry.record_hash,
                        "reviewRequired": True,
                        "semanticPolicy": "extractUnknownThenOwnerReview",
                    },
                    source_kind="import",
                    expected_authority_epoch=claim.report.authority_epoch,
                ),
                context=context,
            )
        except OwnerTruthSourceAuthorityEpochConflict:
            return OwnerTruthBMigrationEntryCompletion(
                ordinal=claimed.ordinal,
                disposition=OwnerTruthBMigrationExecutionDisposition.BLOCKED_RECORD_CHANGED,
                reason_code="ownerAuthorityChangedBeforeSourceReplay",
            )
        except Exception:
            return OwnerTruthBMigrationEntryCompletion(
                ordinal=claimed.ordinal,
                disposition=OwnerTruthBMigrationExecutionDisposition.FAILED_RETRYABLE,
                reason_code="sourceReplayTransactionFailed",
            )
        return OwnerTruthBMigrationEntryCompletion(
            ordinal=claimed.ordinal,
            disposition=OwnerTruthBMigrationExecutionDisposition.SOURCE_QUEUED_FOR_REVIEW,
            reason_code="currentSourceAndCandidateReviewReplayQueued",
            source_id=result.source.source_id,
            source_receipt_id=result.source.receipt_id,
            effect_operation_id=result.effect.operation_id,
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


def owner_truth_b_migration_execution_summary(
    result: OwnerTruthBMigrationExecutionResult,
) -> dict[str, object]:
    if not isinstance(result, OwnerTruthBMigrationExecutionResult):
        raise OwnerTruthBMigrationExecutionError("B migration execution result is required")
    return result.public_summary()


__all__ = [
    "InMemoryOwnerTruthBMigrationExecutionRepository",
    "OwnerTruthBMigrationExecutionRepository",
    "OwnerTruthBMigrationExecutionService",
    "OwnerTruthBMigrationExecutionStore",
    "PostgresOwnerTruthBMigrationExecutionRepository",
    "owner_truth_b_migration_execution_summary",
]
