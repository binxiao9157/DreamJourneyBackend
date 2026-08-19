#!/usr/bin/env python3
"""Prove PC-A5 relationship termination semantics in disposable Postgres."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.core.config import settings
from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
)
from app.services.delegated_access import (
    AccessGrantCommand,
    AccessGrantPurpose,
    DelegatedAccessService,
    GrantOperation,
    ResourceScopeType,
)
from app.services.family_relationship_termination import (
    FamilyRelationshipTerminationCommand,
    FamilyRelationshipTerminationService,
)
from app.services.owner_truth_family_contribution import (
    CreateFamilyContributionGrantCommand,
    OwnerTruthFamilyContributionService,
    ReviewFamilyContributionSubmissionCommand,
    SubmitFamilyContributionForReviewCommand,
)
from app.services.owner_truth_source import OwnerTruthSourceCommandService
from app.services.postgres_store import PostgresStore
from app.services.store_factory import close_store, open_store


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


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", settings.database_url).strip()
    require(base_dsn, "DATABASE_URL is required")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_family_termination_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    store: PostgresStore | None = None

    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="family-relationship-termination-pc-a5",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require(applied["appliedHead"] == "0098", "PC-A5 migration must be current head")

        store = PostgresStore(dsn=test_dsn, pool_min_size=1, pool_max_size=8)
        open_store(store)
        now = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
        owner = "postgres-family-owner"
        member = "postgres-family-member"
        family_member_id = "postgres-family-member-record"
        store.add_family_member(
            owner,
            {
                "id": family_member_id,
                "name": "家庭成员",
                "relation": "亲属",
                "phone": "13800139102",
                "accessStatus": "active",
                "invitationStatus": "accepted",
            },
        )
        delegated = DelegatedAccessService(store, now_provider=lambda: now)
        relationship = delegated.ensure_relationship(
            owner_subject_id=owner,
            family_member_id=family_member_id,
            member_subject_id=member,
            status="accepted",
        )
        owner_context = OwnerTruthCommandContext(
            vault_id=owner,
            owner_subject_id=owner,
            actor_subject_id=owner,
        )
        member_context = OwnerTruthCommandContext(
            vault_id=owner,
            owner_subject_id=owner,
            actor_subject_id=member,
        )
        OwnerTruthSourceCommandService(store).create_text_source(
            command=CreateTextSourceCommand(
                command_id="postgres-owner-bootstrap-source",
                source_id=str(uuid.uuid4()),
                expected_version=0,
                text="Owner private source establishes the Vault.",
                metadata={"origin": "pc-a5-postgres-smoke"},
            ),
            context=owner_context,
        )
        access_grant = delegated.grant_access(
            AccessGrantCommand(
                grantorSubjectId=owner,
                relationshipId=relationship["id"],
                granteeSubjectId=member,
                purpose=AccessGrantPurpose.FAMILY_PERSONA,
                resourceType=ResourceScopeType.FAMILY_MEMBER,
                resourceId=family_member_id,
                operations=[GrantOperation.READ],
            )
        )
        contributions = OwnerTruthFamilyContributionService(store, now_provider=lambda: now)
        contribution_grant = contributions.create_grant(
            command=CreateFamilyContributionGrantCommand(
                command_id="postgres-family-contribution-grant",
                relationship_id=relationship["id"],
                contributor_subject_id=member,
            ),
            context=owner_context,
        ).grant
        pending_id = str(uuid.uuid4())
        contributions.submit_for_review(
            command=SubmitFamilyContributionForReviewCommand(
                command_id="postgres-family-pending",
                submission_id=pending_id,
                grant_id=contribution_grant["id"],
                expected_grant_version=contribution_grant["rowVersion"],
                material_kind="text",
                text="这条贡献尚未接受。",
            ),
            context=member_context,
        )
        accepted_id = str(uuid.uuid4())
        contributions.submit_for_review(
            command=SubmitFamilyContributionForReviewCommand(
                command_id="postgres-family-accepted",
                submission_id=accepted_id,
                grant_id=contribution_grant["id"],
                expected_grant_version=contribution_grant["rowVersion"],
                material_kind="text",
                text="这条贡献已经接受。",
            ),
            context=member_context,
        )
        accepted = contributions.review_submission(
            command=ReviewFamilyContributionSubmissionCommand(
                command_id="postgres-family-accepted-review",
                submission_id=accepted_id,
                expected_version=1,
                decision="accepted",
            ),
            context=owner_context,
        )
        accepted_source_id = str(accepted.submission["sourceId"])
        termination = FamilyRelationshipTerminationService(store, now_provider=lambda: now)

        def terminate(actor: str, command_id: str):
            return termination.terminate(
                command=FamilyRelationshipTerminationCommand(
                    command_id=command_id,
                    relationship_id=relationship["id"],
                    actor_subject_id=actor,
                    expected_epoch=relationship["relationshipEpoch"],
                    second_confirmation=True,
                    publication_grant_action="preserve",
                )
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(terminate, owner, "postgres-owner-terminate"),
                executor.submit(terminate, member, "postgres-member-terminate"),
            ]
            results = [future.result() for future in futures]

        require(
            sorted(result.outcome for result in results) == ["alreadyTerminated", "terminated"],
            "concurrent participant commands must converge once",
        )
        require(
            sum(result.receipt["revokedAccessGrantCount"] for result in results) == 1,
            "access grant must be revoked exactly once",
        )
        relationship_after = store.get_family_relationship(owner, relationship["id"])
        require(relationship_after is not None and relationship_after["status"] == "revoked", "relationship must be revoked")
        require(
            next(item for item in store.list_access_grants(owner_subject_id=owner) if item["id"] == access_grant["id"])["status"] == "revoked",
            "ShareGrant-compatible access authority must be revoked",
        )
        contribution_after = store.get_owner_truth_family_contribution_grant(
            owner,
            contribution_grant["id"],
        )
        require(contribution_after is not None and contribution_after["status"] == "revoked", "contribution grant must be revoked")
        disposal = store.list_family_contribution_disposal_queue(relationship_id=relationship["id"])
        require(len(disposal) == 1 and disposal[0]["submissionId"] == pending_id, "only pending contribution must enter disposal")
        with psycopg.connect(test_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT state FROM owner_truth.sources WHERE vault_id = %s AND id = %s",
                    (owner, accepted_source_id),
                )
                source_row = cursor.fetchone()
                cursor.execute(
                    "SELECT status FROM owner_truth.family_contribution_submissions WHERE id = %s",
                    (pending_id,),
                )
                pending_row = cursor.fetchone()
        require(source_row is not None and source_row[0] == "active", "accepted Source must remain active")
        require(pending_row is not None and pending_row[0] == "withdrawn", "pending contribution must be hidden")

        print(
            "familyRelationshipTerminationPcA5=true concurrentAtomic=true "
            "accountsDeleted=false acceptedSourceDisposition=retainedWithProvenance "
            "publicationGrantDisposition=preservedRequiresOwnerAction"
        )
    finally:
        if store is not None:
            close_store(store)
        try:
            drop_database(admin_dsn, database_name)
        except Exception as error:  # pragma: no cover - cleanup evidence only
            print(f"warning: disposable database cleanup failed: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
