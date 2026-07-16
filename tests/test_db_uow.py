import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app.main as main_module
from app.db.pool import ConnectionPoolExhausted
from app.db.uow import DatabaseUnitOfWork, UnitOfWorkMetrics
from app.main import database_request_unit_of_work
from app.services.postgres_store import PostgresStore
from app.services.store_factory import close_store, init_store


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = {"value": 1}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        self.connection.executed.append((sql, params))
        if self.connection.fail_next:
            self.connection.fail_next = False
            self.connection.aborted = True
            raise RuntimeError("statement failed")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return [self.result]


class FakeConnection:
    def __init__(self, name):
        self.name = name
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self.fail_next = False
        self.aborted = False
        self.executed = []

    def cursor(self, row_factory=None):
        return FakeCursor(self)

    def commit(self):
        if self.aborted:
            raise RuntimeError("cannot commit aborted transaction")
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        self.aborted = False

    def close(self):
        self.closes += 1


class RecordingPool:
    def __init__(self, connections):
        self.available = list(connections)
        self.checked_out = []
        self.returned = []
        self.opened = False
        self.closed = False

    def open(self, *, wait=True):
        self.opened = True

    def close(self):
        self.closed = True

    def getconn(self, *, timeout=None):
        if not self.available:
            raise ConnectionPoolExhausted("pool exhausted")
        connection = self.available.pop(0)
        self.checked_out.append(connection)
        return connection

    def putconn(self, connection):
        self.returned.append(connection)
        self.available.append(connection)

    def stats(self):
        return {
            "poolSize": len(self.available) + len(self.checked_out) - len(self.returned),
            "poolAvailable": len(self.available),
        }


class DatabaseUnitOfWorkTests(unittest.TestCase):
    def test_success_commits_once_and_returns_connection(self):
        connection = FakeConnection("c1")
        pool = RecordingPool([connection])
        metrics = UnitOfWorkMetrics()

        with DatabaseUnitOfWork(
            pool,
            metrics,
            correlation_id="corr-1",
            command_id="cmd-1",
        ) as uow:
            self.assertIs(uow.connection, connection)
            self.assertEqual(connection.commits, 0)

        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(pool.returned, [connection])
        self.assertEqual(metrics.snapshot()["committed"], 1)

    def test_exception_and_rollback_only_never_commit(self):
        failed = FakeConnection("failed")
        rollback_only = FakeConnection("rollback-only")
        pool = RecordingPool([failed, rollback_only])
        metrics = UnitOfWorkMetrics()

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with DatabaseUnitOfWork(
                pool,
                metrics,
                correlation_id="corr-2",
                command_id="cmd-2",
            ):
                raise RuntimeError("boom")
        with DatabaseUnitOfWork(
            pool,
            metrics,
            correlation_id="corr-3",
            command_id="cmd-3",
        ) as uow:
            uow.mark_rollback("httpError")

        self.assertEqual(failed.rollbacks, 1)
        self.assertEqual(failed.commits, 0)
        self.assertEqual(rollback_only.rollbacks, 1)
        self.assertEqual(rollback_only.commits, 0)
        self.assertEqual(metrics.snapshot()["rolledBack"], 2)

    def test_pool_exhaustion_is_counted_without_fallback_connection(self):
        pool = RecordingPool([])
        metrics = UnitOfWorkMetrics()

        with self.assertRaises(ConnectionPoolExhausted):
            with DatabaseUnitOfWork(
                pool,
                metrics,
                correlation_id="corr-4",
                command_id="cmd-4",
            ):
                pass

        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["poolExhausted"], 1)
        self.assertEqual(snapshot["active"], 0)

    def test_statement_failure_rolls_back_before_connection_is_reused(self):
        connection = FakeConnection("reused")
        pool = RecordingPool([connection])
        store = PostgresStore(pool=pool)
        connection.fail_next = True

        with self.assertRaisesRegex(RuntimeError, "statement failed"):
            with store.request_unit_of_work(
                correlation_id="corr-failed",
                command_id="cmd-failed",
            ):
                store._fetchone("SELECT broken")

        with store.request_unit_of_work(
            correlation_id="corr-next",
            command_id="cmd-next",
        ):
            row = store._fetchone("SELECT healthy")

        self.assertEqual(row, {"value": 1})
        self.assertGreaterEqual(connection.rollbacks, 1)
        self.assertFalse(connection.aborted)
        self.assertEqual(connection.commits, 1)

    def test_repository_commit_flag_does_not_commit_inside_request_uow(self):
        connection = FakeConnection("request")
        pool = RecordingPool([connection])
        store = PostgresStore(pool=pool)

        with store.request_unit_of_work(
            correlation_id="corr-request",
            command_id="cmd-request",
        ):
            store._fetchone("UPDATE item", commit=True)
            store._fetchone("SELECT item")
            self.assertEqual(connection.commits, 0)

        self.assertEqual(connection.commits, 1)
        self.assertEqual(store.uow_metrics()["committed"], 1)
        self.assertFalse(hasattr(store, "_connection"))


class DatabaseRequestUnitOfWorkTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _request(path="/profile/u1"):
        return SimpleNamespace(url=SimpleNamespace(path=path))

    async def test_success_response_commits_and_exposes_correlation_id(self):
        connection = FakeConnection("http-success")
        store = PostgresStore(pool=RecordingPool([connection]))

        async def call_next(_request):
            self.assertIsNotNone(store._current_uow.get())
            return SimpleNamespace(status_code=200, headers={})

        with patch.object(main_module, "store", store):
            response = await database_request_unit_of_work(self._request(), call_next)

        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(len(response.headers["X-DreamJourney-Correlation-Id"]), 32)

    async def test_error_response_rolls_back(self):
        connection = FakeConnection("http-error")
        store = PostgresStore(pool=RecordingPool([connection]))

        async def call_next(_request):
            return SimpleNamespace(status_code=409, headers={})

        with patch.object(main_module, "store", store):
            await database_request_unit_of_work(self._request(), call_next)

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)

    async def test_pool_exhaustion_returns_stable_503_contract(self):
        store = PostgresStore(pool=RecordingPool([]))

        async def call_next(_request):
            self.fail("request must not run without a database work unit")

        with patch.object(main_module, "store", store):
            response = await database_request_unit_of_work(self._request(), call_next)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "1")
        self.assertEqual(store.uow_metrics()["poolExhausted"], 1)

    async def test_health_bypasses_database_pool(self):
        store = PostgresStore(pool=RecordingPool([]))

        async def call_next(_request):
            return SimpleNamespace(status_code=200, headers={})

        with patch.object(main_module, "store", store):
            response = await database_request_unit_of_work(
                self._request("/health"),
                call_next,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(store.uow_metrics()["checkouts"], 0)


class StoreLifecycleTests(unittest.TestCase):
    def test_init_and_close_store_manage_pool_lifecycle(self):
        events = []

        class LifecycleStore:
            def open_pool(self, *, wait=True):
                events.append(("open", wait))

            def init_schema(self):
                events.append(("schema", True))

            def drain_expired_digital_human_session_leases(self, *, now_iso):
                events.append(("drain", bool(now_iso)))

            def close_pool(self):
                events.append(("close", True))

        store = LifecycleStore()
        init_store(store)
        close_store(store)

        self.assertEqual(
            events,
            [("open", True), ("schema", True), ("drain", True), ("close", True)],
        )


if __name__ == "__main__":
    unittest.main()
