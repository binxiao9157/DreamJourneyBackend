#!/usr/bin/env python3
"""Exercise P0-S3 external data-rights receipts in a disposable Postgres DB.

The smoke intentionally performs no Provider call.  It proves the durable
contract instead: owner fencing, append-only observations, replay idempotency,
state progression and the value-minimized evidence projection.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
import sys
import uuid
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.services.data_rights_contract import DataRightsRequestAuthority
from app.services.data_rights_evidence_projection import (
    build_data_rights_evidence_projection,
)
from app.services.data_rights_external_effect_receipts import (
    DataRightsExternalEffectReceipt,
)
from app.services.data_rights_external_effect_reconciler import (
    DataRightsExternalEffectAdapterObservation,
    DataRightsExternalEffectReconciler,
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
            cursor.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


def receipt(
    *,
    request_id: str,
    owner_hash: str,
    state: str,
    observed_at: str,
) -> DataRightsExternalEffectReceipt:
    return DataRightsExternalEffectReceipt(
        request_id=request_id,
        owner_subject_hash=owner_hash,
        domain="providerVoice",
        effect_identity_hash=sha256(b"external-effect-smoke-voice").hexdigest(),
        state=state,
        provider_receipt_present=state == "completed",
        reason_code="providerObservation",
        observed_at=observed_at,
        evidence_hash=(
            sha256(f"provider-evidence:{state}".encode("utf-8")).hexdigest()
            if state == "completed"
            else None
        ),
    )


def record(store: PostgresStore, item: DataRightsExternalEffectReceipt, *, command_id: str) -> str:
    with store.request_unit_of_work(
        correlation_id=f"external-effect-receipt-smoke:{command_id}",
        command_id=command_id,
    ):
        return str(store.record_rights_external_effect_receipt(item)["outcome"])


def receipt_count(dsn: str, request_id: str) -> int:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM rights_external_effect_receipts WHERE request_id = %s",
                (request_id,),
            )
            return int(cursor.fetchone()[0])


def expect_rejected(operation, message: str) -> None:
    rejected = False
    try:
        operation()
    except Exception:
        rejected = True
    require(rejected, message)


class FakeReconciliationAdapter:
    def __init__(self) -> None:
        self.calls = []

    def observe(self, *, domain: str, effect_identity_hash: str, attempt: int):
        self.calls.append((domain, effect_identity_hash, attempt))
        if domain == "objectStorage":
            return DataRightsExternalEffectAdapterObservation(
                state="completed",
                provider_receipt_present=True,
                reason_code="externalEffectProviderCompleted",
                evidence_hash=sha256(b"postgres-object-deletion-receipt").hexdigest(),
            )
        if domain == "notificationDelivery":
            return DataRightsExternalEffectAdapterObservation(
                state="unsupported",
                provider_receipt_present=False,
                reason_code="externalEffectAdapterUnsupported",
            )
        raise TimeoutError("raw Provider detail must not persist")


def exercise(dsn: str) -> None:
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=4)
    store.open_pool(wait=True)
    try:
        request = DataRightsRequestAuthority().create_request(
            command_id="external-effect-postgres-smoke-command",
            subject_id="external-effect-postgres-smoke-owner",
            identity_proof={"kind": "reauthenticated"},
            payload={"action": "account.delete", "scope": ["voice", "archive"]},
            now="2026-08-05T09:00:00+00:00",
        ).request
        created = store.create_rights_request(request)
        require(created["outcome"] == "created", "rights request must persist")

        accepted = receipt(
            request_id=request.request_id,
            owner_hash=request.subject_hash,
            state="accepted",
            observed_at="2026-08-05T09:01:00+00:00",
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = set(
                executor.map(
                    lambda index: record(store, accepted, command_id=f"accepted-{index}"),
                    (1, 2),
                )
            )
        require(outcomes == {"appended", "deduplicated"}, "same observation must deduplicate")
        require(receipt_count(dsn, request.request_id) == 1, "one accepted receipt must persist")

        completed = receipt(
            request_id=request.request_id,
            owner_hash=request.subject_hash,
            state="completed",
            observed_at="2026-08-05T09:02:00+00:00",
        )
        require(record(store, completed, command_id="completed") == "appended", "completion is append-only")
        require(record(store, completed, command_id="completed-replay") == "deduplicated", "completion replay deduplicates")
        completed_later = receipt(
            request_id=request.request_id,
            owner_hash=request.subject_hash,
            state="completed",
            observed_at="2026-08-05T09:02:30+00:00",
        )
        require(
            record(store, completed_later, command_id="completed-later-replay") == "deduplicated",
            "same logical callback with a later timestamp must deduplicate",
        )
        require(receipt_count(dsn, request.request_id) == 2, "accepted and completed facts must remain")

        cross_account = receipt(
            request_id=request.request_id,
            owner_hash=sha256(b"other-owner").hexdigest(),
            state="completed",
            observed_at="2026-08-05T09:03:00+00:00",
        )
        expect_rejected(
            lambda: record(store, cross_account, command_id="cross-account"),
            "cross-account receipt must be rejected",
        )
        require(receipt_count(dsn, request.request_id) == 2, "rejection must not append")

        observations = store.list_rights_external_effect_receipts(request.request_id)
        require(
            [str(item.get("state")) for item in observations] == ["accepted", "completed"],
            "observations must preserve progression",
        )
        serialized_observations = json.dumps(observations, ensure_ascii=False, sort_keys=True)
        require(request.subject_hash not in serialized_observations, "query must not expose owner hash")
        require("effectIdentityHash" not in serialized_observations, "query must not expose effect identity")

        evidence = build_data_rights_evidence_projection(
            store.summarize_rights_request(request.request_id) or {},
            access_revocation_events=[
                {
                    "eventType": "RightsAccessRevoked",
                    "providerCapabilityState": "revoked",
                    "status": "pending",
                    "createdAt": "2026-08-05T09:00:30+00:00",
                }
            ],
            linked_effect_observations=observations,
            now="2026-08-05T09:04:00+00:00",
        )
        voice = next(
            item
            for item in evidence["externalEffects"]["domains"]
            if item["domain"] == "providerVoice"
        )
        require(voice["status"] == "completed", "upstream-backed completion must project")
        require(voice["receiptState"] == "partial", "accepted history remains an honest receipt gap")
        evidence_serialized = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        require(request.subject_hash not in evidence_serialized, "evidence projection must not expose owner hash")
        require("external-effect-smoke-voice" not in evidence_serialized, "evidence projection must not expose effect material")

        expect_rejected(
            lambda: _mutate_receipt(dsn, accepted.receipt_id),
            "receipt rows must be append-only",
        )

        reconcile_request = DataRightsRequestAuthority().create_request(
            command_id="external-effect-reconciler-postgres-command",
            subject_id="external-effect-reconciler-postgres-owner",
            identity_proof={"kind": "reauthenticated"},
            payload={"action": "account.delete", "scope": ["all"]},
            now="2026-08-05T10:00:00+00:00",
        ).request
        store.create_rights_request(reconcile_request)
        reconciler = DataRightsExternalEffectReconciler(store, max_attempts=2)
        adapter = FakeReconciliationAdapter()
        first_reconcile = reconciler.reconcile(
            request_id=reconcile_request.request_id,
            access_revocation_status="revoked",
            adapter=adapter,
            now="2026-08-05T10:01:00+00:00",
        )
        second_reconcile = reconciler.reconcile(
            request_id=reconcile_request.request_id,
            access_revocation_status="revoked",
            adapter=adapter,
            now="2026-08-05T10:02:00+00:00",
        )
        require(
            first_reconcile["status"] == "attentionRequired",
            "first timeout pass must remain retryable",
        )
        require(
            second_reconcile["status"] == "manualReviewRequired",
            "retry exhaustion must create manual-review evidence",
        )
        require(
            "raw Provider detail must not persist"
            not in json.dumps(second_reconcile, ensure_ascii=False, sort_keys=True),
            "raw Provider failures must stay outside reconciliation evidence",
        )
        print(
            "Data-rights external effect receipt Postgres smoke passed "
            "(owner fence, replay, append-only, reconciliation and redacted projection verified)."
        )
    finally:
        store.close_pool()


def _mutate_receipt(dsn: str, receipt_id: str) -> None:
    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE rights_external_effect_receipts SET state = 'failed' WHERE id = %s",
                (receipt_id,),
            )
        connection.commit()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", "").strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_rights_external_effect_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="data-rights-external-effect-receipt-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0078" in applied["appliedVersions"], "0078 must be applied")
        require(
            applied["appliedHead"] == verified["expectedHead"],
            "all current migrations must apply before the receipt smoke",
        )
        exercise(test_dsn)
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":
    main()
