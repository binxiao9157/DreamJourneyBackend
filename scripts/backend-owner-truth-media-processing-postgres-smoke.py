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
from threading import Event, Thread
from time import sleep
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
from app.async_effects.owner_truth_media_deletion_worker import (
    OwnerTruthMediaDeletionWorkerRuntime,
)
from app.async_effects.owner_truth_memory_projection_worker import (
    OwnerTruthMemoryProjectionWorkerRuntime,
)
from app.core.config import Settings, settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.owner_truth_media_source_object import (
    FilesystemPrivateMediaObjectStore,
    OwnerTruthMediaCaptureUnavailable,
    OwnerTruthMediaIngestionService,
    OwnerTruthMediaUploadConflict,
    TestOnlyCleanMediaContentSafetyScanner,
)
from app.services.postgres_store import PostgresStore


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class BlockingFilesystemPrivateMediaObjectStore(FilesystemPrivateMediaObjectStore):
    """Hold one delete call so the smoke can exercise lease renewal.

    This adapter exists only in the disposable Postgres smoke. It blocks after
    the worker has fenced the SourceObject but before it removes the local
    private file, which lets a second worker attempt a claim after the original
    one-second lease would otherwise have expired.
    """

    def __init__(
        self,
        *,
        root: str | Path,
        delete_started: Event,
        allow_delete: Event,
    ) -> None:
        super().__init__(root=root)
        self._delete_started = delete_started
        self._allow_delete = allow_delete

    def delete(self, *, storage_key: str) -> None:
        self._delete_started.set()
        if not self._allow_delete.wait(timeout=10):
            raise TimeoutError("lease heartbeat smoke did not release the private delete")
        super().delete(storage_key=storage_key)


