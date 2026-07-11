import unittest
from copy import deepcopy
from datetime import datetime, timezone

from app.services.postgres_store import PostgresStore


class FakePostgresTimeout(RuntimeError):
    def __init__(self, sqlstate):
        super().__init__("simulated postgres timeout")
        self.sqlstate = sqlstate


class ReceiptDatabase:
    OLD_CREATED_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)

    def __init__(self):
        self.receipts = [
            self.receipt("u1", "op-1", "kb.sync", self.legacy_result("alpha secret")),
            self.receipt(
                "u1",
                "op-2",
                "kb.mutation",
                {
                    "receiptEnvelopeVersion": 1,
                    "revision": 2,
                    "mutationSchemaVersion": 2,
                },
            ),
            self.receipt(
                "u2",
                "op-1",
                "kb.mutation",
                self.legacy_result("beta secret"),
            ),
        ]
        self.lock_timeout_users = set()
        self.fail_update_users = set()
        self.executed = []
        self.executemany_calls = 0
        self.user_discovery_queries = 0

    @staticmethod
    def legacy_result(secret):
        return {
            "userId": "private-user",
            "operationId": "legacy-operation",
            "revision": 1,
            "updatedAt": "2020-01-01T00:00:00+00:00",
            "mutationSchemaVersion": 2,
            "graph": {
                "facts": [{"id": "fact-1", "statement": secret}],
            },
            "mutation": None,
        }

    @classmethod
    def receipt(cls, user_id, operation_id, operation_kind, result):
        return {
            "user_id": user_id,
            "operation_id": operation_id,
            "operation_kind": operation_kind,
            "schema_version": 2,
            "payload_hash": f"hash-{user_id}-{operation_id}",
            "result": deepcopy(result),
            "created_at": cls.OLD_CREATED_AT,
        }


class ReceiptCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        params = params or ()
        database = self.connection.database
        self.connection.executed.append((normalized, params))
        database.executed.append((normalized, params))

        if normalized.startswith("SELECT set_config"):
            self.result = {"set_config": params[0]}
        elif normalized.startswith("SELECT pg_advisory_xact_lock"):
            user_id = str(params[0]).split("knowledge:", 1)[-1]
            self.connection.user_id = user_id
            if user_id in database.lock_timeout_users:
                raise FakePostgresTimeout("55P03")
            self.result = {"locked": True}
        elif normalized.startswith("SELECT DISTINCT user_id"):
            database.user_discovery_queries += 1
            if len(params) == 2:
                cutoff, limit = params
                last_user_id = None
            else:
                cutoff, last_user_id, limit = params
            users = sorted(
                {
                    row["user_id"]
                    for row in database.receipts
                    if row["created_at"] < cutoff
                    and (last_user_id is None or row["user_id"] > last_user_id)
                }
            )
            self.result = [{"user_id": user_id} for user_id in users[:limit]]
        elif normalized.startswith(
            "SELECT operation_id, operation_kind, result FROM kb_operation_receipts"
        ):
            if len(params) == 3:
                user_id, cutoff, limit = params
                last_operation_id = None
            else:
                user_id, cutoff, last_operation_id, limit = params
            rows = [
                row
                for row in database.receipts
                if row["user_id"] == user_id
                and row["created_at"] < cutoff
                and (
                    last_operation_id is None
                    or row["operation_id"] > last_operation_id
                )
            ]
            rows.sort(key=lambda row: row["operation_id"])
            self.result = [
                {
                    "operation_id": row["operation_id"],
                    "operation_kind": row["operation_kind"],
                    "result": deepcopy(row["result"]),
                }
                for row in rows[:limit]
            ]
        else:
            raise AssertionError(f"unexpected receipt maintenance SQL: {normalized}")

    def executemany(self, sql, params_list):
        normalized = " ".join(sql.split())
        database = self.connection.database
        database.executemany_calls += 1
        self.connection.executed.append((normalized, params_list))
        database.executed.append((normalized, params_list))
        if not normalized.startswith(
            "UPDATE kb_operation_receipts SET result = %s WHERE user_id = %s AND operation_id = %s"
        ):
            raise AssertionError(f"unexpected receipt batch SQL: {normalized}")

        for index, params in enumerate(params_list):
            result, user_id, operation_id = params
            adapted_result = deepcopy(getattr(result, "obj", result))
            row = next(
                row
                for row in database.receipts
                if row["user_id"] == user_id
                and row["operation_id"] == operation_id
            )
            row["result"] = adapted_result
            self.connection.did_write = True
            if user_id in database.fail_update_users and index == 0:
                raise RuntimeError("simulated user update failure")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result or []


class ReceiptConnection:
    def __init__(self, database):
        self.database = database
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.did_write = False
        self.user_id = None
        self.initial_receipts = deepcopy(database.receipts)

    def cursor(self, row_factory=None):
        return ReceiptCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        if self.did_write:
            self.database.receipts = deepcopy(self.initial_receipts)

    def close(self):
        self.closes += 1


