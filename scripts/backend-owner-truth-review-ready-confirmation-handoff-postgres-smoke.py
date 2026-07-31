#!/usr/bin/env python3
"""Exercise the formal read-only reviewReady -> confirmation inbox handoff.

The smoke uses a disposable Postgres database and the released policy capture
headers.  It verifies discovery only: no Candidate detail, confirmation,
MemoryVersion, Projection, worker or Provider work is allowed to occur.
"""

from __future__ import annotations

import importlib.util
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

import app.main as main_module
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.postgres_store import PostgresStore


def load_formal_confirmation_helpers() -> Any:
    """Reuse the existing disposable-DB and policy-header contract helpers."""

    path = ROOT_DIR / "scripts/backend-owner-truth-interview-confirmation-formal-postgres-smoke.py"
    spec = importlib.util.spec_from_file_location("formal_confirmation_smoke_helpers", path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository fixture failure
        raise RuntimeError("formal confirmation smoke helpers are unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FORMAL = load_formal_confirmation_helpers()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def route_code(response: Any) -> str:
    detail = response.json().get("detail") if response.content else None
    return str(detail.get("code") or "") if isinstance(detail, dict) else ""


def side_effect_counts(dsn: str) -> dict[str, int]:
    """Read only record counts; never inspect any private payload."""

    queries = {
        "decisionReceipts": "SELECT COUNT(*) FROM owner_truth.decision_receipts",
        "memoryVersions": "SELECT COUNT(*) FROM owner_truth.memory_versions",
        "memoryProjectionEntries": "SELECT COUNT(*) FROM owner_truth.memory_projection_entries",
        "providerEffects": "SELECT COUNT(*) FROM async_effects.provider_effects",
    }
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            return {
                name: int(cursor.execute(query).fetchone()[0])
                for name, query in queries.items()
            }


def source_id_for_batch(dsn: str, *, vault_id: str, review_batch_id: str) -> str:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            row = cursor.execute(
                """
                SELECT source_id::text
                FROM owner_truth.interview_review_batch_candidate_admissions
                WHERE vault_id = %s AND review_batch_id = %s
                """,
                (vault_id, review_batch_id),
            ).fetchone()
    require(row is not None, "seeded review batch must retain an admitted Source")
    return str(row[0])


def mark_source_redacted(dsn: str, *, vault_id: str, review_batch_id: str) -> None:
    source_id = source_id_for_batch(dsn, vault_id=vault_id, review_batch_id=review_batch_id)
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE owner_truth.sources SET state = 'redacted' WHERE vault_id = %s AND id = %s",
                (vault_id, source_id),
            )
        connection.commit()


def make_source_epoch_stale(dsn: str, *, vault_id: str, review_batch_id: str) -> None:
    source_id = source_id_for_batch(dsn, vault_id=vault_id, review_batch_id=review_batch_id)
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE owner_truth.sources SET authority_epoch = authority_epoch + 1 "
                "WHERE vault_id = %s AND id = %s",
                (vault_id, source_id),
            )
        connection.commit()


def assert_value_minimized_status(
    *,
    response: Any,
    vault_id: str,
    review_batch_id: str,
) -> None:
    require(response.status_code == 200, f"formal status read failed: {response.text}")
    require(response.headers.get("cache-control") == "no-store", "status must remain no-store")
    body = response.json()
    require(
        body
        == {
            "schemaVersion": "owner-truth-interview-candidate-proposal-status-v1",
            "vaultId": vault_id,
            "reviewBatch": {"reviewBatchId": review_batch_id, "state": "acknowledged"},
            "candidateProposal": {"status": "admitted"},
            "source": {"status": "admitted"},
            "candidateExtraction": {"status": "succeeded"},
            "effectExecution": {"status": "disabled"},
            "candidateReview": {"status": "reviewReady"},
        },
        "status must be exact and value-minimized for the selected review batch",
    )
    rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "candidateId",
        "sourceId",
        "content",
        "receipt",
        "authorization",
        "provider",
        "Synthetic",
    ):
        require(forbidden not in rendered, f"status leaked private field or value: {forbidden}")


