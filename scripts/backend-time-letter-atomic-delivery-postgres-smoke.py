#!/usr/bin/env python3
"""Exercise hidden V4 TimeLetter target delivery in a disposable Postgres DB.

The script proves only the internal transaction contract.  It does not expose
the legacy dispatch route, start a worker, send APNs, or touch deployment data.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import os
from pathlib import Path
import sys
from typing import Any
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.delegated_access import (
    AccessGrantCommand,
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    ResourceScopeType,
)
from app.services.postgres_store import PostgresStore
from app.services.time_letter_delivery_effects import build_time_letter_delivery_plan
from app.services.time_letter_delivery_service import (
    TimeLetterAtomicDeliveryPersistenceError,
    TimeLetterAtomicDeliveryService,
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


def v4_time_letter(
    *,
    letter_id: str,
    owner_subject_id: str,
    recipient_id: str,
    recipient_subject_id: str,
    body: str,
) -> dict[str, Any]:
    return {
        "id": letter_id,
        "kind": "timeLetter",
        "userId": owner_subject_id,
        "ownerSubjectId": owner_subject_id,
        "vaultId": owner_subject_id,
        "authorityEpoch": 1,
        "sealedVersion": 1,
        "sealedPayloadHash": sha256(f"sealed:{letter_id}:v1".encode("utf-8")).hexdigest(),
        "deliveryState": "sealed",
        "deliveryStatus": "scheduled",
        "openAt": "2026-07-20T09:00:00Z",
        "recipients": [
            {
                "id": recipient_id,
                "subjectId": recipient_subject_id,
                "type": "family",
            }
        ],
        "title": "SMOKE_TITLE_MUST_NOT_ENTER_MAILBOX",
        "note": body,
    }


def mailbox_count(dsn: str, user_id: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM mailbox_letters WHERE user_id = %s", (user_id,))
            return int(cursor.fetchone()[0])


def archive_payload(dsn: str, *, owner_subject_id: str, letter_id: str) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor(row_factory=psycopg.rows.dict_row) as cursor:
            cursor.execute(
                "SELECT payload FROM archive_items WHERE user_id = %s AND id = %s",
                (owner_subject_id, letter_id),
            )
            row = cursor.fetchone()
            require(row is not None, "seeded timeLetter disappeared")
            return dict(row["payload"])


def seed_authorized_recipient(
    store: PostgresStore,
    *,
    owner_subject_id: str,
    recipient_id: str,
    recipient_subject_id: str,
    letter_id: str,
) -> None:
    member = store.add_family_member(
        owner_subject_id,
        {
            "id": recipient_id,
            "name": "Smoke Recipient",
            "accessStatus": "active",
            "invitationStatus": "accepted",
            "memberUserId": recipient_subject_id,
        },
    )
    access = DelegatedAccessService(store)
    relationship = access.ensure_relationship_for_member(
        owner_subject_id=owner_subject_id,
        member=member,
        accepted_subject_id=recipient_subject_id,
    )
    access.grant_access(
        AccessGrantCommand(
            grantorSubjectId=owner_subject_id,
            relationshipId=str(relationship["id"]),
            granteeSubjectId=recipient_subject_id,
            purpose=AccessGrantPurpose.TIME_LETTER_READ,
            resourceType=ResourceScopeType.TIME_LETTER,
            resourceId=letter_id,
            operations=[GrantOperation.READ],
            expiresAt="2099-01-01T00:00:00Z",
        )
    )


def grant_time_letter(
    store: PostgresStore,
    *,
    owner_subject_id: str,
    recipient_id: str,
    recipient_subject_id: str,
    letter_id: str,
) -> None:
    relationship = store.get_family_relationship_by_member(owner_subject_id, recipient_id)
    require(relationship is not None, "seeded recipient relationship is required")
    DelegatedAccessService(store).grant_access(
        AccessGrantCommand(
            grantorSubjectId=owner_subject_id,
            relationshipId=str(relationship["id"]),
            granteeSubjectId=recipient_subject_id,
            purpose=AccessGrantPurpose.TIME_LETTER_READ,
            resourceType=ResourceScopeType.TIME_LETTER,
            resourceId=letter_id,
            operations=[GrantOperation.READ],
            expiresAt="2099-01-01T00:00:00Z",
        )
    )


class FailingRecipientMailboxStore(PostgresStore):
    def __init__(self, *args, failing_subject_id: str, **kwargs):
        super().__init__(*args, **kwargs)
        self._failing_subject_id = failing_subject_id

    def add_mailbox_letter(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if user_id == self._failing_subject_id:
            raise TimeLetterAtomicDeliveryPersistenceError("injected Postgres recipient mailbox failure")
        return super().add_mailbox_letter(user_id, payload)


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_time_letter_delivery_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    owner = "owner-time-letter-smoke"
    recipient = "recipient-time-letter-smoke"
    recipient_id = "family-time-letter-smoke"
    due_now = "2026-07-20T09:00:01Z"
    letter_id = "time-letter-atomic-smoke"

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="time-letter-atomic-delivery-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedVersions"][-1] == "0024", "unexpected migration head")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
        store.open_pool(wait=True)
        seed_authorized_recipient(
            store,
            owner_subject_id=owner,
            recipient_id=recipient_id,
            recipient_subject_id=recipient,
            letter_id=letter_id,
        )
        item = v4_time_letter(
            letter_id=letter_id,
            owner_subject_id=owner,
            recipient_id=recipient_id,
            recipient_subject_id=recipient,
            body="SMOKE_BODY_MUST_NEVER_BE_IN_MAILBOX_OR_RECEIPT",
        )
        store.add_archive_item(owner, item)
        plan = build_time_letter_delivery_plan(item, now_iso=due_now)
        service = TimeLetterAtomicDeliveryService(store)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: service.dispatch(plan, now_iso=due_now), range(2)))
        require(
            sorted(result.outcome for result in results) == ["already_terminal", "delivered"],
            "concurrent dispatch must produce one delivery and one terminal replay",
        )
        require(mailbox_count(test_dsn, owner) == 1, "owner must receive one metadata-only mailbox record")
        require(mailbox_count(test_dsn, recipient) == 1, "recipient must receive one metadata-only mailbox record")
        payload = archive_payload(test_dsn, owner_subject_id=owner, letter_id=letter_id)
        require(payload.get("deliveryStatus") == "delivered", "authorized target set must finalize delivered")
        summary = payload.get("deliverySummary") or {}
        require(summary.get("targetCount") == 2, "summary must include owner and recipient")
        require(summary.get("deliveredCount") == 2, "summary must retain both terminal deliveries")
        require("SMOKE_BODY" not in repr(summary), "summary must be value-free")
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT payload::text FROM mailbox_letters ORDER BY id ASC")
                mailbox_text = "\n".join(str(row[0]) for row in cursor.fetchall())
                require("SMOKE_BODY" not in mailbox_text, "mailbox must not retain TimeLetter body")
                require("SMOKE_TITLE" not in mailbox_text, "mailbox must not retain TimeLetter title")
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM async_effects.consumer_inbox
                    WHERE consumer_name = 'timeLetter.deliveryTarget'
                      AND state = 'completed'
                    """
                )
                require(int(cursor.fetchone()[0]) == 2, "each target must retain a terminal consumer inbox")

        revoked_letter_id = "time-letter-atomic-revoked"
        revoked_item = v4_time_letter(
            letter_id=revoked_letter_id,
            owner_subject_id=owner,
            recipient_id=recipient_id,
            recipient_subject_id=recipient,
            body="REVOKED_BODY_MUST_NOT_DELIVER",
        )
        store.add_archive_item(owner, revoked_item)
        revoked_plan = build_time_letter_delivery_plan(revoked_item, now_iso=due_now)
        revoked_result = service.dispatch(revoked_plan, now_iso=due_now)
        require(revoked_result.outcome == "partial", "missing resource grant must be partial")
        require(mailbox_count(test_dsn, owner) == 2, "revoked letter still delivers to owner")
        require(mailbox_count(test_dsn, recipient) == 1, "revoked recipient must not receive a mailbox")
        revoked_payload = archive_payload(test_dsn, owner_subject_id=owner, letter_id=revoked_letter_id)
        require(
            (revoked_payload.get("deliverySummary") or {}).get("skippedRevokedCount") == 1,
            "revoked recipient must retain a skipped receipt summary",
        )

        failing_letter_id = "time-letter-atomic-rollback"
        failing_item = v4_time_letter(
            letter_id=failing_letter_id,
            owner_subject_id=owner,
            recipient_id=recipient_id,
            recipient_subject_id=recipient,
            body="ROLLBACK_BODY_MUST_NOT_PERSIST",
        )
        store.add_archive_item(owner, failing_item)
        grant_time_letter(
            store,
            owner_subject_id=owner,
            recipient_id=recipient_id,
            recipient_subject_id=recipient,
            letter_id=failing_letter_id,
        )
        failing_plan = build_time_letter_delivery_plan(failing_item, now_iso=due_now)
        failing_store = FailingRecipientMailboxStore(
            dsn=test_dsn,
            pool_min_size=1,
            pool_max_size=2,
            failing_subject_id=recipient,
        )
        failing_store.open_pool(wait=True)
        try:
            with unittest_raises(TimeLetterAtomicDeliveryPersistenceError):
                TimeLetterAtomicDeliveryService(failing_store).dispatch(failing_plan, now_iso=due_now)
        finally:
            failing_store.close_pool()
        require(mailbox_count(test_dsn, owner) == 2, "rollback must remove owner mailbox too")
        failed_payload = archive_payload(test_dsn, owner_subject_id=owner, letter_id=failing_letter_id)
        require(failed_payload.get("deliveryStatus") == "scheduled", "rollback must retain scheduled status")

        print(
            "time letter atomic delivery postgres smoke passed "
            "ownerMailbox=2 recipientMailbox=1 concurrent=deduplicated "
            "revokedRecipient=skipped rollback=clean"
        )
    finally:
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


class unittest_raises:
    """Small dependency-free equivalent of ``assertRaises`` for a smoke script."""

    def __init__(self, expected: type[BaseException]):
        self._expected = expected

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, _tb):
        if exc_type is None:
            raise AssertionError(f"expected {self._expected.__name__}")
        if not issubclass(exc_type, self._expected):
            return False
        return True


if __name__ == "__main__":
    main()
