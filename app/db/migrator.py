from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple


MIGRATION_FILE_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
MIGRATION_PHASES = {"expand", "backfill", "verify", "contract"}
MIGRATION_COMPATIBILITY = {"additive", "backwardCompatible", "contract"}
MIGRATION_LOCK_KEY = "dreamjourney-schema-migrator:v1"
BUILD_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class MigrationError(RuntimeError):
    pass


class MigrationManifestError(MigrationError):
    pass


class MigrationChecksumMismatch(MigrationError):
    pass


class MigrationHeadAhead(MigrationError):
    pass


class MigrationNotApplied(MigrationError):
    pass


class ExistingSchemaRequiresAdoption(MigrationError):
    pass


class ExistingSchemaMismatch(MigrationError):
    pass


@dataclass(frozen=True)
class MigrationDefinition:
    version: str
    name: str
    phase: str
    compatibility: str
    checksum: str
    sql: str
    sql_path: Path
    baseline_relations: Tuple[str, ...]
    baseline_columns: Mapping[str, Tuple[str, ...]]
    baseline_triggers: Tuple[str, ...]

    @property
    def is_baseline(self) -> bool:
        return bool(self.baseline_relations)


def _string_list(value: Any, *, field: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise MigrationManifestError(f"{field} must be a list")
    normalized = tuple(str(item).strip() for item in value)
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise MigrationManifestError(f"{field} contains empty or duplicate values")
    return normalized


def load_migrations(migrations_dir: Path) -> List[MigrationDefinition]:
    directory = Path(migrations_dir)
    if not directory.is_dir():
        raise MigrationManifestError("migration directory is missing")
    migrations: List[MigrationDefinition] = []
    versions = set()
    for sql_path in sorted(directory.glob("*.sql")):
        match = MIGRATION_FILE_PATTERN.fullmatch(sql_path.name)
        if match is None:
            raise MigrationManifestError(f"invalid migration filename: {sql_path.name}")
        version, name = match.groups()
        if version in versions:
            raise MigrationManifestError(f"duplicate migration version: {version}")
        metadata_path = sql_path.with_suffix(".json")
        if not metadata_path.is_file():
            raise MigrationManifestError(f"migration metadata is missing: {metadata_path.name}")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MigrationManifestError(f"invalid migration metadata: {metadata_path.name}") from exc
        if not isinstance(metadata, dict):
            raise MigrationManifestError(f"migration metadata must be an object: {metadata_path.name}")
        if str(metadata.get("version") or "") != version:
            raise MigrationManifestError(f"migration version mismatch: {metadata_path.name}")
        if str(metadata.get("name") or "") != name:
            raise MigrationManifestError(f"migration name mismatch: {metadata_path.name}")
        phase = str(metadata.get("phase") or "")
        compatibility = str(metadata.get("compatibility") or "")
        if phase not in MIGRATION_PHASES:
            raise MigrationManifestError(f"unsupported migration phase: {phase}")
        if compatibility not in MIGRATION_COMPATIBILITY:
            raise MigrationManifestError(
                f"unsupported migration compatibility: {compatibility}"
            )
        sql_bytes = sql_path.read_bytes()
        try:
            sql = sql_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MigrationManifestError(f"migration SQL must be UTF-8: {sql_path.name}") from exc
        if not sql.strip():
            raise MigrationManifestError(f"migration SQL is empty: {sql_path.name}")

        baseline = metadata.get("baseline") or {}
        if not isinstance(baseline, dict):
            raise MigrationManifestError("baseline metadata must be an object")
        baseline_relations = _string_list(
            baseline.get("relations"),
            field="baseline.relations",
        )
        columns_value = baseline.get("columns") or {}
        if not isinstance(columns_value, dict):
            raise MigrationManifestError("baseline.columns must be an object")
        baseline_columns = {
            str(table): _string_list(columns, field=f"baseline.columns.{table}")
            for table, columns in columns_value.items()
        }
        if any(table not in baseline_relations for table in baseline_columns):
            raise MigrationManifestError("baseline columns reference an unknown relation")
        baseline_triggers = _string_list(
            baseline.get("triggers"),
            field="baseline.triggers",
        )
        migrations.append(
            MigrationDefinition(
                version=version,
                name=name,
                phase=phase,
                compatibility=compatibility,
                checksum=hashlib.sha256(sql_bytes).hexdigest(),
                sql=sql,
                sql_path=sql_path,
                baseline_relations=baseline_relations,
                baseline_columns=baseline_columns,
                baseline_triggers=baseline_triggers,
            )
        )
        versions.add(version)
    if not migrations:
        raise MigrationManifestError("migration directory contains no SQL migrations")
    if [item.version for item in migrations] != sorted(item.version for item in migrations):
        raise MigrationManifestError("migration versions are not ordered")
    baseline_versions = [item.version for item in migrations if item.is_baseline]
    if baseline_versions and baseline_versions != [migrations[0].version]:
        raise MigrationManifestError("only the first migration may define baseline adoption")
    return migrations


class PostgresMigrator:
    def __init__(
        self,
        *,
        dsn: str,
        migrations_dir: Path,
        build_id: str,
        connection_factory: Optional[Callable[[], Any]] = None,
        lock_timeout_ms: int = 5000,
        statement_timeout_ms: int = 30000,
    ) -> None:
        self.dsn = dsn
        self.migrations_dir = Path(migrations_dir)
        self.build_id = str(build_id or "unknown")
        if BUILD_ID_PATTERN.fullmatch(self.build_id) is None:
            raise MigrationManifestError("build_id must be an opaque machine identifier")
        self.connection_factory = connection_factory
        self.lock_timeout_ms = max(1, int(lock_timeout_ms))
        self.statement_timeout_ms = max(1, int(statement_timeout_ms))

    def plan(self) -> Dict[str, Any]:
        migrations = load_migrations(self.migrations_dir)
        connection = self._connect()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                ledger_exists = self._ledger_exists(cursor)
                ledger = self._ledger_rows(cursor) if ledger_exists else {}
                self._validate_known_ledger(migrations, ledger)
                baseline_action = "none"
                if not ledger and migrations[0].is_baseline:
                    baseline_state = self._baseline_state(cursor, migrations[0])
                    baseline_action = {
                        "fresh": "execute",
                        "complete": "adoptExplicitly",
                        "partial": "blockedMismatch",
                    }[baseline_state]
                pending = [
                    item.version
                    for item in migrations
                    if ledger.get(item.version, {}).get("state") != "applied"
                ]
                return self._report(
                    migrations=migrations,
                    ledger=ledger,
                    applied=(),
                    adopted=(),
                    skipped=(),
                    pending=pending,
                    baseline_action=baseline_action,
                    mode="dryRun",
                )
        finally:
            self._close(connection)

    def apply(self, *, adopt_existing_baseline: bool = False) -> Dict[str, Any]:
        migrations = load_migrations(self.migrations_dir)
        connection = self._connect()
        lock_acquired = False
        applied: List[str] = []
        adopted: List[str] = []
        skipped: List[str] = []
        baseline_action = "none"
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(
                    "SELECT set_config('lock_timeout', %s, false)",
                    (f"{self.lock_timeout_ms}ms",),
                )
                cursor.fetchone()
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, false)",
                    (f"{self.statement_timeout_ms}ms",),
                )
                cursor.fetchone()
                cursor.execute("SELECT pg_advisory_lock(hashtext(%s)) AS locked", (MIGRATION_LOCK_KEY,))
                cursor.fetchone()
                lock_acquired = True
            connection.commit()

            self._ensure_ledger(connection)
            ledger = self._read_ledger(connection)
            self._validate_known_ledger(migrations, ledger)

            for migration in migrations:
                existing = ledger.get(migration.version)
                if existing is not None:
                    self._validate_checksum(migration, existing)
                    if existing.get("state") == "applied":
                        skipped.append(migration.version)
                        continue

                if migration.is_baseline and not ledger:
                    with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                        baseline_state = self._baseline_state(cursor, migration)
                    if baseline_state == "partial":
                        raise ExistingSchemaMismatch("existing schema does not match baseline")
                    if baseline_state == "complete":
                        baseline_action = "adopt"
                        if not adopt_existing_baseline:
                            raise ExistingSchemaRequiresAdoption(
                                "existing schema baseline adoption must be explicit"
                            )
                        self._record_adopted(connection, migration)
                        adopted.append(migration.version)
                        ledger[migration.version] = {
                            "version": migration.version,
                            "checksum": migration.checksum,
                            "state": "applied",
                        }
                        continue
                    baseline_action = "execute"

                self._execute_migration(connection, migration)
                applied.append(migration.version)
                ledger[migration.version] = {
                    "version": migration.version,
                    "checksum": migration.checksum,
                    "state": "applied",
                }

            final_ledger = self._read_ledger(connection)
            return self._report(
                migrations=migrations,
                ledger=final_ledger,
                applied=applied,
                adopted=adopted,
                skipped=skipped,
                pending=(),
                baseline_action=baseline_action,
                mode="apply",
            )
        finally:
            if lock_acquired:
                try:
                    with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                        cursor.execute(
                            "SELECT pg_advisory_unlock(hashtext(%s)) AS unlocked",
                            (MIGRATION_LOCK_KEY,),
                        )
                        cursor.fetchone()
                    connection.commit()
                except Exception:
                    connection.rollback()
            self._close(connection)

    def verify(self) -> Dict[str, Any]:
        migrations = load_migrations(self.migrations_dir)
        connection = self._connect()
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                if not self._ledger_exists(cursor):
                    raise MigrationNotApplied("schema migration ledger is missing")
                ledger = self._ledger_rows(cursor)
            self._validate_known_ledger(migrations, ledger)
            for migration in migrations:
                existing = ledger.get(migration.version)
                if existing is None or existing.get("state") != "applied":
                    raise MigrationNotApplied(f"migration is not applied: {migration.version}")
                self._validate_checksum(migration, existing)
            return self._report(
                migrations=migrations,
                ledger=ledger,
                applied=(),
                adopted=(),
                skipped=[item.version for item in migrations],
                pending=(),
                baseline_action="none",
                mode="verify",
            )
        finally:
            self._close(connection)

    def _execute_migration(self, connection: Any, migration: MigrationDefinition) -> None:
        self._record_running(connection, migration)
        try:
            with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
                cursor.execute(migration.sql, prepare=False)
                cursor.execute(
                    """
                    UPDATE schema_migrations
                    SET state = 'applied', applied_at = NOW(), execution_mode = %s,
                        error_code = NULL
                    WHERE version = %s
                    """,
                    ("execute", migration.version),
                )
            connection.commit()
        except Exception as exc:
            connection.rollback()
            self._record_failed(connection, migration, type(exc).__name__)
            raise

    def _record_running(self, connection: Any, migration: MigrationDefinition) -> None:
        with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                """
                INSERT INTO schema_migrations (
                    version, name, checksum, state, started_at, applied_at,
                    build_id, execution_mode, error_code
                ) VALUES (%s, %s, %s, 'running', NOW(), NULL, %s, 'execute', NULL)
                ON CONFLICT (version) DO UPDATE SET
                    name = EXCLUDED.name,
                    checksum = EXCLUDED.checksum,
                    state = 'running',
                    started_at = NOW(),
                    applied_at = NULL,
                    build_id = EXCLUDED.build_id,
                    execution_mode = 'execute',
                    error_code = NULL
                """,
                (migration.version, migration.name, migration.checksum, self.build_id),
            )
        connection.commit()

    def _record_adopted(self, connection: Any, migration: MigrationDefinition) -> None:
        with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                """
                INSERT INTO schema_migrations (
                    version, name, checksum, state, started_at, applied_at,
                    build_id, execution_mode, error_code
                ) VALUES (%s, %s, %s, 'applied', NOW(), NOW(), %s, %s, NULL)
                ON CONFLICT (version) DO UPDATE SET
                    name = EXCLUDED.name,
                    checksum = EXCLUDED.checksum,
                    state = 'applied',
                    applied_at = NOW(),
                    build_id = EXCLUDED.build_id,
                    execution_mode = EXCLUDED.execution_mode,
                    error_code = NULL
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    self.build_id,
                    "adopted",
                ),
            )
        connection.commit()

    def _record_failed(
        self,
        connection: Any,
        migration: MigrationDefinition,
        error_code: str,
    ) -> None:
        with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            cursor.execute(
                """
                INSERT INTO schema_migrations (
                    version, name, checksum, state, started_at, applied_at,
                    build_id, execution_mode, error_code
                ) VALUES (%s, %s, %s, 'failed', NOW(), NULL, %s, 'execute', %s)
                ON CONFLICT (version) DO UPDATE SET
                    name = EXCLUDED.name,
                    checksum = EXCLUDED.checksum,
                    state = 'failed',
                    applied_at = NULL,
                    build_id = EXCLUDED.build_id,
                    execution_mode = 'execute',
                    error_code = EXCLUDED.error_code
                """,
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    self.build_id,
                    str(error_code or "MigrationError")[:128],
                ),
            )
        connection.commit()

    @staticmethod
    def _ensure_ledger(connection: Any) -> None:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        checksum TEXT NOT NULL,
                        state TEXT NOT NULL,
                        started_at TIMESTAMPTZ NOT NULL,
                        applied_at TIMESTAMPTZ,
                        build_id TEXT NOT NULL,
                        execution_mode TEXT NOT NULL,
                        error_code TEXT,
                        CHECK (checksum ~ '^[0-9a-f]{64}$'),
                        CHECK (state IN ('running', 'applied', 'failed')),
                        CHECK (execution_mode IN ('execute', 'adopted'))
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_schema_migrations_state
                    ON schema_migrations(state, version)
                    """
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _read_ledger(self, connection: Any) -> Dict[str, Dict[str, Any]]:
        with connection.cursor(row_factory=self._dict_row_factory()) as cursor:
            return self._ledger_rows(cursor)

    @staticmethod
    def _ledger_exists(cursor: Any) -> bool:
        cursor.execute(
            "SELECT to_regclass('public.schema_migrations') AS relation"
        )
        row = cursor.fetchone() or {}
        return bool(row.get("relation"))

    @staticmethod
    def _ledger_rows(cursor: Any) -> Dict[str, Dict[str, Any]]:
        cursor.execute(
            """
            SELECT version, name, checksum, state, started_at, applied_at,
                   build_id, execution_mode, error_code
            FROM schema_migrations
            ORDER BY version
            """
        )
        return {str(row["version"]): dict(row) for row in cursor.fetchall()}

    def _baseline_state(self, cursor: Any, migration: MigrationDefinition) -> str:
        present = []
        for relation in migration.baseline_relations:
            cursor.execute(
                "SELECT to_regclass(%s) AS relation",
                (f"public.{relation}",),
            )
            present.append(bool((cursor.fetchone() or {}).get("relation")))
        if not any(present):
            return "fresh"
        if not all(present):
            return "partial"
        for table, required_columns in migration.baseline_columns.items():
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                """,
                (table,),
            )
            actual_columns = {str(row["column_name"]) for row in cursor.fetchall()}
            if not set(required_columns).issubset(actual_columns):
                return "partial"
        for trigger in migration.baseline_triggers:
            cursor.execute(
                """
                SELECT tgname FROM pg_trigger
                WHERE tgname = %s AND NOT tgisinternal
                """,
                (trigger,),
            )
            if cursor.fetchone() is None:
                return "partial"
        return "complete"

    @staticmethod
    def _validate_checksum(
        migration: MigrationDefinition,
        existing: Mapping[str, Any],
    ) -> None:
        if str(existing.get("checksum") or "") != migration.checksum:
            raise MigrationChecksumMismatch(
                f"migration checksum mismatch: {migration.version}"
            )

    def _validate_known_ledger(
        self,
        migrations: Sequence[MigrationDefinition],
        ledger: Mapping[str, Mapping[str, Any]],
    ) -> None:
        known = {item.version: item for item in migrations}
        unknown_applied = sorted(
            version
            for version, row in ledger.items()
            if version not in known and row.get("state") == "applied"
        )
        if unknown_applied:
            raise MigrationHeadAhead(
                f"database migration head is ahead: {unknown_applied[-1]}"
            )
        for version, migration in known.items():
            existing = ledger.get(version)
            if existing is not None:
                self._validate_checksum(migration, existing)

    @staticmethod
    def _report(
        *,
        migrations: Sequence[MigrationDefinition],
        ledger: Mapping[str, Mapping[str, Any]],
        applied: Sequence[str],
        adopted: Sequence[str],
        skipped: Sequence[str],
        pending: Sequence[str],
        baseline_action: str,
        mode: str,
    ) -> Dict[str, Any]:
        applied_versions = sorted(
            version
            for version, row in ledger.items()
            if row.get("state") == "applied"
        )
        return {
            "schemaVersion": 1,
            "status": "ready" if not pending else "pending",
            "mode": mode,
            "expectedHead": migrations[-1].version,
            "appliedHead": applied_versions[-1] if applied_versions else None,
            "migrationCount": len(migrations),
            "appliedVersions": list(applied),
            "adoptedVersions": list(adopted),
            "skippedVersions": list(skipped),
            "pendingVersions": list(pending),
            "baselineAction": baseline_action,
        }

    def _connect(self):
        if self.connection_factory is not None:
            return self.connection_factory()
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("psycopg is not installed") from exc
        return psycopg.connect(self.dsn)

    @staticmethod
    def _dict_row_factory():
        try:
            from psycopg.rows import dict_row

            return dict_row
        except ImportError:  # pragma: no cover - deployment dependency
            return None

    @staticmethod
    def _close(connection: Any) -> None:
        close = getattr(connection, "close", None)
        if callable(close):
            close()


def default_migrations_dir() -> Path:
    configured = os.environ.get("DATABASE_MIGRATIONS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "db" / "migrations"