def assert_value_minimized_inbox(
    *,
    response: Any,
    vault_id: str,
    expected_batch_ids: set[str],
) -> None:
    require(response.status_code == 200, f"formal confirmation inbox failed: {response.text}")
    require(response.headers.get("cache-control") == "no-store", "inbox must remain no-store")
    body = response.json()
    require(
        body.get("schemaVersion") == "owner-truth-interview-candidate-confirmation-inbox-v1"
        and body.get("vaultId") == vault_id,
        "inbox must return its typed owner/vault envelope",
    )
    confirmations = body.get("confirmations")
    require(isinstance(confirmations, list), "inbox confirmations must be a list")
    returned_ids = {str(item.get("reviewBatchId") or "") for item in confirmations}
    require(returned_ids == expected_batch_ids, "inbox must contain only live owner/vault review batches")
    require(
        all(
            item == {
                "reviewBatchId": item["reviewBatchId"],
                "readiness": "reviewReady",
                "batchCandidateCount": 1,
                "singleCandidateCount": 0,
            }
            for item in confirmations
        ),
        "inbox must expose only content-free review-ready summaries",
    )
    rendered = json.dumps(body, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "candidateId",
        "sourceId",
        "content",
        "receipt",
        "admissionId",
        "authorization",
        "provider",
        "Synthetic",
    ):
        require(forbidden not in rendered, f"inbox leaked private field or value: {forbidden}")


