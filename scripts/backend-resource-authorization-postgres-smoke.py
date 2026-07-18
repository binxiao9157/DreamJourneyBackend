#!/usr/bin/env python3
import base64
import json
import os
import secrets
import urllib.error
import urllib.request

from app.services.auth_sessions import AuthSessionService
from app.services.postgres_store import PostgresStore


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").strip().rstrip("/")
MACHINE_TOKEN = os.environ.get(
    "BACKEND_API_TOKEN",
    os.environ.get("DREAMJOURNEY_BACKEND_API_TOKEN", ""),
).strip()
PRODUCTION_ENVIRONMENTS = {"prod", "production"}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def request_json(method, path, *, token=None, payload=None, expected_status=200):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            response_headers = {key.lower(): value for key, value in response.headers.items()}
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        status = error.code
        response_headers = {key.lower(): value for key, value in error.headers.items()}
        raw_body = error.read().decode("utf-8")
    response_body = json.loads(raw_body) if raw_body else {}
    require(status == expected_status, f"{method} {path} expected {expected_status}, got {status}")
    return response_body, response_headers


def require_production_enforcement(runtime):
    require(isinstance(runtime, dict), "runtime contract must be an object")
    environment = str(runtime.get("environment") or "").strip().lower()
    require(
        environment in PRODUCTION_ENVIRONMENTS,
        "deployed resource authorization smoke requires a production runtime",
    )
    auth = runtime.get("auth")
    require(isinstance(auth, dict), "runtime auth contract is required")
    route_authentication = auth.get("routeAuthentication")
    require(
        isinstance(route_authentication, dict)
        and route_authentication.get("mode") == "enforce",
        "production route authentication must enforce",
    )
    ownership_mode = str(auth.get("ownershipMode") or "").strip().lower()
    require(
        ownership_mode in {"shadow", "enforce"},
        "production ownership authorization mode must be explicit",
    )
    cross_account_policy = auth.get("crossAccountPolicy")
    require(
        isinstance(cross_account_policy, dict)
        and cross_account_policy.get("principalBoundRouteEnforcement") is True,
        "production principal-bound owner routes must enforce",
    )
    return {
        "ownershipMode": ownership_mode,
        "crossAccountMode": str(cross_account_policy.get("mode") or "").strip().lower(),
    }


def cleanup(store, user_ids):
    with store.request_unit_of_work(
        correlation_id="resource-auth-smoke-cleanup",
        command_id="cleanupResourceAuthorizationSmoke",
    ) as unit_of_work:
        with unit_of_work.connection.cursor() as cursor:
            for table in (
                "care_snapshots",
                "family_members",
                "voice_profiles",
                "push_device_tokens",
                "echo_delayed_replies",
                "mailbox_letters",
                "archive_items",
                "memories",
                "session_events",
                "auth_sessions",
                "token_families",
            ):
                cursor.execute(f"DELETE FROM {table} WHERE user_id = ANY(%s)", (list(user_ids),))
            cursor.execute("DELETE FROM users WHERE id = ANY(%s)", (list(user_ids),))


