#!/usr/bin/env python3
"""Exercise the closed-pilot family contribution lane in disposable Postgres.

The smoke creates a temporary database, applies the real migration chain and
uses authenticated API requests.  It proves that an Owner can grant exactly
one accepted family contributor the ability to submit static material, while
the contributor receives neither a Vault read nor any voice/persona/digital
human authority.  No production account or business record is used.
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

import app.main as main_module
from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.services.delegated_access import DelegatedAccessService
from app.services.owner_truth_source import OwnerTruthSourceCommandService
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


def login(client: TestClient, *, phone: str, nickname: str) -> tuple[str, dict[str, str], str]:
    response = client.post(
        "/auth/login",
        json={"phone": phone, "nickname": nickname, "password": "formal-family-smoke"},
    )
    require(response.status_code == 200, f"temporary login failed: {response.text}")
    body = response.json()
    return (
        str(body["user"]["id"]),
        {"Authorization": f"Bearer {body['auth']['accessToken']}"},
        str(body["auth"]["sessionId"]),
    )


def formal_headers(
    headers: dict[str, str],
    *,
    session_id: str,
    decision_id: str,
) -> dict[str, str]:
    return {
        **headers,
        "X-DreamJourney-Feature": "ownerTruthFamilyContribution",
        "X-DreamJourney-Feature-Decision-Id": decision_id,
        "X-DreamJourney-Feature-Allowed": "true",
        "X-DreamJourney-Policy-Version": "release-policy-v1",
        "X-DreamJourney-Policy-Revision": "1",
        "X-DreamJourney-Account-Generation": sha256(
            session_id.encode("utf-8")
        ).hexdigest()[:24],
    }


def seed_owner_vault(store: PostgresStore, *, vault_id: str, owner_subject_id: str) -> None:
    OwnerTruthSourceCommandService(store).create_text_source(
        command=CreateTextSourceCommand(
            command_id="formal-family-postgres-owner-bootstrap",
            source_id=str(uuid.uuid4()),
            expected_version=0,
            text="Synthetic Owner source establishes this disposable private Vault.",
            metadata={"origin": "formalFamilyPostgresSmoke"},
        ),
        context=OwnerTruthCommandContext(
            vault_id=vault_id,
            owner_subject_id=owner_subject_id,
            actor_subject_id=owner_subject_id,
        ),
    )


def persisted_grant_summary(dsn: str, *, vault_id: str, grant_id: str) -> tuple[str, dict[str, Any]]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT admission_mode, authorization_evidence
                FROM owner_truth.family_contribution_grants
                WHERE vault_id = %s AND id = %s
                """,
                (vault_id, grant_id),
            )
            row = cursor.fetchone()
    require(row is not None, "formal grant persistence is missing")
    evidence = row[1]
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    require(isinstance(evidence, dict), "authorization evidence must remain an object")
    return str(row[0]), evidence


