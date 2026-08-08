#!/usr/bin/env python3
"""Disposable-Postgres smoke for the server-side OTP provider contract."""

import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.identity_bindings import (
    HttpJsonIdentityChallengeAdapter,
    IdentityBindingService,
    IdentityChallengeDeliveryError,
    IdentityChallengeVerificationFailed,
)
from app.services.postgres_store import PostgresStore


HMAC_KEY = "identity-challenge-provider-postgres-smoke-key-" + ("x" * 40)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def database_dsn(base_dsn, database_name):
    parameters = conninfo_to_dict(base_dsn)
    parameters["dbname"] = database_name
    return make_conninfo(**parameters)


def create_database(admin_dsn, database_name):
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )


def drop_database(admin_dsn, database_name):
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


class AcceptedGateway:
    def __init__(self, *, accepted=True):
        self.accepted = accepted
        self.requests = []

    def post_json(self, *, url, headers, payload, timeout_seconds):
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": dict(payload),
                "timeoutSeconds": timeout_seconds,
            }
        )
        if not self.accepted:
            raise IdentityChallengeDeliveryError()
        if url.endswith("/status"):
            return {"deliveryState": "delivered", "receiptId": "postgres-smoke-receipt"}
        return {"accepted": True, "receiptId": "postgres-smoke-receipt"}


def service_for(store, gateway):
    return IdentityBindingService(
        store,
        hmac_key=HMAC_KEY,
        hmac_key_version="v1",
        adapter=HttpJsonIdentityChallengeAdapter(
            endpoint="https://sms.example.test/v1/challenges",
            status_endpoint="https://sms.example.test/v1/challenges/status",
            api_key="test-server-only-api-key",
            timeout_seconds=5,
            transport=gateway,
        ),
        challenge_ttl_seconds=60,
        max_attempts=3,
    )


def main():
    base_dsn = settings.database_url
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = database_dsn(base_dsn, "postgres")
    database_name = f"dj_identity_provider_smoke_{uuid.uuid4().hex[:10]}"
    smoke_dsn = database_dsn(base_dsn, database_name)
    store = None

    try:
        create_database(admin_dsn, database_name)
        applied = PostgresMigrator(
            dsn=smoke_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="identity-challenge-provider-smoke",
            lock_timeout_ms=15_000,
            statement_timeout_ms=30_000,
        ).apply()
        require(applied["status"] == "ready", "migrations must reach ready")
        require("0085" in applied["appliedVersions"], "0085 must be applied")

        store = PostgresStore(
            dsn=smoke_dsn,
            pool_min_size=1,
            pool_max_size=2,
            pool_timeout_seconds=5,
        )
        store.open_pool(wait=True)
        gateway = AcceptedGateway()
        service = service_for(store, gateway)

        challenge = service.create_challenge(
            identity_type="phone",
            target="+8613800138000",
            purpose="login",
        )
        challenge_id = challenge["challenge"]["challengeId"]
        require(len(gateway.requests) == 1, "gateway must receive one request")
        delivered = gateway.requests[0]["payload"]
        delivered_code = delivered["code"]
        require(delivered["target"] == "8613800138000", "target normalization drift")
        require(delivered["challengeId"] == challenge_id, "challenge ID drift")
        require(delivered_code.isdigit() and len(delivered_code) == 6, "OTP shape drift")

        persisted = store.get_auth_challenge(challenge_id)
        require(persisted is not None, "accepted delivery must persist a challenge")
        require(persisted["providerMode"] == "httpJson", "provider mode drift")
        require(persisted["internalVerificationEnabled"] is True, "server verification drift")
        require(persisted["deliveryState"] == "accepted", "delivery state drift")
        require(persisted["recoveryState"] == "available", "recovery state drift")
        require(
            persisted["providerReceiptHash"] != "postgres-smoke-receipt",
            "raw provider receipt reached persistence",
        )
        for raw in ("8613800138000", delivered_code):
            require(raw not in str(persisted), "raw value reached persisted record")

        recovered = service.challenge_state(challenge_id, recover_delivery=True)
        require(
            recovered["challenge"]["deliveryState"] == "delivered",
            "delivery recovery did not converge",
        )
        require(
            recovered["challenge"]["recoveryState"] == "notRequired",
            "delivery recovery terminal state drift",
        )

        wrong_code = "111111" if delivered_code != "111111" else "222222"
        try:
            service.verify_challenge(challenge_id, wrong_code)
            raise AssertionError("wrong OTP must not verify")
        except IdentityChallengeVerificationFailed:
            pass
        verified = service.verify_challenge(challenge_id, delivered_code)
        require(verified["status"] == "verified", "delivered OTP must verify")
        try:
            service.verify_challenge(challenge_id, delivered_code)
            raise AssertionError("OTP replay must not verify")
        except IdentityChallengeVerificationFailed:
            pass

        failed_gateway = AcceptedGateway(accepted=False)
        failed_service = service_for(store, failed_gateway)
        try:
            failed_service.create_challenge(
                identity_type="phone",
                target="+8613900000000",
                purpose="login",
            )
            raise AssertionError("rejected provider request must not be accepted")
        except IdentityChallengeDeliveryError:
            pass
        require(len(failed_gateway.requests) == 1, "failed request must reach gateway")
        with store.request_unit_of_work(
            correlation_id="identity-challenge-provider-smoke-inspect",
            command_id="inspectIdentityChallengeProviderSmoke",
        ):
            failed_count = store._fetchone(
                "SELECT COUNT(*) AS count FROM auth_challenges WHERE provider_mode = %s",
                ("httpJson",),
            )
        require(int(failed_count["count"]) == 1, "failed delivery persisted a challenge")
        print("Identity challenge provider Postgres smoke passed")
    finally:
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
