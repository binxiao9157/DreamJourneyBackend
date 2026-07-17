import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import Settings
from app.db.migrator import MigrationChecksumMismatch
from app.db.pool import ConnectionPoolExhausted
from app.db.readiness import (
    DatabaseReadinessError,
    PostgresReadinessProbe,
    SchemaReadinessError,
)
from app.main import app
from app.services.readiness import ReadinessService


EVIDENCE_TIME = "2026-07-16T13:00:00+00:00"


class FakeReadinessStore:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "databaseReason": "readWriteProbeSucceeded",
            "schemaReason": "migrationHeadVerified",
        }
        self.error = error
        self.calls = 0

    def readiness_probe(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return dict(self.result)


class ReadinessServiceTests(unittest.TestCase):
    @staticmethod
    def settings(**overrides):
        values = {
            "environment": "production",
            "store_backend": "postgres",
            "backend_api_token": "configured-system-token",
            "auth_access_ttl_seconds": 900,
            "auth_refresh_ttl_seconds": 2592000,
            "auth_ownership_mode": "shadow",
        }
        values.update(overrides)
        return Settings(**values)

    def service(self, store, **settings_overrides):
        return ReadinessService(
            settings=self.settings(**settings_overrides),
            store=store,
            clock=lambda: EVIDENCE_TIME,
        )

    def test_ready_requires_database_schema_and_auth_without_exposing_secrets(self):
        store = FakeReadinessStore()

        payload = self.service(store).evaluate()

        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            [component["component"] for component in payload["components"]],
            ["database", "schema", "auth"],
        )
        self.assertTrue(all(item["status"] == "ready" for item in payload["components"]))
        self.assertEqual(store.calls, 1)
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("configured-system-token", serialized)
        self.assertNotIn("postgresql://", serialized)
        self.assertNotIn("checksum", serialized.lower())

    def test_missing_required_auth_config_fails_closed_but_optional_providers_do_not(self):
        store = FakeReadinessStore()

        payload = self.service(
            store,
            backend_api_token=None,
            deepseek_api_key=None,
            volcengine_api_key=None,
            tencent_digital_human_app_key=None,
        ).evaluate()

        self.assertEqual(payload["status"], "notReady")
        auth = next(item for item in payload["components"] if item["component"] == "auth")
        self.assertEqual(auth["status"], "notReady")
        self.assertEqual(auth["reason"], "requiredAuthConfigMissing")

    def test_production_route_authentication_must_be_enforced(self):
        payload = self.service(
            FakeReadinessStore(),
            auth_route_mode="shadow",
        ).evaluate()

        self.assertEqual(payload["status"], "notReady")
        auth = next(item for item in payload["components"] if item["component"] == "auth")
        self.assertEqual(auth["status"], "notReady")
        self.assertEqual(auth["reason"], "routeAuthenticationNotEnforced")

    def test_pool_and_schema_failures_are_machine_safe_and_fail_closed(self):
        pool_payload = self.service(
            FakeReadinessStore(error=ConnectionPoolExhausted("secret pool detail"))
        ).evaluate()
        schema_payload = self.service(
            FakeReadinessStore(error=SchemaReadinessError("migrationChecksumMismatch"))
        ).evaluate()

        self.assertEqual(pool_payload["status"], "notReady")
        self.assertEqual(pool_payload["components"][0]["reason"], "databasePoolExhausted")
        self.assertEqual(pool_payload["components"][1]["status"], "unknown")
        self.assertEqual(schema_payload["status"], "notReady")
        self.assertEqual(schema_payload["components"][0]["status"], "ready")
        self.assertEqual(schema_payload["components"][1]["reason"], "migrationChecksumMismatch")
        self.assertNotIn("secret pool detail", json.dumps(pool_payload))

    def test_every_evaluation_uses_a_fresh_evidence_timestamp(self):
        timestamps = iter(
            [
                "2026-07-16T13:00:00+00:00",
                "2026-07-16T13:00:01+00:00",
            ]
        )
        service = ReadinessService(
            settings=self.settings(),
            store=FakeReadinessStore(),
            clock=lambda: next(timestamps),
        )

        first = service.evaluate()
        second = service.evaluate()

        self.assertNotEqual(first["evidenceTimestamp"], second["evidenceTimestamp"])


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.connection.statements.append((normalized, params))
        if normalized == "SELECT 1 AS value":
            self.result = {"value": 1}
        elif "current_setting('transaction_read_only')" in normalized:
            self.result = {"read_only": "on" if self.connection.read_only else "off"}
        elif normalized.startswith("SELECT COUNT(*) AS value FROM dreamjourney_readiness_probe"):
            self.result = {"value": 1}
        else:
            self.result = None

    def fetchone(self):
        return self.result


