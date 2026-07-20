#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.db.recovery import (
    RecoveryContractError,
    verify_integrity_metrics,
    write_recovery_record_atomic,
)


def fetch_scalar(cursor, query, params=()):
    cursor.execute(query, params)
    row = cursor.fetchone()
    if row is None:
        return 0
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def qualified_table_name(schema, table):
    return f"{schema}.{table}"


def base_tables(cursor, schema):
    rows = cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """,
        (schema,),
    ).fetchall()
    return tuple(str(row["table_name"]) for row in rows)


def table_columns(cursor, schema, table):
    rows = cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        ORDER BY column_name
        """,
        (schema, table),
    ).fetchall()
    return frozenset(str(row["column_name"]) for row in rows)


def table_count(cursor, sql, schema, table):
    return int(
        fetch_scalar(
            cursor,
            sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(schema, table)),
        )
    )


def count_public_owner_orphans(cursor, sql, table):
    return int(
        fetch_scalar(
            cursor,
            sql.SQL(
                "SELECT COUNT(*) FROM {table} child "
                "LEFT JOIN public.users owner ON owner.id = child.user_id "
                "WHERE child.user_id IS NOT NULL AND owner.id IS NULL"
            ).format(table=sql.Identifier("public", table)),
        )
    )


def count_public_purged_owner_violations(cursor, sql, table):
    return int(
        fetch_scalar(
            cursor,
            sql.SQL(
                "SELECT COUNT(*) FROM {table} child "
                "JOIN public.users owner ON owner.id = child.user_id "
                "WHERE child.user_id IS NOT NULL "
                "AND owner.payload->>'deletionState' = 'purged'"
            ).format(table=sql.Identifier("public", table)),
        )
    )


def audit_public_direct_user_id(cursor, sql):
    """Audit every legacy public base table that directly stores a user ID."""

    checked = []
    orphan_counts = {}
    purged_counts = {}
    for table in base_tables(cursor, "public"):
        if "user_id" not in table_columns(cursor, "public", table):
            continue
        qualified = qualified_table_name("public", table)
        checked.append(qualified)
        orphan_counts[qualified] = count_public_owner_orphans(cursor, sql, table)
        purged_counts[qualified] = count_public_purged_owner_violations(cursor, sql, table)
    return {
        "status": "complete",
        "checkedTables": checked,
        "orphanOwnerCountsByTable": orphan_counts,
        "purgedOwnerViolationCountsByTable": purged_counts,
    }


def audit_owner_truth_vault_scope(cursor, sql):
    """Audit Owner Truth rows against their Vault root without inspecting values."""

    checked = []
    missing_vault_counts = {}
    owner_mismatch_counts = {}
    unclassified = []
    for table in base_tables(cursor, "owner_truth"):
        qualified = qualified_table_name("owner_truth", table)
        columns = table_columns(cursor, "owner_truth", table)
        checked.append(qualified)
        missing_vault_counts[qualified] = 0
        owner_mismatch_counts[qualified] = 0
        if table == "vaults":
            continue
        if "vault_id" in columns:
            missing_vault_counts[qualified] = int(
                fetch_scalar(
                    cursor,
                    sql.SQL(
                        "SELECT COUNT(*) FROM {table} child "
                        "LEFT JOIN owner_truth.vaults vault ON vault.vault_id = child.vault_id "
                        "WHERE vault.vault_id IS NULL"
                    ).format(table=sql.Identifier("owner_truth", table)),
                )
            )
            if "owner_subject_id" in columns:
                owner_mismatch_counts[qualified] = int(
                    fetch_scalar(
                        cursor,
                        sql.SQL(
                            "SELECT COUNT(*) FROM {table} child "
                            "JOIN owner_truth.vaults vault ON vault.vault_id = child.vault_id "
                            "WHERE child.owner_subject_id IS DISTINCT FROM vault.owner_subject_id"
                        ).format(table=sql.Identifier("owner_truth", table)),
                    )
                )
            continue
        if "run_id" in columns:
            missing_vault_counts[qualified] = int(
                fetch_scalar(
                    cursor,
                    sql.SQL(
                        "SELECT COUNT(*) FROM {table} child "
                        "LEFT JOIN owner_truth.legacy_migration_runs run ON run.id = child.run_id "
                        "LEFT JOIN owner_truth.vaults vault ON vault.vault_id = run.vault_id "
                        "WHERE run.id IS NULL OR vault.vault_id IS NULL"
                    ).format(table=sql.Identifier("owner_truth", table)),
                )
            )
            continue
        unclassified.append(qualified)
    return {
        "status": "unverified" if unclassified else "complete",
        "checkedTables": checked,
        "missingVaultCountsByTable": missing_vault_counts,
        "ownerSubjectMismatchCountsByTable": owner_mismatch_counts,
        "unclassifiedTables": unclassified,
        # Owner Truth currently has no enforced bridge to the public identity
        # root. Record that limitation instead of manufacturing a relationship.
        "identityRootStatus": "unverified",
    }