def main():
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(MACHINE_TOKEN, "BACKEND_API_TOKEN is required")
    dsn = os.environ.get("DATABASE_URL", "").strip()
    require(dsn, "DATABASE_URL is required")
    runtime, _ = request_json("GET", "/config/runtime")
    enforcement = require_production_enforcement(runtime)

    suffix = secrets.token_hex(8)
    store = PostgresStore(
        dsn=dsn,
        pool_min_size=1,
        pool_max_size=3,
        pool_timeout_seconds=2.0,
    )
    store.open_pool(wait=True)
    user_ids = set()
    try:
        owner = store.upsert_user(phone=f"196{suffix[:8]}", nickname="resource owner smoke")
        attacker = store.upsert_user(phone=f"195{suffix[:8]}", nickname="resource attacker smoke")
        owner_id = str(owner["id"])
        attacker_id = str(attacker["id"])
        user_ids.update({owner_id, attacker_id})
        auth_service = AuthSessionService(
            store,
            access_ttl_seconds=300,
            refresh_ttl_seconds=900,
        )
        owner_token = auth_service.issue(owner_id)["accessToken"]
        attacker_token = auth_service.issue(attacker_id)["accessToken"]

        memory_id = f"resource-memory-{suffix}"
        memory, _ = request_json(
            "POST",
            "/memories",
            token=owner_token,
            payload={"id": memory_id, "title": "owner memory"},
        )
        require(memory["memory"]["userId"] == owner_id, "omitted owner must derive from principal")

        _, nested_headers = request_json(
            "POST",
            "/memories",
            token=owner_token,
            payload={
                "title": "forged nested owner",
                "metadata": {"ownerUserId": attacker_id},
            },
            expected_status=403,
        )
        require(
            nested_headers.get("x-dreamjourney-authorization-reason") == "ownerClaimMismatch",
            "nested owner mismatch must be denied by the typed policy",
        )

        collision, _ = request_json(
            "POST",
            "/memories",
            token=attacker_token,
            payload={"id": memory_id, "title": "takeover attempt"},
            expected_status=409,
        )
        require(
            (collision.get("detail") or {}).get("code") == "resourceOwnershipConflict",
            "same resource id must return a neutral ownership conflict",
        )

        archive_id = f"resource-archive-{suffix}"
        archive_payload = {
            "id": archive_id,
            "kind": "photo",
            "title": "owner archive",
            "privacyMetadata": {"scope": "generationAllowed"},
        }
        archive, archive_headers = request_json(
            "POST",
            "/archive/items",
            token=owner_token,
            payload=archive_payload,
        )
        require(archive["item"]["userId"] == owner_id, "archive owner must be canonical")
        require(archive["item"]["ownerUserId"] == owner_id, "archive owner alias must be canonical")
        require(
            archive_headers.get("x-dreamjourney-auth-principal") == "user",
            "archive owner must derive from an authenticated user principal",
        )
        require(
            archive_headers.get("x-dreamjourney-authorization-reason")
            == "ownerDerivedFromPrincipal",
            "omitted archive owner must be derived from the principal",
        )

        _, forged_archive_headers = request_json(
            "POST",
            "/archive/items",
            token=owner_token,
            payload={
                **archive_payload,
                "id": f"{archive_id}-forged",
                "userId": attacker_id,
                "title": "forged archive owner",
            },
            expected_status=403,
        )
        require(
            forged_archive_headers.get("x-dreamjourney-authorization-reason")
            == "ownerPrincipalMismatch",
            "forged archive owner must be denied",
        )
        _, nested_archive_headers = request_json(
            "POST",
            "/archive/items",
            token=owner_token,
            payload={
                **archive_payload,
                "id": f"{archive_id}-nested-forged",
                "metadata": {"ownerUserId": attacker_id},
            },
            expected_status=403,
        )
        require(
            nested_archive_headers.get("x-dreamjourney-authorization-reason")
            == "ownerClaimMismatch",
            "forged nested archive owner must be denied",
        )

        updated_archive, _ = request_json(
            "POST",
            "/archive/items",
            token=owner_token,
            payload={**archive_payload, "title": "owner archive updated"},
        )
        require(
            updated_archive["item"].get("title") == "owner archive updated",
            "same owner must be able to update an archive item",
        )

        archive_collision, _ = request_json(
            "POST",
            "/archive/items",
            token=attacker_token,
            payload={**archive_payload, "title": "archive takeover attempt"},
            expected_status=409,
        )
        require(
            (archive_collision.get("detail") or {}).get("code") == "archiveItemOwnershipConflict",
            "archive id collision must not transfer owner",
        )

        owner_archives, _ = request_json(
            "GET",
            f"/archive/items/{owner_id}",
            token=owner_token,
        )
        persisted_archive = next(
            (item for item in owner_archives["items"] if item.get("id") == archive_id),
            None,
        )
        require(
            persisted_archive is not None
            and persisted_archive.get("title") == "owner archive updated",
            "cross-owner collision must leave the original payload unchanged",
        )
        _, cross_owner_get_headers = request_json(
            "GET",
            f"/archive/items/{owner_id}",
            token=attacker_token,
            expected_status=403,
        )
        require(
            cross_owner_get_headers.get("x-dreamjourney-authorization-reason")
            == "ownerPrincipalMismatch",
            "cross-owner archive GET must be denied",
        )
        attacker_archives, _ = request_json(
            "GET",
            f"/archive/items/{attacker_id}",
            token=attacker_token,
        )
        require(
            all(item.get("id") != archive_id for item in attacker_archives["items"]),
            "archive collision must not create an attacker-owned copy",
        )

        _, delete_headers = request_json(
            "DELETE",
            f"/archive/items/{attacker_id}/{archive_id}",
            token=attacker_token,
            expected_status=403,
        )
        require(
            delete_headers.get("x-dreamjourney-authorization-reason") == "resourceOwnerMismatch",
            "child resource delete must resolve the database owner",
        )
        _, analysis_headers = request_json(
            "POST",
            "/archive/image-analysis?dryRun=true",
            token=attacker_token,
            payload={
                "archiveItemId": archive_id,
                "imageBase64": base64.b64encode(b"not-an-image").decode("ascii"),
                "privacyMetadata": {"scope": "generationAllowed"},
            },
            expected_status=403,
        )
        require(
            analysis_headers.get("x-dreamjourney-authorization-reason") == "resourceOwnerMismatch",
            "archive analysis must resolve the database owner before provider work",
        )
        _, stale_headers = request_json(
            "POST",
            "/archive/image-analysis?dryRun=true",
            token=owner_token,
            payload={
                "archiveItemId": archive_id,
                "expectedVersion": 999,
                "imageBase64": base64.b64encode(b"not-an-image").decode("ascii"),
                "privacyMetadata": {"scope": "generationAllowed"},
            },
            expected_status=403,
        )
        require(
            stale_headers.get("x-dreamjourney-authorization-reason") == "resourceVersionMismatch",
            "stale resource command must be rejected before provider work",
        )

        quarantined_archive_id = f"resource-archive-quarantined-{suffix}"
        request_json(
            "POST",
            "/archive/items",
            token=owner_token,
            payload={
                "id": quarantined_archive_id,
                "kind": "photo",
                "title": "quarantine list fixture",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        with store.request_unit_of_work(
            correlation_id=f"resource-quarantine-list-{suffix}",
            command_id="quarantineArchiveListFixture",
        ) as unit_of_work:
            with unit_of_work.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE archive_items
                    SET authority_state = 'quarantined'
                    WHERE user_id = %s AND id = %s
                    RETURNING id
                    """,
                    (owner_id, quarantined_archive_id),
                )
                require(cursor.fetchone() is not None, "archive quarantine fixture must exist")
        filtered_archives, _ = request_json(
            "GET",
            f"/archive/items/{owner_id}",
            token=owner_token,
        )
        filtered_archive_ids = {
            str(item.get("id") or "") for item in filtered_archives["items"]
        }
        require(archive_id in filtered_archive_ids, "active same-owner archive must remain readable")
        require(
            quarantined_archive_id not in filtered_archive_ids,
            "ordinary archive list must hide quarantined records",
        )

        letter_id = f"resource-letter-{suffix}"
        request_json(
            "POST",
            "/mailbox/letters",
            token=MACHINE_TOKEN,
            payload={
                "id": letter_id,
                "userId": owner_id,
                "title": "owner letter",
                "body": "value-free smoke fixture",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
        )
        mailbox_collision, _ = request_json(
            "POST",
            "/mailbox/letters",
            token=MACHINE_TOKEN,
            payload={
                "id": letter_id,
                "userId": attacker_id,
                "title": "mailbox takeover",
                "body": "value-free smoke fixture",
                "privacyMetadata": {"scope": "generationAllowed"},
            },
            expected_status=409,
        )
        require(
            (mailbox_collision.get("detail") or {}).get("code") == "resourceOwnershipConflict",
            "machine mailbox write must not move an existing resource",
        )

        owner_mutation_rejected = False
        try:
            with store.request_unit_of_work(
                correlation_id=f"resource-owner-trigger-{suffix}",
                command_id="verifyImmutableResourceOwner",
            ) as unit_of_work:
                with unit_of_work.connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE archive_items SET user_id = %s WHERE id = %s",
                        (attacker_id, archive_id),
                    )
        except Exception:
            owner_mutation_rejected = True
        require(owner_mutation_rejected, "database trigger must reject direct owner mutation")

        with store.request_unit_of_work(
            correlation_id=f"resource-authority-inspect-{suffix}",
            command_id="inspectResourceAuthority",
        ):
            authority = store._fetchone(
                """
                SELECT user_id, vault_id, owner_subject_id, row_version, authority_state, payload
                FROM archive_items WHERE id = %s
                """,
                (archive_id,),
            )
        require(authority["user_id"] == owner_id, "database owner must remain unchanged")
        require(authority["vault_id"] == owner_id, "canonical vault must match database owner")
        require(authority["owner_subject_id"] == owner_id, "canonical subject must match database owner")
        require(int(authority["row_version"]) >= 2, "same-owner update must advance resource version")
        require(authority["authority_state"] == "active", "new resource authority must be active")
        require(
            authority["payload"].get("title") == "owner archive updated",
            "database payload must survive cross-owner collision unchanged",
        )

        owner_memories, _ = request_json(
            "GET",
            f"/memories/{owner_id}",
            token=owner_token,
        )
        attacker_memories, _ = request_json(
            "GET",
            f"/memories/{attacker_id}",
            token=attacker_token,
        )
        require(
            any(item.get("id") == memory_id and item.get("title") == "owner memory" for item in owner_memories["memories"]),
            "owner resource must survive collision attempt",
        )
        require(
            all(item.get("id") != memory_id for item in attacker_memories["memories"]),
            "attacker vault must not receive the collided resource",
        )

        print(
            json.dumps(
                {
                    "archiveAnalysisCrossVaultDenied": True,
                    "archiveDeleteCrossVaultDenied": True,
                    "archiveGetCrossVaultDenied": True,
                    "archiveOwnerDerivedFromPrincipal": True,
                    "archiveOwnerForgeryDenied": True,
                    "archivePayloadPreservedAfterCollision": True,
                    "archiveQuarantineHiddenFromList": True,
                    "archiveSameOwnerUpdateAllowed": True,
                    "canonicalOwnerPersisted": True,
                    "databaseOwnerImmutable": True,
                    "mailboxOwnershipTransferDenied": True,
                    "nestedOwnerClaimDenied": True,
                    "ownerDerivedFromPrincipal": True,
                    "principalBoundRouteEnforcementVerified": True,
                    "globalOwnershipMode": enforcement["ownershipMode"],
                    "globalCrossAccountMode": enforcement["crossAccountMode"],
                    "resourceCollisionDenied": True,
                    "staleResourceVersionDenied": True,
                    "status": "passed",
                },
                sort_keys=True,
            )
        )
    finally:
        if user_ids:
            cleanup(store, user_ids)
        store.close_pool()


if __name__ == "__main__":
    main()
