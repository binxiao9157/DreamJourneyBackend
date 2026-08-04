#!/usr/bin/env python3
"""Exercise the Stage 2 private media path in disposable Postgres.

The smoke applies the complete migration chain to a temporary database, uses
authenticated API calls to upload one private text document, then runs the
real media and Candidate workers. It never writes production business rows or
calls an external OCR/ASR provider.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any
import uuid

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.main as main_module
from app.async_effects.owner_truth_candidate_extraction_worker import (
    OwnerTruthCandidateExtractionWorkerRuntime,
)
from app.async_effects.owner_truth_media_processing_worker import (
    OwnerTruthMediaProcessingWorkerRuntime,
)
from app.async_effects.owner_truth_memory_projection_worker import (
    OwnerTruthMemoryProjectionWorkerRuntime,
)
from app.core.config import Settings, settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.owner_truth_media_source_object import (
    FilesystemPrivateMediaObjectStore,
    OwnerTruthMediaIngestionService,
    TestOnlyCleanMediaContentSafetyScanner,
)
from app.services.postgres_store import PostgresStore


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


def route_code(response: Any) -> str:
    detail = response.json().get("detail") if response.content else None
    return str(detail.get("code") or "") if isinstance(detail, dict) else ""


def login(client: TestClient, *, phone: str) -> tuple[str, dict[str, str], str]:
    response = client.post(
        "/auth/login",
        json={"phone": phone, "nickname": "Stage 2 media smoke", "password": "media-smoke"},
    )
    require(response.status_code == 200, f"temporary login failed: {response.text}")
    body = response.json()
    return (
        str(body["user"]["id"]),
        {"Authorization": f"Bearer {body['auth']['accessToken']}"},
        str(body["auth"]["sessionId"]),
    )


def captured_policy_headers(
    client: TestClient,
    headers: dict[str, str],
    *,
    session_id: str,
    feature: str,
) -> dict[str, str]:
    """Fetch a server snapshot, then create the minimal client capture it requires.

    A policy snapshot is server-authored. The decision ID remains a client-side
    correlation value, which is the same shape used by the iOS client. This
    keeps the smoke in the normal release-policy lane without a QA bypass.
    """

    snapshot_response = client.get(
        "/v2/release-policy",
        params={"feature": feature, "clientBuild": 1},
        headers=headers,
    )
    require(
        snapshot_response.status_code == 200,
        f"release-policy snapshot failed for {feature}: {snapshot_response.text}",
    )
    snapshot = snapshot_response.json()
    decisions = snapshot.get("features") or []
    require(len(decisions) == 1, f"release-policy returned an ambiguous {feature} decision")
    decision = decisions[0]
    require(
        decision.get("feature") == feature and decision.get("enabled") is True,
        f"closed-pilot policy must enable {feature}: {decision}",
    )
    return {
        **headers,
        "X-DreamJourney-Feature": feature,
        "X-DreamJourney-Feature-Decision-Id": f"media-formal-smoke-{feature}-{uuid.uuid4()}",
        "X-DreamJourney-Feature-Allowed": "true",
        "X-DreamJourney-Policy-Version": str(snapshot["policyVersion"]),
        "X-DreamJourney-Policy-Revision": str(snapshot["policyRevision"]),
        "X-DreamJourney-Client-Build": str(snapshot["minClient"]),
        "X-DreamJourney-Account-Generation": sha256(
            session_id.encode("utf-8")
        ).hexdigest()[:24],
    }


def persisted_summary(
    dsn: str,
    *,
    vault_id: str,
    source_object_id: str,
    source_text: str,
) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT state, processing_status, processing_generation,
                    derived_source_id, storage_provider, storage_key
                FROM owner_truth.media_source_objects
                WHERE vault_id = %s AND id = %s
                """,
                (vault_id, source_object_id),
            )
            media = cursor.fetchone()
            require(media is not None, "media SourceObject persistence is missing")
            derived_source_id = str(media[3] or "")
            require(derived_source_id, "processed media must reference a derived Source")

            cursor.execute(
                """
                SELECT content_payload, metadata
                FROM owner_truth.sources
                WHERE vault_id = %s AND id = %s
                """,
                (vault_id, derived_source_id),
            )
            source = cursor.fetchone()
            require(source is not None, "derived private Source persistence is missing")
            content_payload = source[0]
            if isinstance(content_payload, str):
                content_payload = json.loads(content_payload)
            metadata = source[1]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)

            cursor.execute(
                """
                SELECT id, decision_status, payload
                FROM owner_truth.memory_candidates
                WHERE vault_id = %s AND source_id = %s
                """,
                (vault_id, derived_source_id),
            )
            candidates = cursor.fetchall()

            cursor.execute(
                """
                SELECT upload_token_hash
                FROM owner_truth.media_source_object_upload_intents
                WHERE vault_id = %s AND source_object_id = %s
                """,
                (vault_id, source_object_id),
            )
            intent = cursor.fetchone()

            cursor.execute(
                """
                SELECT state, processor_id, processing_generation, attempt,
                    extracted_text_sha256, derived_source_id
                FROM owner_truth.media_source_object_processing_results
                WHERE vault_id = %s AND source_object_id = %s
                """,
                (vault_id, source_object_id),
            )
            results = cursor.fetchall()

    require(media[0] == "processed", "media object must reach processed state")
    require(media[1] == "succeeded", "media processing must succeed")
    require(int(media[2]) == 1, "initial processing generation must remain one")
    require(media[4] == "filesystem", "disposable smoke must use private filesystem storage")
    require(str(media[5] or ""), "server must retain an internal private storage key")
    require(isinstance(content_payload, dict), "derived Source payload must be structured")
    require(content_payload.get("text") == source_text, "derived Source text changed during processing")
    require(isinstance(metadata, dict), "derived Source metadata must be structured")
    require(metadata.get("origin") == "mediaSourceObjectProcessing", "derived Source origin changed")
    require(len(candidates) == 1, "media Source must create exactly one review Candidate")
    require(candidates[0][1] == "pending", "media Candidate must remain pending Owner review")
    require(len(results) == 1 and results[0][0] == "succeeded", "processing receipt missing")
    require(str(results[0][5]) == derived_source_id, "processing receipt lost Source binding")
    require(intent is not None and len(str(intent[0])) == 64, "upload token hash is missing")
    return {
        "mediaState": media[0],
        "processingStatus": media[1],
        "processingGeneration": int(media[2]),
        "candidateCount": len(candidates),
        "candidateDecisionStatus": candidates[0][1],
        "processingResultCount": len(results),
    }


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_media_processing_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None
    client: TestClient | None = None

    previous_store = main_module.store
    previous_ingestion_service = main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE
    previous_backend_token = main_module.BACKEND_API_TOKEN
    previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
    previous_route_mode = main_module.AUTH_ROUTE_MODE
    previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
    previous_release_policy_command_mode = main_module.RELEASE_POLICY_COMMAND_MODE
    previous_context_authority_closed_pilot_enabled = (
        main_module.OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED
    )
    previous_closed_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
    previous_closed_pilot_features = set(
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features
    )

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-media-processing-postgres-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=30000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(
            str(applied.get("appliedHead") or "") == "0074",
            "Stage 2 media processing migration must be the schema head",
        )

        with TemporaryDirectory(prefix="dreamjourney-media-postgres-smoke-") as media_root:
            object_store = FilesystemPrivateMediaObjectStore(root=media_root)
            store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=4)
            store.open_pool(wait=True)
            main_module.store = store
            main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE = OwnerTruthMediaIngestionService(
                store=store,
                object_store=object_store,
                safety_scanner=TestOnlyCleanMediaContentSafetyScanner(),
                enabled=True,
                max_upload_bytes=1024 * 1024,
                upload_intent_ttl_seconds=900,
                on_verified=main_module._queue_verified_owner_truth_media_processing,
            )
            main_module.BACKEND_API_TOKEN = ""
            main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
            main_module.AUTH_ROUTE_MODE = "enforce"
            main_module.AUTH_OWNERSHIP_MODE = "enforce"
            main_module.RELEASE_POLICY_COMMAND_MODE = "enforce"
            main_module.OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED = True
            main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.discard(
                "ownerMediaCaptureV1"
            )
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.discard(
                "ownerTruthCandidateReview"
            )

            client = TestClient(main_module.app)
            owner_id, owner_headers, owner_session_id = login(
                client,
                phone="13900000373",
            )
            other_id, other_headers, other_session_id = login(
                client,
                phone="13900000374",
            )
            # The formal Context authority derives a personal Vault directly
            # from the authenticated Owner. Keep the SourceObject on that same
            # Vault so Candidate acceptance can reach Context without a second
            # fixture-only authority model.
            vault_id = owner_id
            source_text = "A private Stage 2 document remains pending until Owner review."
            body = source_text.encode("utf-8")
            intent_path = f"/v2/vaults/{vault_id}/source-objects/upload-intents"
            payload = {
                "commandId": str(uuid.uuid4()),
                "expectedAuthorityEpoch": 0,
                "mediaKind": "document",
                "fileName": "private-memory.txt",
                "contentType": "text/plain",
                "fileSizeBytes": len(body),
                "contentSha256": sha256(body).hexdigest(),
                "purpose": "memoryCapture",
                "clientCreatedAt": "2026-08-03T00:00:00Z",
            }

            default_closed = client.post(intent_path, headers=owner_headers, json=payload)
            require(default_closed.status_code == 403, "media capture must default closed")
            require(route_code(default_closed) == "release_policy_denied", "denial code changed")

            main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset(
                {owner_id, other_id}
            )
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.add(
                "ownerMediaCaptureV1"
            )
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.add(
                "ownerTruthCandidateReview"
            )
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.add(
                "echoTextInput"
            )
            owner_policy_headers = captured_policy_headers(
                client,
                owner_headers,
                session_id=owner_session_id,
                feature="ownerMediaCaptureV1",
            )
            other_policy_headers = captured_policy_headers(
                client,
                other_headers,
                session_id=other_session_id,
                feature="ownerMediaCaptureV1",
            )

            created = client.post(intent_path, headers=owner_policy_headers, json=payload)
            require(created.status_code == 201, f"media intent failed: {created.text}")
            created_body = created.json()
            upload_intent = created_body["uploadIntent"]
            source_object_id = str(created_body["sourceObject"]["sourceObjectId"])
            upload_token = str(upload_intent["uploadToken"])
            require(source_text not in created.text, "intent response leaked private content")
            require("storageKey" not in created.text, "intent response leaked storage key")

            replay = client.post(intent_path, headers=owner_policy_headers, json=payload)
            require(replay.status_code == 200, f"intent replay failed: {replay.text}")
            require(replay.json().get("status") == "deduplicated", "intent replay duplicated work")
            require("uploadToken" not in replay.json()["uploadIntent"], "replay leaked token")

            uploaded = client.put(
                f"{intent_path}/{upload_intent['uploadIntentId']}/content",
                headers={
                    **owner_policy_headers,
                    "X-DreamJourney-Upload-Token": upload_token,
                    "Content-Type": "text/plain",
                },
                content=body,
            )
            require(uploaded.status_code == 200, f"private upload failed: {uploaded.text}")
            uploaded_object = uploaded.json()["sourceObject"]
            require(uploaded_object["processingStatus"] == "queued", "processing not queued")
            require(source_text not in uploaded.text, "upload response leaked private content")
            require(upload_token not in uploaded.text, "upload response leaked token")
            require("storageKey" not in uploaded.text, "upload response leaked storage key")

            cross_owner = client.get(
                f"/v2/vaults/{vault_id}/source-objects/{source_object_id}",
                headers=other_policy_headers,
            )
            require(cross_owner.status_code == 404, "cross Owner media read must be hidden")
            require(route_code(cross_owner) == "ownerTruthMediaVaultNotFound", "cross Owner code changed")

            worker_settings = Settings(
                environment="test",
                async_effect_v1_enabled=True,
                async_effect_worker_enabled=True,
                owner_truth_candidate_extraction_worker_enabled=True,
                owner_truth_memory_projection_worker_enabled=True,
                owner_truth_media_capture_enabled=True,
                owner_truth_media_processing_worker_enabled=True,
                owner_truth_media_storage_provider="filesystem",
                owner_truth_media_storage_root=media_root,
            )
            media_result = OwnerTruthMediaProcessingWorkerRuntime(
                settings=worker_settings,
                store=store,
                worker_id="media-processing-postgres-smoke",
                retry_seconds=0,
                object_store=object_store,
            ).run_once()
            require(media_result.get("status") == "completed", f"media worker failed: {media_result}")
            require(
                media_result.get("candidateExtractionRequested") == "accepted",
                "media worker did not queue Candidate extraction",
            )
            candidate_result = OwnerTruthCandidateExtractionWorkerRuntime(
                settings=worker_settings,
                store=store,
                worker_id="media-candidate-postgres-smoke",
                retry_seconds=1,
            ).run_once()
            require(
                candidate_result.get("status") == "completed"
                and candidate_result.get("candidateCount") == 1,
                f"Candidate worker failed: {candidate_result}",
            )

            fetched = client.get(
                f"/v2/vaults/{vault_id}/source-objects/{source_object_id}",
                headers=owner_policy_headers,
            )
            require(fetched.status_code == 200, f"media status read failed: {fetched.text}")
            require(fetched.json()["sourceObject"]["state"] == "processed", "state did not persist")
            require(source_text not in fetched.text, "status response leaked private content")
            require("storageKey" not in fetched.text, "status response leaked storage key")

            summary = persisted_summary(
                test_dsn,
                vault_id=vault_id,
                source_object_id=source_object_id,
                source_text=source_text,
            )
            require(upload_token not in json.dumps(summary), "summary leaked upload token")

            candidate_headers = captured_policy_headers(
                client,
                owner_headers,
                session_id=owner_session_id,
                feature="ownerTruthCandidateReview",
            )
            candidate_inbox = client.get(
                f"/v2/vaults/{vault_id}/candidates",
                headers=candidate_headers,
            )
            require(
                candidate_inbox.status_code == 200,
                f"formal Candidate inbox failed: {candidate_inbox.text}",
            )
            candidate_rows = candidate_inbox.json().get("candidates") or []
            require(len(candidate_rows) == 1, "media Candidate must reach the formal Owner inbox")
            candidate = candidate_rows[0]
            candidate_id = str(candidate.get("candidateId") or "")
            candidate_version = int(candidate.get("candidateVersion") or 0)
            candidate_source_id = str(candidate.get("sourceId") or "")
            require(
                candidate_id and candidate_version == 1 and candidate_source_id,
                "formal media Candidate must retain review identifiers",
            )
            require(
                "X-DreamJourney-QA-Owner-Truth" not in candidate_headers,
                "formal media Candidate review must not use a QA header",
            )
            other_candidate_inbox = client.get(
                f"/v2/vaults/{vault_id}/candidates",
                headers=captured_policy_headers(
                    client,
                    other_headers,
                    session_id=other_session_id,
                    feature="ownerTruthCandidateReview",
                ),
            )
            require(
                other_candidate_inbox.status_code == 403,
                "non-owner must not read the formal media Candidate inbox",
            )

            candidate_decision = client.post(
                f"/v2/vaults/{vault_id}/candidates/{candidate_id}/decisions",
                headers=captured_policy_headers(
                    client,
                    owner_headers,
                    session_id=owner_session_id,
                    feature="ownerTruthCandidateReview",
                ),
                json={
                    "commandId": f"media-candidate-accept-{uuid.uuid4()}",
                    "expectedCandidateVersion": candidate_version,
                    "action": "accept",
                    "reasonCode": "ownerReviewed",
                },
            )
            require(
                candidate_decision.status_code == 201,
                f"formal Candidate acceptance failed: {candidate_decision.text}",
            )
            decision_body = candidate_decision.json()
            require(
                (decision_body.get("receipt") or {}).get("decision") == "accepted"
                and (decision_body.get("memoryActivation") or {}).get("status") == "created",
                "accepted media Candidate must create one MemoryVersion",
            )
            memory_version_id = str(
                (decision_body.get("memoryActivation") or {}).get("memoryVersionId") or ""
            )
            require(memory_version_id, "Candidate acceptance must return the created MemoryVersion")

            projection_worker = OwnerTruthMemoryProjectionWorkerRuntime(
                settings=worker_settings,
                store=store,
                worker_id="media-candidate-projection-postgres-smoke",
                retry_seconds=1,
            )
            projection_results: list[dict[str, Any]] = []
            for _ in range(4):
                projection_result = projection_worker.run_once()
                projection_results.append(projection_result)
                if projection_result.get("status") == "idle":
                    break
                require(
                    projection_result.get("status") == "completed"
                    and projection_result.get("projectionOutcome") in {"rebuilt", "unchanged"},
                    f"Memory projection failed: {projection_result}",
                )
            require(
                any(result.get("status") == "completed" for result in projection_results),
                "accepted media Candidate must schedule a confirmed memory projection",
            )

            context_response = client.post(
                "/context/build",
                headers=captured_policy_headers(
                    client,
                    owner_headers,
                    session_id=owner_session_id,
                    feature="echoTextInput",
                ),
                json={
                    "userId": owner_id,
                    "intent": "echo_chat",
                    "query": "请只依据已经确认的个人回忆回答。",
                    "personaScope": "personal",
                    "digitalHumanId": owner_id,
                },
            )
            require(
                context_response.status_code == 200,
                f"formal media Context failed: {context_response.text}",
            )
            context_packet = context_response.json().get("contextPacket") or {}
            require(
                context_packet.get("contextVersion") == "echo-context-v4-owner",
                "formal media Context must use confirmed Projection authority",
            )
            selected_context = context_packet.get("selectedContext") or []
            require(
                any(
                    ((item.get("citation") or {}).get("sourceId") == candidate_source_id)
                    for item in selected_context
                ),
                "formal media Context must cite the confirmed media-derived Source",
            )
            require(
                OwnerTruthMediaProcessingWorkerRuntime(
                    settings=worker_settings,
                    store=store,
                    worker_id="media-processing-postgres-replay",
                    retry_seconds=0,
                    object_store=object_store,
                ).run_once()["status"] == "idle",
                "completed media work replayed",
            )
            require(
                OwnerTruthCandidateExtractionWorkerRuntime(
                    settings=worker_settings,
                    store=store,
                    worker_id="media-candidate-postgres-replay",
                ).run_once()["status"] == "idle",
                "completed Candidate work replayed",
            )
            require(len(list(Path(media_root).rglob("*.bin"))) == 1, "private object count changed")

            print(
                json.dumps(
                    {
                        "schemaHead": applied.get("appliedHead"),
                        "defaultClosed": True,
                        "ownerBoundUpload": True,
                        "commandReplayDeduplicated": True,
                        "crossOwnerDenied": True,
                        "privateObjectCount": 1,
                        "derivedSource": True,
                        "pendingCandidate": True,
                        "formalPolicySnapshotCaptured": True,
                        "candidateConfirmed": True,
                        "memoryVersionCreated": True,
                        "projectionReady": True,
                        "contextBuilt": True,
                        "qaHeaderUsed": False,
                        "responseRedaction": True,
                        **summary,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    finally:
        if client is not None:
            client.close()
        main_module.store = previous_store
        main_module.OWNER_TRUTH_MEDIA_INGESTION_SERVICE = previous_ingestion_service
        main_module.BACKEND_API_TOKEN = previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = previous_ownership_mode
        main_module.RELEASE_POLICY_COMMAND_MODE = previous_release_policy_command_mode
        main_module.OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED = (
            previous_context_authority_closed_pilot_enabled
        )
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = previous_closed_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = (
            previous_closed_pilot_features
        )
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
