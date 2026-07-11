import unittest
from copy import deepcopy
from datetime import datetime, timezone

from app.services.postgres_store import PostgresStore


class FakePostgresTimeout(RuntimeError):
    def __init__(self, sqlstate):
        super().__init__(f"postgres timeout {sqlstate}")
        self.sqlstate = sqlstate


class CompactionDatabase:
    def __init__(self, *, fail_after_writes=None, compactor_available=True):
        self.snapshot_revisions = {}
        self.minimum_since_revisions = {}
        self.changes = []
        self.receipts = set()
        self.fail_after_writes = fail_after_writes
        self.write_count = 0
        self.compactor_available = compactor_available
        self.lock_timeout_users = set()
        self.statement_timeout_users = set()
        self.session_unlocks = 0
        self.executed = []
        self.add_user("u1", include_second_receipt=False)

    def add_user(self, user_id, *, include_second_receipt=True):
        self.snapshot_revisions[user_id] = 5
        operation_prefix = "" if user_id == "u1" else f"{user_id}-"
        for revision, created_at in (
            (1, "2026-01-01T00:00:00+00:00"),
            (2, "2026-01-02T00:00:00+00:00"),
            (3, "2026-01-03T00:00:00+00:00"),
            (4, "2026-07-10T00:00:00+00:00"),
            (5, "2026-07-11T00:00:00+00:00"),
        ):
            operation_id = f"{operation_prefix}op-{revision}"
            self.changes.append(
                {
                    "user_id": user_id,
                    "revision": revision,
                    "operation_id": operation_id,
                    "created_at": created_at,
                }
            )
            if revision != 2 or include_second_receipt:
                self.receipts.add((user_id, operation_id))

    def operation_id(self, user_id, revision):
        prefix = "" if user_id == "u1" else f"{user_id}-"
        return f"{prefix}op-{revision}"


