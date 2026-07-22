#!/usr/bin/env python3
"""Verify formal Owner Truth natural input in a deployed, isolated Postgres DB.

The deployed API is checked for readiness and its public ``echoTextInput``
policy snapshot is used as the formal client capture. Route writes then run
against a newly migrated temporary database inside the deployed API container.
No production business data or interview content is retained.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app import main as main_module
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.auth_sessions import AuthSessionService
from app.services.postgres_store import PostgresStore


BASE_URL = os.environ.get("BACKEND_BASE_URL", "").strip().rstrip("/")
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()
SMOKE_CLIENT_BUILD = 9010


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def database_dsn(base_dsn: str, database_name: str) -> str:
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
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(database_name)
                )
            )


def deployed_json(path: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        headers={
            "Accept": "application/json",
            "X-DreamJourney-Client-Build": str(SMOKE_CLIENT_BUILD),
            "X-DreamJourney-Runtime-Contract-Version": "2",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as error:
        raise AssertionError(f"GET {path} returned non-JSON") from error
    require(status == 200, f"GET {path} expected 200, got {status}")
    require(isinstance(payload, dict), f"GET {path} must return an object")
    return payload


def assert_deployed_container_context() -> None:
    require(BASE_URL, "BACKEND_BASE_URL is required")
    require(
        os.environ.get("DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE") == "1",
        "DREAMJOURNEY_DEPLOYED_CONTAINER_SMOKE=1 is required",
    )
    require(
        any(path.exists() for path in (Path("/.dockerenv"), Path("/run/.containerenv"))),
        "natural-input deployed smoke must run inside the deployed API container",
    )


def assert_deployed_readiness_and_policy() -> dict[str, object]:
    readiness = deployed_json("/ready")
    require(readiness.get("status") == "ready", "deployed API is not ready")
    components = {
        str(item.get("component") or ""): str(item.get("status") or "")
        for item in readiness.get("components") or []
        if isinstance(item, dict)
    }
    require(components.get("database") == "ready", "deployed database is not ready")
    require(components.get("schema") == "ready", "deployed schema is not ready")

    query = urllib.parse.urlencode(
        {
            "audience": "owner",
            "cohort": "closedPilotAdultSelf",
            "clientBuild": str(SMOKE_CLIENT_BUILD),
            "feature": "echoTextInput",
        }
    )
    snapshot = deployed_json(f"/v2/release-policy?{query}")
    features = snapshot.get("features") or []
    require(len(features) == 1 and isinstance(features[0], dict), "echoTextInput policy missing")
    decision = features[0]
    require(decision.get("feature") == "echoTextInput", "unexpected policy feature")
    require(decision.get("enabled") is True, "echoTextInput must be policy enabled")
    require(
        decision.get("releaseVisible") is True,
        "echoTextInput must be visible to the closed-pilot owner cohort",
    )
    require(
        str(snapshot.get("policyVersion") or ""),
        "deployed policy version is required",
    )
    require(
        isinstance(snapshot.get("policyRevision"), int),
        "deployed policy revision is required",
    )

    confirmation_query = urllib.parse.urlencode(
        {
            "audience": "owner",
            "cohort": "closedPilotAdultSelf",
            "clientBuild": str(SMOKE_CLIENT_BUILD),
            "feature": "ownerTruthCandidateReview",
        }
    )
    confirmation_snapshot = deployed_json(
        f"/v2/release-policy?{confirmation_query}"
    )
    confirmation_features = confirmation_snapshot.get("features") or []
    require(
        len(confirmation_features) == 1 and isinstance(confirmation_features[0], dict),
        "ownerTruthCandidateReview policy missing",
    )
    confirmation_decision = confirmation_features[0]
    require(
        confirmation_decision.get("feature") == "ownerTruthCandidateReview",
        "unexpected candidate confirmation feature",
    )
    require(
        confirmation_decision.get("enabled") is False
        and confirmation_decision.get("releaseVisible") is False,
        "candidate confirmation must remain default closed",
    )
    return snapshot


def app_request(
    method: str,
    path: str,
    *,
    token: str,
    payload: dict[str, object] | None = None,
    policy_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object], dict[str, str]]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "X-DreamJourney-Client-Build": str(SMOKE_CLIENT_BUILD),
        "X-DreamJourney-Runtime-Contract-Version": "2",
    }
    headers.update(policy_headers or {})
    # Do not use a TestClient context manager: its shutdown hook would close
    # the temporary store that this smoke deliberately shares across requests.
    client = TestClient(main_module.app, raise_server_exceptions=True)
    request_arguments: dict[str, object] = {"headers": headers}
    if payload is not None:
        request_arguments["json"] = payload
    response = client.request(method, path, **request_arguments)
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {}
    return (
        response.status_code,
        body if isinstance(body, dict) else {},
        {key.lower(): value for key, value in response.headers.items()},
    )


def detail_code(body: dict[str, object]) -> str:
    detail = body.get("detail")
    return str(detail.get("code") or "") if isinstance(detail, dict) else ""


def issue_access(store: PostgresStore, user_id: str) -> dict[str, object]:
    return AuthSessionService(
        store,
        access_ttl_seconds=300,
        refresh_ttl_seconds=900,
    ).issue(user_id)


def exercise_formal_natural_input(
    dsn: str,
    policy_snapshot: dict[str, object],
) -> dict[str, bool]:
    store = PostgresStore(
        dsn=dsn,
        pool_min_size=1,
        pool_max_size=2,
        pool_timeout_seconds=4.0,
    )
    store.open_pool(wait=True)
    previous_store = main_module.store
    main_module.store = store
    try:
        suffix = secrets.token_hex(8)
        user = store.upsert_user(
            phone=f"196{secrets.randbelow(100_000_000):08d}",
            nickname=f"natural input smoke {suffix}",
        )
        auth = issue_access(store, str(user["id"]))
        access_token = str(auth["accessToken"])
        account_generation = hashlib.sha256(
            str(auth["sessionId"]).encode("utf-8")
        ).hexdigest()[:24]
        vault_id = f"vault-natural-input-{suffix}"
        thread_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        start_payload = {
            "commandId": str(uuid.uuid4()),
            "threadId": thread_id,
            "sessionId": session_id,
        }
        start_path = f"/v2/vaults/{vault_id}/interview-sessions"

        denied_status, denied_body, _ = app_request(
            "POST",
            start_path,
            token=access_token,
            payload=start_payload,
        )
        require(denied_status == 403, "formal route must reject missing policy capture")
        require(
            detail_code(denied_body) == "release_policy_denied",
            "missing capture must return release_policy_denied",
        )
        denied_detail = denied_body.get("detail")
        require(
            isinstance(denied_detail, dict)
            and denied_detail.get("reason") == "missingCapturedPolicy",
            "missing capture must expose only the expected policy reason",
        )

        confirmation_batch_id = str(uuid.uuid4())
        confirmation_path = (
            f"/v2/vaults/{vault_id}/interview-review-batches/"
            f"{confirmation_batch_id}/confirmation"
        )
        confirmation_status, confirmation_body, _ = app_request(
            "GET",
            confirmation_path,
            token=access_token,
        )
        require(
            confirmation_status == 403,
            "candidate confirmation must reject a missing dedicated policy capture",
        )
        confirmation_detail = confirmation_body.get("detail")
        require(
            isinstance(confirmation_detail, dict)
            and confirmation_detail.get("code") == "release_policy_denied"
            and confirmation_detail.get("feature") == "ownerTruthCandidateReview",
            "candidate confirmation must remain behind its own default-closed feature",
        )

        confirmation_action_status, confirmation_action_body, _ = app_request(
            "POST",
            f"{confirmation_path}/batch-accept",
            token=access_token,
            payload={
                "commandId": f"smoke-confirmation-batch-accept-{suffix}",
                "selections": [],
            },
        )
        require(
            confirmation_action_status == 403,
            "candidate confirmation action must reject a missing dedicated policy capture",
        )
        confirmation_action_detail = confirmation_action_body.get("detail")
        require(
            isinstance(confirmation_action_detail, dict)
            and confirmation_action_detail.get("code") == "release_policy_denied"
            and confirmation_action_detail.get("feature") == "ownerTruthCandidateReview",
            "candidate confirmation action must remain behind its own default-closed feature",
        )

        policy_headers = {
            "X-DreamJourney-Feature": "echoTextInput",
            "X-DreamJourney-Feature-Decision-Id": f"smoke-natural-input-{suffix}",
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": str(policy_snapshot["policyVersion"]),
            "X-DreamJourney-Policy-Revision": str(policy_snapshot["policyRevision"]),
            "X-DreamJourney-Account-Generation": account_generation,
            "X-DreamJourney-Policy-Audience": "owner",
            "X-DreamJourney-Policy-Cohort": "closedPilotAdultSelf",
        }
        start_status, start_body, start_headers = app_request(
            "POST",
            start_path,
            token=access_token,
            payload=start_payload,
            policy_headers=policy_headers,
        )
        require(start_status == 201, "matching policy capture must start a session")
        require(start_headers.get("cache-control") == "no-store", "start receipt must not cache")
        start_receipt = start_body.get("receipt")
        require(
            isinstance(start_receipt, dict) and start_receipt.get("state") == "active",
            "start must return a content-free active receipt",
        )

        crisis_text = "我真的撑不住了。"
        crisis_status, crisis_body, crisis_headers = app_request(
            "POST",
            f"{start_path}/{session_id}/messages",
            token=access_token,
            payload={
                "commandId": str(uuid.uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid.uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": crisis_text,
            },
            policy_headers=policy_headers,
        )
        require(
            crisis_status == 409,
            "crisis narrative must be interrupted before interview persistence",
        )
        require(
            crisis_headers.get("cache-control") == "no-store",
            "crisis safety override must not cache",
        )
        require(
            crisis_body.get("schemaVersion")
            == "owner-truth-interview-safety-override-v1",
            "crisis override schema must remain typed",
        )
        require(
            crisis_body.get("status") == "safetyOverride"
            and crisis_body.get("persisted") is False
            and crisis_body.get("retryable") is False,
            "crisis override must remain a non-persisted terminal result",
        )
        crisis_decision = crisis_body.get("safetyDecision")
        require(
            isinstance(crisis_decision, dict)
            and crisis_decision.get("riskClass") == "highDistress"
            and crisis_decision.get("action") == "respondWithNeutralSafetyText",
            "crisis override must carry the existing neutral safety decision",
        )
        require(
            crisis_text not in json.dumps(crisis_body, ensure_ascii=False, sort_keys=True),
            "crisis override must not echo raw narrative text",
        )

        pre_append_state_status, pre_append_state_body, _ = app_request(
            "GET",
            f"{start_path}/{session_id}/state",
            token=access_token,
            policy_headers=policy_headers,
        )
        pre_append_session = pre_append_state_body.get("session")
        require(
            pre_append_state_status == 200
            and isinstance(pre_append_session, dict)
            and pre_append_session.get("ownerTurnCount") == 0
            and pre_append_session.get("threadVersion") == 1
            and pre_append_session.get("rowVersion") == 1,
            "crisis override must leave the interview session unchanged",
        )

        append_status, append_body, append_headers = app_request(
            "POST",
            f"{start_path}/{session_id}/messages",
            token=access_token,
            payload={
                "commandId": str(uuid.uuid4()),
                "threadId": thread_id,
                "messageId": str(uuid.uuid4()),
                "expectedThreadVersion": 1,
                "expectedSessionVersion": 1,
                "text": "仅用于隔离 smoke 的自然输入文本。",
            },
            policy_headers=policy_headers,
        )
        require(append_status == 201, "matching policy capture must append a narrative")
        require(append_headers.get("cache-control") == "no-store", "append receipt must not cache")
        append_receipt = append_body.get("receipt")
        require(
            isinstance(append_receipt, dict) and append_receipt.get("messageSequence") == 1,
            "append must return the first content-free message receipt",
        )

        state_status, state_body, state_headers = app_request(
            "GET",
            f"{start_path}/{session_id}/state",
            token=access_token,
            policy_headers=policy_headers,
        )
        require(state_status == 200, "matching policy capture must read session state")
        require(state_headers.get("cache-control") == "no-store", "state must not cache")
        serialized = json.dumps(state_body, ensure_ascii=False, sort_keys=True)
        require("仅用于隔离 smoke" not in serialized, "state must not echo narrative content")

        presentation_status, presentation_body, presentation_headers = app_request(
            "GET",
            f"{start_path}/{session_id}/presentation",
            token=access_token,
            policy_headers=policy_headers,
        )
        require(
            presentation_status == 200,
            "matching policy capture must read product continuation guidance",
        )
        require(
            presentation_headers.get("cache-control") == "no-store",
            "presentation must not cache",
        )
        require(
            presentation_body == {
                "schemaVersion": "owner-truth-interview-session-presentation-v1",
                "vaultId": vault_id,
                "presentation": {
                    "state": "narrativeRecorded",
                    "canContinue": True,
                    "canContinueLater": True,
                },
            },
            "presentation must expose only bounded continuation guidance",
        )
        presentation_serialized = json.dumps(
            presentation_body,
            ensure_ascii=False,
            sort_keys=True,
        )
        for forbidden in (
            "仅用于隔离 smoke",
            "threadId",
            "sessionId",
            "candidate",
            "memory",
            "fatigue",
            "ownerTurnCount",
            "pendingReviewBatchId",
        ):
            require(
                forbidden not in presentation_serialized,
                "presentation must remain content and internals free",
            )

        boundary_path = f"{start_path}/{session_id}/boundary"
        skip_once_payload = {
            "commandId": str(uuid.uuid4()),
            "threadId": thread_id,
            "expectedSessionVersion": 2,
            "boundary": "skipOnce",
        }
        skip_once_status, skip_once_body, _ = app_request(
            "POST",
            boundary_path,
            token=access_token,
            payload=skip_once_payload,
            policy_headers=policy_headers,
        )
        skip_once_receipt = skip_once_body.get("receipt")
        require(skip_once_status == 201, "matching policy capture must persist skipOnce")
        require(
            isinstance(skip_once_receipt, dict)
            and skip_once_receipt.get("state") == "active"
            and skip_once_receipt.get("boundary") == "skipOnce"
            and skip_once_receipt.get("sessionVersion") == 3,
            "skipOnce must remain active while the current opportunity awaits owner input",
        )

        skip_once_append_payload = {
            "commandId": str(uuid.uuid4()),
            "threadId": thread_id,
            "messageId": str(uuid.uuid4()),
            "expectedThreadVersion": 2,
            "expectedSessionVersion": 3,
            "text": "这段输入完成后，本轮跳过应只影响此前的一次机会。",
        }
        skip_once_append_status, skip_once_append_body, _ = app_request(
            "POST",
            f"{start_path}/{session_id}/messages",
            token=access_token,
            payload=skip_once_append_payload,
            policy_headers=policy_headers,
        )
        skip_once_append_receipt = skip_once_append_body.get("receipt")
        require(
            skip_once_append_status == 201,
            "next owner narrative must be accepted after skipOnce",
        )
        require(
            isinstance(skip_once_append_receipt, dict)
            and skip_once_append_receipt.get("state") == "active"
            and skip_once_append_receipt.get("boundary") == "open"
            and skip_once_append_receipt.get("sessionVersion") == 4,
            "next owner narrative must consume skipOnce without a public reopen command",
        )
        skip_once_replay_status, skip_once_replay_body, _ = app_request(
            "POST",
            f"{start_path}/{session_id}/messages",
            token=access_token,
            payload=skip_once_append_payload,
            policy_headers=policy_headers,
        )
        skip_once_replay_receipt = skip_once_replay_body.get("receipt")
        require(skip_once_replay_status == 200, "skipOnce owner append must deduplicate")
        require(
            isinstance(skip_once_replay_receipt, dict)
            and skip_once_replay_receipt.get("status") == "deduplicated"
            and skip_once_replay_receipt.get("boundary") == "open"
            and skip_once_replay_receipt.get("sessionVersion") == 4,
            "skipOnce owner append replay must preserve the consumed boundary",
        )

        boundary_payload = {
            "commandId": str(uuid.uuid4()),
            "threadId": thread_id,
            "expectedSessionVersion": 4,
            "boundary": "cooldown",
        }
        boundary_status, boundary_body, boundary_headers = app_request(
            "POST",
            boundary_path,
            token=access_token,
            payload=boundary_payload,
            policy_headers=policy_headers,
        )
        require(boundary_status == 201, "matching policy capture must persist cooldown")
        require(
            boundary_headers.get("cache-control") == "no-store",
            "boundary receipt must not cache",
        )
        boundary_receipt = boundary_body.get("receipt")
        require(
            isinstance(boundary_receipt, dict)
            and boundary_receipt == {
                "status": "created",
                "threadId": thread_id,
                "sessionId": session_id,
                "threadVersion": 3,
                "sessionVersion": 5,
                "state": "paused",
                "boundary": "cooldown",
            },
            "boundary must return only a value-minimized paused receipt",
        )
        boundary_serialized = json.dumps(boundary_body, ensure_ascii=False, sort_keys=True)
        require(
            "仅用于隔离 smoke" not in boundary_serialized,
            "boundary receipt must not echo narrative content",
        )

        replay_status, replay_body, _ = app_request(
            "POST",
            boundary_path,
            token=access_token,
            payload=boundary_payload,
            policy_headers=policy_headers,
        )
        replay_receipt = replay_body.get("receipt")
        require(replay_status == 200, "same boundary command must deduplicate")
        require(
            isinstance(replay_receipt, dict)
            and replay_receipt.get("status") == "deduplicated"
            and replay_receipt.get("sessionVersion") == 5,
            "deduplicated boundary must retain the committed session version",
        )

        paused_state_status, paused_state_body, _ = app_request(
            "GET",
            f"{start_path}/{session_id}/state",
            token=access_token,
            policy_headers=policy_headers,
        )
        paused_session = paused_state_body.get("session")
        require(paused_state_status == 200, "paused state must remain readable")
        require(
            isinstance(paused_session, dict)
            and paused_session.get("state") == "paused"
            and paused_session.get("boundary") == "cooldown",
            "cooldown must persist a paused state",
        )

        paused_presentation_status, paused_presentation_body, _ = app_request(
            "GET",
            f"{start_path}/{session_id}/presentation",
            token=access_token,
            policy_headers=policy_headers,
        )
        require(paused_presentation_status == 200, "paused presentation must remain readable")
        require(
            paused_presentation_body == {
                "schemaVersion": "owner-truth-interview-session-presentation-v1",
                "vaultId": vault_id,
                "presentation": {
                    "state": "paused",
                    "canContinue": False,
                    "canContinueLater": True,
                },
            },
            "cooldown must project only bounded paused guidance",
        )

        restore_path = f"{start_path}/{session_id}/restore-do-not-ask"
        cooldown_restore_status, cooldown_restore_body, _ = app_request(
            "POST",
            restore_path,
            token=access_token,
            payload={
                "commandId": str(uuid.uuid4()),
                "threadId": thread_id,
                "expectedSessionVersion": 5,
                "confirmed": True,
            },
            policy_headers=policy_headers,
        )
        require(
            cooldown_restore_status == 409
            and detail_code(cooldown_restore_body) == "ownerTruthInterviewSessionConflict",
            "restore route must reject cooldown rather than reopening a different boundary",
        )

        do_not_ask_thread_id = str(uuid.uuid4())
        do_not_ask_session_id = str(uuid.uuid4())
        do_not_ask_start_status, _, _ = app_request(
            "POST",
            start_path,
            token=access_token,
            payload={
                "commandId": str(uuid.uuid4()),
                "threadId": do_not_ask_thread_id,
                "sessionId": do_not_ask_session_id,
            },
            policy_headers=policy_headers,
        )
        require(do_not_ask_start_status == 201, "doNotAsk restore smoke must start a separate session")
        do_not_ask_boundary_path = f"{start_path}/{do_not_ask_session_id}/boundary"
        do_not_ask_status, do_not_ask_body, _ = app_request(
            "POST",
            do_not_ask_boundary_path,
            token=access_token,
            payload={
                "commandId": str(uuid.uuid4()),
                "threadId": do_not_ask_thread_id,
                "expectedSessionVersion": 1,
                "boundary": "doNotAsk",
            },
            policy_headers=policy_headers,
        )
        do_not_ask_receipt = do_not_ask_body.get("receipt")
        require(
            do_not_ask_status == 201
            and isinstance(do_not_ask_receipt, dict)
            and do_not_ask_receipt.get("state") == "paused"
            and do_not_ask_receipt.get("boundary") == "doNotAsk"
            and do_not_ask_receipt.get("sessionVersion") == 2,
            "doNotAsk must persist before an explicit restore can be evaluated",
        )

        do_not_ask_restore_path = f"{start_path}/{do_not_ask_session_id}/restore-do-not-ask"
        unconfirmed_status, unconfirmed_body, _ = app_request(
            "POST",
            do_not_ask_restore_path,
            token=access_token,
            payload={
                "commandId": str(uuid.uuid4()),
                "threadId": do_not_ask_thread_id,
                "expectedSessionVersion": 2,
                "confirmed": False,
            },
            policy_headers=policy_headers,
        )
        require(
            unconfirmed_status == 400
            and detail_code(unconfirmed_body) == "ownerTruthInterviewSessionInvalid",
            "doNotAsk restore must require explicit confirmation",
        )

        restore_command_id = str(uuid.uuid4())
        restore_payload = {
            "commandId": restore_command_id,
            "threadId": do_not_ask_thread_id,
            "expectedSessionVersion": 2,
            "confirmed": True,
        }
        restored_status, restored_body, restored_headers = app_request(
            "POST",
            do_not_ask_restore_path,
            token=access_token,
            payload=restore_payload,
            policy_headers=policy_headers,
        )
        restored_receipt = restored_body.get("receipt")
        require(
            restored_status == 201
            and restored_headers.get("cache-control") == "no-store"
            and isinstance(restored_receipt, dict)
            and restored_receipt == {
                "status": "created",
                "threadId": do_not_ask_thread_id,
                "sessionId": do_not_ask_session_id,
                "threadVersion": 1,
                "sessionVersion": 3,
                "state": "active",
                "boundary": "open",
            },
            "confirmed doNotAsk restore must produce only an active/open receipt",
        )
        restore_replay_status, restore_replay_body, _ = app_request(
            "POST",
            do_not_ask_restore_path,
            token=access_token,
            payload=restore_payload,
            policy_headers=policy_headers,
        )
        restore_replay_receipt = restore_replay_body.get("receipt")
        require(
            restore_replay_status == 200
            and isinstance(restore_replay_receipt, dict)
            and restore_replay_receipt.get("status") == "deduplicated"
            and restore_replay_receipt.get("state") == "active"
            and restore_replay_receipt.get("boundary") == "open"
            and restore_replay_receipt.get("sessionVersion") == 3,
            "doNotAsk restore replay must preserve the original active/open result",
        )

        return {
            "formalMissingCaptureDenied": True,
            "formalMatchingCaptureStarted": True,
            "formalCrisisNarrativeSafetyOverridden": True,
            "formalCrisisNarrativeNotPersisted": True,
            "formalMatchingCaptureAppended": True,
            "formalMatchingCaptureRead": True,
            "formalMatchingCapturePresentation": True,
            "formalSkipOnceConsumedByNextOwnerNarrative": True,
            "formalSkipOnceOwnerNarrativeDeduplicated": True,
            "formalBoundaryPersisted": True,
            "formalBoundaryDeduplicated": True,
            "formalBoundaryPausedStateVerified": True,
            "formalBoundaryPausedPresentationVerified": True,
            "formalCooldownRestoreRejected": True,
            "formalDoNotAskRestoreRequiresConfirmation": True,
            "formalDoNotAskRestoreConfirmed": True,
            "formalDoNotAskRestoreDeduplicated": True,
            "contentFreeStateVerified": True,
            "contentFreePresentationVerified": True,
            "deployedCandidateReviewPolicyDefaultClosed": True,
            "formalCandidateConfirmationDenied": True,
            "formalCandidateConfirmationActionDenied": True,
        }
    finally:
        main_module.store = previous_store
        store.close_pool()


def main() -> None:
    assert_deployed_container_context()
    policy_snapshot = assert_deployed_readiness_and_policy()
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = database_dsn(base_dsn, "postgres")
    database_name = f"dj_natural_input_smoke_{uuid.uuid4().hex[:12]}"
    temporary_dsn = database_dsn(base_dsn, database_name)

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=temporary_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-natural-input-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified.get("status") == "ready", "temporary schema is not ready")
        exercise = exercise_formal_natural_input(temporary_dsn, policy_snapshot)
        result = {
            "status": "passed",
            "schemaVersion": 1,
            "deployedReadiness": True,
            "deployedPolicySnapshot": True,
            "deployedContainer": True,
            "temporaryDatabase": True,
            "productionBusinessDataMutated": False,
            "migrationHead": verified.get("expectedHead"),
            "appliedMigrationCount": len(applied.get("appliedVersions") or []),
            **exercise,
        }
        serialized = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        if OUTPUT_PATH:
            output = Path(OUTPUT_PATH)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(serialized, encoding="utf-8")
            output.chmod(0o600)
        print(serialized, end="")
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
