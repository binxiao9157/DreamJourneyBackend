#!/usr/bin/env python3
import json
import shutil
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir, load_migrations


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


def migrator(dsn, build_id, migrations_dir=None):
    return PostgresMigrator(
        dsn=dsn,
        migrations_dir=migrations_dir or default_migrations_dir(),
        build_id=build_id,
        lock_timeout_ms=1000,
        statement_timeout_ms=15000,
    )


def main():
    base_dsn = settings.database_url
    parameters = conninfo_to_dict(base_dsn)
    admin_dsn = database_dsn(base_dsn, "postgres")
    prefix = "dj_migration_smoke_"
    database_names = [
        prefix + uuid.uuid4().hex[:10],
        prefix + uuid.uuid4().hex[:10],
        prefix + uuid.uuid4().hex[:10],
    ]
    require(parameters.get("user"), "database user is required")
    migrations = load_migrations(default_migrations_dir())
    expected_versions = [migration.version for migration in migrations]

    try:
        first_name, concurrent_name, legacy_name = database_names
        create_database(admin_dsn, first_name)
        first_dsn = database_dsn(base_dsn, first_name)
        first_migrator = migrator(first_dsn, "g2-fresh")

        plan = first_migrator.plan()
        require(plan["baselineAction"] == "execute", "fresh baseline plan")
        applied = first_migrator.apply()
        verified = first_migrator.verify()
        repeated = first_migrator.apply()
        require(applied["appliedVersions"] == expected_versions, "fresh migration apply")
        require(verified["status"] == "ready", "fresh migration verify")
        require(repeated["skippedVersions"] == expected_versions, "repeat no-op")

        baseline = load_migrations(default_migrations_dir())[0]
        with psycopg.connect(first_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(%s)",
                    (list(baseline.baseline_columns),),
                )
                table_count = int(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT state, execution_mode FROM schema_migrations WHERE version = '0001'"
                )
                ledger = cursor.fetchone()
                cursor.execute(
                    "SELECT COUNT(*) FROM pg_trigger "
                    "WHERE tgname = 'evidence_events_no_update' AND NOT tgisinternal"
                )
                trigger_count = int(cursor.fetchone()[0])
        require(table_count == 19, "fresh schema table count")
        require(ledger == ("applied", "execute"), "fresh migration receipt")
        require(trigger_count == 1, "append-only trigger")

        create_database(admin_dsn, concurrent_name)
        concurrent_dsn = database_dsn(base_dsn, concurrent_name)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda index: migrator(
                        concurrent_dsn,
                        f"g2-concurrent-{index}",
                    ).apply(),
                    range(2),
                )
            )
        require(
            sum(len(result["appliedVersions"]) for result in results)
            == len(expected_versions),
            "concurrent migrators must apply once",
        )
        require(
            sum(len(result["skippedVersions"]) for result in results)
            == len(expected_versions),
            "second concurrent migrator must observe applied head",
        )

        create_database(admin_dsn, legacy_name)
        legacy_dsn = database_dsn(base_dsn, legacy_name)
        with tempfile.TemporaryDirectory() as legacy_migrations_dir:
            legacy_migrations_path = Path(legacy_migrations_dir)
            for migration_path in default_migrations_dir().iterdir():
                if migration_path.name.startswith(("0001_", "0002_", "0003_")):
                    shutil.copy2(migration_path, legacy_migrations_path / migration_path.name)
            legacy_applied = migrator(
                legacy_dsn,
                "g2-legacy-head",
                migrations_dir=legacy_migrations_path,
            ).apply()
        require(
            legacy_applied["appliedVersions"] == expected_versions[:3],
            "legacy fixture must stop before owner authority migration",
        )
        with psycopg.connect(legacy_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO archive_items (id, user_id, payload)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        "legacy-owner-conflict",
                        "owner-a",
                        Jsonb(
                            {
                                "id": "legacy-owner-conflict",
                                "userId": "owner-a",
                                "metadata": {"ownerUserId": "owner-b"},
                            }
                        ),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO mailbox_letters (id, user_id, payload)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        "legacy-mailbox-recipient",
                        "recipient-a",
                        Jsonb(
                            {
                                "id": "legacy-mailbox-recipient",
                                "userId": "recipient-a",
                                "ownerUserId": "source-owner-a",
                                "kind": "timeLetterReminder",
                            }
                        ),
                    ),
                )
        legacy_upgrade = migrator(legacy_dsn, "g2-legacy-upgrade").apply()
        require(
            legacy_upgrade["appliedVersions"] == [expected_versions[-1]],
            "legacy upgrade must apply only owner authority migration",
        )
        with psycopg.connect(legacy_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT vault_id, owner_subject_id, row_version, authority_state
                    FROM archive_items WHERE id = 'legacy-owner-conflict'
                    """
                )
                quarantined = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT canonical_user_id, observed_owner_claims, incident_code, status
                    FROM resource_authority_incidents
                    WHERE resource_type = 'archiveItem' AND resource_id = 'legacy-owner-conflict'
                    """
                )
                incident = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT vault_id, owner_subject_id, row_version, authority_state
                    FROM mailbox_letters WHERE id = 'legacy-mailbox-recipient'
                    """
                )
                mailbox_recipient = cursor.fetchone()
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM resource_authority_incidents
                    WHERE resource_type = 'mailboxLetter'
                      AND resource_id = 'legacy-mailbox-recipient'
                    """
                )
                mailbox_incident_count = int(cursor.fetchone()[0])
        require(
            quarantined == ("owner-a", "owner-a", 1, "quarantined"),
            "legacy owner mismatch must be quarantined under the database owner",
        )
        require(incident is not None, "legacy owner mismatch incident must be persisted")
        require(incident[0] == "owner-a", "incident canonical owner")
        require(set(incident[1]) == {"owner-a", "owner-b"}, "incident observed claims")
        require(
            incident[2:] == ("legacyOwnerClaimMismatch", "quarantined"),
            "incident classification",
        )
        require(
            mailbox_recipient == ("recipient-a", "recipient-a", 1, "active"),
            "mailbox source owner metadata must not replace the recipient resource owner",
        )
        require(
            mailbox_incident_count == 0,
            "mailbox source owner metadata must not create an authority incident",
        )

        owner_mutation_rejected = False
        try:
            with psycopg.connect(legacy_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE archive_items SET user_id = 'owner-b' WHERE id = 'legacy-owner-conflict'"
                    )
        except Exception:
            owner_mutation_rejected = True
        require(owner_mutation_rejected, "database trigger must reject owner mutation")

        print(
            json.dumps(
                {
                    "status": "passed",
                    "schemaVersion": 1,
                    "freshApply": True,
                    "freshTableCount": table_count,
                    "repeatNoop": True,
                    "verifyHead": verified["expectedHead"],
                    "concurrentApplyCount": len(expected_versions),
                    "concurrentSkipCount": len(expected_versions),
                    "legacyConflictQuarantined": True,
                    "legacyIncidentPersisted": True,
                    "legacyMailboxSourceOwnerAccepted": True,
                    "ownerMutationRejected": True,
                },
                sort_keys=True,
            )
        )
    finally:
        for database_name in database_names:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
