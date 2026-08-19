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
    PublicationVisitorAccessDenied,
    PublicationVisitorAccessService,
    PublicationVisitorAccessUnavailable,
    PublicationVisitorEligibility,
    PublicationVisitorSessionCommand,
    StaticPublicationVisitorEligibilityResolver,
)
from app.services.publication_authority import PublicationAuthorityAccessDenied
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
authority_service = _authority_smoke["authority_service"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_rejected(operation, message: str) -> None:
    try:
        operation()
    except (PublicationVisitorAccessUnavailable, PublicationVisitorAccessConflict):
        return
    raise AssertionError(message)


def expect_owner_access_denied(operation, message: str) -> None:
    try:
        operation()
    except (PublicationAuthorityAccessDenied, PublicationVisitorAccessDenied):
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
        grantee_display_label="手机号尾号 9949",
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
    grant_credential: str | None,
    suffix: str,
    product_contract: bool = False,
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
            product_contract=product_contract,
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


def owner_publications(store: PostgresStore, *, seed):
    with store.request_unit_of_work(
        correlation_id=f"publication-owner-management-smoke:publications:{seed.context.vault_id}",
        command_id=None,
    ):
        return authority_service(store).list_owner_publications(context=seed.context)


def owner_publication_versions(store: PostgresStore, *, seed, publication_id: str):
    with store.request_unit_of_work(
        correlation_id=(
            "publication-owner-management-smoke:versions:"
            f"{seed.context.vault_id}:{publication_id}"
        ),
        command_id=None,
    ):
        return authority_service(store).list_owner_publication_versions(
            context=seed.context,
            publication_id=publication_id,
        )


def owner_grants(store: PostgresStore, *, seed, visitor_subject_id: str):
    with store.request_unit_of_work(
        correlation_id=f"publication-owner-management-smoke:grants:{seed.context.vault_id}",
        command_id=None,
    ):
        return visitor_service(store, visitor_subject_id=visitor_subject_id).list_owner_grants(
            context=seed.context
        )


def owner_grant(store: PostgresStore, *, seed, visitor_subject_id: str, grant_id: str):
    summaries = {
        summary.grant_id: summary
        for summary in owner_grants(
            store,
            seed=seed,
            visitor_subject_id=visitor_subject_id,
        )
    }
    require(grant_id in summaries, f"Owner ShareGrant summary is missing {grant_id}")
    return summaries[grant_id]


def recipient_invitations(store: PostgresStore, *, visitor_subject_id: str):
    with store.request_unit_of_work(
        correlation_id=f"publication-visitor-access-smoke:invitations:{visitor_subject_id}",
        command_id=None,
    ):
        return visitor_service(
            store,
            visitor_subject_id=visitor_subject_id,
        ).list_recipient_invitations(visitor_subject_id=visitor_subject_id)


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

        publications = owner_publications(store, seed=seed)
        require(len(publications) == 1, "Owner management read must return the confirmed publication")
        publication_summary = publications[0]
        require(
            publication_summary.publication_id == publication.publication_id
            and publication_summary.publication_version_id == publication.publication_version_id
            and publication_summary.publication_state == "confirmed"
            and publication_summary.projection_state == "active",
            "Owner management read must remain bound to the independent public projection",
        )
        require(
            publication_summary.preview_title == "确认的公开回忆"
            and publication_summary.preview_body == "这是由发布者重新整理并确认的公开说明。",
            "Owner management read must expose only the owner-authored public preview",
        )
        require(
            "memory" not in str(publication_summary).lower()
            and "source" not in str(publication_summary).lower()
            and "kblite" not in str(publication_summary).lower(),
            "Owner management read must not expose private source or memory identifiers",
        )
        versions = owner_publication_versions(
            store,
            seed=seed,
            publication_id=publication.publication_id,
        )
        require(len(versions) == 1, "Owner version audit must return the confirmed version")
        version_summary = versions[0]
        require(
            version_summary.publication_version_id == publication.publication_version_id
            and version_summary.version_number == 1
            and version_summary.projection_state == "active",
            "Owner version audit must remain bound to the immutable public projection",
        )
        require(
            len(version_summary.items) == 1
            and version_summary.items[0].public_title == "确认的公开回忆"
            and version_summary.items[0].public_body
            == "这是由发布者重新整理并确认的公开说明。",
            "Owner version audit must expose only the confirmed public snapshot",
        )
        other_owner_context = type(seed.context)(
            vault_id=seed.context.vault_id,
            owner_subject_id="publication-management-other-owner",
            actor_subject_id="publication-management-other-owner",
        )
        with store.request_unit_of_work(
            correlation_id=f"publication-owner-management-smoke:cross-owner:{seed.context.vault_id}",
            command_id=None,
        ):
            expect_owner_access_denied(
                lambda: authority_service(store).list_owner_publications(context=other_owner_context),
                "cross-owner publication management read must fail closed",
            )
            expect_owner_access_denied(
                lambda: authority_service(store).list_owner_publication_versions(
                    context=other_owner_context,
                    publication_id=publication.publication_id,
                ),
                "cross-owner publication version audit must fail closed",
            )

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

        grants = owner_grants(store, seed=seed, visitor_subject_id=visitor_subject_id)
        require(len(grants) == 1, "Owner management read must return the issued ShareGrant")
        grant_summary = grants[0]
        require(
            grant_summary.grant_id == issued.grant_id
            and grant_summary.publication_id == publication.publication_id
            and grant_summary.publication_version_id == publication.publication_version_id
            and grant_summary.state == "active"
            and grant_summary.use_remaining == 1,
            "Owner ShareGrant summary must include lifecycle state and the safety counter",
        )
        require(
            grant_summary.grantee_display_label == "手机号尾号 9949",
            "Owner ShareGrant summary must persist only the masked recipient label",
        )
        with store.request_unit_of_work(
            correlation_id=f"publication-owner-management-smoke:grant-cross-owner:{seed.context.vault_id}",
            command_id=None,
        ):
            expect_owner_access_denied(
                lambda: visitor_service(
                    store,
                    visitor_subject_id=visitor_subject_id,
                ).list_owner_grants(context=other_owner_context),
                "cross-owner ShareGrant management read must fail closed",
            )

        invitations = recipient_invitations(
            store,
            visitor_subject_id=visitor_subject_id,
        )
        require(
            len(invitations) == 1
            and invitations[0].grant_id == issued.grant_id
            and invitations[0].publication_id == publication.publication_id
            and invitations[0].publication_version_id == publication.publication_version_id
            and invitations[0].state == "active"
            and invitations[0].title == "确认的公开回忆",
            "registered recipient must list only its active public invitation metadata",
        )
        require(
            recipient_invitations(store, visitor_subject_id="unrelated-recipient") == (),
            "unrelated registered accounts must not discover ShareGrants",
        )

        product_visitor_subject_id = "publication-product-visitor-smoke"
        product_grant = issue(
            store,
            seed=seed,
            publication_id=publication.publication_id,
            publication_version_id=publication.publication_version_id,
            visitor_subject_id=product_visitor_subject_id,
            use_limit=8,
        )
        product_invitations = recipient_invitations(
            store,
            visitor_subject_id=product_visitor_subject_id,
        )
        require(
            len(product_invitations) == 1
            and product_invitations[0].grant_id == product_grant.grant_id,
            "product recipient must discover its in-app invitation without a raw credential",
        )
        product_session_credential = "visitor-session-product-" + "s" * 32
        product_admission = admit(
            store,
            visitor_subject_id=product_visitor_subject_id,
            grant_id=product_grant.grant_id,
            grant_credential=None,
            suffix="product",
            product_contract=True,
        )
        product_projection = read_projection(
            store,
            visitor_subject_id=product_visitor_subject_id,
            session_id=product_admission.session_id,
            session_credential=product_session_credential,
        )
        require(
            product_projection.publication_id == publication.publication_id
            and product_projection.publication_version_id == publication.publication_version_id,
            "credentialless product admission must remain bound to the registered recipient and public projection",
        )

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
        require(
            owner_grant(
                store,
                seed=seed,
                visitor_subject_id=visitor_subject_id,
                grant_id=issued.grant_id,
            ).use_remaining
            == 0,
            "Owner ShareGrant summary must reflect the atomically consumed use",
        )

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
        require(
            owner_grant(
                store,
                seed=seed,
                visitor_subject_id=visitor_subject_id,
                grant_id=issued.grant_id,
            ).state
            == "revoked",
            "Owner ShareGrant summary must reflect revocation without exposing a credential",
        )
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
                    SELECT authority_epoch, token_hash, grantee_subject_hash, use_count,
                        grantee_display_label
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
                require(
                    str(grant[4]) == "手机号尾号 9949",
                    "only the masked recipient label may persist",
                )
                cursor.execute(
                    "SELECT state, expected_grant_use_count FROM publication.visitor_sessions WHERE share_grant_id = %s",
                    (issued.grant_id,),
                )
                session = cursor.fetchone()
                require(session == ("revoked", 1), "revoked session must retain bound use count")
        print(
            "Publication visitor access Postgres smoke passed "
            "(registered invitation list, product admission, owner management and version summaries, projection-only read, "
            "adult/direct admission, CAS, revoke and projection block verified)."
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
        require("0104" in applied["appliedVersions"], "ShareGrant recipient label migration must apply")
        exercise(test_dsn)
    finally:
        drop_database(admin_dsn, database_name)


if __name__ == "__main__":  # pragma: no cover
    main()