class ReceiptConnectionFactory:
    def __init__(self, database):
        self.database = database
        self.connections = []

    def __call__(self):
        connection = ReceiptConnection(self.database)
        self.connections.append(connection)
        return connection


class KnowledgeReceiptPostgresMaintenanceTests(unittest.TestCase):
    def run_maintenance(self, database, *, apply, batch_size=100):
        factory = ReceiptConnectionFactory(database)
        report = PostgresStore(
            connection_factory=factory
        ).maintain_knowledge_operation_receipts(
            keep_days=30,
            batch_size=batch_size,
            apply=apply,
            lock_timeout_ms=17,
            statement_timeout_ms=29,
        )
        return report, factory

    @staticmethod
    def immutable_receipt_fields(database):
        return [
            {
                key: deepcopy(value)
                for key, value in row.items()
                if key != "result"
            }
            for row in database.receipts
        ]

    def test_dry_run_reports_candidates_without_update_or_private_content(self):
        database = ReceiptDatabase()
        original = deepcopy(database.receipts)

        report, factory = self.run_maintenance(database, apply=False, batch_size=1)

        self.assertEqual(report["mode"], "dryRun")
        self.assertEqual(report["scanned"], 3)
        self.assertEqual(report["candidate"], 2)
        self.assertEqual(report["updated"], 0)
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["alreadyCompact"], 1)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(report["byKind"]["kb.sync"]["candidate"], 1)
        self.assertEqual(report["byKind"]["kb.mutation"]["candidate"], 1)
        self.assertGreater(report["estimatedBytes"]["saved"], 0)
        self.assertEqual(database.receipts, original)
        self.assertEqual(database.executemany_calls, 0)
        self.assertFalse(any(sql.startswith("UPDATE") for sql, _ in database.executed))
        self.assertFalse(any("FOR UPDATE" in sql for sql, _ in database.executed))
        self.assertTrue(
            any("pg_advisory_xact_lock" in sql for sql, _ in database.executed)
        )
        self.assertTrue(any("lock_timeout" in sql for sql, _ in database.executed))
        self.assertNotIn("alpha secret", str(report))
        self.assertNotIn("beta secret", str(report))
        self.assertEqual(database.user_discovery_queries, 3)
        self.assertEqual(report["scannedUsers"], 2)
        self.assertEqual(report["processedUsers"], 2)
        self.assertEqual(len(factory.connections), 5)
        self.assertEqual(factory.connections[1].rollbacks, 1)
        self.assertEqual(factory.connections[3].rollbacks, 1)
        self.assertTrue(all(connection.closes == 1 for connection in factory.connections))

    def test_apply_locks_users_batches_updates_and_preserves_identity_columns(self):
        database = ReceiptDatabase()
        immutable_before = self.immutable_receipt_fields(database)

        report, factory = self.run_maintenance(database, apply=True, batch_size=1)

        self.assertEqual(report["mode"], "apply")
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["candidate"], 2)
        self.assertEqual(report["updated"], 2)
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["alreadyCompact"], 1)
        self.assertEqual(report["failed"], 0)
        self.assertEqual(self.immutable_receipt_fields(database), immutable_before)
        self.assertTrue(
            all("graph" not in str(row["result"]) for row in database.receipts)
        )
        self.assertTrue(any("FOR UPDATE" in sql for sql, _ in database.executed))
        self.assertTrue(
            any("pg_advisory_xact_lock" in sql for sql, _ in database.executed)
        )
        self.assertTrue(any("lock_timeout" in sql for sql, _ in database.executed))
        update_statements = [
            sql for sql, _ in database.executed if sql.startswith("UPDATE")
        ]
        self.assertTrue(update_statements)
        self.assertTrue(all("SET result = %s" in sql for sql in update_statements))
        self.assertTrue(all(connection.commits == 1 for connection in factory.connections))

    def test_lock_timeout_rolls_back_one_user_and_continues(self):
        database = ReceiptDatabase()
        database.lock_timeout_users.add("u1")
        u1_before = deepcopy(
            [row for row in database.receipts if row["user_id"] == "u1"]
        )

        report, factory = self.run_maintenance(database, apply=True)

        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["failedUsers"], 1)
        self.assertEqual(report["failureReasons"]["lockTimeout"], 1)
        self.assertEqual(report["updated"], 1)
        self.assertEqual(
            [row for row in database.receipts if row["user_id"] == "u1"],
            u1_before,
        )
        u2_result = next(
            row["result"] for row in database.receipts if row["user_id"] == "u2"
        )
        self.assertEqual(u2_result["receiptEnvelopeVersion"], 1)
        self.assertEqual(factory.connections[1].rollbacks, 1)
        self.assertEqual(factory.connections[2].commits, 1)

    def test_user_update_error_rolls_back_that_user_and_continues(self):
        database = ReceiptDatabase()
        database.fail_update_users.add("u1")
        u1_before = deepcopy(
            [row for row in database.receipts if row["user_id"] == "u1"]
        )

        report, factory = self.run_maintenance(database, apply=True)

        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["failedUsers"], 1)
        self.assertEqual(report["failureReasons"]["error"], 1)
        self.assertEqual(report["candidate"], 2)
        self.assertEqual(report["updated"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(
            [row for row in database.receipts if row["user_id"] == "u1"],
            u1_before,
        )
        u2_result = next(
            row["result"] for row in database.receipts if row["user_id"] == "u2"
        )
        self.assertEqual(u2_result["receiptEnvelopeVersion"], 1)
        self.assertEqual(factory.connections[1].rollbacks, 1)
        self.assertEqual(factory.connections[2].commits, 1)

    def test_apply_is_idempotent(self):
        database = ReceiptDatabase()
        first, _ = self.run_maintenance(database, apply=True, batch_size=1)
        after_first = deepcopy(database.receipts)
        database.executemany_calls = 0

        second, _ = self.run_maintenance(database, apply=True, batch_size=1)

        self.assertEqual(first["updated"], 2)
        self.assertEqual(second["candidate"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["skipped"], 3)
        self.assertEqual(second["alreadyCompact"], 3)
        self.assertEqual(second["estimatedBytes"]["saved"], 0)
        self.assertEqual(database.executemany_calls, 0)
        self.assertEqual(database.receipts, after_first)

    def test_rejects_invalid_parameters_before_connecting(self):
        cases = [
            {"keep_days": -1},
            {"keep_days": True},
            {"batch_size": 0},
            {"batch_size": False},
            {"lock_timeout_ms": 0},
            {"statement_timeout_ms": 0},
            {"apply": "yes"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                database = ReceiptDatabase()
                factory = ReceiptConnectionFactory(database)
                arguments = {
                    "keep_days": 30,
                    "batch_size": 100,
                    "apply": False,
                    "lock_timeout_ms": 5000,
                    "statement_timeout_ms": 30000,
                    **overrides,
                }
                with self.assertRaises(ValueError):
                    PostgresStore(
                        connection_factory=factory
                    ).maintain_knowledge_operation_receipts(**arguments)
                self.assertEqual(factory.connections, [])

    def test_keep_days_zero_is_allowed(self):
        database = ReceiptDatabase()

        report, _ = self.run_maintenance(database, apply=False)
        factory = ReceiptConnectionFactory(database)
        zero_day_report = PostgresStore(
            connection_factory=factory
        ).maintain_knowledge_operation_receipts(keep_days=0)

        self.assertEqual(report["candidate"], 2)
        self.assertEqual(zero_day_report["retention"]["keepDays"], 0)
        self.assertEqual(zero_day_report["candidate"], 2)

    def test_dirty_compact_envelope_is_canonicalized_instead_of_skipped(self):
        database = ReceiptDatabase()
        database.receipts.append(
            database.receipt(
                "u3",
                "dirty-compact",
                "kb.mutation",
                {
                    "receiptEnvelopeVersion": 1,
                    "userId": "private-user",
                    "operationId": "dirty-compact",
                    "revision": 9,
                    "mutationSchemaVersion": 2,
                    "graph": {
                        "facts": [
                            {"id": "fact-private", "statement": "dirty private text"}
                        ]
                    },
                    "mutation": None,
                },
            )
        )

        report, _ = self.run_maintenance(database, apply=True)

        dirty = next(
            row["result"]
            for row in database.receipts
            if row["operation_id"] == "dirty-compact"
        )
        self.assertEqual(report["candidate"], 3)
        self.assertEqual(report["updated"], 3)
        self.assertEqual(
            sum(kind["updated"] for kind in report["byKind"].values()),
            report["updated"],
        )
        self.assertNotIn("graph", dirty)
        self.assertNotIn("mutation", dirty)
        self.assertNotIn("userId", dirty)
        self.assertNotIn("dirty private text", str(dirty))

        second, _ = self.run_maintenance(database, apply=True)
        self.assertEqual(second["candidate"], 0)
        self.assertEqual(second["alreadyCompact"], 4)

    def test_empty_operation_id_is_scanned_and_reported_as_failed(self):
        database = ReceiptDatabase()
        database.receipts.append(
            database.receipt(
                "u3",
                "",
                "kb.sync",
                database.legacy_result("empty operation private text"),
            )
        )

        report, _ = self.run_maintenance(database, apply=False)

        self.assertEqual(report["scannedUsers"], 3)
        self.assertEqual(report["failedUsers"], 1)
        self.assertEqual(report["failureReasons"]["error"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertNotIn("empty operation private text", str(report))


if __name__ == "__main__":
    unittest.main()
