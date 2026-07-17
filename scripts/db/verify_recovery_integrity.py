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


OWNER_TABLES = (
    "auth_sessions",
    "archive_items",
    "care_snapshots",
    "digital_human_sessions",
    "echo_delayed_replies",
    "family_members",
    "kb_change_feed_state",
    "kb_changes",
    "kb_operation_receipts",
    "kb_snapshots",
    "mailbox_letters",
    "memories",
    "password_credentials",
    "profiles",
    "push_device_tokens",
    "voice_clone_slots",
    "voice_profiles",
)


def fetch_scalar(cursor, query, params=()):
    cursor.execute(query, params)
    row = cursor.fetchone()
    if row is None:
        return 0
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


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
                public_tables = fetch_scalar(
                    cursor,
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'",
                )
                existing_rows = cursor.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
                ).fetchall()
                existing_tables = {str(row["table_name"]) for row in existing_rows}
                row_counts = {}
                orphan_count = 0
                purged_violation_count = 0
                for table in sorted(existing_tables):
                    row_counts[table] = int(
                        fetch_scalar(cursor, sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
                    )
                for table in OWNER_TABLES:
                    if table not in existing_tables:
                        continue
                    orphan_count += int(
                        fetch_scalar(
                            cursor,
                            sql.SQL(
                                "SELECT COUNT(*) FROM {table} child "
                                "LEFT JOIN users owner ON owner.id = child.user_id "
                                "WHERE child.user_id IS NOT NULL AND owner.id IS NULL"
                            ).format(table=sql.Identifier(table)),
                        )
                    )
                    purged_violation_count += int(
                        fetch_scalar(
                            cursor,
                            sql.SQL(
                                "SELECT COUNT(*) FROM {table} child "
                                "JOIN users owner ON owner.id = child.user_id "
                                "WHERE child.user_id IS NOT NULL "
                                "AND owner.payload->>'deletionState' = 'purged'"
                            ).format(table=sql.Identifier(table)),
                        )
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
            "schemaVersion": 1,
            "schemaHead": migration.get("appliedHead"),
            "targetSchemaHead": migration.get("expectedHead"),
            "relationCount": int(public_tables),
            "rowCounts": row_counts,
            "orphanOwnerCount": orphan_count,
            "invalidPayloadHashCount": invalid_hash_count,
            "purgedOwnerViolationCount": purged_violation_count,
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
