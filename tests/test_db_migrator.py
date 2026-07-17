import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from app.db.migrator import (
    ExistingSchemaMismatch,
    ExistingSchemaRequiresAdoption,
    MigrationChecksumMismatch,
    MigrationHeadAhead,
    PostgresMigrator,
    default_migrations_dir,
    load_migrations,
)


def write_migration(
    directory: Path,
    *,
    version: str = "0001",
    name: str = "baseline",
    sql: str = "-- migration:baseline\nCREATE TABLE users (id TEXT PRIMARY KEY);\n",
    baseline=None,
):
    (directory / f"{version}_{name}.sql").write_text(sql, encoding="utf-8")
    metadata = {
        "version": version,
        "name": name,
        "phase": "expand",
        "compatibility": "additive",
    }
    if baseline is not None:
        metadata["baseline"] = baseline
    (directory / f"{version}_{name}.json").write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )


class FakeMigrationDatabase:
    def __init__(self):
        self.ledger_exists = False
        self.ledger = {}
        self.relations = set()
        self.columns = {}
        self.triggers = set()
        self.executed_migrations = []
        self.control_statements = []
        self.fail_next_migration = False
        self.lock = Lock()

    def connect(self):
        return FakeMigrationConnection(self)


class FakeMigrationCursor:
    def __init__(self, connection):
        self.connection = connection
        self.database = connection.database
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None, prepare=None):
        normalized = " ".join(sql.split())
        params = params or ()
        self.database.control_statements.append((normalized, params, prepare))
        if normalized.startswith("SELECT pg_advisory_lock"):
            self.database.lock.acquire()
            self.result = {"locked": True}
        elif normalized.startswith("SELECT pg_advisory_unlock"):
            if self.database.lock.locked():
                self.database.lock.release()
            self.result = {"unlocked": True}
        elif normalized.startswith("SELECT set_config"):
            self.result = {"set_config": params[0] if params else ""}
        elif "to_regclass('public.schema_migrations')" in normalized:
            self.result = {"relation": "schema_migrations" if self.database.ledger_exists else None}
        elif normalized.startswith("SELECT to_regclass(%s) AS relation"):
            relation = str(params[0]).replace("public.", "")
            self.result = {"relation": relation if relation in self.database.relations else None}
        elif normalized.startswith("SELECT column_name FROM information_schema.columns"):
            table_name = str(params[0])
            self.result = [
                {"column_name": column}
                for column in sorted(self.database.columns.get(table_name, set()))
            ]
        elif normalized.startswith("SELECT tgname FROM pg_trigger"):
            trigger_name = str(params[0])
            self.result = {"tgname": trigger_name} if trigger_name in self.database.triggers else None
        elif normalized.startswith("CREATE TABLE IF NOT EXISTS schema_migrations"):
            self.database.ledger_exists = True
            self.result = None
        elif normalized.startswith("CREATE INDEX IF NOT EXISTS idx_schema_migrations_state"):
            self.result = None
        elif normalized.startswith("SELECT version, name, checksum, state"):
            self.result = [dict(item) for _, item in sorted(self.database.ledger.items())]
        elif normalized.startswith("INSERT INTO schema_migrations") and "state = 'running'" in normalized:
            version, name, checksum, build_id = params
            self.database.ledger[version] = {
                "version": version,
                "name": name,
                "checksum": checksum,
                "state": "running",
                "started_at": "2026-07-16T00:00:00+00:00",
                "applied_at": None,
                "build_id": build_id,
                "execution_mode": "execute",
                "error_code": None,
            }
        elif normalized.startswith("INSERT INTO schema_migrations") and "state = 'applied'" in normalized:
            version, name, checksum, build_id, execution_mode = params
            self.database.ledger[version] = {
                "version": version,
                "name": name,
                "checksum": checksum,
                "state": "applied",
                "started_at": "2026-07-16T00:00:00+00:00",
                "applied_at": "2026-07-16T00:00:01+00:00",
                "build_id": build_id,
                "execution_mode": execution_mode,
                "error_code": None,
            }
        elif normalized.startswith("UPDATE schema_migrations SET state = 'applied'"):
            execution_mode, version = params
            self.database.ledger[version].update(
                state="applied",
                applied_at="2026-07-16T00:00:01+00:00",
                execution_mode=execution_mode,
                error_code=None,
            )
        elif normalized.startswith("INSERT INTO schema_migrations") and "state = 'failed'" in normalized:
            version, name, checksum, build_id, error_code = params
            self.database.ledger[version] = {
                "version": version,
                "name": name,
                "checksum": checksum,
                "state": "failed",
                "started_at": "2026-07-16T00:00:00+00:00",
                "applied_at": None,
                "build_id": build_id,
                "execution_mode": "execute",
                "error_code": error_code,
            }
        elif "-- migration:" in sql:
            if self.database.fail_next_migration:
                self.database.fail_next_migration = False
                raise RuntimeError("migration statement failed")
            marker = sql.split("-- migration:", 1)[1].splitlines()[0].strip()
            self.database.executed_migrations.append(marker)
            if marker == "baseline":
                self.database.relations.add("users")
                self.database.columns["users"] = {"id"}
            self.result = None
        else:
            self.result = None

    def fetchone(self):
        if isinstance(self.result, list):
            return self.result[0] if self.result else None
        return self.result

    def fetchall(self):
        if isinstance(self.result, list):
            return self.result
        return [] if self.result is None else [self.result]


