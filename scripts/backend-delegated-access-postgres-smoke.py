#!/usr/bin/env python3
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from app.services.auth_sessions import AuthSessionService
from app.services.postgres_store import PostgresStore


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").strip().rstrip("/")
MACHINE_TOKEN = os.environ.get(
    "BACKEND_API_TOKEN",
    os.environ.get("DREAMJOURNEY_BACKEND_API_TOKEN", ""),
).strip()
IN_PROCESS_FULL_CONTRACT = os.environ.get(
    "DELEGATED_ACCESS_SMOKE_IN_PROCESS",
    "1",
).strip().lower() in {"1", "true", "yes", "on"}
_IN_PROCESS_CLIENT = None


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def http_json(method, path, *, token=None, payload=None, query=None):
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")

    if _IN_PROCESS_CLIENT is not None:
        response = _IN_PROCESS_CLIENT.request(
            method,
            path,
            headers=headers,
            json=payload,
            params=query,
        )
        status = response.status_code
        response_headers = {
            key.lower(): value for key, value in response.headers.items()
        }
        raw_body = response.text
    else:
        url = f"{BASE_URL}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status = response.status
                response_headers = {
                    key.lower(): value for key, value in response.headers.items()
                }
                raw_body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            status = error.code
            response_headers = {
                key.lower(): value for key, value in error.headers.items()
            }
            raw_body = error.read().decode("utf-8", errors="replace")
        except (TimeoutError, urllib.error.URLError) as error:
            raise AssertionError(
                f"{method} {path} transport failed ({type(error).__name__})"
            ) from None

    try:
        response_body = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        raise AssertionError(f"{method} {path} returned a non-JSON response") from None
    return status, response_body, response_headers


def request_json(
    method,
    path,
    *,
    token=None,
    payload=None,
    query=None,
    expected_status=200,
):
    status, response_body, response_headers = http_json(
        method,
        path,
        token=token,
        payload=payload,
        query=query,
    )
    require(
        status == expected_status,
        f"{method} {path} expected {expected_status}, got {status}",
    )
    return response_body, response_headers


def require_detail_code(response_body, expected_code, message):
    detail = response_body.get("detail") if isinstance(response_body, dict) else None
    actual_code = detail.get("code") if isinstance(detail, dict) else None
    require(actual_code == expected_code, message)


def care_snapshot(summary):
    now = datetime.now(timezone.utc)
    return {
        "generatedAt": now.isoformat(),
        "windowStart": (now - timedelta(days=7)).isoformat(),
        "windowEnd": now.isoformat(),
        "windowDayCount": 7,
        "dataCoverageSummary": "delegated access smoke fixture",
        "totalTurns": 3,
        "userTurnCount": 2,
        "characterCount": 24,
        "uniqueTokenCount": 12,
        "lexicalDiversity": 0.5,
        "negativeEmotionMentions": 0,
        "sleepMentions": 0,
        "bodyDiscomfortMentions": 0,
        "repetitionRatio": 0.0,
        "averageWordsPerMinute": 80.0,
        "slowSpeechTurnCount": 0,
        "longPauseTurnCount": 0,
        "emotionVolatilityScore": 0.1,
        "riskLevel": "stable",
        "summary": summary,
        "suggestions": [],
        "weeklyHighlights": [],
        "riskSignalDescriptions": [],
        "dailyTrend": [],
        "trendSummary": "stable",
    }


def time_letter_payload(owner_id, family_member_id, letter_id, title):
    return {
        "userId": owner_id,
        "id": letter_id,
        "kind": "timeLetter",
        "title": title,
        "note": "delegated access smoke fixture",
        "openAt": "2000-01-01T00:00:00Z",
        "recipients": [
            {
                "id": family_member_id,
                "name": "smoke member",
                "type": "family",
            }
        ],
        "privacyMetadata": {"scope": "generationAllowed"},
    }


def grant_payload(
    owner_id,
    relationship_id,
    member_id,
    *,
    purpose,
    resource_type,
    expires_at,
    resource_id=None,
):
    payload = {
        "userId": owner_id,
        "relationshipId": relationship_id,
        "granteeSubjectId": member_id,
        "purpose": purpose,
        "resourceType": resource_type,
        "operations": ["read"],
        "expiresAt": expires_at,
    }
    if resource_id is not None:
        payload["resourceId"] = resource_id
    return payload