class FakeConnection:
    def __init__(self, *, read_only=False):
        self.read_only = read_only
        self.statements = []
        self.rollbacks = 0

    def cursor(self, row_factory=None):
        return FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1


class FakePool:
    def __init__(self, connection=None, error=None):
        self.connection = connection
        self.error = error
        self.returned = []

    def getconn(self, *, timeout=None):
        if self.error is not None:
            raise self.error
        return self.connection

    def putconn(self, connection):
        self.returned.append(connection)


class PostgresReadinessProbeTests(unittest.TestCase):
    def test_probe_performs_rollback_only_write_and_schema_verification(self):
        connection = FakeConnection()
        pool = FakePool(connection)
        verified = []
        probe = PostgresReadinessProbe(
            pool=pool,
            checkout_timeout_seconds=0.25,
            schema_verifier=lambda candidate: verified.append(candidate),
        )

        result = probe.run()

        self.assertEqual(result["databaseReason"], "readWriteProbeSucceeded")
        self.assertEqual(result["schemaReason"], "migrationHeadVerified")
        self.assertEqual(verified, [connection])
        self.assertGreaterEqual(connection.rollbacks, 2)
        self.assertEqual(pool.returned, [connection])
        statements = "\n".join(statement for statement, _ in connection.statements)
        self.assertIn("set_config('statement_timeout'", statements)
        self.assertIn("CREATE TEMP TABLE dreamjourney_readiness_probe", statements)
        self.assertIn("INSERT INTO dreamjourney_readiness_probe", statements)

    def test_read_only_and_checksum_mismatch_are_typed_failures(self):
        read_only = FakeConnection(read_only=True)
        with self.assertRaisesRegex(DatabaseReadinessError, "databaseReadOnly"):
            PostgresReadinessProbe(
                pool=FakePool(read_only),
                checkout_timeout_seconds=0.25,
                schema_verifier=lambda _connection: None,
            ).run()

        connection = FakeConnection()

        def fail_schema(_connection):
            raise MigrationChecksumMismatch("sensitive migration detail")

        with self.assertRaisesRegex(SchemaReadinessError, "migrationChecksumMismatch"):
            PostgresReadinessProbe(
                pool=FakePool(connection),
                checkout_timeout_seconds=0.25,
                schema_verifier=fail_schema,
            ).run()

    def test_pool_exhaustion_is_propagated_without_fallback(self):
        with self.assertRaises(ConnectionPoolExhausted):
            PostgresReadinessProbe(
                pool=FakePool(error=ConnectionPoolExhausted("exhausted")),
                checkout_timeout_seconds=0.25,
                schema_verifier=lambda _connection: None,
            ).run()


class InfrastructureEndpointTests(unittest.TestCase):
    def test_live_and_ready_are_anonymous_no_store_infrastructure_endpoints(self):
        store = FakeReadinessStore()
        settings = ReadinessServiceTests.settings()
        with patch.object(main_module, "store", store), patch.object(main_module, "settings", settings):
            client = TestClient(app)
            live = client.get("/live")
            ready = client.get("/ready")
            health = client.get("/health")

        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json()["component"], "process")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["status"], "ready")
        self.assertEqual(ready.headers["cache-control"], "no-store")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["deprecated"])
        self.assertEqual(health.json()["readinessEndpoint"], "/ready")

    def test_not_ready_returns_503_without_business_uow_or_credentials(self):
        store = FakeReadinessStore(error=ConnectionPoolExhausted("internal"))
        settings = ReadinessServiceTests.settings()
        with patch.object(main_module, "store", store), patch.object(main_module, "settings", settings):
            response = TestClient(app).get("/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "notReady")
        self.assertNotIn("internal", response.text)


if __name__ == "__main__":
    unittest.main()