class FakeMigrationConnection:
    def __init__(self, database):
        self.database = database
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def cursor(self, row_factory=None):
        return FakeMigrationCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class PostgresMigratorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.migrations_dir = Path(self.temp_dir.name)
        self.baseline = {
            "relations": ["users"],
            "columns": {"users": ["id"]},
            "triggers": [],
        }
        write_migration(self.migrations_dir, baseline=self.baseline)

    def tearDown(self):
        self.temp_dir.cleanup()

    def migrator(self, database, *, build_id="test-build"):
        return PostgresMigrator(
            dsn="postgresql://not-used",
            migrations_dir=self.migrations_dir,
            build_id=build_id,
            connection_factory=database.connect,
            lock_timeout_ms=250,
            statement_timeout_ms=1000,
        )

    def test_loader_uses_file_bytes_for_deterministic_checksum(self):
        first = load_migrations(self.migrations_dir)[0]
        write_migration(
            self.migrations_dir,
            sql="-- migration:baseline\nCREATE TABLE users (id TEXT PRIMARY KEY, value TEXT);\n",
            baseline=self.baseline,
        )
        second = load_migrations(self.migrations_dir)[0]

        self.assertEqual(first.version, "0001")
        self.assertNotEqual(first.checksum, second.checksum)
        self.assertEqual(len(first.checksum), 64)

    def test_fresh_database_executes_once_and_repeat_is_noop(self):
        database = FakeMigrationDatabase()
        migrator = self.migrator(database)

        first = migrator.apply()
        second = migrator.apply()

        self.assertEqual(first["appliedVersions"], ["0001"])
        self.assertEqual(second["appliedVersions"], [])
        self.assertEqual(second["skippedVersions"], ["0001"])
        self.assertEqual(database.executed_migrations, ["baseline"])
        self.assertEqual(database.ledger["0001"]["state"], "applied")

    def test_existing_schema_requires_explicit_adoption_and_does_not_execute_sql(self):
        database = FakeMigrationDatabase()
        database.relations = {"users"}
        database.columns = {"users": {"id"}}
        migrator = self.migrator(database)

        with self.assertRaises(ExistingSchemaRequiresAdoption):
            migrator.apply()
        result = migrator.apply(adopt_existing_baseline=True)

        self.assertEqual(result["adoptedVersions"], ["0001"])
        self.assertEqual(database.executed_migrations, [])
        self.assertEqual(database.ledger["0001"]["execution_mode"], "adopted")

    def test_partial_existing_schema_is_rejected(self):
        write_migration(
            self.migrations_dir,
            baseline={
                "relations": ["users", "archive_items"],
                "columns": {"users": ["id"], "archive_items": ["id"]},
                "triggers": [],
            },
        )
        database = FakeMigrationDatabase()
        database.relations = {"users"}
        database.columns = {"users": {"id"}}

        with self.assertRaises(ExistingSchemaMismatch):
            self.migrator(database).apply(adopt_existing_baseline=True)

    def test_checksum_drift_and_old_binary_head_are_rejected(self):
        database = FakeMigrationDatabase()
        migrator = self.migrator(database)
        migrator.apply()
        database.ledger["0001"]["checksum"] = "0" * 64

        with self.assertRaises(MigrationChecksumMismatch):
            migrator.verify()

        database.ledger["0001"]["checksum"] = load_migrations(self.migrations_dir)[0].checksum
        database.ledger["0002"] = {
            "version": "0002",
            "name": "future",
            "checksum": "1" * 64,
            "state": "applied",
            "started_at": "2026-07-16T00:00:00+00:00",
            "applied_at": "2026-07-16T00:00:01+00:00",
            "build_id": "future",
            "execution_mode": "execute",
            "error_code": None,
        }
        with self.assertRaises(MigrationHeadAhead):
            migrator.verify()

    def test_failed_migration_records_failure_and_restarts_forward(self):
        database = FakeMigrationDatabase()
        database.fail_next_migration = True
        migrator = self.migrator(database)

        with self.assertRaisesRegex(RuntimeError, "migration statement failed"):
            migrator.apply()
        self.assertEqual(database.ledger["0001"]["state"], "failed")
        self.assertEqual(database.ledger["0001"]["error_code"], "RuntimeError")

        result = migrator.apply()
        self.assertEqual(result["appliedVersions"], ["0001"])
        self.assertEqual(database.ledger["0001"]["state"], "applied")

    def test_concurrent_migrators_are_serialized_by_advisory_lock(self):
        database = FakeMigrationDatabase()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.migrator(database).apply(), range(2)))

        self.assertEqual(database.executed_migrations, ["baseline"])
        self.assertEqual(sum(len(result["appliedVersions"]) for result in results), 1)
        lock_calls = [
            statement
            for statement, _, _ in database.control_statements
            if statement.startswith("SELECT pg_advisory_lock")
        ]
        self.assertEqual(len(lock_calls), 2)

    def test_lock_and_statement_timeouts_are_set_before_migration(self):
        database = FakeMigrationDatabase()
        self.migrator(database).apply()

        set_config_params = [
            params
            for statement, params, _ in database.control_statements
            if statement.startswith("SELECT set_config")
        ]
        self.assertIn(("250ms",), set_config_params)
        self.assertIn(("1000ms",), set_config_params)
        statements = [statement for statement, _, _ in database.control_statements]
        lock_index = next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("SELECT pg_advisory_lock")
        )
        timeout_indexes = [
            index
            for index, statement in enumerate(statements)
            if statement.startswith("SELECT set_config")
        ]
        self.assertTrue(timeout_indexes)
        self.assertLess(max(timeout_indexes), lock_index)

    def test_repository_baseline_is_additive_and_complete(self):
        migration = load_migrations(default_migrations_dir())[0]
        sql_upper = migration.sql.upper()

        self.assertEqual(migration.version, "0001")
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(len(migration.baseline_columns), 19)
        self.assertIn("evidence_events_no_update", migration.baseline_triggers)
        self.assertTrue(
            any(
                "schema_migrations" in str(constant)
                for constant in PostgresMigrator._ensure_ledger.__code__.co_consts
            )
        )
        for destructive in ("DROP TABLE", "TRUNCATE ", "ALTER TABLE", "DELETE FROM"):
            self.assertNotIn(destructive, sql_upper)

    def test_resource_authority_migration_adds_immutable_owner_guards(self):
        migration = load_migrations(default_migrations_dir())[-1]

        self.assertEqual(migration.version, "0004")
        self.assertEqual(migration.name, "resource_owner_authority")
        self.assertEqual(migration.phase, "expand")
        self.assertIn("NEW.user_id IS DISTINCT FROM OLD.user_id", migration.sql)
        self.assertIn("NEW.vault_id := NEW.user_id", migration.sql)
        self.assertIn("NEW.owner_subject_id := NEW.user_id", migration.sql)
        self.assertIn("NEW.row_version := OLD.row_version + 1", migration.sql)
        self.assertIn("resource_owner_claims", migration.sql)
        self.assertIn("resource_kind = 'mailbox_letters'", migration.sql)
        self.assertIn("('userId', 'recipientUserId')", migration.sql)
        self.assertIn("resource_owner_claims(NEW.payload, TG_TABLE_NAME)", migration.sql)
        self.assertIn("resource_authority_incidents", migration.sql)
        self.assertIn("SET authority_state = ''quarantined''", migration.sql)
        self.assertIn("resource payload owner claim conflicts with canonical owner", migration.sql)
        for table in (
            "memories",
            "archive_items",
            "mailbox_letters",
            "echo_delayed_replies",
            "push_device_tokens",
            "voice_profiles",
            "family_members",
            "care_snapshots",
            "digital_human_sessions",
        ):
            with self.subTest(table=table):
                self.assertIn(f"CREATE TRIGGER {table}_owner_authority", migration.sql)
                self.assertIn(f"ON {table}(vault_id, id)", migration.sql)


if __name__ == "__main__":
    unittest.main()