def database_now(store):
    row = store._fetchone("SELECT NOW() AS current_time")
    require(row is not None, "database clock query failed")
    value = row["current_time"]
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def relationship_state(store, owner_id, relationship_id):
    relationship = store.get_family_relationship(owner_id, relationship_id)
    require(relationship is not None, "family relationship was not persisted")
    return relationship


def require_grant_events(store, grant_id, relationship_id, expected):
    events = store.list_grant_events(grant_id)
    mutation_events = [
        event for event in events if event["eventType"] in {"granted", "revoked"}
    ]
    actual = [
        (event["eventType"], event["grantVersion"], event["reason"])
        for event in mutation_events
    ]
    require(actual == expected, "grant event sequence does not match the contract")
    require(
        all(event["relationshipId"] == relationship_id for event in events),
        "grant event relationship binding mismatch",
    )
    return mutation_events


def wait_until_care_denied(owner_id, family_member_id, member_token, timeout_seconds=20):
    path = f"/care/snapshots/latest/{owner_id}"
    deadline = time.monotonic() + timeout_seconds
    while True:
        status, response_body, response_headers = http_json(
            "GET",
            path,
            token=member_token,
            query={"viewerFamilyMemberID": family_member_id},
        )
        if status == 403:
            return response_body, response_headers
        require(status == 200, f"expired care read expected 200 or 403, got {status}")
        if time.monotonic() >= deadline:
            raise AssertionError("care grant did not expire within the smoke deadline")
        time.sleep(0.5)


def cleanup(store, user_ids, owner_ids):
    users = list(user_ids)
    owners = list(owner_ids)
    relationships = store._fetchall(
        "SELECT id FROM family_relationships WHERE vault_id = ANY(%s)",
        (owners,),
    )
    relationship_ids = [str(row["id"]) for row in relationships]

    with store.request_unit_of_work(
        correlation_id="delegated-access-smoke-cleanup",
        command_id="cleanupDelegatedAccessSmoke",
    ) as unit_of_work:
        with unit_of_work.connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
            for subject_id in users:
                cursor.execute(
                    "SELECT * FROM purge_delegated_access_for_subject(%s)",
                    (subject_id,),
                )
            for table in (
                "care_snapshots",
                "archive_items",
                "family_members",
            ):
                cursor.execute(
                    f"DELETE FROM {table} WHERE user_id = ANY(%s)",
                    (users,),
                )
            cursor.execute(
                "DELETE FROM session_events WHERE user_id = ANY(%s)",
                (users,),
            )
            cursor.execute(
                "DELETE FROM auth_sessions WHERE user_id = ANY(%s)",
                (users,),
            )
            cursor.execute(
                "DELETE FROM token_families WHERE user_id = ANY(%s)",
                (users,),
            )
            cursor.execute("DELETE FROM users WHERE id = ANY(%s)", (users,))

    remaining = store._fetchone(
        """
        SELECT
            (SELECT COUNT(*) FROM users WHERE id = ANY(%s)) AS users,
            (SELECT COUNT(*) FROM auth_sessions WHERE user_id = ANY(%s)) AS sessions,
            (SELECT COUNT(*) FROM family_members WHERE user_id = ANY(%s)) AS members,
            (SELECT COUNT(*) FROM family_relationships WHERE vault_id = ANY(%s)) AS relationships,
            (SELECT COUNT(*) FROM access_grants WHERE vault_id = ANY(%s)) AS grants,
            (SELECT COUNT(*) FROM grant_events WHERE relationship_id = ANY(%s)) AS events
        """,
        (users, users, users, owners, owners, relationship_ids),
    )
    require(remaining is not None, "cleanup verification query failed")
    require(
        all(int(value or 0) == 0 for value in remaining.values()),
        "delegated access smoke cleanup left persisted rows",
    )


