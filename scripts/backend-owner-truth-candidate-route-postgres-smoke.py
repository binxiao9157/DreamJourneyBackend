#!/usr/bin/env python3
"""Exercise Owner Truth Candidate routes in a disposable Postgres DB.

The running service never has its QA flag changed.  This script runs in a
separate Python process, creates a temporary database, applies migrations,
and removes the database on exit.  It proves both the legacy explicit-QA
boundary and the server-authorized closed-pilot path from Source capture,
through both typed workers, to confirmed-projection Context, citation evidence
and immutable correction.  It is not a production-account smoke.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

import app.main as main_module
from app.async_effects.owner_truth_candidate_extraction_worker import (
    OwnerTruthCandidateExtractionWorkerRuntime,
)
from app.async_effects.owner_truth_memory_projection_worker import (
    OwnerTruthMemoryProjectionWorkerRuntime,
)
from app.core.config import Settings, settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore
from app.services.release_policy import ReleasePolicyCommandGate, ReleasePolicyService


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


def content_hash(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def login(
    client: TestClient,
    *,
    phone: str,
    nickname: str,
    qa: bool = False,
) -> tuple[str, dict[str, str], str]:
    response = client.post(
        "/auth/login",
        json={"phone": phone, "nickname": nickname, "password": "candidate-route-smoke"},
    )
    require(response.status_code == 200, f"temporary owner login failed: {response.text}")
    body = response.json()
    headers = {
        "Authorization": f"Bearer {body['auth']['accessToken']}",
    }
    if qa:
        headers["X-DreamJourney-QA-Owner-Truth"] = "1"
    return str(body["user"]["id"]), headers, str(body["auth"]["sessionId"])


def policy_headers(
    headers: dict[str, str],
    *,
    session_id: str,
    feature: str,
) -> dict[str, str]:
    """Produce the same value-minimized policy capture required from iOS."""

    captured = dict(headers)
    captured.update(
        {
            "X-DreamJourney-Feature": feature,
            "X-DreamJourney-Feature-Decision-Id": f"decision-{uuid.uuid4()}",
            "X-DreamJourney-Feature-Allowed": "true",
            "X-DreamJourney-Policy-Version": "release-policy-v1",
            "X-DreamJourney-Policy-Revision": "1",
            "X-DreamJourney-Account-Generation": sha256(
                session_id.encode("utf-8")
            ).hexdigest()[:24],
        }
    )
    return captured


def seed_pending_candidate(
    dsn: str,
    *,
    vault_id: str,
    owner_subject_id: str,
) -> tuple[str, str]:
    source_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())
    proposal = {"summary": "仅用于隔离 Candidate 路由验证的文字摘要"}
    payload = {
        "content": proposal,
        "contentSchemaVersion": "owner-truth-v1",
        "evidenceRefs": [
            {
                "sourceId": source_id,
                "sourceVersion": 1,
                "span": {"start": 0, "end": 12},
            }
        ],
        "reviewMode": "single",
        "schemaVersion": "owner-truth-candidate-proposal-v1",
    }
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO owner_truth.vaults (vault_id, owner_subject_id) VALUES (%s, %s)",
                (vault_id, owner_subject_id),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.sources (
                    id, vault_id, owner_subject_id, source_kind, content_hash,
                    policy_version, authority_epoch
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_id,
                    vault_id,
                    owner_subject_id,
                    "text",
                    content_hash({"source": "candidate-route-smoke"}),
                    "owner-truth-v1",
                    0,
                ),
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.memory_candidates (
                    id, vault_id, owner_subject_id, source_id, candidate_kind,
                    perspective_type, epistemic_status, policy_version,
                    authority_epoch, content_hash, payload_schema_version, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    candidate_id,
                    vault_id,
                    owner_subject_id,
                    source_id,
                    "experience",
                    "firstPerson",
                    "recalled",
                    "owner-truth-v1",
                    0,
                    content_hash(proposal),
                    "owner-truth-v1",
                    Jsonb(payload),
                ),
            )
        connection.commit()
    return candidate_id, proposal["summary"]


def route_code(response: Any) -> str:
    detail = response.json().get("detail") if response.content else None
    if isinstance(detail, dict):
        return str(detail.get("code") or "")
    return ""


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_owner_truth_candidate_route_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    previous_store = main_module.store
    previous_backend_token = main_module.BACKEND_API_TOKEN
    previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
    previous_route_mode = main_module.AUTH_ROUTE_MODE
    previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
    previous_qa_enabled = main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED
    previous_command_mode = main_module.RELEASE_POLICY_COMMAND_MODE
    previous_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
    previous_release_policy_service = main_module.RELEASE_POLICY_SERVICE
    previous_release_policy_command_gate = main_module.RELEASE_POLICY_COMMAND_GATE
    previous_context_authority_enabled = (
        main_module.OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED
    )

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-candidate-route-g2",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=2)
        store.open_pool(wait=True)
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"

        client = TestClient(main_module.app)
        owner_id, owner_headers, _owner_session_id = login(
            client,
            phone="13900000101",
            nickname="Candidate route smoke owner",
            qa=True,
        )
        vault_id = "vault-candidate-route-smoke"
        candidate_id, proposal_summary = seed_pending_candidate(
            test_dsn,
            vault_id=vault_id,
            owner_subject_id=owner_id,
        )

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False
        default_hidden = client.get(
            f"/v2/vaults/{vault_id}/candidates",
            headers=owner_headers,
        )
        require(default_hidden.status_code == 404, "candidate route must remain hidden by default")
        require(
            route_code(default_hidden) == "ownerTruthCandidateReviewUnavailable",
            "hidden candidate route must return its typed unavailable code",
        )

        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True
        missing_policy_capture = client.get(
            f"/v2/vaults/{vault_id}/candidates",
            headers={"Authorization": owner_headers["Authorization"]},
        )
        require(
            missing_policy_capture.status_code == 403,
            "normal candidate route must reject a missing server policy capture",
        )
        require(
            route_code(missing_policy_capture) == "release_policy_denied",
            "normal candidate route must fail closed without a policy capture",
        )

        inbox = client.get(f"/v2/vaults/{vault_id}/candidates", headers=owner_headers)
        require(inbox.status_code == 200, f"owner inbox failed: {inbox.text}")
        inbox_body = inbox.json()
        require(inbox.headers.get("cache-control") == "no-store", "candidate inbox must be no-store")
        require(inbox_body.get("schemaVersion") == "owner-truth-candidate-inbox-v1", "inbox schema drift")
        require(len(inbox_body.get("candidates") or []) == 1, "owner must see the seeded pending candidate")
        require(
            inbox_body["candidates"][0].get("candidateId") == candidate_id,
            "inbox must bind the exact seeded candidate",
        )
        # The owner-only QA inbox intentionally carries the candidate preview so
        # an Owner can review it. The protection boundary is the default-hidden
        # route plus authenticated owner and QA-header gates above, not a
        # lossy response once that triple gate has been explicitly satisfied.
        require(
            (inbox_body["candidates"][0].get("content") or {}).get("summary")
            == proposal_summary,
            "owner QA inbox must return the reviewable candidate preview",
        )

        other_vault = client.get("/v2/vaults/vault-other/candidates", headers=owner_headers)
        require(other_vault.status_code == 403, "cross-vault candidate lookup must be denied")
        require(
            route_code(other_vault) == "ownerTruthCandidateReviewDenied",
            "cross-vault denial must remain typed",
        )

        other_owner_id, other_owner_headers, _other_session_id = login(
            client,
            phone="13900000102",
            nickname="Candidate route smoke observer",
            qa=True,
        )
        require(other_owner_id != owner_id, "temporary smoke identities must be distinct")
        other_owner = client.get(f"/v2/vaults/{vault_id}/candidates", headers=other_owner_headers)
        require(other_owner.status_code == 403, "non-owner candidate lookup must be denied")
        require(
            route_code(other_owner) == "ownerTruthCandidateReviewDenied",
            "non-owner denial must remain typed",
        )

        decision_payload = {
            "commandId": "candidate-route-smoke-accept-v1",
            "expectedCandidateVersion": 1,
            "action": "accept",
            "reasonCode": "qaRouteSmoke",
        }
        decision = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate_id}/decisions",
            headers=owner_headers,
            json=decision_payload,
        )
        require(decision.status_code == 201, f"owner decision failed: {decision.text}")
        decision_body = decision.json()
        require(decision_body.get("status") == "created", "fresh decision must be created")
        require(
            decision_body.get("schemaVersion") == "owner-truth-candidate-decision-memory-v1",
            "decision schema drift",
        )
        require(
            (decision_body.get("receipt") or {}).get("decision") == "accepted",
            "decision receipt must remain accepted",
        )
        require(
            (decision_body.get("memoryActivation") or {}).get("status") == "created",
            "accepted candidate must activate one MemoryVersion",
        )
        require(proposal_summary not in str(decision_body), "decision response must not echo raw proposal summary")

        replay = client.post(
            f"/v2/vaults/{vault_id}/candidates/{candidate_id}/decisions",
            headers=owner_headers,
            json=decision_payload,
        )
        require(replay.status_code == 200, "same command must replay instead of writing again")
        require(replay.json().get("status") == "deduplicated", "decision replay must be deduplicated")
        require(
            (replay.json().get("receipt") or {}).get("receiptId")
            == (decision_body.get("receipt") or {}).get("receiptId"),
            "decision replay must preserve the immutable receipt",
        )

        empty_inbox = client.get(f"/v2/vaults/{vault_id}/candidates", headers=owner_headers)
        require(empty_inbox.status_code == 200, "post-decision inbox lookup failed")
        require(empty_inbox.json().get("candidates") == [], "terminal candidate must leave pending inbox")

        # Closed-pilot must use the same Postgres store and the real workers,
        # never a QA header or a manually seeded Candidate.
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False
        main_module.RELEASE_POLICY_COMMAND_MODE = "enforce"
        closed_pilot_policy = ReleasePolicyService(
            policy_revision=1,
            min_client_build=1,
            ttl_seconds=300,
            closed_pilot_enabled_features={
                "ownerTextCaptureV1",
                "ownerTruthCandidateReview",
            },
            shadow_mode=False,
        )
        main_module.RELEASE_POLICY_SERVICE = closed_pilot_policy
        main_module.RELEASE_POLICY_COMMAND_GATE = ReleasePolicyCommandGate(closed_pilot_policy)
        main_module.OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED = True

        formal_owner_id, formal_auth_headers, formal_session_id = login(
            client,
            phone="13900000103",
            nickname="Closed pilot source owner",
        )
        formal_other_owner_id, formal_other_headers, formal_other_session_id = login(
            client,
            phone="13900000104",
            nickname="Closed pilot source observer",
        )
        require(
            formal_owner_id != formal_other_owner_id,
            "closed-pilot smoke identities must be distinct",
        )
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset({formal_owner_id})
        # The production iOS AccountLease derives the personal Vault from the
        # authenticated user ID.  Context authority uses the same canonical
        # personal Vault, so the end-to-end lane must not invent a separate
        # fixture-only Vault identifier here.
        formal_vault_id = formal_owner_id
        source_command_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"dreamjourney-owner-truth-route-smoke:{formal_vault_id}:source",
            )
        )
        source_content = "在河边散步时，听家人讲起从前的故事。"
        source_response = client.post(
            f"/v2/vaults/{formal_vault_id}/sources",
            headers=policy_headers(
                formal_auth_headers,
                session_id=formal_session_id,
                feature="ownerTextCaptureV1",
            ),
            json={
                "commandId": source_command_id,
                "expectedAuthorityEpoch": 0,
                "kind": "text",
                "content": source_content,
                "purpose": "memoryCapture",
                "clientCreatedAt": "2026-08-02T12:00:00Z",
            },
        )
        require(source_response.status_code == 201, f"closed-pilot Source failed: {source_response.text}")
        source_body = source_response.json()
        require(
            source_body.get("candidateExtraction") == {"status": "requested"},
            "closed-pilot Source must persist one extraction request",
        )
        require(
            source_content not in json.dumps(source_body, ensure_ascii=False, sort_keys=True),
            "Source write receipt must not echo private text",
        )

        worker_settings = Settings(
            async_effect_v1_enabled=True,
            async_effect_worker_enabled=True,
            owner_truth_candidate_extraction_worker_enabled=True,
            owner_truth_memory_projection_worker_enabled=True,
        )
        extraction_result = OwnerTruthCandidateExtractionWorkerRuntime(
            settings=worker_settings,
            store=store,
            worker_id="closed-pilot-source-candidate-extraction-smoke",
            retry_seconds=1,
        ).run_once()
        require(
            extraction_result.get("status") == "completed"
            and extraction_result.get("candidateCount") == 1,
            "closed-pilot worker must create one pending Candidate from Source",
        )
        require(
            source_content
            not in json.dumps(extraction_result, ensure_ascii=False, sort_keys=True),
            "candidate worker result must not echo private Source text",
        )

        formal_candidate_headers = policy_headers(
            formal_auth_headers,
            session_id=formal_session_id,
            feature="ownerTruthCandidateReview",
        )
        formal_inbox = client.get(
            f"/v2/vaults/{formal_vault_id}/candidates",
            headers=formal_candidate_headers,
        )
        require(formal_inbox.status_code == 200, f"closed-pilot inbox failed: {formal_inbox.text}")
        formal_candidates = formal_inbox.json().get("candidates") or []
        require(len(formal_candidates) == 1, "closed-pilot Source must reach the Owner inbox")
        formal_candidate = formal_candidates[0]
        formal_candidate_id = str(formal_candidate.get("candidateId") or "")
        formal_candidate_version = int(formal_candidate.get("candidateVersion") or 0)
        formal_source_id = str(formal_candidate.get("sourceId") or "")
        require(
            formal_candidate_id and formal_candidate_version == 1 and formal_source_id,
            "closed-pilot Candidate must retain stable review identifiers",
        )
        require(
            "X-DreamJourney-QA-Owner-Truth" not in formal_candidate_headers,
            "closed-pilot Candidate review must not use a QA header",
        )
        denied_other_owner = client.get(
            f"/v2/vaults/{formal_vault_id}/candidates",
            headers=policy_headers(
                formal_other_headers,
                session_id=formal_other_session_id,
                feature="ownerTruthCandidateReview",
            ),
        )
        require(
            denied_other_owner.status_code == 403,
            "non-allowlisted account must not read closed-pilot Candidate inbox",
        )

        formal_decision = client.post(
            f"/v2/vaults/{formal_vault_id}/candidates/{formal_candidate_id}/decisions",
            headers=policy_headers(
                formal_auth_headers,
                session_id=formal_session_id,
                feature="ownerTruthCandidateReview",
            ),
            json={
                "commandId": "closed-pilot-source-candidate-accept-v1",
                "expectedCandidateVersion": formal_candidate_version,
                "action": "accept",
                "reasonCode": "ownerReviewed",
            },
        )
        require(formal_decision.status_code == 201, f"closed-pilot decision failed: {formal_decision.text}")
        formal_decision_body = formal_decision.json()
        require(
            (formal_decision_body.get("receipt") or {}).get("decision") == "accepted"
            and (formal_decision_body.get("memoryActivation") or {}).get("status") == "created",
            "closed-pilot Candidate decision must atomically create its MemoryVersion",
        )
        require(
            source_content not in json.dumps(formal_decision_body, ensure_ascii=False, sort_keys=True),
            "closed-pilot decision receipt must not echo private Source text",
        )

        projection_worker = OwnerTruthMemoryProjectionWorkerRuntime(
            settings=worker_settings,
            store=store,
            worker_id="closed-pilot-source-candidate-projection-smoke",
            retry_seconds=1,
        )
        projection_results = [projection_worker.run_once() for _ in range(2)]
        require(
            all(
                result.get("status") == "completed"
                and result.get("projectionOutcome") in {"rebuilt", "unchanged"}
                for result in projection_results
            ),
            "accepted closed-pilot Candidate must drain queued confirmed Projection rebuilds",
        )

        context_response = client.post(
            "/context/build",
            headers=policy_headers(
                formal_auth_headers,
                session_id=formal_session_id,
                feature="echoTextInput",
            ),
            json={
                "userId": formal_owner_id,
                "intent": "echo_chat",
                "query": "请只使用已经确认的个人回忆陪我聊聊。",
                "personaScope": "personal",
                "digitalHumanId": formal_owner_id,
            },
        )
        require(context_response.status_code == 200, f"closed-pilot Context failed: {context_response.text}")
        context_packet = context_response.json().get("contextPacket") or {}
        require(
            context_packet.get("contextVersion") == "echo-context-v4-owner",
            "closed-pilot Context must use confirmed Projection authority",
        )
        selected_context = context_packet.get("selectedContext") or []
        require(
            len(selected_context) == 1
            and ((selected_context[0].get("citation") or {}).get("sourceId") == formal_source_id),
            "closed-pilot Context must cite the confirmed Source through Projection",
        )

        answer_text = "我只会依据已经确认的个人记忆回答。"
        citation_response = client.post(
            f"/v2/vaults/{formal_vault_id}/answer-citation-receipts",
            headers=policy_headers(
                formal_auth_headers,
                session_id=formal_session_id,
                feature="ownerTruthCandidateReview",
            ),
            json={
                "commandId": "closed-pilot-answer-citation-v1",
                "intent": "echo_chat",
                "query": "请说说这段已经确认的个人经历。",
                "answerText": answer_text,
            },
        )
        require(
            citation_response.status_code == 201,
            f"closed-pilot Answer/Citation failed: {citation_response.text}",
        )
        citation_summary = citation_response.json().get("answerCitation") or {}
        require(
            citation_summary.get("citationCount") == 1
            and citation_summary.get("contextVersion") == "echo-context-v4-owner-qa",
            "closed-pilot citation must use confirmed-projection materialization",
        )
        require(
            source_content not in json.dumps(citation_summary, ensure_ascii=False, sort_keys=True)
            and answer_text not in json.dumps(citation_summary, ensure_ascii=False, sort_keys=True),
            "closed-pilot citation receipt must remain value-minimized",
        )

        citation_read = client.get(
            f"/v2/vaults/{formal_vault_id}/answers/{citation_summary['answerId']}/citations",
            headers=policy_headers(
                formal_auth_headers,
                session_id=formal_session_id,
                feature="ownerTruthCandidateReview",
            ),
        )
        require(
            citation_read.status_code == 200,
            f"closed-pilot citation read failed: {citation_read.text}",
        )
        citation_records = (citation_read.json().get("answerCitation") or {}).get("citations") or []
        require(len(citation_records) == 1, "closed-pilot citation read must return one typed citation")
        typed_citation = citation_records[0]
        typed_citation_payload = typed_citation.get("citation") or {}
        require(
            typed_citation_payload.get("sourceId") == formal_source_id,
            "closed-pilot citation read must bind the Source selected by Context",
        )

        denied_correction = client.post(
            f"/v2/vaults/{formal_vault_id}/memories/{typed_citation_payload['memoryId']}/corrections",
            headers=policy_headers(
                formal_other_headers,
                session_id=formal_other_session_id,
                feature="ownerTruthCandidateReview",
            ),
            json={
                "commandId": "closed-pilot-correction-denied-v1",
                "answerId": citation_summary["answerId"],
                "citationId": typed_citation["citationId"],
                "expectedMemoryVersionId": typed_citation_payload["memoryVersionId"],
                "correctionText": "非 allowlist 账号不得创建纠错。",
                "reasonCode": "ownerReportedCorrection",
            },
        )
        require(
            denied_correction.status_code == 403
            and route_code(denied_correction) == "release_policy_denied",
            "non-allowlisted account must not create a closed-pilot correction",
        )

        correction_text = "不是父亲，而是外祖父讲起了从前的故事。"
        correction_request = client.post(
            f"/v2/vaults/{formal_vault_id}/memories/{typed_citation_payload['memoryId']}/corrections",
            headers=policy_headers(
                formal_auth_headers,
                session_id=formal_session_id,
                feature="ownerTruthCandidateReview",
            ),
            json={
                "commandId": "closed-pilot-correction-request-v1",
                "answerId": citation_summary["answerId"],
                "citationId": typed_citation["citationId"],
                "expectedMemoryVersionId": typed_citation_payload["memoryVersionId"],
                "correctionText": correction_text,
                "reasonCode": "ownerReportedCorrection",
            },
        )
        require(
            correction_request.status_code == 201,
            f"closed-pilot correction request failed: {correction_request.text}",
        )
        correction_summary = correction_request.json().get("correctionRequest") or {}
        require(
            correction_summary.get("status") == "pendingReview"
            and correction_summary.get("candidateVersion") == 1
            and correction_summary.get("correctionSourceId"),
            "closed-pilot correction must create a pending Candidate backed by a private Source",
        )
        require(
            correction_text not in json.dumps(correction_summary, ensure_ascii=False, sort_keys=True),
            "closed-pilot correction request must not echo private correction text",
        )

        correction_resolution = client.post(
            (
                f"/v2/vaults/{formal_vault_id}/correction-requests/"
                f"{correction_summary['correctionRequestId']}/resolve"
            ),
            headers=policy_headers(
                formal_auth_headers,
                session_id=formal_session_id,
                feature="ownerTruthCandidateReview",
            ),
            json={
                "commandId": "closed-pilot-correction-resolve-v1",
                "expectedCandidateVersion": correction_summary["candidateVersion"],
                "expectedMemoryVersionId": correction_summary["expectedMemoryVersionId"],
                "action": "correct",
                "correctedValue": {"summary": "外祖父在河边讲起从前的故事"},
                "correctedValueSchemaVersion": "owner-truth-v1",
                "reasonCode": "ownerConfirmedCorrection",
            },
        )
        require(
            correction_resolution.status_code == 201,
            f"closed-pilot correction resolution failed: {correction_resolution.text}",
        )
        correction_resolution_summary = correction_resolution.json().get("correctionResolution") or {}
        require(
            correction_resolution_summary.get("decision") == "corrected"
            and correction_resolution_summary.get("replacementMemoryVersionId")
            and correction_resolution_summary.get("supersededMemoryVersionId")
            == typed_citation_payload["memoryVersionId"],
            "closed-pilot correction must replace exactly the cited MemoryVersion",
        )

        correction_projection_result = OwnerTruthMemoryProjectionWorkerRuntime(
            settings=worker_settings,
            store=store,
            worker_id="closed-pilot-source-candidate-correction-projection-smoke",
            retry_seconds=1,
        ).run_once()
        require(
            correction_projection_result.get("status") == "completed"
            and correction_projection_result.get("projectionOutcome") in {"rebuilt", "unchanged"},
            "corrected closed-pilot Candidate must rebuild confirmed Projection",
        )

        corrected_context_response = client.post(
            "/context/build",
            headers=policy_headers(
                formal_auth_headers,
                session_id=formal_session_id,
                feature="echoTextInput",
            ),
            json={
                "userId": formal_owner_id,
                "intent": "echo_chat",
                "query": "请只使用已经确认的个人回忆陪我聊聊。",
                "personaScope": "personal",
                "digitalHumanId": formal_owner_id,
            },
        )
        require(
            corrected_context_response.status_code == 200,
            f"corrected closed-pilot Context failed: {corrected_context_response.text}",
        )
        corrected_selected_context = (
            (corrected_context_response.json().get("contextPacket") or {}).get("selectedContext") or []
        )
        require(
            len(corrected_selected_context) == 1
            and ((corrected_selected_context[0].get("citation") or {}).get("sourceId")
                 == correction_summary["correctionSourceId"]),
            "corrected Context must cite the replacement Source instead of the superseded Source",
        )

        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT authorization_evidence
                    FROM owner_truth.decision_receipts
                    WHERE vault_id = %s AND candidate_id = %s
                    """,
                    (formal_vault_id, correction_summary["candidateId"]),
                )
                row = cursor.fetchone()
        require(row is not None, "closed-pilot correction must persist a DecisionReceipt")
        evidence = row[0]
        require(
            isinstance(evidence, dict)
            and evidence.get("feature") == "ownerTruthCandidateReview",
            "closed-pilot correction DecisionReceipt must retain release-policy evidence",
        )
        require(
            correction_text not in json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            "closed-pilot correction authorization evidence must remain value-minimized",
        )

        print(
            "owner truth candidate route postgres smoke passed "
            f"schemaHead={verified['expectedHead']} defaultHidden=true qaHeaderRequired=true "
            "ownerInbox=true crossVaultDenied=true crossOwnerDenied=true "
            "decisionCreated=true decisionDeduplicated=true pendingRemoved=true "
            "closedPilotSourceCandidate=true closedPilotProjectionContext=true "
            "closedPilotCitationCorrection=true"
        )
    finally:
        main_module.store = previous_store
        main_module.BACKEND_API_TOKEN = previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = previous_ownership_mode
        main_module.OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = previous_qa_enabled
        main_module.RELEASE_POLICY_COMMAND_MODE = previous_command_mode
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = previous_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE = previous_release_policy_service
        main_module.RELEASE_POLICY_COMMAND_GATE = previous_release_policy_command_gate
        main_module.OWNER_TRUTH_CONTEXT_AUTHORITY_CLOSED_PILOT_ENABLED = (
            previous_context_authority_enabled
        )
        if store is not None:
            store.close_pool()
        try:
            drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(
                f"warning: failed to drop temporary database {database_name}: {exc}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
