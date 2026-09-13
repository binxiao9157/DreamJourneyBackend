#!/usr/bin/env python3
"""Run all configured worker activation probes against disposable resources."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.worker_activation import run_worker_activation_preflight
from app.async_effects.worker_deployment_registry import LONG_RUNNING_WORKERS
from app.core.config import Settings
from app.db.migrator import PostgresMigrator, default_migrations_dir


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def dsn_for_database(base_dsn: str, database_name: str) -> str:
    parameters = conninfo_to_dict(base_dsn)
    parameters["dbname"] = database_name
    return make_conninfo(**parameters)


def create_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))


def drop_database(admin_dsn: str, database_name: str) -> None:
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name)))


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", "").strip()
    require(base_dsn, "DATABASE_URL is required")
    database_name = f"dj_worker_activation_smoke_{uuid.uuid4().hex[:12]}"
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    test_dsn = dsn_for_database(base_dsn, database_name)
    try:
        create_database(admin_dsn, database_name)
        migration = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="worker-activation-smoke",
            lock_timeout_ms=1_000,
            statement_timeout_ms=30_000,
        ).apply()
        require(migration["appliedHead"] == "0121", "isolated schema must reach 0121")

        with tempfile.TemporaryDirectory(prefix="dj-worker-activation-media-") as media_root:
            os.environ["DATABASE_URL"] = test_dsn
            os.environ["OWNER_TRUTH_MEDIA_STORAGE_ROOT"] = media_root
            settings = Settings.from_env()
            results = []
            for spec in LONG_RUNNING_WORKERS:
                if not spec.enabled(settings):
                    results.append(
                        {
                            "worker": spec.worker,
                            "composeService": spec.compose_service,
                            "configured": False,
                            "ready": None,
                            "reason": "disabledByExistingConfiguration",
                        }
                    )
                    continue
                decision = run_worker_activation_preflight(
                    worker=spec.worker,
                    settings=settings,
                )
                results.append(
                    {
                        "worker": spec.worker,
                        "composeService": spec.compose_service,
                        "configured": True,
                        **decision.public_descriptor(),
                    }
                )
                require(decision.ready, f"configured worker not ready: {spec.worker}")

        print(
            json.dumps(
                {
                    "schemaVersion": "dreamjourney-worker-activation-postgres-smoke-v1",
                    "status": "passed",
                    "schemaHead": "0121",
                    "temporaryDatabase": True,
                    "temporaryMediaRoot": True,
                    "workers": results,
                },
                sort_keys=True,
            )
        )
    finally:
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
