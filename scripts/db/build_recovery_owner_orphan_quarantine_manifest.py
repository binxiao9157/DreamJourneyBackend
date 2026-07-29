#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
from typing import Tuple

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.recovery import (
    RecoveryContractError,
    validate_recovery_target,
    write_recovery_record_atomic,
)
from app.db.recovery_owner_orphan_quarantine import (
    RecoveryOwnerOrphanCandidate,
    RecoveryOwnerOrphanTableInventory,
    build_owner_orphan_quarantine_manifest,
)


def _row_value(row, name, index):
    if isinstance(row, dict):
        return row[name]
    return row[index]


def _validate_dsn_target(dsn: str, target_database: str) -> None:
    parameters = conninfo_to_dict(dsn)
    dsn_target = str(
        parameters.get("dbname") or parameters.get("database") or ""
    ).strip().lower()
    if dsn_target != target_database:
        raise RecoveryContractError("recoveryDsnTargetMismatch")


def _validate_connected_target(cursor, target_database: str) -> None:
    cursor.execute("SELECT current_database()")
    row = cursor.fetchone()
    connected_target = str(_row_value(row, "current_database", 0)).strip().lower()
    if connected_target != target_database:
        raise RecoveryContractError("recoveryConnectedTargetMismatch")


def _read_redaction_key(path: Path) -> bytes:
    key_file = Path(path)
    if not key_file.is_file():
        raise RecoveryContractError("missingRecoveryOrphanRedactionKeyFile")
    if key_file.stat().st_mode & 0o077:
        raise RecoveryContractError("insecureRecoveryOrphanRedactionKeyFile")
    return key_file.read_bytes()


def _prepare_output_directory(output: Path) -> None:
    output_directory = output.parent
    if output_directory.exists():
        if not output_directory.is_dir():
            raise RecoveryContractError("invalidRecoveryOrphanOutputDirectory")
        return
    output_directory.mkdir(parents=True, mode=0o700)
    output_directory.chmod(0o700)


