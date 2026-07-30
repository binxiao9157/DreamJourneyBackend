#!/usr/bin/env python3
"""Exercise Time Letter recipient-admission shadow in a disposable Postgres DB.

The smoke proves a narrow internal read path only: a due, completed recipient
delivery target can be admitted only with a verified recipient inbox bridge
and an exact active ``timeLetter.read`` grant. It neither writes a business
message or legacy mailbox row nor starts a worker, notification, session or
Provider effect.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.async_effects.business_message_recipient_admission import (
    TimeLetterRecipientMessageAdmissionInput,
    TimeLetterRecipientMessageAdmissionService,
)
from app.async_effects.message_notification_effects import (
    BusinessCompletionMessageSource,
    InAppMessageKind,
)
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.delegated_access import (
    AccessGrantCommand,
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    RevokeAccessGrantCommand,
    ResourceScopeType,
)
from app.services.postgres_store import PostgresStore
from app.services.time_letter_delivery_effects import (
    TimeLetterDeliveryCompletion,
    TimeLetterDeliveryDisposition,
    TimeLetterDeliveryTarget,
    TimeLetterSealedSnapshot,
)


NOW = datetime(2026, 7, 30, 8, 0, tzinfo=timezone.utc)
OWNER_SUBJECT_ID = "subject-owner-recipient-admission-smoke"
OWNER_VAULT_ID = "vault-owner-recipient-admission-smoke"
RECIPIENT_SUBJECT_ID = "subject-recipient-admission-smoke"
RECIPIENT_VAULT_ID = "vault-recipient-admission-smoke"
RECIPIENT_LEGACY_USER_ID = "user-recipient-admission-smoke"
FAMILY_MEMBER_ID = "family-recipient-admission-smoke"
LETTER_ID = "letter-recipient-admission-smoke"


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


def recipient_account_payload(*, access_state: str = "active") -> str:
    return json.dumps(
        {
            "accessState": access_state,
            "authEpoch": 9,
            "deletionState": "active",
        },
        sort_keys=True,
    )


def seed_identity_bridge(dsn: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO subjects (id, status) VALUES (%s, 'active')", (OWNER_SUBJECT_ID,))
            cursor.execute(
                "INSERT INTO subjects (id, status) VALUES (%s, 'active')",
                (RECIPIENT_SUBJECT_ID,),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.vaults (vault_id, owner_subject_id, authority_epoch, status)
                VALUES (%s, %s, 5, 'active'), (%s, %s, 5, 'active')
                """,
                (OWNER_VAULT_ID, OWNER_SUBJECT_ID, RECIPIENT_VAULT_ID, RECIPIENT_SUBJECT_ID),
            )
            cursor.execute(
                """
                INSERT INTO users (id, phone, nickname, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (
                    RECIPIENT_LEGACY_USER_ID,
                    "+8613800138001",
                    "recipient smoke",
                    recipient_account_payload(),
                ),
            )
            cursor.execute(
                """
                INSERT INTO identity_hash_key_versions (version, key_fingerprint, status)
                VALUES ('v1', %s, 'active')
                """,
                (digest("recipient-admission-smoke-key"),),
            )
            cursor.execute(
                """
                INSERT INTO identity_bindings (
                    id, subject_id, identity_type, target_hash_key_version,
                    target_hash, provider_mode, status, verified_at
                ) VALUES ('binding-recipient-admission-smoke', %s, 'phone', 'v1', %s,
                          'synthetic', 'active', NOW())
                """,
                (RECIPIENT_SUBJECT_ID, digest("recipient-admission-smoke-phone")),
            )
            cursor.execute(
                """
                INSERT INTO auth_challenges (
                    id, identity_type, target_hash_key_version, target_hash, code_hash,
                    provider_mode, purpose, status, attempts, max_attempts,
                    internal_verification_enabled, expires_at
                ) VALUES ('challenge-recipient-admission-smoke', 'phone', 'v1', %s, %s,
                          'synthetic', 'login', 'consumed', 1, 3, true, NOW() + INTERVAL '1 hour')
                """,
                (digest("recipient-admission-smoke-phone"), digest("recipient-admission-smoke-code")),
            )
            cursor.execute(
                """
                INSERT INTO identity_proofs (
                    id, challenge_id, binding_id, subject_id, provider_mode, verified_at
                ) VALUES ('proof-recipient-admission-smoke',
                          'challenge-recipient-admission-smoke',
                          'binding-recipient-admission-smoke', %s, 'synthetic', NOW())
                """,
                (RECIPIENT_SUBJECT_ID,),
            )
            cursor.execute(
                """
                INSERT INTO legacy_identity_aliases (
                    legacy_account_user_id, legacy_alias_hash, subject_id, vault_id,
                    claim_state, identity_proof_id, reason_code, claimed_at
                ) VALUES (%s, %s, %s, %s, 'verified', 'proof-recipient-admission-smoke',
                          'smokeVerifiedBridge', NOW())
                """,
                (
                    RECIPIENT_LEGACY_USER_ID,
                    digest("recipient-admission-smoke-alias"),
                    RECIPIENT_SUBJECT_ID,
                    RECIPIENT_VAULT_ID,
                ),
            )


def delivery_evidence(store: PostgresStore) -> tuple[
    TimeLetterDeliveryTarget,
    TimeLetterDeliveryCompletion,
    BusinessCompletionMessageSource,
]:
    target = TimeLetterDeliveryTarget(
        snapshot=TimeLetterSealedSnapshot(
            owner_subject_id=OWNER_SUBJECT_ID,
            vault_id=OWNER_VAULT_ID,
            letter_id=LETTER_ID,
            sealed_version=2,
            authority_epoch=5,
            sealed_payload_hash=digest("recipient-admission-sealed-letter"),
            open_at="2026-07-30T07:00:00Z",
        ),
        recipient_id=FAMILY_MEMBER_ID,
        recipient_subject_id=RECIPIENT_SUBJECT_ID,
        role="recipient",
    )
    completion = TimeLetterDeliveryCompletion(
        target=target,
        disposition=TimeLetterDeliveryDisposition.DELIVERED,
        reason_code="mailboxPersisted",
    )
    command = completion.consumer_command
    with store.request_unit_of_work(
        correlation_id="recipient-admission-smoke-effect-accept",
        command_id="recipientAdmissionSmokeEffectAccept",
    ):
        store.effect_kernel_repository().accept(command.intent)
    with store.request_unit_of_work(
        correlation_id="recipient-admission-smoke-effect-complete",
        command_id="recipientAdmissionSmokeEffectComplete",
    ):
        receipt = store.async_effect_consumer_repository().consume(command)
    return (
        target,
        completion,
        BusinessCompletionMessageSource(
            intent=command.intent,
            completion=receipt,
            message_kind=InAppMessageKind.TIME_LETTER,
        ),
    )


def grant_exact_time_letter_access(store: PostgresStore) -> dict[str, object]:
    access = DelegatedAccessService(store, now_provider=lambda: NOW)
    relationship = access.ensure_relationship(
        owner_subject_id=OWNER_SUBJECT_ID,
        family_member_id=FAMILY_MEMBER_ID,
        member_subject_id=RECIPIENT_SUBJECT_ID,
        status="accepted",
    )
    return access.grant_access(
        AccessGrantCommand(
            grantorSubjectId=OWNER_SUBJECT_ID,
            relationshipId=str(relationship["id"]),
            granteeSubjectId=RECIPIENT_SUBJECT_ID,
            purpose=AccessGrantPurpose.TIME_LETTER_READ,
            resourceType=ResourceScopeType.TIME_LETTER,
            resourceId=LETTER_ID,
            operations=[GrantOperation.READ],
            expiresAt=NOW + timedelta(days=1),
        )
    )


def table_count(dsn: str, table: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(*table.split("."))))
            return int(cursor.fetchone()[0])


def exercise(dsn: str) -> None:
    seed_identity_bridge(dsn)
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=2)
    store.open_pool(wait=True)
    try:
        target, completion, owner_source = delivery_evidence(store)
        grant = grant_exact_time_letter_access(store)
        baseline_grant_events = table_count(dsn, "grant_events")
        admission_input = TimeLetterRecipientMessageAdmissionInput(
            source=owner_source,
            delivery_completion=completion,
            target=target,
            now_iso=NOW.isoformat(),
        )
        service = TimeLetterRecipientMessageAdmissionService(store)
        admitted = service.evaluate_shadow(admission_input, enabled=True)
        require(admitted.would_admit, "verified bridge plus exact grant must wouldAdmit")
        require(admitted.admission is not None, "wouldAdmit needs an evidence summary")
        require(admitted.admission.access_decision.grant_id == grant["id"], "exact grant must match")
        require(
            admitted.admission.access_decision.receipt_id is None,
            "read-only admission must not record an access receipt",
        )
        summary = json.dumps(admitted.value_free_summary(), sort_keys=True)
        for raw_value in (
            OWNER_SUBJECT_ID,
            OWNER_VAULT_ID,
            RECIPIENT_SUBJECT_ID,
            RECIPIENT_VAULT_ID,
            RECIPIENT_LEGACY_USER_ID,
            LETTER_ID,
        ):
            require(raw_value not in summary, "admission summary leaked a raw identifier")
        require(
            table_count(dsn, "grant_events") == baseline_grant_events,
            "read-only admission must not append an access event",
        )

        revoked = DelegatedAccessService(store, now_provider=lambda: NOW).revoke_access(
            RevokeAccessGrantCommand(
                grantorSubjectId=OWNER_SUBJECT_ID,
                grantId=str(grant["id"]),
                expectedVersion=int(grant["rowVersion"]),
                reason="smokeGrantRevoked",
            )
        )
        require(revoked["status"] == "revoked", "synthetic grant must revoke")
        denied_after_revoke = service.evaluate_shadow(admission_input, enabled=True)
        require(
            denied_after_revoke.reason_code == "delegatedAccessDenied:activeGrantRequired",
            "revoked exact grant must fail closed",
        )

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE users SET payload = %s::jsonb WHERE id = %s",
                    (recipient_account_payload(access_state="suspended_restorable"), RECIPIENT_LEGACY_USER_ID),
                )
        denied_after_account_suspend = service.evaluate_shadow(admission_input, enabled=True)
        require(
            denied_after_account_suspend.reason_code == "recipientInboxUnavailable",
            "suspended recipient inbox must fail before delegated access use",
        )
    finally:
        store.close_pool()

    require(table_count(dsn, "mailbox_letters") == 0, "admission must not write legacy mailbox")
    require(
        table_count(dsn, "async_effects.business_message_projections") == 0,
        "admission must not write business-message projection",
    )


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_time_letter_recipient_admission_{uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    created = False
    try:
        create_database(admin_dsn, database_name)
        created = True
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="time-letter-recipient-admission-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        require(migrator.verify()["status"] == "ready", "temporary schema must verify")
        exercise(test_dsn)
        print(
            "Time Letter recipient-admission Postgres smoke passed "
            "(shadow only; no mailbox, message projection, worker, notification, session, or Provider effect)."
        )
    finally:
        if created:
            drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