class CompactionCursor:
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
        if normalized.startswith("SELECT pg_try_advisory_lock"):
            self.result = {"locked": database.compactor_available}
        elif normalized.startswith("SELECT pg_advisory_unlock"):
            database.session_unlocks += 1
            self.result = {"unlocked": True}
        elif normalized.startswith("SELECT set_config"):
            self.result = {"set_config": params[0]}
        elif normalized.startswith("SELECT pg_advisory_xact_lock"):
            lock_key = str(params[0])
            user_id = lock_key.split("knowledge:", 1)[-1]
            self.connection.user_id = user_id
            if user_id in database.lock_timeout_users:
                raise FakePostgresTimeout("55P03")
            self.result = {"locked": True}
        elif normalized.startswith("SELECT user_id FROM ("):
            users = set(database.snapshot_revisions)
            users.update(change["user_id"] for change in database.changes)
            users.update(database.minimum_since_revisions)
            self.result = [{"user_id": user_id} for user_id in sorted(users)]
        elif normalized.startswith("SELECT revision FROM kb_snapshots"):
            user_id = params[0]
            if user_id in database.statement_timeout_users:
                raise FakePostgresTimeout("57014")
            revision = database.snapshot_revisions.get(user_id)
            self.result = None if revision is None else {"revision": revision}
        elif normalized.startswith("SELECT minimum_since_revision FROM kb_change_feed_state"):
            minimum = database.minimum_since_revisions.get(params[0])
            self.result = None if minimum is None else {"minimum_since_revision": minimum}
        elif normalized.startswith("SELECT c.revision, c.created_at"):
            user_id, minimum = params
            self.result = [
                {
                    "revision": change["revision"],
                    "created_at": change["created_at"],
                    "has_receipt": (user_id, change["operation_id"]) in database.receipts,
                }
                for change in sorted(
                    database.changes,
                    key=lambda item: (item["user_id"], item["revision"]),
                )
                if change["user_id"] == user_id and change["revision"] > minimum
            ]
        elif normalized.startswith("DELETE FROM kb_changes"):
            user_id, revisions = params
            self.connection.before_write()
            revision_set = set(revisions)
            deleted = [
                change
                for change in database.changes
                if change["user_id"] == user_id and change["revision"] in revision_set
            ]
            database.changes = [
                change for change in database.changes if change not in deleted
            ]
            self.result = [{"revision": change["revision"]} for change in deleted]
        elif normalized.startswith("INSERT INTO kb_change_feed_state"):
            user_id, minimum = params
            self.connection.before_write()
            database.minimum_since_revisions[user_id] = minimum
            self.result = {"minimum_since_revision": minimum}
        else:
            raise AssertionError(f"unexpected compaction SQL: {normalized}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result or []


class CompactionConnection:
    def __init__(self, database):
        self.database = database
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.user_id = None
        self.did_write = False
        self._initial_state = deepcopy(
            (database.changes, database.minimum_since_revisions)
        )

    def cursor(self, row_factory=None):
        return CompactionCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        if self.did_write:
            self.database.changes, self.database.minimum_since_revisions = deepcopy(
                self._initial_state
            )

    def close(self):
        self.closes += 1

    def before_write(self):
        self.did_write = True
        self.database.write_count += 1
        if (
            self.database.fail_after_writes is not None
            and self.database.write_count > self.database.fail_after_writes
        ):
            raise RuntimeError("simulated compaction failure")


class CompactionConnectionFactory:
    def __init__(self, database):
        self.database = database
        self.connections = []

    def __call__(self):
        connection = CompactionConnection(self.database)
        self.connections.append(connection)
        return connection


class KnowledgeChangeFeedCompactionTests(unittest.TestCase):
    NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)

    def run_compaction(self, database, *, apply, keep_recent_revisions=1):
        factory = CompactionConnectionFactory(database)
        report = PostgresStore(
            connection_factory=factory
        ).maintain_kb_change_feed_compaction(
            keep_recent_revisions=keep_recent_revisions,
            keep_days=30,
            apply=apply,
            now=self.NOW,
        )
        return report, factory

    def test_dry_run_has_zero_dml_and_closes_every_connection(self):
        database = CompactionDatabase()

        report, factory = self.run_compaction(database, apply=False)

        self.assertEqual(report["mode"], "dryRun")
        self.assertEqual(report["plannedChanges"], 1)
        self.assertEqual(report["deletedChanges"], 0)
        self.assertEqual(report["legacyBarriers"], 1)
        self.assertEqual([change["revision"] for change in database.changes], [1, 2, 3, 4, 5])
        self.assertEqual(database.minimum_since_revisions, {})
        self.assertFalse(
            any(
                statement.startswith(("DELETE", "INSERT", "UPDATE"))
                for statement, _ in database.executed
            )
        )
        self.assertTrue(
            any("lock_timeout" in statement for statement, _ in database.executed)
        )
        self.assertTrue(
            any("statement_timeout" in statement for statement, _ in database.executed)
        )
        self.assertEqual(len(factory.connections), 2)
        self.assertTrue(all(connection.closes == 1 for connection in factory.connections))
        self.assertEqual(factory.connections[1].rollbacks, 1)
        self.assertEqual(database.session_unlocks, 1)

    def test_apply_uses_one_short_transaction_per_user(self):
        database = CompactionDatabase()
        database.add_user("u2")

        report, factory = self.run_compaction(database, apply=True)

        self.assertEqual(report["advancedUsers"], 2)
        self.assertEqual(report["deletedChanges"], 4)
        self.assertEqual(len(factory.connections), 3)
        coordinator, first_user, second_user = factory.connections
        self.assertFalse(any("knowledge:u" in str(params) for _, params in coordinator.executed))
        self.assertEqual(first_user.commits, 1)
        self.assertEqual(second_user.commits, 1)
        self.assertTrue(all(connection.closes == 1 for connection in factory.connections))
        self.assertEqual(database.session_unlocks, 1)

    def test_lock_timeout_skips_one_user_and_continues_others(self):
        database = CompactionDatabase()
        database.add_user("u2")
        database.lock_timeout_users.add("u1")

        report, factory = self.run_compaction(database, apply=True)

        self.assertEqual(report["advancedUsers"], 1)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["skippedUsers"], 1)
        self.assertEqual(report["skipReasons"]["lockTimeout"], 1)
        self.assertEqual(report["skipReasons"]["statementTimeout"], 0)
        self.assertNotIn("u1", database.minimum_since_revisions)
        self.assertEqual(database.minimum_since_revisions["u2"], 3)
        self.assertEqual(factory.connections[1].rollbacks, 1)
        self.assertEqual(factory.connections[1].closes, 1)

    def test_statement_timeout_skips_one_user_and_continues_others(self):
        database = CompactionDatabase()
        database.add_user("u2")
        database.statement_timeout_users.add("u1")

        report, factory = self.run_compaction(database, apply=True)

        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["advancedUsers"], 1)
        self.assertEqual(report["skippedUsers"], 1)
        self.assertEqual(report["skipReasons"]["lockTimeout"], 0)
        self.assertEqual(report["skipReasons"]["statementTimeout"], 1)
        self.assertNotIn("u1", database.minimum_since_revisions)
        self.assertEqual(database.minimum_since_revisions["u2"], 3)
        self.assertEqual(factory.connections[1].rollbacks, 1)
        self.assertEqual(factory.connections[1].closes, 1)

    def test_second_compaction_advances_an_existing_nonzero_floor(self):
        database = CompactionDatabase()
        receipts = deepcopy(database.receipts)

        first, _ = self.run_compaction(database, apply=True)
        database.receipts.add(("u1", "op-2"))
        second, _ = self.run_compaction(database, apply=True)

        self.assertEqual(first["deletedChanges"], 1)
        self.assertEqual(second["deletedChanges"], 2)
        self.assertEqual(database.minimum_since_revisions, {"u1": 3})
        self.assertEqual(
            [change["revision"] for change in database.changes],
            [4, 5],
        )
        self.assertTrue(receipts.issubset(database.receipts))

    def test_apply_rolls_back_user_delete_when_floor_update_fails_and_unlocks(self):
        database = CompactionDatabase(fail_after_writes=1)
        factory = CompactionConnectionFactory(database)
        store = PostgresStore(connection_factory=factory)

        with self.assertRaises(RuntimeError):
            store.maintain_kb_change_feed_compaction(
                keep_recent_revisions=1,
                keep_days=30,
                apply=True,
                now=self.NOW,
            )

        self.assertEqual([change["revision"] for change in database.changes], [1, 2, 3, 4, 5])
        self.assertEqual(database.minimum_since_revisions, {})
        self.assertEqual(factory.connections[1].commits, 0)
        self.assertEqual(factory.connections[1].rollbacks, 1)
        self.assertEqual(factory.connections[1].closes, 1)
        self.assertEqual(database.session_unlocks, 1)

    def test_session_lock_prevents_overlapping_compactors(self):
        database = CompactionDatabase(compactor_available=False)

        report, factory = self.run_compaction(database, apply=True)

        self.assertEqual(report["status"], "skipped")
        self.assertTrue(report["compactorAlreadyRunning"])
        self.assertEqual(len(factory.connections), 1)
        self.assertEqual(database.session_unlocks, 0)
        self.assertEqual(factory.connections[0].closes, 1)

    def test_retention_keeps_union_of_recent_revisions_and_recent_days(self):
        recent_by_age = CompactionDatabase()
        recent_by_age.receipts.add(("u1", "op-2"))
        recent_by_age.changes[1]["created_at"] = "2026-07-01T00:00:00+00:00"
        age_report, _ = self.run_compaction(recent_by_age, apply=False)

        recent_by_revision = CompactionDatabase()
        recent_by_revision.receipts.add(("u1", "op-2"))
        revision_report, _ = self.run_compaction(
            recent_by_revision,
            apply=False,
            keep_recent_revisions=4,
        )

        self.assertEqual(age_report["plannedChanges"], 1)
        self.assertEqual(age_report["retentionBarriers"], 1)
        self.assertEqual(revision_report["plannedChanges"], 1)
        self.assertEqual(revision_report["retentionBarriers"], 1)


if __name__ == "__main__":
    unittest.main()
