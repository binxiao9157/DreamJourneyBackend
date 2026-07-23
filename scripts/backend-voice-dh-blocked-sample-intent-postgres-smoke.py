#!/usr/bin/env python3
"""Verify the G0 blocked Voice/DH sample-intent chain in disposable Postgres.

This smoke intentionally uses only synthetic hashes. It creates no SampleObject,
audio retention, training command, Provider call, credential, or public route.
"""

from __future__ import annotations

from hashlib import sha256
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
from app.services.safety_policy import (
    HighRiskCapability,
    SubjectEligibilityDecision,
    SubjectEligibilityReason,
)
from app.services.voice_dh_authority import (
    PostgresVoiceDHAuthorityRepository,
    VoiceDHAuthorityContext,
    VoiceDHAuthorityService,
    VoiceDHBlockedSampleIntentCommand,
    VoiceDHProvider,
    VoiceDHPurpose,
    VoiceProfileVersionAdmissionCommand,
)
from app.services.voice_dh_consent_policy import (
    VoiceDHPurpose as ConsentVoiceDHPurpose,
    VoiceDHPurposeConsentDecision,
    VoiceDHPurposeConsentDisposition,
)
from app.services.voice_training_preflight_shadow import (
    VoiceTrainingCommandPreflightShadow,
    VoiceTrainingEvidenceReference,
    VoiceTrainingPreflightRequest,
    VoiceTrainingProfileReference,
    VoiceTrainingSampleDescriptor,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


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


def profile_command() -> VoiceProfileVersionAdmissionCommand:
    return VoiceProfileVersionAdmissionCommand(
        command_id="voice-dh-sample-profile-001",
        profile_id="voice-training-profile-postgres-smoke",
        profile_version=1,
        subject_id="owner-voice-sample-postgres-smoke",
        purpose=VoiceDHPurpose.TRAINING,
        provider=VoiceDHProvider.VOLCENGINE_VOICE_CLONE,
        policy_version="voice-policy-v1",
        consent_receipt_hash="a" * 64,
        purpose_grant_hash="b" * 64,
        payload_hash="c" * 64,
    )


def preflight_request() -> VoiceTrainingPreflightRequest:
    return VoiceTrainingPreflightRequest(
        evaluationMode="syntheticG0",
        vaultId="vault-voice-sample-postgres-smoke",
        ownerSubjectId="owner-voice-sample-postgres-smoke",
        actorSubjectId="owner-voice-sample-postgres-smoke",
        subjectId="owner-voice-sample-postgres-smoke",
        authorityEpoch=0,
        purpose=ConsentVoiceDHPurpose.TRAINING,
        provider="volcengineVoiceClone",
        region="cn-mainland",
        requestHash=digest("voice-dh-sample-preflight-request"),
        profileReference=VoiceTrainingProfileReference(
            profileId="voice-training-profile-postgres-smoke",
            profileVersion=1,
        ),
        consentDecision=VoiceDHPurposeConsentDecision(
            policyVersion="voice-policy-v1",
            purpose=ConsentVoiceDHPurpose.TRAINING,
            status=VoiceDHPurposeConsentDisposition.DENIED,
            reasonCodes=("syntheticG0Only",),
            syntheticPreconditionsSatisfied=True,
            requiredExternalGates=("G1", "G3", "G4"),
        ),
        subjectEligibility=SubjectEligibilityDecision(
            capability=HighRiskCapability.CLONED_VOICE,
            allowed=True,
            decision="allow",
            reason=SubjectEligibilityReason.ELIGIBLE_LIVING_ADULT_SELF,
        ),
        randomConsentStatementReference=VoiceTrainingEvidenceReference(
            referenceHash=digest("statement"),
        ),
        livenessReference=VoiceTrainingEvidenceReference(referenceHash=digest("liveness")),
        qualityReference=VoiceTrainingEvidenceReference(referenceHash=digest("quality")),
        sampleDescriptor=VoiceTrainingSampleDescriptor(
            sourceObjectReferenceHash=digest("source-object"),
            sampleHash=digest("sample"),
            mediaFormat="wav",
            durationMilliseconds=12_000,
            byteLength=128_000,
        ),
    )


def main() -> None:
    admin_dsn: str | None = None
    database_name: str | None = None
    created_database = False
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_voice_dh_sample_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)

    try:
        create_database(admin_dsn, database_name)
        created_database = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="voice-dh-blocked-sample-intent-g0",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(verified["expectedHead"] == "0043", "blocked sample migration must be schema head")

        context = VoiceDHAuthorityContext(
            vault_id="vault-voice-sample-postgres-smoke",
            owner_subject_id="owner-voice-sample-postgres-smoke",
            actor_subject_id="owner-voice-sample-postgres-smoke",
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
            admitted_profile_command = profile_command()
            profile = service.admit_self_profile_version(
                context=context,
                command=admitted_profile_command,
            )
            require(profile.profile_version_id is not None, "blocked parent profile is required")
            request = preflight_request()
            decision = VoiceTrainingCommandPreflightShadow().observe(request, enabled=True)
            sample_command = VoiceDHBlockedSampleIntentCommand.from_synthetic_preflight(
                context=context,
                profile_version_id=profile.profile_version_id,
                profile_command=admitted_profile_command,
                profile_policy_version="voice-policy-v1",
                command_id="voice-dh-sample-intent-001",
                request=request,
                decision=decision,
            )
            created = service.admit_blocked_training_sample_intent(
                context=context,
                command=sample_command,
            )
            replayed = service.admit_blocked_training_sample_intent(
                context=context,
                command=sample_command,
            )
            require(created.outcome == "created", "first sample intent must be created")
            require(replayed.outcome == "deduplicated", "identical sample intent must deduplicate")
            require(
                created.sample_intent_id == replayed.sample_intent_id,
                "sample intent replay must keep its deterministic ID",
            )
            with connection.cursor() as cursor:
                cursor.execute("SELECT status FROM voice_dh.sample_intents")
                sample = cursor.fetchone()
                cursor.execute(
                    "SELECT resource_kind, count(*) FROM voice_dh.authority_receipts "
                    "GROUP BY resource_kind ORDER BY resource_kind"
                )
                receipt_counts = {str(kind): int(count) for kind, count in cursor.fetchall()}
            require(sample is not None and sample[0] == "blocked", "sample intent must remain blocked")
            require(receipt_counts == {"sampleIntent": 1, "voiceProfileVersion": 1}, "paired receipts required")
            summary = created.value_free_summary()
            require(summary["providerEffectPerformed"] is False, "Provider effects must remain impossible")
            require(summary["sampleObjectCreated"] is False, "no sample object may be created")
            require(summary["trainingCommandCreated"] is False, "no training command may be created")
            connection.commit()

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                try:
                    cursor.execute("UPDATE voice_dh.sample_intents SET status = 'notAccepted'")
                except psycopg.Error:
                    connection.rollback()
                else:
                    raise AssertionError("Voice/DH sample intents must be append-only")

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
                            'sampleIntent', %s, %s, %s, 'blockedAdmission',
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
                            VoiceDHPurpose.TRAINING.value,
                            "voice-policy-v1",
                            "d" * 64,
                            "c" * 64,
                        ),
                    )
                except psycopg.Error:
                    connection.rollback()
                else:
                    raise AssertionError("sample intent receipts must bind an exact blocked intent")

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
            "voiceDhBlockedSampleIntentG0=true "
            "sampleStatus=blocked receiptCount=2 providerEffectPerformed=false "
            "sampleObjectCreated=false trainingCommandCreated=false"
        )
    finally:
        if created_database and admin_dsn is not None and database_name is not None:
            try:
                drop_database(admin_dsn, database_name)
            except Exception as error:  # pragma: no cover - cleanup evidence only
                print(f"warning: disposable database cleanup failed: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
