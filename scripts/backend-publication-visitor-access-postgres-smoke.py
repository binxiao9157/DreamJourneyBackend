#!/usr/bin/env python3
"""Exercise P2-S2b Visitor admission and projection-only reads in Postgres.

The smoke creates a throwaway database, applies every migration, and proves
that the default-off internal contract binds a ShareGrant to one independent
public projection. It proves the admitted Visitor can read only that projection
and loses access after revocation. It never touches the configured application
database and does not create any public reader, deep link, or provider answer
route.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import runpy
import sys
import uuid

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.db.migrator import PostgresMigrator, default_migrations_dir
from app.domain.publication.share_grant_session import (
    PublicationAdultVerificationState,
    PublicationVisitorRelationshipOrigin,
)
from app.services.postgres_store import PostgresStore
from app.services.publication_visitor_access import (
    PublicationGrantIssueCommand,
    PublicationGrantRevokeCommand,
    PublicationVisitorAccessConflict,
    PublicationVisitorAccessService,
    PublicationVisitorAccessUnavailable,
    PublicationVisitorEligibility,
    PublicationVisitorSessionCommand,
    StaticPublicationVisitorEligibilityResolver,
)
from app.services.publication_visitor_reader import PublicationVisitorReaderService


_authority_smoke = runpy.run_path(
    str(ROOT_DIR / "scripts" / "backend-publication-authority-postgres-smoke.py")
)
create_database = _authority_smoke["create_database"]
drop_database = _authority_smoke["drop_database"]
dsn_for_database = _authority_smoke["dsn_for_database"]
seed_publishable_memory = _authority_smoke["seed_publishable_memory"]
create_draft = _authority_smoke["create_draft"]
confirm_draft = _authority_smoke["confirm_draft"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_rejected(operation, message: str) -> None:
    try:
        operation()
    except (PublicationVisitorAccessUnavailable, PublicationVisitorAccessConflict):
        return
    raise AssertionError(message)


def visitor_service(store: PostgresStore, *, visitor_subject_id: str) -> PublicationVisitorAccessService:
    return PublicationVisitorAccessService(
        store.publication_visitor_access_repository(),
        enabled=True,
        eligibility_resolver=StaticPublicationVisitorEligibilityResolver(
            {
                visitor_subject_id: PublicationVisitorEligibility(
                    adult_verification=PublicationAdultVerificationState.VERIFIED,
                    relationship_origin=PublicationVisitorRelationshipOrigin.DIRECT,
                )
            }
        ),
    )


def visitor_reader_service(
    store: PostgresStore,
    *,
    visitor_subject_id: str,
) -> PublicationVisitorReaderService:
    return PublicationVisitorReaderService(
        store.publication_visitor_access_repository(),
        enabled=True,
        eligibility_resolver=StaticPublicationVisitorEligibilityResolver(
            {
                visitor_subject_id: PublicationVisitorEligibility(
                    adult_verification=PublicationAdultVerificationState.VERIFIED,
                    relationship_origin=PublicationVisitorRelationshipOrigin.DIRECT,
                )
            }
        ),
    )


def issue(
    store: PostgresStore,
    *,
    seed,
    publication_id: str,
    publication_version_id: str,
    visitor_subject_id: str,
    use_limit: int,
):
    command = PublicationGrantIssueCommand(
        command_id=str(uuid.uuid4()),
        publication_id=publication_id,
        publication_version_id=publication_version_id,
        grantee_subject_id=visitor_subject_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        use_limit=use_limit,
    )
    with store.request_unit_of_work(
        correlation_id=f"publication-visitor-access-smoke:issue:{publication_id}",
        command_id=command.command_id,
    ):
        return visitor_service(store, visitor_subject_id=visitor_subject_id).issue_grant(
            context=seed.context,
            command=command,
        )


def admit(
    store: PostgresStore,
    *,
    visitor_subject_id: str,
    grant_id: str,
    grant_credential: str,
    suffix: str,
):
    command = PublicationVisitorSessionCommand(
        command_id=str(uuid.uuid4()),
        grant_credential=grant_credential,
        session_credential=f"visitor-session-{suffix}-" + "s" * 32,
    )
    with store.request_unit_of_work(
        correlation_id=f"publication-visitor-access-smoke:admit:{grant_id}:{suffix}",
        command_id=command.command_id,
    ):
        return visitor_service(store, visitor_subject_id=visitor_subject_id).admit_visitor(
            visitor_subject_id=visitor_subject_id,
            grant_id=grant_id,
            command=command,
        )


def revoke(store: PostgresStore, *, seed, grant_id: str, visitor_subject_id: str):
    command = PublicationGrantRevokeCommand(command_id=str(uuid.uuid4()), grant_id=grant_id)
    with store.request_unit_of_work(
        correlation_id=f"publication-visitor-access-smoke:revoke:{grant_id}",
        command_id=command.command_id,
    ):
        return visitor_service(store, visitor_subject_id=visitor_subject_id).revoke_grant(
            context=seed.context,
            command=command,
        )


def read_projection(
    store: PostgresStore,
    *,
    visitor_subject_id: str,
    session_id: str,
    session_credential: str,
):
    with store.request_unit_of_work(
        correlation_id=f"publication-visitor-access-smoke:read:{session_id}",
        command_id=None,
    ):
        return visitor_reader_service(
            store,
            visitor_subject_id=visitor_subject_id,
        ).read_projection(
            visitor_subject_id=visitor_subject_id,
            session_id=session_id,
            session_credential=session_credential,
        )


def exercise(dsn: str) -> None:
    store = PostgresStore(dsn=dsn, pool_min_size=1, pool_max_size=6)
    store.open_pool(wait=True)
    try:
        seed = seed_publishable_memory(dsn, label="visitor-access")
        draft = create_draft(store, seed)
        publication = confirm_draft(store, seed, draft, command_id=str(uuid.uuid4()))
        visitor_subject_id = "publication-visitor-smoke"

        issued = issue(
            store,
            seed=seed,
            publication_id=publication.publication_id,
            publication_version_id=publication.publication_version_id,
            visitor_subject_id=visitor_subject_id,
            use_limit=1,
        )
        require(issued.outcome == "created", "ShareGrant must be issued")
        require(bool(issued.grant_credential), "raw grant credential must be returned once")
        assert issued.grant_credential is not None

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                (
                    f"concurrent-{index}",
                    executor.submit(
                        admit,
                        store,
                        visitor_subject_id=visitor_subject_id,
                        grant_id=issued.grant_id,
                        grant_credential=issued.grant_credential,
                        suffix=f"concurrent-{index}",
                    ),
                )
                for index in range(2)
            ]
            admitted = 0
            rejected = 0
            admitted_session_id = ""
            admitted_session_credential = ""
            for suffix, future in futures:
                try:
                    result = future.result()
                    admitted += 1
                    require(result.use_remaining == 0, "admission must consume the final use")
                    admitted_session_id = result.session_id
                    admitted_session_credential = f"visitor-session-{suffix}-" + "s" * 32
                except (PublicationVisitorAccessUnavailable, PublicationVisitorAccessConflict):
                    rejected += 1
        require(admitted == 1 and rejected == 1, "usage CAS must admit exactly one Visitor")
        require(bool(admitted_session_id), "one Visitor session must be available for projection read")

        read_result = read_projection(
            store,
            visitor_subject_id=visitor_subject_id,
            session_id=admitted_session_id,
            session_credential=admitted_session_credential,
        )
        projection_payload = read_result.payload()
        require(
            set(projection_payload)
            == {
                "schemaVersion",
                "visitorSessionId",
                "publicationId",
                "publicationVersionId",
                "expiresAt",
                "title",
                "body",
                "aiDisclosure",
                "source",
                "answerBoundary",
            },
            "Visitor read must return the projection-only payload",
        )
        require(
            projection_payload["publicationId"] == publication.publication_id
            and projection_payload["publicationVersionId"] == publication.publication_version_id,
            "Visitor read must remain bound to the admitted publication version",
        )
        require(
            bool(projection_payload["title"]) and bool(projection_payload["body"]),
            "Visitor read must contain the independently stored projection copy",
        )
        serialized_projection = str(projection_payload)
        for forbidden_field in (
            "vaultId",
            "ownerSubjectId",
            "memoryVersionId",
            "grantId",
            "grantCredential",
            "sessionCredential",
            "authorityEpoch",
            "kbliteFacts",
            "voiceProfileId",
            "digitalHumanId",
        ):
            require(
                forbidden_field not in serialized_projection,
                f"Visitor read leaked private field {forbidden_field}",
            )

        revoke_result = revoke(
            store,
            seed=seed,
            grant_id=issued.grant_id,
            visitor_subject_id=visitor_subject_id,
        )
        require(revoke_result.revoked_session_count == 1, "revoke must close the active session")
        expect_rejected(
            lambda: read_projection(
                store,
                visitor_subject_id=visitor_subject_id,
                session_id=admitted_session_id,
                session_credential=admitted_session_credential,
            ),
            "revoked ShareGrant must reject its existing Visitor projection read",
        )
        expect_rejected(
            lambda: admit(
                store,
                visitor_subject_id=visitor_subject_id,
                grant_id=issued.grant_id,
                grant_credential=issued.grant_credential,
                suffix="after-revoke",
            ),
            "revoked ShareGrant must reject a new Visitor session",
        )

        blocked_grant = issue(
            store,
            seed=seed,
            publication_id=publication.publication_id,
            publication_version_id=publication.publication_version_id,
            visitor_subject_id=visitor_subject_id,
            use_limit=1,
        )
        assert blocked_grant.grant_credential is not None
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE publication.public_projections
                    SET state = 'blocked', block_reason_code = 'smokeProjectionBlocked',
                        blocked_at = NOW(), updated_at = NOW()
                    WHERE publication_version_id = %s AND state = 'active'
                    """,
                    (publication.publication_version_id,),
                )
                require(cursor.rowcount == 1, "smoke must block the independent projection")
            connection.commit()
        expect_rejected(
            lambda: admit(
                store,
                visitor_subject_id=visitor_subject_id,
                grant_id=blocked_grant.grant_id,
                grant_credential=blocked_grant.grant_credential,
                suffix="after-block",
            ),
            "blocked public projection must reject Visitor admission",
        )

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT authority_epoch, token_hash, grantee_subject_hash, use_count
                    FROM publication.share_grants
                    WHERE id = %s
                    """,
                    (issued.grant_id,),
                )
                grant = cursor.fetchone()
                require(grant is not None, "ShareGrant must persist")
                require(int(grant[0]) == 0, "ShareGrant must bind the authority epoch")
                require(str(grant[1]) != issued.grant_credential, "raw grant credential must not persist")
                require(int(grant[3]) == 1, "usage count must be atomically persisted")
                cursor.execute(
                    "SELECT state, expected_grant_use_count FROM publication.visitor_sessions WHERE share_grant_id = %s",
                    (issued.grant_id,),
                )
                session = cursor.fetchone()
                require(session == ("revoked", 1), "revoked session must retain bound use count")
        print(
            "Publication visitor access Postgres smoke passed "
            "(projection-only read, adult/direct admission, CAS, revoke and projection block verified)."
        )
    finally:
        store.close_pool()


def main() -> None:
    base_dsn = os.environ.get("DATABASE_URL", "").strip()
    require(base_dsn, "DATABASE_URL is required")
    parameters = conninfo_to_dict(base_dsn)
    require(bool(parameters.get("user")), "DATABASE_URL must identify a database user")
    admin_dsn = dsn_for_database(base_dsn, "postgres")
    database_name = f"dj_publication_visitor_access_{uuid.uuid4().hex[:12]}"
    test_dsn = dsn_for_database(base_dsn, database_name)
    try:
        create_database(admin_dsn, database_name)
        migrator = PostgresMigrator(
            dsn=test_dsn,
            migrations_dir=default_migrations_dir(),
            build_id="publication-visitor-access-smoke",
            lock_timeout_ms=1000,
            statement_timeout_ms=15000,
        )
        applied = migrator.apply()
        verified = migrator.verify()
        require(verified["status"] == "ready", "migration head must verify")
        require("0081" in applied["appliedVersions"], "ShareGrant authority migration must apply")
        exercise(test_dsn)
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":  # pragma: no cover
    main()