def _public_user_id_tables(cursor) -> Tuple[str, ...]:
    rows = cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables AS table_info
        WHERE table_info.table_schema = 'public'
          AND table_info.table_type = 'BASE TABLE'
          AND EXISTS (
              SELECT 1
              FROM information_schema.columns
              WHERE table_schema = 'public'
                AND table_name = table_info.table_name
                AND column_name = 'user_id'
          )
        ORDER BY table_info.table_name
        """
    ).fetchall()
    return tuple(str(_row_value(row, "table_name", 0)) for row in rows)


def _primary_key_columns(cursor, table: str) -> Tuple[str, ...]:
    rows = cursor.execute(
        """
        SELECT key_column_usage.column_name
        FROM information_schema.table_constraints
        JOIN information_schema.key_column_usage
          ON table_constraints.constraint_name = key_column_usage.constraint_name
         AND table_constraints.table_schema = key_column_usage.table_schema
         AND table_constraints.table_name = key_column_usage.table_name
        WHERE table_constraints.table_schema = 'public'
          AND table_constraints.table_name = %s
          AND table_constraints.constraint_type = 'PRIMARY KEY'
        ORDER BY key_column_usage.ordinal_position
        """,
        (table,),
    ).fetchall()
    return tuple(str(_row_value(row, "column_name", 0)) for row in rows)


def _orphan_count(cursor, table: str) -> int:
    query = sql.SQL(
        "SELECT COUNT(*) FROM {table} child "
        "LEFT JOIN public.users owner ON owner.id = child.user_id "
        "WHERE child.user_id IS NOT NULL AND owner.id IS NULL"
    ).format(table=sql.Identifier("public", table))
    cursor.execute(query)
    row = cursor.fetchone()
    return int(_row_value(row, "count", 0))


def _orphan_candidates(
    cursor,
    table: str,
    primary_key_columns: Tuple[str, ...],
    limit: int,
) -> Tuple[RecoveryOwnerOrphanCandidate, ...]:
    if not primary_key_columns:
        return ()
    selected_columns = [sql.Identifier("child", "user_id")]
    selected_columns.extend(
        sql.Identifier("child", column) for column in primary_key_columns
    )
    order_by = sql.SQL(", ").join(
        sql.Identifier("child", column) for column in primary_key_columns
    )
    query = sql.SQL(
        "SELECT {columns} FROM {table} child "
        "LEFT JOIN public.users owner ON owner.id = child.user_id "
        "WHERE child.user_id IS NOT NULL AND owner.id IS NULL "
        "ORDER BY {order_by} LIMIT %s"
    ).format(
        columns=sql.SQL(", ").join(selected_columns),
        table=sql.Identifier("public", table),
        order_by=order_by,
    )
    rows = cursor.execute(query, (limit,)).fetchall()
    candidates = []
    for row in rows:
        owner_id = _row_value(row, "user_id", 0)
        values = tuple(
            _row_value(row, column, index + 1)
            for index, column in enumerate(primary_key_columns)
        )
        candidates.append(
            RecoveryOwnerOrphanCandidate(
                schema_name="public",
                table_name=table,
                primary_key_columns=primary_key_columns,
                primary_key_values=values,
                owner_id=owner_id,
            )
        )
    return tuple(candidates)


def collect_owner_orphan_inventory(
    *,
    dsn: str,
    target_database: str,
    production_database: str,
    candidate_limit: int,
) -> Tuple[RecoveryOwnerOrphanTableInventory, ...]:
    target = validate_recovery_target(target_database, production_database)
    _validate_dsn_target(dsn, target)
    inventories = []
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            cursor.execute("SET TRANSACTION READ ONLY")
            _validate_connected_target(cursor, target)
            for table in _public_user_id_tables(cursor):
                primary_key_columns = _primary_key_columns(cursor, table)
                orphan_count = _orphan_count(cursor, table)
                candidates = ()
                if orphan_count:
                    candidates = _orphan_candidates(
                        cursor,
                        table,
                        primary_key_columns,
                        candidate_limit,
                    )
                inventories.append(
                    RecoveryOwnerOrphanTableInventory(
                        schema_name="public",
                        table_name=table,
                        primary_key_columns=primary_key_columns,
                        orphan_count=orphan_count,
                        candidates=candidates,
                        candidate_limit=candidate_limit,
                    )
                )
    return tuple(inventories)


def _summary(manifest):
    return {
        "schemaVersion": manifest["schemaVersion"],
        "status": manifest["status"],
        "mode": manifest["mode"],
        "orphanOwnerCount": manifest["orphanOwnerCount"],
        "tableCount": len(manifest["tableInventories"]),
        "unlocatableTables": manifest["unlocatableTables"],
        "blockers": manifest["blockers"],
        "manifestDigest": manifest["manifestDigest"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Build a value-free owner-orphan quarantine inventory from an isolated "
            "recovery database. The database transaction is read-only."
        )
    )
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--production-database", default="dreamjourney")
    parser.add_argument("--schema-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--redaction-key-file", type=Path, required=True)
    parser.add_argument("--max-candidates", type=int, default=100)
    args = parser.parse_args()

    try:
        target = validate_recovery_target(
            args.target_database,
            args.production_database,
        )
        if args.max_candidates < 1 or args.max_candidates > 1000:
            raise RecoveryContractError("invalidRecoveryOrphanCandidateLimit")
        _validate_dsn_target(args.dsn, target)
        redaction_key = _read_redaction_key(args.redaction_key_file)
        inventories = collect_owner_orphan_inventory(
            dsn=args.dsn,
            target_database=target,
            production_database=args.production_database,
            candidate_limit=args.max_candidates,
        )
        manifest = build_owner_orphan_quarantine_manifest(
            target_database=target,
            production_database=args.production_database,
            schema_head=args.schema_head,
            table_inventories=inventories,
            redaction_key=redaction_key,
        )
        _prepare_output_directory(args.output)
        write_recovery_record_atomic(args.output, manifest)
    except (OSError, psycopg.Error, RecoveryContractError, ValueError) as exc:
        code = exc.code if isinstance(exc, RecoveryContractError) else type(exc).__name__
        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "failed",
                    "errorCode": code,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    print(json.dumps(_summary(manifest), sort_keys=True))
    if manifest["status"] != "clear":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
