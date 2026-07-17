#!/usr/bin/env python3
import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event

from app.core.config import settings
from app.services.auth_sessions import AuthSessionError, AuthSessionService, auth_token_hash
from app.services.postgres_store import PostgresStore


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def refresh_once(service, refresh_token):
    try:
        return {"outcome": "success", "auth": service.refresh(refresh_token)}
    except AuthSessionError as error:
        return {"outcome": error.code, "auth": None}


def cleanup(store, user_ids):
    with store.request_unit_of_work(
        correlation_id="auth-token-family-smoke-cleanup",
        command_id="cleanupAuthTokenFamilySmoke",
    ) as unit_of_work:
        with unit_of_work.connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
            cursor.execute(
                "DELETE FROM session_events WHERE user_id = ANY(%s)",
                (list(user_ids),),
            )
            cursor.execute(
                "DELETE FROM token_families WHERE user_id = ANY(%s)",
                (list(user_ids),),
            )
            cursor.execute(
                "DELETE FROM auth_sessions WHERE user_id = ANY(%s)",
                (list(user_ids),),
            )
            cursor.execute(
                "DELETE FROM users WHERE id = ANY(%s)",
                (list(user_ids),),
            )


def main():
    dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(dsn, "DATABASE_URL is required")
    suffix = secrets.token_hex(8)
    concurrent_user = f"smoke-token-concurrent-{suffix}"
    rollback_user = f"smoke-token-rollback-{suffix}"
    revoke_user = f"smoke-token-revoke-{suffix}"
    other_user = f"smoke-token-other-{suffix}"
    race_phone = f"199{suffix[:8]}"
    purge_race_phone = f"198{suffix[:8]}"
    user_ids = {concurrent_user, rollback_user, revoke_user, other_user}

    store = PostgresStore(
        dsn=dsn,
        pool_min_size=2,
        pool_max_size=4,
        pool_timeout_seconds=2.0,
    )
    store.open_pool(wait=True)
    try:
        service = AuthSessionService(
            store,
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
        )

        concurrent = service.issue(concurrent_user)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda _: refresh_once(service, concurrent["refreshToken"]),
                    range(2),
                )
            )
        successes = [item["auth"] for item in results if item["outcome"] == "success"]
        failures = [item["outcome"] for item in results if item["outcome"] != "success"]
        require(len(successes) == 1, "concurrent refresh must issue exactly one successor")
        require(
            failures == ["refresh_token_reuse_detected"],
            "concurrent duplicate refresh must be classified as reuse",
        )
        require(
            service.resolve_access_token(successes[0]["accessToken"]) is None,
            "reuse detection must revoke the issued descendant",
        )
        events = store.list_auth_session_events(concurrent["tokenFamilyId"])
        require(
            sum(event["eventType"] == "refreshReuseDetected" for event in events) == 1,
            "reuse receipt must be persisted exactly once",
        )

        rollback = service.issue(rollback_user)
        failing_successor = {
            "sessionId": rollback["sessionId"],
            "accessTokenHash": auth_token_hash("dja_rollback_collision"),
            "refreshTokenHash": auth_token_hash("djr_rollback_collision"),
            "status": "active",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "accessExpiresAt": rollback["accessExpiresAt"],
            "refreshExpiresAt": rollback["refreshExpiresAt"],
            "contractVersion": 2,
        }
        failed = False
        try:
            store.rotate_auth_session_refresh(
                auth_token_hash(rollback["refreshToken"]),
                successor=failing_successor,
                rotated_at_iso=datetime.now(timezone.utc).isoformat(),
                rotation_receipt_id=f"ase_smoke_collision_{suffix}",
                reuse_receipt_id=f"ase_smoke_collision_reuse_{suffix}",
            )
        except Exception:
            failed = True
        require(failed, "injected successor insert failure must surface")
        recovered = service.refresh(rollback["refreshToken"])
        require(
            recovered["sessionVersion"] == 2,
            "failed rotation must roll back refresh consumption",
        )

        first = service.issue(revoke_user)
        second = service.issue(revoke_user)
        other = service.issue(other_user)
        revocation = service.revoke_all_for_user(revoke_user, reason="riskEventSmoke")
        require(revocation["revokedFamilyCount"] == 2, "all-device revoke family count")
        require(service.resolve_access_token(first["accessToken"]) is None, "first family revoked")
        require(service.resolve_access_token(second["accessToken"]) is None, "second family revoked")
        require(service.resolve_access_token(other["accessToken"]) is not None, "other user isolated")
        with store.request_unit_of_work(
            correlation_id=f"auth-token-receipt-{suffix}",
            command_id="inspectAllDeviceReceipt",
        ):
            aggregate_receipt = store._fetchone(
                "SELECT id FROM session_events WHERE id = %s",
                (revocation["revocationReceiptId"],),
            )
        require(aggregate_receipt is not None, "all-device receipt must be persisted")

        race_user = store.upsert_user(phone=race_phone, nickname="auth race smoke")
        race_user_id = str(race_user["id"])
        user_ids.add(race_user_id)
        race_existing = service.issue(race_user_id)
        issue_started = Event()

        def issue_after_delete_starts():
            issue_started.set()
            try:
                service.issue(race_user_id)
            except AuthSessionError as error:
                return error.code
            return "unexpected_success"

        with ThreadPoolExecutor(max_workers=1) as executor:
            with store.auth_user_operation(race_user_id):
                future = executor.submit(issue_after_delete_starts)
                require(issue_started.wait(timeout=2), "late issue worker did not start")
                deleted = store.soft_delete_user(race_user_id, phone=race_phone)
                service.revoke_all_for_user(race_user_id, reason="accountSoftDeletedSmoke")
            late_issue_outcome = future.result(timeout=2)
        require(deleted["deletionState"] == "softDeleted", "race account soft delete")
        require(
            late_issue_outcome == "account_session_issuance_blocked",
            "late session issue must fail after account deletion",
        )
        require(
            service.resolve_access_token(race_existing["accessToken"]) is None,
            "account deletion must revoke the pre-existing session",
        )

        purge_race_user = store.upsert_user(
            phone=purge_race_phone,
            nickname="auth purge race smoke",
        )
        purge_race_user_id = str(purge_race_user["id"])
        user_ids.add(purge_race_user_id)
        purge_race_auth = service.issue(purge_race_user_id)
        initial_resolved = Event()
        continue_revoke = Event()
        original_resolve = service.resolve_access_token
        resolve_count = 0

        def gated_resolve(access_token, *, now=None):
            nonlocal resolve_count
            resolved = original_resolve(access_token, now=now)
            if access_token == purge_race_auth["accessToken"]:
                resolve_count += 1
                if resolve_count == 1:
                    initial_resolved.set()
                    require(
                        continue_revoke.wait(timeout=5),
                        "all-device revoke was not released after purge",
                    )
            return resolved

        service.resolve_access_token = gated_resolve
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                revoke_future = executor.submit(
                    service.revoke_access_token,
                    purge_race_auth["accessToken"],
                    scope="allDevices",
                    reason="logoutAfterPurgeSmoke",
                )
                require(
                    initial_resolved.wait(timeout=5),
                    "all-device revoke did not complete its initial lookup",
                )
                store.soft_delete_user(
                    purge_race_user_id,
                    phone=purge_race_phone,
                    requested_at_iso="2000-01-01T00:00:00+00:00",
                )
                purged_items = store.purge_expired_deleted_users(
                    "2000-02-01T00:00:00+00:00"
                )
                continue_revoke.set()
                purge_race_revocation = revoke_future.result(timeout=5)
        finally:
            service.resolve_access_token = original_resolve
            continue_revoke.set()
        require(
            any(item.get("id") == purge_race_user_id for item in purged_items),
            "purge race account must reach the terminal state",
        )
        require(
            purge_race_revocation is None,
            "stale all-device access lookup must not revoke after purge",
        )
        require(
            store.soft_delete_user(purge_race_user_id, phone=purge_race_phone) is None,
            "purged account must not transition back to soft deleted",
        )
        require(
            store.restore_user(purge_race_user_id, phone=purge_race_phone) is None,
            "purged account must not be restored",
        )
        with store.request_unit_of_work(
            correlation_id=f"auth-token-purge-race-inspect-{suffix}",
            command_id="inspectPurgeRaceEvents",
        ):
            post_purge_event = store._fetchone(
                "SELECT id FROM session_events WHERE user_id = %s LIMIT 1",
                (purge_race_user_id,),
            )
        require(
            post_purge_event is None,
            "all-device revoke must not recreate an event after purge",
        )

        constraint_user = f"smoke-token-constraint-{suffix}"
        constraint_failed = False
        try:
            with store.request_unit_of_work(
                correlation_id=f"auth-token-constraint-{suffix}",
                command_id="verifyFamilyVersionConstraint",
            ) as unit_of_work:
                with unit_of_work.connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO token_families (
                            id, user_id, status, current_session_version,
                            contract_version, created_at, updated_at
                        ) VALUES (%s, %s, 'active', 1, 1, NOW(), NOW())
                        """,
                        (f"tf_constraint_{suffix}", constraint_user),
                    )
                    cursor.execute(
                        """
                        INSERT INTO auth_sessions (
                            id, user_id, access_token_hash, refresh_token_hash,
                            status, payload, access_expires_at, refresh_expires_at,
                            family_id, session_version, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, 'active', '{}'::jsonb,
                            NOW() + INTERVAL '15 minutes', NOW() + INTERVAL '1 day',
                            %s, NULL, NOW(), NOW()
                        )
                        """,
                        (
                            f"auth_constraint_{suffix}",
                            constraint_user,
                            secrets.token_hex(32),
                            secrets.token_hex(32),
                            f"tf_constraint_{suffix}",
                        ),
                    )
        except Exception:
            constraint_failed = True
        require(
            constraint_failed,
            "family-backed auth session must reject a null session version",
        )

        with store.request_unit_of_work(
            correlation_id=f"auth-token-family-inspect-{suffix}",
            command_id="inspectAuthTokenFamilySmoke",
        ):
            persisted = store._fetchall(
                "SELECT payload::text AS payload FROM auth_sessions WHERE user_id = %s",
                (rollback_user,),
            )
        serialized = json.dumps(persisted, sort_keys=True)
        require(rollback["accessToken"] not in serialized, "raw access token persisted")
        require(rollback["refreshToken"] not in serialized, "raw refresh token persisted")

        print(
            json.dumps(
                {
                    "status": "passed",
                    "schemaVersion": 1,
                    "concurrentSuccessorCount": len(successes),
                    "reuseDetected": True,
                    "descendantRevoked": True,
                    "consumeIssueRollback": True,
                    "allDeviceRevoke": True,
                    "allDeviceReceiptPersisted": True,
                    "deleteIssueRaceBlocked": True,
                    "purgedAccountTerminal": True,
                    "allDevicePurgeRaceBlocked": True,
                    "familyVersionConstraint": True,
                    "crossUserIsolation": True,
                    "opaqueTokenPersistence": True,
                },
                sort_keys=True,
            )
        )
    finally:
        try:
            cleanup(store, user_ids)
        finally:
            store.close_pool()


if __name__ == "__main__":
    main()
