#!/usr/bin/env python3
"""Verify the default-deny Voice/DH Authority schema in disposable Postgres.

The smoke creates no Provider effect and never uses a production business
database. It proves that a self-only future profile admission is immutable,
bound to the current Vault authority, paired with a receipt, and remains
``blocked``.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.voice_dh_authority import (
    PostgresVoiceDHAuthorityRepository,
    VoiceDHAuthorityContext,
    VoiceDHAuthorityService,
    VoiceDHProvider,
    VoiceDHPurpose,
    VoiceProfileVersionAdmissionCommand,
)


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


def command(*, command_id: str, payload_hash: str = "c" * 64) -> VoiceProfileVersionAdmissionCommand:
    return VoiceProfileVersionAdmissionCommand(
        command_id=command_id,
        profile_id="voice-profile-postgres-smoke",
        profile_version=1,
        subject_id="owner-voice-postgres-smoke",
        purpose=VoiceDHPurpose.PRIVATE_SYNTHESIS,
        provider=VoiceDHProvider.VOLCENGINE_VOICE_CLONE,
        policy_version="voice-policy-v1",
        consent_receipt_hash="a" * 64,
        purpose_grant_hash="b" * 64,
        payload_hash=payload_hash,
    )


def main() -> None:
    admin_dsn: str | None = None
    database_name: str | None = None
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_voice_dh_authority_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="voice-dh-authority-g0",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        context = VoiceDHAuthorityContext(
            vault_id="vault-voice-postgres-smoke",
            owner_subject_id="owner-voice-postgres-smoke",
            actor_subject_id="owner-voice-postgres-smoke",
            authority_epoch=0,
        )
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.vaults (
                        vault_id, owner_subject_id, authority_epoch, status
                    ) VALUES (%s, %s, %s, 'active')
                    """,
                    (context.vault_id, context.owner_subject_id, context.authority_epoch),
                )
            service = VoiceDHAuthorityService(
                PostgresVoiceDHAuthorityRepository(connection),
                enabled=True,
            )
            created = service.admit_self_profile_version(
                context=context,
                command=command(command_id="voice-dh-authority-smoke-001"),
            )
            replayed = service.admit_self_profile_version(
                context=context,
                command=command(command_id="voice-dh-authority-smoke-001"),
            )
            require(created.outcome == "created", "first blocked admission must be created")
            require(replayed.outcome == "deduplicated", "identical blocked admission must deduplicate")
            require(created.profile_version_id == replayed.profile_version_id, "replay must keep profile ID")
            with connection.cursor() as cursor:
                cursor.execute("SELECT status FROM voice_dh.voice_profile_versions")
                profile = cursor.fetchone()
                cursor.execute("SELECT count(*) FROM voice_dh.authority_receipts")
                receipt_count = int(cursor.fetchone()[0])
            require(profile is not None and profile[0] == "blocked", "profile must remain blocked")
            require(receipt_count == 1, "one authority receipt is required")
            connection.commit()

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute("UPDATE voice_dh.voice_profile_versions SET status = 'notAccepted'")
                except psycopg.Error:
                    connection.rollback()
                else:
                    raise AssertionError("Voice/DH Authority rows must be append-only")

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute(
                        """
                        INSERT INTO voice_dh.authority_receipts (
                            id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                            resource_kind, resource_id, purpose, policy_version, operation,
                            reason_code, command_id_hash, payload_hash
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            'voiceProfileVersion', %s, %s, %s, 'blockedAdmission',
                            'g0DefaultDenyNoProviderEffect', %s, %s
                        )
                        """,
                        (
                            str(uuid.uuid4()),
                            context.vault_id,
                            context.owner_subject_id,
                            context.actor_subject_id,
                            context.authority_epoch,
                            str(uuid.uuid4()),
                            VoiceDHPurpose.PRIVATE_SYNTHESIS.value,
                            "voice-policy-v1",
                            "d" * 64,
                            "c" * 64,
                        ),
                    )
                except psycopg.Error:
                    connection.rollback()
                else:
                    raise AssertionError("Voice/DH receipts must bind an exact profile authority record")

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'voice_dh'
                    ORDER BY table_name, ordinal_position
                    """
                )
                columns = {str(row[0]) for row in cursor.fetchall()}
            forbidden = {
                "audio_base64",
                "access_token",
                "provider_speaker_id",
                "secret_key",
                "text_content",
                "url",
            }
            require(not (columns & forbidden), "Authority schema must not store media or provider secrets")

        print(
            "voiceDhAuthorityG0=true "
            "profileStatus=blocked receiptCount=1 providerEffectPerformed=false"
        )
    finally:
        if admin_dsn is not None and database_name is not None:
            try:
                drop_database(admin_dsn, database_name)
            except Exception as error:  # pragma: no cover - cleanup evidence only
                print(f"warning: disposable database cleanup failed: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