def audit_async_effects_operation_scope(cursor, sql):
    """Audit async rows against their operation root and list value-free exemptions."""

    checked = []
    missing_operation_counts = {}
    scope_mismatch_counts = {}
    unclassified = []
    exemptions = []
    root_vault_missing_count = 0
    root_owner_mismatch_count = 0
    required_child_columns = {"operation_id", "owner_subject_id", "vault_id", "authority_epoch"}
    for table in base_tables(cursor, "async_effects"):
        qualified = qualified_table_name("async_effects", table)
        columns = table_columns(cursor, "async_effects", table)
        if table == "worker_loss_observations":
            exemptions.append(
                {"table": qualified, "reason": "valueFreeRuntimeObservation"}
            )
            continue
        checked.append(qualified)
        missing_operation_counts[qualified] = 0
        scope_mismatch_counts[qualified] = 0
        if table == "operations":
            root_vault_missing_count = int(
                fetch_scalar(
                    cursor,
                    "SELECT COUNT(*) FROM async_effects.operations operation "
                    "LEFT JOIN owner_truth.vaults vault ON vault.vault_id = operation.vault_id "
                    "WHERE vault.vault_id IS NULL",
                )
            )
            root_owner_mismatch_count = int(
                fetch_scalar(
                    cursor,
                    "SELECT COUNT(*) FROM async_effects.operations operation "
                    "JOIN owner_truth.vaults vault ON vault.vault_id = operation.vault_id "
                    "WHERE operation.owner_subject_id IS DISTINCT FROM vault.owner_subject_id",
                )
            )
            continue
        if not required_child_columns.issubset(columns):
            unclassified.append(qualified)
            continue
        missing_operation_counts[qualified] = int(
            fetch_scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {table} child "
                    "LEFT JOIN async_effects.operations operation "
                    "ON operation.operation_id = child.operation_id "
                    "WHERE operation.operation_id IS NULL"
                ).format(table=sql.Identifier("async_effects", table)),
            )
        )
        scope_mismatch_counts[qualified] = int(
            fetch_scalar(
                cursor,
                sql.SQL(
                    "SELECT COUNT(*) FROM {table} child "
                    "JOIN async_effects.operations operation "
                    "ON operation.operation_id = child.operation_id "
                    "WHERE child.owner_subject_id IS DISTINCT FROM operation.owner_subject_id "
                    "OR child.vault_id IS DISTINCT FROM operation.vault_id "
                    "OR child.authority_epoch IS DISTINCT FROM operation.authority_epoch"
                ).format(table=sql.Identifier("async_effects", table)),
            )
        )
    return (
        {
            "status": "unverified" if unclassified else "complete",
            "checkedTables": checked,
            "missingOperationCountsByTable": missing_operation_counts,
            "scopeMismatchCountsByTable": scope_mismatch_counts,
            "unclassifiedTables": unclassified,
            "rootVaultMissingCount": root_vault_missing_count,
            "rootOwnerSubjectMismatchCount": root_owner_mismatch_count,
            # Operation epochs have no independent authority ledger yet.
            "rootAuthorityStatus": "unverified",
        },
        exemptions,
    )


def main():
    parser = argparse.ArgumentParser(description="Verify an isolated DreamJourney recovery database.")
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--cutoff-lsn", required=True)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--production-database", default="dreamjourney")
    parser.add_argument("--expected-schema-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        migration = PostgresMigrator(
            dsn=args.dsn,
            migrations_dir=default_migrations_dir(),
            build_id="recovery-integrity",
        ).verify()
        connection = psycopg.connect(args.dsn, row_factory=dict_row)
        try:
            with connection.cursor() as cursor:
                public_tables = base_tables(cursor, "public")
                existing_tables = set(public_tables)
                row_counts = {}
                for table in public_tables:
                    row_counts[table] = table_count(cursor, sql, "public", table)
                public_direct_user_audit = audit_public_direct_user_id(cursor, sql)
                owner_truth_audit = audit_owner_truth_vault_scope(cursor, sql)
                async_effects_audit, explicit_exemptions = audit_async_effects_operation_scope(
                    cursor,
                    sql,
                )
                orphan_count = sum(
                    public_direct_user_audit["orphanOwnerCountsByTable"].values()
                )
                purged_violation_count = sum(
                    public_direct_user_audit[
                        "purgedOwnerViolationCountsByTable"
                    ].values()
                )
                invalid_hash_count = 0
                if "kb_operation_receipts" in existing_tables:
                    invalid_hash_count += int(
                        fetch_scalar(
                            cursor,
                            "SELECT COUNT(*) FROM kb_operation_receipts "
                            "WHERE payload_hash !~ '^[0-9a-f]{64}$'",
                        )
                    )
                if "evidence_events" in existing_tables:
                    invalid_hash_count += int(
                        fetch_scalar(
                            cursor,
                            "SELECT COUNT(*) FROM evidence_events "
                            "WHERE payload_hash !~ '^[0-9a-f]{64}$'",
                        )
                    )
        finally:
            connection.close()

        metrics = {
            "schemaVersion": 3,
            "schemaHead": migration.get("appliedHead"),
            "targetSchemaHead": migration.get("expectedHead"),
            "relationCount": len(public_tables),
            "rowCounts": row_counts,
            "orphanOwnerCount": orphan_count,
            "invalidPayloadHashCount": invalid_hash_count,
            "purgedOwnerViolationCount": purged_violation_count,
            "auditDomains": {
                "publicDirectUserId": public_direct_user_audit,
                "ownerTruthVaultScope": owner_truth_audit,
                "asyncEffectsOperationScope": async_effects_audit,
            },
            "explicitExemptions": explicit_exemptions,
            "migrationState": migration.get("status"),
        }
        report = verify_integrity_metrics(
            metrics,
            expected_schema_head=args.expected_schema_head,
            backup_id=args.backup_id,
            cutoff_lsn=args.cutoff_lsn,
            target_database=args.target_database,
            production_database=args.production_database,
        )
        write_recovery_record_atomic(args.output, report)
    except (RecoveryContractError, OSError, ValueError, RuntimeError) as exc:
        code = exc.code if isinstance(exc, RecoveryContractError) else type(exc).__name__
        print(json.dumps({"schemaVersion": 1, "status": "failed", "errorCode": code}), file=sys.stderr)
        raise SystemExit(1) from exc
    except Exception as exc:  # CLI boundary: never emit a DSN-bearing provider traceback
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "failed",
                    "errorCode": "databaseVerificationFailed",
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "verified":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
