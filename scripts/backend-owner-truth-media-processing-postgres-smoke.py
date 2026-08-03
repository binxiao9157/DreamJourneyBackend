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


def capture_headers(
    headers: dict[str, str],
    *,
    session_id: str,
    decision_id: str,
) -> dict[str, str]:
    return {
        **headers,
        "X-DreamJourney-Feature": "ownerMediaCaptureV1",
        "X-DreamJourney-Feature-Decision-Id": decision_id,
        "X-DreamJourney-Feature-Allowed": "true",
        "X-DreamJourney-Policy-Version": "release-policy-v1",
        "X-DreamJourney-Policy-Revision": "1",
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
                SELECT source_text, metadata
                FROM owner_truth.sources
                WHERE vault_id = %s AND id = %s
                """,
                (vault_id, derived_source_id),
            )
            source = cursor.fetchone()
            require(source is not None, "derived private Source persistence is missing")
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
    require(source[0] == source_text, "derived Source text changed during processing")
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
            main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
            main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.discard(
                "ownerMediaCaptureV1"
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
            vault_id = "vault-media-processing-postgres-smoke"
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
            owner_policy_headers = capture_headers(
                owner_headers,
                session_id=owner_session_id,
                decision_id="media-processing-postgres-owner",
            )
            other_policy_headers = capture_headers(
                other_headers,
                session_id=other_session_id,
                decision_id="media-processing-postgres-other",
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
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = previous_closed_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = (
            previous_closed_pilot_features
        )
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