def persisted_source_metadata(dsn: str, *, vault_id: str, source_id: str) -> dict[str, Any]:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT metadata FROM owner_truth.sources
                WHERE vault_id = %s AND id = %s
                """,
                (vault_id, source_id),
            )
            row = cursor.fetchone()
    require(row is not None, "formal contributor source persistence is missing")
    metadata = row[0]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    require(isinstance(metadata, dict), "source metadata must remain an object")
    return metadata


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_formal_family_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    previous_store = main_module.store
    previous_backend_token = main_module.BACKEND_API_TOKEN
    previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
    previous_route_mode = main_module.AUTH_ROUTE_MODE
    previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
    previous_qa_enabled = main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED
    previous_closed_pilot_owner_ids = main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS
    previous_closed_pilot_features = set(
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features
    )

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="owner-truth-family-contribution-formal-g0",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        # This smoke owns the family-authorization contract introduced by
        # migration 0072. Newer additive migrations must not turn that
        # contract check into a false failure merely because the global schema
        # head has advanced.
        require(
            "0072" in tuple(applied.get("appliedVersions") or ()),
            "formal family authorization migration must be applied",
        )

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED = False
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset()
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.discard(
            "ownerTruthFamilyContribution"
        )

        client = TestClient(main_module.app)
        owner_id, owner_headers, owner_session_id = login(
            client,
            phone="13900000272",
            nickname="Formal family owner smoke",
        )
        member_id, member_headers, _member_session_id = login(
            client,
            phone="13900000273",
            nickname="Formal family member smoke",
        )
        other_id, other_headers, _other_session_id = login(
            client,
            phone="13900000274",
            nickname="Formal family outsider smoke",
        )
        vault_id = "vault-formal-family-postgres-smoke"
        seed_owner_vault(store, vault_id=vault_id, owner_subject_id=owner_id)
        relationship = DelegatedAccessService(store).ensure_relationship(
            owner_subject_id=owner_id,
            family_member_id="formal-family-postgres-member",
            member_subject_id=member_id,
            status="accepted",
        )
        formal_path = f"/v2/vaults/{vault_id}/family-contribution/grants"
        create_payload = {
            "commandId": "formal-family-postgres-grant-001",
            "relationshipId": relationship["id"],
            "contributorSubjectId": member_id,
        }

        default_closed = client.post(formal_path, headers=owner_headers, json=create_payload)
        require(default_closed.status_code == 403, "formal grant must default closed")
        require(route_code(default_closed) == "release_policy_denied", "default denial code changed")

        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = frozenset({owner_id})
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features.add(
            "ownerTruthFamilyContribution"
        )
        owner_policy_headers = formal_headers(
            owner_headers,
            session_id=owner_session_id,
            decision_id="formal-family-postgres-decision-001",
        )
        created = client.post(formal_path, headers=owner_policy_headers, json=create_payload)
        require(created.status_code == 201, f"formal grant creation failed: {created.text}")
        grant = created.json()["grant"]
        require(grant.get("admissionMode") == "closedPilot", "grant mode must be formal")
        require("authorizationEvidence" not in created.text, "public grant leaked authorization evidence")

        persisted_mode, evidence = persisted_grant_summary(
            test_dsn,
            vault_id=vault_id,
            grant_id=str(grant["grantId"]),
        )
        require(persisted_mode == "closedPilot", "stored formal admission mode changed")
        require(
            evidence.get("feature") == "ownerTruthFamilyContribution",
            "stored evidence must bind the formal feature",
        )
        evidence_text = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        require(
            owner_headers["Authorization"] not in evidence_text
            and owner_session_id not in evidence_text,
            "stored evidence must not contain bearer/session material",
        )

        submission_id = str(uuid.uuid4())
        submitted = client.post(
            f"{formal_path}/{grant['grantId']}/submissions",
            headers=member_headers,
            json={
                "commandId": "formal-family-postgres-submit-001",
                "submissionId": submission_id,
                "expectedGrantVersion": grant["rowVersion"],
                "materialKind": "text",
                "text": "由已接受家人提交的隔离验证静态材料。",
            },
        )
        require(submitted.status_code == 202, f"formal review submission failed: {submitted.text}")
        require(
            submitted.json().get("submission", {}).get("status") == "pendingReview",
            "family material must remain pending until Owner review",
        )
        require(
            submitted.json().get("candidateExtraction") == {"status": "notRequested"},
            "family contributor must not trigger extraction before review",
        )
        require("隔离验证静态材料" not in submitted.text, "submission receipt leaked content")

        review_list = client.get(
            f"/v2/vaults/{vault_id}/family-contribution/submissions",
            headers=owner_policy_headers,
        )
        require(review_list.status_code == 200, f"Owner review list failed: {review_list.text}")
        owner_submission = next(
            item
            for item in review_list.json().get("submissions", [])
            if item.get("submissionId") == submission_id
        )
        require(
            "隔离验证静态材料" in str(owner_submission.get("text") or ""),
            "Owner review list must include the submitted text",
        )
        contributor_list = client.get(
            "/v2/family-contribution/submissions",
            headers=member_headers,
        )
        require(
            contributor_list.status_code == 200,
            f"contributor submission list failed: {contributor_list.text}",
        )
        require(
            "隔离验证静态材料" not in contributor_list.text,
            "contributor status list must not echo submitted material",
        )

        accepted = client.post(
            f"/v2/vaults/{vault_id}/family-contribution/submissions/{submission_id}/decisions",
            headers=owner_policy_headers,
            json={
                "commandId": "formal-family-postgres-review-001",
                "expectedVersion": 1,
                "decision": "accepted",
                "reason": "ownerAccepted",
            },
        )
        require(accepted.status_code == 200, f"Owner review failed: {accepted.text}")
        accepted_payload = accepted.json()
        require(
            accepted_payload.get("submission", {}).get("status") == "accepted",
            "accepted review must persist the terminal state",
        )
        require(
            accepted_payload.get("candidateExtraction") == {"status": "requested"},
            "only Owner acceptance may request Candidate extraction",
        )
        source_id = str(accepted_payload.get("source", {}).get("sourceId") or "")
        require(bool(source_id), "accepted text contribution must create a Source")
        metadata = persisted_source_metadata(test_dsn, vault_id=vault_id, source_id=source_id)
        require(
            metadata.get("origin") == "familyContributionReview"
            and metadata.get("ownerReviewed") is True
            and metadata.get("familyContributionSubmissionId") == submission_id,
            "persisted source lost its reviewed contribution boundary",
        )

        outsider = client.post(
            f"{formal_path}/{grant['grantId']}/submissions",
            headers=other_headers,
            json={
                "commandId": "formal-family-postgres-submit-outsider",
                "submissionId": str(uuid.uuid4()),
                "expectedGrantVersion": grant["rowVersion"],
                "materialKind": "text",
                "text": "Outsider must never contribute.",
            },
        )
        require(outsider.status_code == 403, "ungranted contributor must be denied")
        require(
            route_code(outsider) == "familyContributionGrantContributorMismatch",
            "ungranted contributor denial changed",
        )

        no_vault_read = client.get(
            f"/v2/vaults/{vault_id}/candidates",
            headers=member_headers,
        )
        require(no_vault_read.status_code in {401, 403, 404}, "contributor received Vault read")

        revoked = client.post(
            f"{formal_path}/{grant['grantId']}/revoke",
            headers=owner_policy_headers,
            json={"commandId": "formal-family-postgres-revoke-001", "expectedVersion": 1},
        )
        require(revoked.status_code == 200, f"formal revoke failed: {revoked.text}")
        replay = client.post(
            f"{formal_path}/{grant['grantId']}/submissions",
            headers=member_headers,
            json={
                "commandId": "formal-family-postgres-submit-after-revoke",
                "submissionId": str(uuid.uuid4()),
                "expectedGrantVersion": 1,
                "materialKind": "text",
                "text": "Revoked contribution must not be admitted.",
            },
        )
        require(replay.status_code == 409, "revoked grant must block submission")
        require(route_code(replay) == "familyContributionGrantInactive", "revoke denial changed")
        withdrawn = client.get(
            "/v2/family-contribution/submissions",
            headers=member_headers,
        )
        withdrawn_submission = next(
            item
            for item in withdrawn.json().get("submissions", [])
            if item.get("submissionId") == submission_id
        )
        require(
            withdrawn_submission.get("status") == "withdrawn",
            "revocation must immediately withdraw prior contribution status",
        )
        require(
            "隔离验证静态材料" not in withdrawn.text,
            "withdrawn contributor projection must remain material-free",
        )

        print(
            json.dumps(
                {
                    "schemaHead": applied.get("appliedHead"),
                    "defaultClosed": True,
                    "formalGrant": True,
                    "ownerReviewRequired": True,
                    "acceptedSourceOnly": True,
                    "submissionContentHiddenFromContributor": True,
                    "outsiderDenied": True,
                    "privateReadDenied": True,
                    "revocationWithdrawsContribution": True,
                    "ownerId": owner_id,
                    "memberId": member_id,
                    "outsiderId": other_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    finally:
        main_module.store = previous_store
        main_module.BACKEND_API_TOKEN = previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = previous_ownership_mode
        main_module.OWNER_TRUTH_FAMILY_CONTRIBUTION_QA_ENABLED = previous_qa_enabled
        main_module.RELEASE_POLICY_CLOSED_PILOT_OWNER_IDS = previous_closed_pilot_owner_ids
        main_module.RELEASE_POLICY_SERVICE.closed_pilot_enabled_features = (
            previous_closed_pilot_features
        )
        if store is not None:
            store.close_pool()
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