class UnavailableFilesystemPrivateMediaObjectStore(FilesystemPrivateMediaObjectStore):
    """Test-only provider failure for one revocation-first delete attempt."""

    def delete(self, *, storage_key: str) -> None:
        del storage_key
        raise OwnerTruthMediaCaptureUnavailable("disposable deletion provider is unavailable")


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
    run_lease_heartbeat_smoke = (
        os.environ.get("RUN_OWNER_TRUTH_MEDIA_LEASE_HEARTBEAT_SMOKE") == "1"
    )
    run_physical_deletion_smoke = run_lease_heartbeat_smoke or (
        os.environ.get("RUN_OWNER_TRUTH_MEDIA_PHYSICAL_DELETION_SMOKE") == "1"
    )

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
            str(applied.get("appliedHead") or "") == "0075",
            "Stage 2 media lifecycle migration must be the schema head",
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
                # This disposable smoke explicitly invokes the default-off
                # deletion worker to prove its terminal evidence contract. It
                # does not enable the production worker profile.
                owner_truth_media_deletion_worker_enabled=True,
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

            # A disabled image OCR provider is an intentional retryable
            # failure fixture. It proves the worker only admits a dead letter
            # after the bounded third attempt, while the SourceObject and
            # typed consumer receipt become terminal in the same transaction.
            image_body = b"\x89PNG\r\n\x1a\nprivate-dead-letter-smoke"
            image_payload = {
                "commandId": str(uuid.uuid4()),
                "expectedAuthorityEpoch": 0,
                "mediaKind": "image",
                "fileName": "private-dead-letter.png",
                "contentType": "image/png",
                "fileSizeBytes": len(image_body),
                "contentSha256": sha256(image_body).hexdigest(),
                "purpose": "memoryCapture",
                "clientCreatedAt": "2026-08-05T00:00:00Z",
            }
            image_created = client.post(intent_path, headers=owner_policy_headers, json=image_payload)
            require(image_created.status_code == 201, f"image intent failed: {image_created.text}")
            image_created_body = image_created.json()
            image_source_object_id = str(image_created_body["sourceObject"]["sourceObjectId"])
            image_upload_intent = image_created_body["uploadIntent"]
            image_uploaded = client.put(
                f"{intent_path}/{image_upload_intent['uploadIntentId']}/content",
                headers={
                    **owner_policy_headers,
                    "X-DreamJourney-Upload-Token": str(image_upload_intent["uploadToken"]),
                    "Content-Type": "image/png",
                },
                content=image_body,
            )
            require(image_uploaded.status_code == 200, f"image upload failed: {image_uploaded.text}")
            require(
                (image_uploaded.json().get("sourceObject") or {}).get("processingStatus") == "queued",
                "disabled image OCR fixture must queue processing",
            )
            image_worker = OwnerTruthMediaProcessingWorkerRuntime(
                settings=worker_settings,
                store=store,
                worker_id="media-processing-dead-letter-postgres-smoke",
                retry_seconds=0,
                object_store=object_store,
            )
            image_attempts = [image_worker.run_once() for _ in range(3)]
            require(
                [attempt.get("status") for attempt in image_attempts]
                == ["retryWait", "retryWait", "failed"],
                f"disabled image OCR must exhaust exactly three attempts: {image_attempts}",
            )
            dead_letter_result = image_attempts[-1]
            require(
                dead_letter_result.get("reason") == "mediaProcessingRetriesExhausted"
                and dead_letter_result.get("deadLetterCause") == "maxAttemptsExceeded"
                and dead_letter_result.get("deadLetterState") == "open"
                and dead_letter_result.get("deadLetterNextAction") == "authorizedReplayRequired",
                f"terminal media failure must expose value-free dead-letter state: {dead_letter_result}",
            )
            with psycopg.connect(test_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT state, attempt
                        FROM async_effects.jobs
                        WHERE job_id = %s
                        """,
                        (dead_letter_result["jobId"],),
                    )
                    dead_letter_job = cursor.fetchone()
                    cursor.execute(
                        """
                        SELECT reason_code, state, attempt
                        FROM async_effects.dead_letters
                        WHERE job_id = %s
                        """,
                        (dead_letter_result["jobId"],),
                    )
                    dead_letter_rows = cursor.fetchall()
                    cursor.execute(
                        """
                        SELECT state, outcome
                        FROM async_effects.business_receipts
                        WHERE operation_id = %s
                          AND receipt_type = 'consumer.ownerTruth.mediaProcessing.completion'
                        """,
                        (dead_letter_result["operationId"],),
                    )
                    dead_letter_receipts = cursor.fetchall()
                    cursor.execute(
                        """
                        SELECT processing_status, retryable
                        FROM owner_truth.media_source_objects
                        WHERE vault_id = %s AND id = %s
                        """,
                        (vault_id, image_source_object_id),
                    )
                    dead_letter_source = cursor.fetchone()
            require(dead_letter_job == ("failed", 3), "dead-letter job must be terminal at attempt three")
            require(
                dead_letter_rows == [("maxAttemptsExceeded", "open", 3)],
                "exactly one open max-attempt dead letter must be durable",
            )
            require(
                dead_letter_receipts == [("failed", "failed")],
                "terminal media failure must keep one failed typed consumer receipt",
            )
            require(
                dead_letter_source == ("failed", False),
                "terminal media failure must not leave the SourceObject retryable",
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

            deletion_path = (
                f"/v2/vaults/{vault_id}/source-objects/{source_object_id}/deletions"
            )
            deletion_payload = {
                "commandId": str(uuid.uuid4()),
                "expectedAuthorityEpoch": 0,
                "clientRequestedAt": "2026-08-05T00:00:00Z",
            }
            cross_owner_deletion = client.post(
                deletion_path,
                headers=other_policy_headers,
                json=deletion_payload,
            )
            require(
                cross_owner_deletion.status_code == 404,
                "cross Owner media deletion must be hidden",
            )

            deletion = client.post(
                deletion_path,
                headers=owner_policy_headers,
                json=deletion_payload,
            )
            require(deletion.status_code == 202, f"media deletion failed: {deletion.text}")
            deletion_body = deletion.json()
            deletion_state = deletion_body.get("deletion") or {}
            require(
                deletion_body.get("schemaVersion") == "owner-truth-media-deletion-response-v1",
                "media deletion response schema changed",
            )
            require(
                deletion_body.get("status") == "deletionRequested"
                and (deletion_body.get("sourceObject") or {}).get("state") == "deleted"
                and (deletion_body.get("sourceObject") or {}).get("processingStatus") == "blocked",
                "accepted deletion must revoke read and processing access first",
            )
            require(
                deletion_state == {
                    "accessState": "accessRevoked",
                    "deletionStatus": "pending",
                    "retryable": True,
                    "failureCode": None,
                    "updatedAt": deletion_state.get("updatedAt"),
                }
                and isinstance(deletion_state.get("updatedAt"), str),
                "deletion receipt must remain value-minimized and pending",
            )
            rendered_deletion = json.dumps(deletion_body, ensure_ascii=False, sort_keys=True)
            require("storageKey" not in rendered_deletion, "deletion response leaked storage key")
            require("storageProvider" not in rendered_deletion, "deletion response leaked provider")
            require(source_text not in rendered_deletion, "deletion response leaked private text")

            deletion_replay = client.post(
                deletion_path,
                headers=owner_policy_headers,
                json=deletion_payload,
            )
            require(
                deletion_replay.status_code == 200
                and deletion_replay.json().get("status") == "deletionDeduplicated",
                "same deletion command must deduplicate",
            )
            processing_retry_after_deletion = client.post(
                f"/v2/vaults/{vault_id}/source-objects/{source_object_id}/processing-retries",
                headers=owner_policy_headers,
            )
            require(
                processing_retry_after_deletion.status_code == 409
                and route_code(processing_retry_after_deletion) == "ownerTruthMediaAccessRevoked",
                "revoked media must not be reprocessed",
            )
            deleted_read = client.get(
                f"/v2/vaults/{vault_id}/source-objects/{source_object_id}",
                headers=owner_policy_headers,
            )
            require(
                deleted_read.status_code == 200
                and (deleted_read.json().get("sourceObject") or {}).get("state") == "deleted",
                "Owner status read must remain a tombstone rather than restoring access",
            )

            context_after_deletion = client.post(
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
                    "query": "只依据仍可使用的确认记忆回答。",
                    "personaScope": "personal",
                    "digitalHumanId": owner_id,
                },
            )
            require(
                context_after_deletion.status_code == 200,
                f"Context after media deletion failed: {context_after_deletion.text}",
            )
            selected_after_deletion = (
                (context_after_deletion.json().get("contextPacket") or {}).get("selectedContext")
                or []
            )
            require(
                not any(
                    ((item.get("citation") or {}).get("sourceId") == candidate_source_id)
                    for item in selected_after_deletion
                ),
                "revoked media-derived Source must not remain in Context",
            )

            with psycopg.connect(test_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT provider_name, capability, state
                        FROM async_effects.provider_effects
                        WHERE vault_id = %s AND resource_id = %s
                          AND purpose = 'privateMediaDeletion'
                        """,
                        (vault_id, source_object_id),
                    )
                    provider_effect_rows = cursor.fetchall()
            require(
                provider_effect_rows == [("objectStorage", "privateMediaDeletion", "accepted")],
                "accepted deletion must record one value-free provider effect receipt",
            )

            # A provider outage keeps access revoked and makes this immutable
            # deletion generation terminal. The retry route below creates a
            # new generation rather than replaying this failed job.
            with store.request_unit_of_work(
                correlation_id="stage2-media-deletion-load",
                command_id="stage2MediaDeletionLoad",
            ):
                repository = store.owner_truth_media_source_object_repository()
                deletion_source = repository.get_source_object(
                    vault_id=vault_id,
                    source_object_id=source_object_id,
                    owner_subject_id=owner_id,
                )
            deletion_generation = int(deletion_source["deletionGeneration"])
            deletion_failure = OwnerTruthMediaDeletionWorkerRuntime(
                settings=worker_settings,
                store=store,
                worker_id="media-deletion-dead-letter-postgres-smoke",
                object_store=UnavailableFilesystemPrivateMediaObjectStore(root=media_root),
            ).run_once()
            require(
                deletion_failure.get("status") == "failed"
                and deletion_failure.get("reason") == "privateMediaDeletionUnavailable"
                and deletion_failure.get("deletionStatus") == "partial"
                and deletion_failure.get("deletionRetryable") is True
                and deletion_failure.get("deadLetterCause") == "manualInterventionRequired"
                and deletion_failure.get("deadLetterState") == "open"
                and deletion_failure.get("deadLetterNextAction") == "manualInterventionRequired",
                f"terminal media deletion must expose value-free dead-letter state: {deletion_failure}",
            )
            with psycopg.connect(test_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT state, attempt
                        FROM async_effects.jobs
                        WHERE job_id = %s
                        """,
                        (deletion_failure["jobId"],),
                    )
                    deletion_failure_job = cursor.fetchone()
                    cursor.execute(
                        """
                        SELECT reason_code, state, attempt
                        FROM async_effects.dead_letters
                        WHERE job_id = %s
                        """,
                        (deletion_failure["jobId"],),
                    )
                    deletion_dead_letter_rows = cursor.fetchall()
                    cursor.execute(
                        """
                        SELECT state, outcome
                        FROM async_effects.business_receipts
                        WHERE operation_id = %s
                          AND receipt_type = 'consumer.ownerTruth.mediaSourceObject.deletion.completion'
                        """,
                        (deletion_failure["operationId"],),
                    )
                    deletion_failure_receipts = cursor.fetchall()
                    cursor.execute(
                        """
                        SELECT access_state, deletion_status, deletion_retryable
                        FROM owner_truth.media_source_objects
                        WHERE vault_id = %s AND id = %s
                        """,
                        (vault_id, source_object_id),
                    )
                    deletion_failure_source = cursor.fetchone()
            require(
                deletion_failure_job == ("failed", 1),
                "failed deletion job must become terminal before dead-letter admission",
            )
            require(
                deletion_dead_letter_rows == [("manualInterventionRequired", "open", 1)],
                "failed deletion must admit exactly one open manual-intervention dead letter",
            )
            require(
                deletion_failure_receipts == [("failed", "failed")],
                "failed deletion must keep one failed typed consumer receipt",
            )
            require(
                deletion_failure_source == ("accessRevoked", "partial", True),
                "failed deletion must retain revoked access and a retryable partial state",
            )
            deletion_retry_payload = {
                "commandId": str(uuid.uuid4()),
                "expectedAuthorityEpoch": 0,
                "clientRequestedAt": "2026-08-05T00:01:00Z",
            }
            deletion_retry = client.post(
                f"/v2/vaults/{vault_id}/source-objects/{source_object_id}/deletion-retries",
                headers=owner_policy_headers,
                json=deletion_retry_payload,
            )
            require(
                deletion_retry.status_code == 202,
                f"retryable deletion must requeue safely: {deletion_retry.text}",
            )
            require(
                deletion_retry.json().get("status") == "deletionRetryRequested"
                and (deletion_retry.json().get("sourceObject") or {}).get("state") == "deleted"
                and (deletion_retry.json().get("deletion") or {}).get("deletionStatus")
                == "pending",
                "deletion retry must preserve revoked access and reset only its effect state",
            )
            deletion_retry_replay = client.post(
                f"/v2/vaults/{vault_id}/source-objects/{source_object_id}/deletion-retries",
                headers=owner_policy_headers,
                json=deletion_retry_payload,
            )
            require(
                deletion_retry_replay.status_code == 200
                and deletion_retry_replay.json().get("status") == "deletionDeduplicated",
                "deletion retry command must deduplicate",
            )
            with store.request_unit_of_work(
                correlation_id="stage2-media-deletion-stale-outcome",
                command_id="stage2MediaDeletionStaleOutcome",
            ):
                repository = store.owner_truth_media_source_object_repository()
                try:
                    repository.record_deletion_outcome(
                        vault_id=vault_id,
                        source_object_id=source_object_id,
                        owner_subject_id=owner_id,
                        deletion_generation=deletion_generation,
                        outcome="completed",
                        retryable=False,
                    )
                except OwnerTruthMediaUploadConflict:
                    pass
                else:
                    raise AssertionError("old deletion generation must not overwrite a retry")
            with psycopg.connect(test_dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT COUNT(*)
                        FROM async_effects.provider_effects
                        WHERE vault_id = %s AND resource_id = %s
                          AND purpose = 'privateMediaDeletion'
                        """,
                        (vault_id, source_object_id),
                    )
                    deletion_effect_count = int(cursor.fetchone()[0])
            require(
                deletion_effect_count == 2,
                "each accepted deletion generation must create one provider-effect receipt",
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
            physical_deletion_completed = False
            lease_heartbeat_protected = False
            # The normal completed document plus the intentionally retained
            # dead-letter image are both private objects. Physical deletion
            # below targets only the completed document's SourceObject.
            retained_dead_letter_object_count = 1
            if run_physical_deletion_smoke:
                deletion_worker = OwnerTruthMediaDeletionWorkerRuntime(
                    settings=worker_settings,
                    store=store,
                    worker_id="media-deletion-postgres-smoke",
                    object_store=object_store,
                )
                if run_lease_heartbeat_smoke:
                    delete_started = Event()
                    allow_delete = Event()
                    blocking_object_store = BlockingFilesystemPrivateMediaObjectStore(
                        root=media_root,
                        delete_started=delete_started,
                        allow_delete=allow_delete,
                    )
                    protected_worker = OwnerTruthMediaDeletionWorkerRuntime(
                        settings=worker_settings,
                        store=store,
                        worker_id="media-deletion-postgres-lease-heartbeat-primary",
                        lease_seconds=1,
                        heartbeat_interval_seconds=0.1,
                        object_store=blocking_object_store,
                    )
                    protected_result: dict[str, Any] = {}
                    protected_failure: list[BaseException] = []

                    def run_protected_delete() -> None:
                        try:
                            protected_result.update(protected_worker.run_once())
                        except BaseException as exc:  # thread boundary for smoke diagnostics
                            protected_failure.append(exc)

                    protected_thread = Thread(
                        target=run_protected_delete,
                        name="owner-truth-media-deletion-lease-heartbeat-smoke",
                    )
                    protected_thread.start()
                    try:
                        require(
                            delete_started.wait(timeout=5),
                            "protected deletion worker did not reach the provider call",
                        )
                        # This deliberately exceeds the first one-second lease. A
                        # successful contender claim here would prove that the
                        # heartbeat did not keep the active worker protected.
                        sleep(1.25)
                        contender = OwnerTruthMediaDeletionWorkerRuntime(
                            settings=worker_settings,
                            store=store,
                            worker_id="media-deletion-postgres-lease-heartbeat-contender",
                            lease_seconds=1,
                            heartbeat_interval_seconds=0.1,
                            object_store=object_store,
                        ).run_once()
                        require(
                            contender.get("status") == "idle",
                            f"second deletion worker claimed an active lease: {contender}",
                        )
                    finally:
                        allow_delete.set()
                        protected_thread.join(timeout=10)
                    require(
                        not protected_thread.is_alive(),
                        "protected deletion worker did not finish after provider release",
                    )
                    require(
                        not protected_failure,
                        "protected deletion worker raised while completing its lease",
                    )
                    physical_deletion = protected_result
                    lease_heartbeat_protected = True
                else:
                    physical_deletion = deletion_worker.run_once()
                require(
                    physical_deletion.get("status") == "completed"
                    and physical_deletion.get("deletionStatus") == "completed"
                    and physical_deletion.get("businessOutcome") == "completed",
                    f"physical media deletion failed: {physical_deletion}",
                )
                require(
                    len(list(Path(media_root).rglob("*.bin")))
                    == retained_dead_letter_object_count,
                    "completed deletion must remove only its private filesystem object",
                )
                physical_deletion_completed = True
            else:
                require(
                    len(list(Path(media_root).rglob("*.bin")))
                    == 1 + retained_dead_letter_object_count,
                    "P0-S1 must not claim physical deletion before its dedicated worker exists",
                )

            print(
                json.dumps(
                    {
                        "schemaHead": applied.get("appliedHead"),
                        "defaultClosed": True,
                        "ownerBoundUpload": True,
                        "commandReplayDeduplicated": True,
                        "crossOwnerDenied": True,
                        "privateObjectCount": (
                            retained_dead_letter_object_count
                            if physical_deletion_completed
                            else 1 + retained_dead_letter_object_count
                        ),
                        "derivedSource": True,
                        "pendingCandidate": True,
                        "formalPolicySnapshotCaptured": True,
                        "candidateConfirmed": True,
                        "memoryVersionCreated": True,
                        "projectionReady": True,
                        "contextBuilt": True,
                        "deletionAccessRevoked": True,
                        "deletionReplayDeduplicated": True,
                        "deletionRetryRequeued": True,
                        "deletionRetryReplayDeduplicated": True,
                        "staleDeletionOutcomeBlocked": True,
                        "deletionProviderReceiptAccepted": True,
                        "physicalDeletionCompleted": physical_deletion_completed,
                        "leaseHeartbeatProtected": lease_heartbeat_protected,
                        "mediaProcessingDeadLetterAdmitted": True,
                        "mediaDeletionDeadLetterAdmitted": True,
                        "deletedMediaExcludedFromContext": True,
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