def main() -> None:
    require(
        os.environ.get("DREAMJOURNEY_OWNER_TRUTH_REVIEW_READY_HANDOFF_SMOKE") == "1",
        "DREAMJOURNEY_OWNER_TRUTH_REVIEW_READY_HANDOFF_SMOKE=1 is required",
    )
    base_dsn = os.environ.get("OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL", "").strip()
    require(base_dsn, "OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL is required")
    parameters = FORMAL.conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "OWNER_TRUTH_FORMAL_SMOKE_ADMIN_DATABASE_URL needs a database user")
    admin_dsn = FORMAL.dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_review_ready_handoff_smoke_{uuid.uuid4().hex[:12]}"
    test_dsn = FORMAL.dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    previous_store = main_module.store
    previous_backend_token = main_module.BACKEND_API_TOKEN
    previous_legacy_phone_login = main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED
    previous_route_mode = main_module.AUTH_ROUTE_MODE
    previous_ownership_mode = main_module.AUTH_OWNERSHIP_MODE
    policy_service = main_module.RELEASE_POLICY_SERVICE
    previous_visible = set(policy_service._CLOSED_PILOT_OWNER_VISIBLE)

    try:
        FORMAL.create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="review-ready-confirmation-handoff-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        require(migrator.apply()["appliedHead"], "temporary database migrations must apply")
        require(migrator.verify()["status"] == "ready", "temporary database migration head must verify")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=3)
        store.open_pool(wait=True)
        main_module.store = store
        main_module.BACKEND_API_TOKEN = ""
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = True
        main_module.AUTH_ROUTE_MODE = "enforce"
        main_module.AUTH_OWNERSHIP_MODE = "enforce"
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible | {"ownerTruthCandidateReview"}

        with TestClient(main_module.app) as client:
            owner_a_id, owner_a_headers, owner_a_session_id = FORMAL.login(client, phone="13900000381")
            owner_b_id, owner_b_headers, owner_b_session_id = FORMAL.login(client, phone="13900000382")

            owner_a_vault_id = "vault-review-ready-handoff-owner-a"
            owner_b_vault_id = "vault-review-ready-handoff-owner-b"
            selected_batch_id, _ = FORMAL.seed_reviewable_batch(
                test_dsn, vault_id=owner_a_vault_id, owner_subject_id=owner_a_id
            )
            second_batch_id, _ = FORMAL.seed_reviewable_batch(
                test_dsn, vault_id=owner_a_vault_id, owner_subject_id=owner_a_id
            )
            redacted_batch_id, _ = FORMAL.seed_reviewable_batch(
                test_dsn, vault_id=owner_a_vault_id, owner_subject_id=owner_a_id
            )
            stale_epoch_batch_id, _ = FORMAL.seed_reviewable_batch(
                test_dsn, vault_id=owner_a_vault_id, owner_subject_id=owner_a_id
            )
            owner_b_batch_id, _ = FORMAL.seed_reviewable_batch(
                test_dsn, vault_id=owner_b_vault_id, owner_subject_id=owner_b_id
            )
            mark_source_redacted(test_dsn, vault_id=owner_a_vault_id, review_batch_id=redacted_batch_id)
            make_source_epoch_stale(test_dsn, vault_id=owner_a_vault_id, review_batch_id=stale_epoch_batch_id)
            baseline_effects = side_effect_counts(test_dsn)
            require(
                baseline_effects == {
                    "decisionReceipts": 0,
                    "memoryVersions": 0,
                    "memoryProjectionEntries": 0,
                    "providerEffects": 0,
                },
                "fixture must start without DecisionReceipt, MemoryVersion, Projection or ProviderEffect",
            )

            status_path = (
                f"/v2/vaults/{owner_a_vault_id}/interview-review-batches/"
                f"{selected_batch_id}/candidate-proposal/status"
            )
            inbox_path = f"/v2/vaults/{owner_a_vault_id}/interview-candidate-confirmations"
            status = client.get(
                status_path,
                headers=FORMAL.formal_headers(
                    owner_a_headers,
                    session_id=owner_a_session_id,
                    decision_id="review-ready-handoff-status-a",
                ),
            )
            assert_value_minimized_status(
                response=status,
                vault_id=owner_a_vault_id,
                review_batch_id=selected_batch_id,
            )
            inbox = client.get(
                inbox_path,
                headers=FORMAL.formal_headers(
                    owner_a_headers,
                    session_id=owner_a_session_id,
                    decision_id="review-ready-handoff-inbox-a",
                ),
            )
            assert_value_minimized_inbox(
                response=inbox,
                vault_id=owner_a_vault_id,
                expected_batch_ids={selected_batch_id, second_batch_id},
            )

            owner_b_denied = client.get(
                inbox_path,
                headers=FORMAL.formal_headers(
                    owner_b_headers,
                    session_id=owner_b_session_id,
                    decision_id="review-ready-handoff-owner-b-denied",
                ),
            )
            require(
                owner_b_denied.status_code == 403
                and route_code(owner_b_denied) == "ownerTruthInterviewCandidateReviewDenied",
                "another authenticated Owner must not read this Owner Vault inbox",
            )
            qa_bypass_denied = client.get(
                inbox_path,
                headers={**owner_a_headers, "X-DreamJourney-QA-Owner-Truth": "1"},
            )
            require(
                qa_bypass_denied.status_code == 403
                and route_code(qa_bypass_denied) == "release_policy_denied",
                "QA header must not replace the formal confirmation inbox policy",
            )
            require(
                owner_b_batch_id not in json.dumps(inbox.json(), ensure_ascii=False),
                "another Owner Vault batch must not be discoverable",
            )
            require(
                side_effect_counts(test_dsn) == baseline_effects,
                "read-only status/inbox handoff must not create DecisionReceipt, MemoryVersion, Projection or ProviderEffect",
            )

        print(
            "review-ready confirmation handoff postgres smoke passed "
            "formalPolicyOnly=true statusValueMinimized=true inboxOwnerVaultIsolated=true "
            "staleAndRedactedFiltered=true ownerBForbidden=true qaBypassDenied=true "
            "noDecisionReceiptMemoryVersionProjectionOrProviderEffect=true"
        )
    finally:
        main_module.store = previous_store
        main_module.BACKEND_API_TOKEN = previous_backend_token
        main_module.AUTH_LEGACY_PHONE_LOGIN_ENABLED = previous_legacy_phone_login
        main_module.AUTH_ROUTE_MODE = previous_route_mode
        main_module.AUTH_OWNERSHIP_MODE = previous_ownership_mode
        policy_service._CLOSED_PILOT_OWNER_VISIBLE = previous_visible
        if store is not None:
            store.close_pool()
        try:
            FORMAL.drop_database(admin_dsn, database_name)
        except Exception as exc:  # pragma: no cover - cleanup diagnostics only
            print(f"warning: failed to drop temporary database {database_name}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