def main():
    global _IN_PROCESS_CLIENT
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(MACHINE_TOKEN, "BACKEND_API_TOKEN is required")
    dsn = os.environ.get("DATABASE_URL", "").strip()
    require(dsn, "DATABASE_URL is required")

    suffix = secrets.token_hex(8)
    phone_suffix = f"{secrets.randbelow(100_000_000):08d}"
    owner_phone = f"191{phone_suffix}"
    member_phone = f"192{phone_suffix}"
    user_ids = set()
    owner_ids = set()
    result = None

    store = PostgresStore(
        dsn=dsn,
        pool_min_size=1,
        pool_max_size=3,
        pool_timeout_seconds=2.0,
    )
    store.open_pool(wait=True)
    try:
        owner = store.upsert_user(
            phone=owner_phone,
            nickname="delegated access owner smoke",
        )
        owner_id = str(owner["id"])
        user_ids.add(owner_id)
        owner_ids.add(owner_id)

        member = store.upsert_user(
            phone=member_phone,
            nickname="delegated access member smoke",
        )
        member_id = str(member["id"])
        user_ids.add(member_id)

        auth_service = AuthSessionService(
            store,
            access_ttl_seconds=300,
            refresh_ttl_seconds=900,
        )
        owner_token = auth_service.issue(owner_id)["accessToken"]
        member_token = auth_service.issue(member_id)["accessToken"]

        default_off, _ = request_json(
            "GET",
            f"/family/access-grants/{owner_id}",
            token=owner_token,
            expected_status=403,
        )
        require_detail_code(
            default_off,
            "delegatedAccessContractDefaultOff",
            "deployed delegated access contract API must remain default-off",
        )

        require(
            IN_PROCESS_FULL_CONTRACT,
            "DELEGATED_ACCESS_SMOKE_IN_PROCESS must be enabled for the full Postgres contract",
        )
        import app.main as main_module
        from fastapi.testclient import TestClient

        main_module.DELEGATED_ACCESS_CONTRACT_API_ENABLED = True
        _IN_PROCESS_CLIENT = TestClient(main_module.app)

        _, machine_headers = request_json(
            "GET",
            f"/family/access-grants/{owner_id}",
            token=MACHINE_TOKEN,
            expected_status=403,
        )
        require(
            machine_headers.get("x-dreamjourney-route-auth-reason")
            == "userPrincipalRequired",
            "machine principal must not access delegated user routes",
        )

        invited, _ = request_json(
            "POST",
            "/family/invite",
            token=owner_token,
            payload={
                "userId": owner_id,
                "name": "smoke member",
                "relation": "family",
                "phone": member_phone,
            },
        )
        invited_member = invited.get("member") or {}
        family_member_id = str(invited_member.get("id") or "")
        invitation_code = str(invited_member.get("invitationCode") or "")
        require(family_member_id, "family invitation did not return a member id")
        require(invitation_code, "family invitation did not return an invitation code")
        require(
            invited_member.get("relationshipStatus") == "pending",
            "new phone invitation must create a pending relationship",
        )
        require(
            invited_member.get("accessGrants") == [],
            "pending invitation must not create an access grant",
        )

        accept_path = (
            "/family/invitations/"
            f"{urllib.parse.quote(invitation_code, safe='')}/accept"
        )
        accepted, _ = request_json(
            "POST",
            accept_path,
            token=member_token,
            payload={"phone": member_phone},
        )
        accepted_member = accepted.get("member") or {}
        relationship_id = str(accepted_member.get("relationshipId") or "")
        require(relationship_id, "accepted invitation did not return relationship id")
        require(
            accepted_member.get("relationshipStatus") == "accepted",
            "phone invitation must become accepted",
        )
        require(
            accepted_member.get("memberSubjectId") == member_id,
            "accepted invitation must bind the authenticated member subject",
        )
        require(
            accepted_member.get("accessGrants") == [],
            "accepted relationship must not imply an access grant",
        )
        require(
            int(accepted_member.get("grantEpoch") or 0) == 0,
            "accepted relationship must begin at grant epoch zero",
        )
        accepted_epoch = int(accepted_member.get("relationshipEpoch") or 0)
        require(accepted_epoch >= 1, "accepted relationship epoch is missing")

        duplicate_accept, _ = request_json(
            "POST",
            accept_path,
            token=member_token,
            payload={"phone": member_phone},
        )
        duplicate_member = duplicate_accept.get("member") or {}
        require(
            duplicate_member.get("relationshipId") == relationship_id
            and int(duplicate_member.get("relationshipEpoch") or 0)
            == accepted_epoch
            and duplicate_member.get("accessGrants") == [],
            "duplicate invitation acceptance must be idempotent",
        )

        no_grant_write, _ = request_json(
            "POST",
            "/care/snapshots",
            token=owner_token,
            payload={
                "userId": owner_id,
                "viewerFamilyMemberID": family_member_id,
                "snapshot": care_snapshot("care before grant"),
            },
            expected_status=403,
        )
        require(
            no_grant_write.get("detail") == "active care grant is required",
            "care write must require an independent active grant",
        )
        request_json(
            "GET",
            f"/care/snapshots/latest/{owner_id}",
            token=member_token,
            query={"viewerFamilyMemberID": family_member_id},
            expected_status=403,
        )

        grant_clock = database_now(store)
        long_expiry = (grant_clock + timedelta(minutes=30)).isoformat()
        conflicting_expiry = (grant_clock + timedelta(minutes=31)).isoformat()
        subject_mismatch, _ = request_json(
            "POST",
            "/family/access-grants",
            token=owner_token,
            payload=grant_payload(
                owner_id,
                relationship_id,
                owner_id,
                purpose="care.snapshot",
                resource_type="careSnapshot",
                expires_at=long_expiry,
            ),
            expected_status=403,
        )
        require_detail_code(
            subject_mismatch,
            "relationshipSubjectMismatch",
            "grant grantee must match the accepted member subject",
        )
        scope_mismatch, _ = request_json(
            "POST",
            "/family/access-grants",
            token=owner_token,
            payload=grant_payload(
                owner_id,
                relationship_id,
                member_id,
                purpose="care.snapshot",
                resource_type="timeLetter",
                expires_at=long_expiry,
                resource_id=f"scope-mismatch-{suffix}",
            ),
            expected_status=400,
        )
        require_detail_code(
            scope_mismatch,
            "accessGrantCommandInvalid",
            "grant purpose/resource mismatch must be rejected",
        )

        care_granted, _ = request_json(
            "POST",
            "/family/access-grants",
            token=owner_token,
            payload=grant_payload(
                owner_id,
                relationship_id,
                member_id,
                purpose="care.snapshot",
                resource_type="careSnapshot",
                expires_at=long_expiry,
            ),
        )
        care_grant = care_granted.get("grant") or {}
        require(care_grant.get("status") == "active", "care grant was not activated")

        duplicate_granted, _ = request_json(
            "POST",
            "/family/access-grants",
            token=owner_token,
            payload=grant_payload(
                owner_id,
                relationship_id,
                member_id,
                purpose="care.snapshot",
                resource_type="careSnapshot",
                expires_at=long_expiry,
            ),
        )
        duplicate_grant = duplicate_granted.get("grant") or {}
        require(
            duplicate_grant.get("id") == care_grant.get("id"),
            "duplicate active grant must return the existing grant",
        )
        conflicting_grant, _ = request_json(
            "POST",
            "/family/access-grants",
            token=owner_token,
            payload=grant_payload(
                owner_id,
                relationship_id,
                member_id,
                purpose="care.snapshot",
                resource_type="careSnapshot",
                expires_at=conflicting_expiry,
            ),
            expected_status=409,
        )
        require_detail_code(
            conflicting_grant,
            "grantScopeAlreadyActive",
            "same active scope with different terms must conflict",
        )
        state_after_duplicate = relationship_state(store, owner_id, relationship_id)
        require(
            state_after_duplicate["grantEpoch"] == 1,
            "duplicate active grant must not bump grant epoch",
        )
        require_grant_events(
            store,
            str(care_grant["id"]),
            relationship_id,
            [("granted", 1, "ownerGranted")],
        )

        saved, _ = request_json(
            "POST",
            "/care/snapshots",
            token=owner_token,
            payload={
                "userId": owner_id,
                "viewerFamilyMemberID": family_member_id,
                "snapshot": care_snapshot("care after explicit grant"),
            },
        )
        require(saved.get("status") == "saved", "granted care snapshot was not saved")
        allowed_care, _ = request_json(
            "GET",
            f"/care/snapshots/latest/{owner_id}",
            token=member_token,
            query={"viewerFamilyMemberID": family_member_id},
        )
        require(
            ((allowed_care.get("item") or {}).get("snapshot") or {}).get("summary")
            == "care after explicit grant",
            "explicit care grant did not authorize the member read",
        )
        initial_care_receipts = store.list_access_receipts(
            owner_subject_id=owner_id,
            grant_id=str(care_grant["id"]),
        )
        require(
            len(initial_care_receipts) == 1
            and initial_care_receipts[0]["granteeSubjectId"] == member_id,
            "cross-owner care read must persist an active grant receipt",
        )

        paused, _ = request_json(
            "POST",
            f"/family/relationships/{owner_id}/{relationship_id}/lifecycle",
            token=owner_token,
            payload={"operation": "pause", "expectedEpoch": accepted_epoch},
        )
        paused_relationship = paused.get("relationship") or {}
        paused_epoch = int(paused_relationship.get("relationshipEpoch") or 0)
        require(
            paused_relationship.get("status") == "paused"
            and paused_epoch == accepted_epoch + 1,
            "relationship pause must advance the relationship epoch",
        )
        request_json(
            "GET",
            f"/care/snapshots/latest/{owner_id}",
            token=member_token,
            query={"viewerFamilyMemberID": family_member_id},
            expected_status=403,
        )
        stale_relationship, _ = request_json(
            "POST",
            f"/family/relationships/{owner_id}/{relationship_id}/lifecycle",
            token=owner_token,
            payload={"operation": "resume", "expectedEpoch": accepted_epoch},
            expected_status=409,
        )
        require_detail_code(
            stale_relationship,
            "relationshipEpochMismatch",
            "stale relationship epoch must be rejected",
        )
        resumed, _ = request_json(
            "POST",
            f"/family/relationships/{owner_id}/{relationship_id}/lifecycle",
            token=owner_token,
            payload={"operation": "resume", "expectedEpoch": paused_epoch},
        )
        resumed_relationship = resumed.get("relationship") or {}
        resumed_epoch = int(resumed_relationship.get("relationshipEpoch") or 0)
        require(
            resumed_relationship.get("status") == "accepted"
            and resumed_epoch == paused_epoch + 1,
            "relationship resume must restore accepted status with a new epoch",
        )
        require(
            relationship_state(store, owner_id, relationship_id)["grantEpoch"] == 1,
            "pause/resume must not mutate grant epoch",
        )
        request_json(
            "GET",
            f"/care/snapshots/latest/{owner_id}",
            token=member_token,
            query={"viewerFamilyMemberID": family_member_id},
        )

        revoked, _ = request_json(
            "POST",
            f"/family/access-grants/{owner_id}/{care_grant['id']}/revoke",
            token=owner_token,
            payload={
                "expectedVersion": care_grant["rowVersion"],
                "reason": "ownerRequested",
            },
        )
        revoked_grant = revoked.get("grant") or {}
        require(
            revoked_grant.get("status") == "revoked"
            and int(revoked_grant.get("rowVersion") or 0) == 2,
            "grant revoke must produce row version two",
        )
        request_json(
            "GET",
            f"/care/snapshots/latest/{owner_id}",
            token=member_token,
            query={"viewerFamilyMemberID": family_member_id},
            expected_status=403,
        )
        stale_revoke, _ = request_json(
            "POST",
            f"/family/access-grants/{owner_id}/{care_grant['id']}/revoke",
            token=owner_token,
            payload={"expectedVersion": 1, "reason": "ownerRequested"},
            expected_status=409,
        )
        require_detail_code(
            stale_revoke,
            "grantVersionMismatch",
            "stale grant row version must be rejected",
        )
        duplicate_revoked, _ = request_json(
            "POST",
            f"/family/access-grants/{owner_id}/{care_grant['id']}/revoke",
            token=owner_token,
            payload={"expectedVersion": 2, "reason": "ownerRequested"},
        )
        require(
            (duplicate_revoked.get("grant") or {}).get("rowVersion") == 2,
            "duplicate revoke must return the existing revoked grant",
        )
        require(
            relationship_state(store, owner_id, relationship_id)["grantEpoch"] == 2,
            "duplicate revoke must not bump grant epoch",
        )
        care_events = require_grant_events(
            store,
            str(care_grant["id"]),
            relationship_id,
            [
                ("granted", 1, "ownerGranted"),
                ("revoked", 2, "ownerRequested"),
            ],
        )

        expiry_at = database_now(store) + timedelta(seconds=6)
        expiring_granted, _ = request_json(
            "POST",
            "/family/access-grants",
            token=owner_token,
            payload=grant_payload(
                owner_id,
                relationship_id,
                member_id,
                purpose="care.snapshot",
                resource_type="careSnapshot",
                expires_at=expiry_at.isoformat(),
            ),
        )
        expiring_grant = expiring_granted.get("grant") or {}
        request_json(
            "GET",
            f"/care/snapshots/latest/{owner_id}",
            token=member_token,
            query={"viewerFamilyMemberID": family_member_id},
        )
        expiry_epoch = relationship_state(store, owner_id, relationship_id)[
            "grantEpoch"
        ]
        require(expiry_epoch == 3, "expiring grant must bump grant epoch once")
        _, expiry_headers = wait_until_care_denied(
            owner_id,
            family_member_id,
            member_token,
        )
        require(
            expiry_headers.get("x-dreamjourney-authorization-reason")
            == "activeGrantRequired",
            "expired care grant must fail delegated authorization",
        )
        listed_grants, _ = request_json(
            "GET",
            f"/family/access-grants/{owner_id}",
            token=owner_token,
            query={"relationshipId": relationship_id},
        )
        listed_expiring = next(
            (
                grant
                for grant in listed_grants.get("grants") or []
                if grant.get("id") == expiring_grant.get("id")
            ),
            None,
        )
        require(
            listed_expiring is not None and listed_expiring.get("status") == "expired",
            "expired grant must be projected as expired",
        )
        require(
            relationship_state(store, owner_id, relationship_id)["grantEpoch"]
            == expiry_epoch,
            "passage of grant expiry must not fabricate a grant mutation",
        )
        require_grant_events(
            store,
            str(expiring_grant["id"]),
            relationship_id,
            [("granted", 1, "ownerGranted")],
        )

        renewed_granted, _ = request_json(
            "POST",
            "/family/access-grants",
            token=owner_token,
            payload=grant_payload(
                owner_id,
                relationship_id,
                member_id,
                purpose="care.snapshot",
                resource_type="careSnapshot",
                expires_at=long_expiry,
            ),
        )
        renewed_grant = renewed_granted.get("grant") or {}
        require(
            renewed_grant.get("status") == "active"
            and renewed_grant.get("id") != expiring_grant.get("id"),
            "expired care scope must be regranted with a new grant",
        )
        request_json(
            "GET",
            f"/care/snapshots/latest/{owner_id}",
            token=member_token,
            query={"viewerFamilyMemberID": family_member_id},
        )
        require(
            relationship_state(store, owner_id, relationship_id)["grantEpoch"] == 5,
            "expired-scope regrant must record revoke and grant epoch bumps",
        )
        expiring_events = require_grant_events(
            store,
            str(expiring_grant["id"]),
            relationship_id,
            [
                ("granted", 1, "ownerGranted"),
                ("revoked", 2, "expiredBeforeRegrant"),
            ],
        )
        renewed_events = require_grant_events(
            store,
            str(renewed_grant["id"]),
            relationship_id,
            [("granted", 1, "ownerGranted")],
        )

        first_letter_id = f"delegated-letter-a-{suffix}"
        second_letter_id = f"delegated-letter-b-{suffix}"
        for letter_id, title in (
            (first_letter_id, "delegated letter a"),
            (second_letter_id, "delegated letter b"),
        ):
            created_letter, _ = request_json(
                "POST",
                "/archive/items",
                token=owner_token,
                payload=time_letter_payload(
                    owner_id,
                    family_member_id,
                    letter_id,
                    title,
                ),
            )
            require(
                (created_letter.get("item") or {}).get("id") == letter_id,
                "time letter fixture was not persisted",
            )

        missing_resource, _ = request_json(
            "POST",
            "/family/access-grants",
            token=owner_token,
            payload=grant_payload(
                owner_id,
                relationship_id,
                member_id,
                purpose="timeLetter.read",
                resource_type="timeLetter",
                expires_at=long_expiry,
            ),
            expected_status=400,
        )
        require_detail_code(
            missing_resource,
            "accessGrantCommandInvalid",
            "timeLetter.read must require a resource id",
        )
        letter_granted, _ = request_json(
            "POST",
            "/family/access-grants",
            token=owner_token,
            payload=grant_payload(
                owner_id,
                relationship_id,
                member_id,
                purpose="timeLetter.read",
                resource_type="timeLetter",
                resource_id=first_letter_id,
                expires_at=long_expiry,
            ),
        )
        letter_grant = letter_granted.get("grant") or {}
        require(
            letter_grant.get("resourceId") == first_letter_id,
            "time letter grant lost its resource binding",
        )
        detail_now = datetime.now(timezone.utc).isoformat()
        allowed_letter, _ = request_json(
            "GET",
            f"/archive/time-letters/{owner_id}/{first_letter_id}/detail",
            token=member_token,
            query={"viewerUserId": member_id, "now": detail_now},
        )
        require(
            (allowed_letter.get("access") or {}).get("role") == "recipient",
            "resource-bound time letter grant did not allow its recipient",
        )
        _, denied_letter_headers = request_json(
            "GET",
            f"/archive/time-letters/{owner_id}/{second_letter_id}/detail",
            token=member_token,
            query={"viewerUserId": member_id, "now": detail_now},
            expected_status=403,
        )
        require(
            denied_letter_headers.get("x-dreamjourney-authorization-reason")
            == "activeGrantRequired",
            "timeLetter.read grant must not authorize another letter",
        )
        require(
            relationship_state(store, owner_id, relationship_id)["grantEpoch"] == 6,
            "time letter grant must advance grant epoch once",
        )
        letter_events = require_grant_events(
            store,
            str(letter_grant["id"]),
            relationship_id,
            [("granted", 1, "ownerGranted")],
        )

        relationship_revoked, _ = request_json(
            "POST",
            f"/family/relationships/{owner_id}/{relationship_id}/lifecycle",
            token=owner_token,
            payload={"operation": "revoke", "expectedEpoch": resumed_epoch},
        )
        final_relationship = relationship_revoked.get("relationship") or {}
        require(
            final_relationship.get("status") == "revoked"
            and int(final_relationship.get("relationshipEpoch") or 0)
            == resumed_epoch + 1,
            "relationship revoke must be terminal and advance its epoch",
        )
        request_json(
            "GET",
            f"/archive/time-letters/{owner_id}/{first_letter_id}/detail",
            token=member_token,
            query={"viewerUserId": member_id, "now": detail_now},
            expected_status=403,
        )
        persisted_relationship = relationship_state(
            store,
            owner_id,
            relationship_id,
        )
        require(
            persisted_relationship["grantEpoch"] == 7,
            "relationship revoke must atomically revoke grants and advance grant epoch",
        )

        renewed_events = require_grant_events(
            store,
            str(renewed_grant["id"]),
            relationship_id,
            [
                ("granted", 1, "ownerGranted"),
                ("revoked", 2, "relationshipRevoked"),
            ],
        )
        letter_events = require_grant_events(
            store,
            str(letter_grant["id"]),
            relationship_id,
            [
                ("granted", 1, "ownerGranted"),
                ("revoked", 2, "relationshipRevoked"),
            ],
        )

        access_receipts = store.list_access_receipts(
            owner_subject_id=owner_id,
        )
        require(
            len(access_receipts) >= 5,
            "every successful delegated read must persist an access receipt",
        )
        require(
            all(
                receipt["decision"] == "allow"
                and receipt["granteeSubjectId"] == member_id
                for receipt in access_receipts
            ),
            "delegated access receipts must bind the verified member subject",
        )

        all_events = care_events + expiring_events + renewed_events + letter_events
        require(len(all_events) == 8, "unexpected persisted grant mutation event count")
        require(
            len({event["id"] for event in all_events}) == 8,
            "grant events must have unique append-only identities",
        )
        require(
            all(event["actorSubjectId"] == owner_id for event in all_events),
            "grant event actor must be the owner subject",
        )

        result = {
            "acceptedRelationshipNoImplicitGrant": True,
            "careExplicitGrantAllowed": True,
            "careGrantRequired": True,
            "duplicateAcceptIdempotent": True,
            "duplicateGrantIdempotent": True,
            "duplicateRevokeIdempotent": True,
            "expiredScopeRegranted": True,
            "expiryRevalidated": True,
            "grantEpoch": persisted_relationship["grantEpoch"],
            "grantEventCount": len(all_events),
            "grantEventsVerified": True,
            "grantReceiptCount": len(access_receipts),
            "grantReceiptsVerified": True,
            "machineUserRouteDenied": True,
            "mismatchContractsVerified": True,
            "pauseResumeRevalidated": True,
            "relationshipRevokeRevalidated": True,
            "serverDefaultOffVerified": True,
            "schemaVersion": 1,
            "status": "passed",
            "timeLetterResourceSpecific": True,
        }
    finally:
        if _IN_PROCESS_CLIENT is not None:
            _IN_PROCESS_CLIENT.close()
            _IN_PROCESS_CLIENT = None
        try:
            if user_ids:
                cleanup(store, user_ids, owner_ids)
        finally:
            store.close_pool()

    require(result is not None, "delegated access smoke did not produce a result")
    result["cleanupCompleted"] = True
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
