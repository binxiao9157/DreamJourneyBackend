"""G0 tests for default-deny ShareGrant and adult Visitor sessions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations
from app.domain.publication.schema_authz import (
    PublicationAuthorizationContext,
    PublicationAuthorizationPrincipal,
    PublicationPrincipalKind,
)
from app.domain.publication.share_grant_session import (
    PublicationAdultVerificationState,
    PublicationShareGrant,
    PublicationShareGrantAction,
    PublicationShareGrantCommand,
    PublicationShareGrantSessionDisposition,
    PublicationShareGrantState,
    PublicationVisitorIdentity,
    PublicationVisitorRelationshipOrigin,
    PublicationVisitorSessionProposal,
    evaluate_publication_share_grant_session,
)


ROOT = Path(__file__).parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0053_publication_share_grant_session_metadata.sql"
MIGRATION_JSON = ROOT / "db/migrations/0053_publication_share_grant_session_metadata.json"
NOW = datetime(2026, 7, 29, 0, 0, tzinfo=timezone.utc)
GRANT_ID = "980d0a7c-95d0-4f13-90cf-0b9600be06ee"
PUBLICATION_ID = "d397c4e0-2d8d-466d-9a29-5a4ec336d2a7"
VERSION_ID = "efa96059-8a4a-4f8d-9dd7-2b52cbbeca91"
SESSION_ID = "5c0c1d15-b9f6-4568-9dcf-f03fa9a884cd"
COMMAND_ID = "c697fb14-d8cb-4c15-9104-ff65a477e665"


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(**overrides: object) -> PublicationAuthorizationContext:
    values: dict[str, object] = {
        "vault_id": "vault-publication-owner",
        "owner_subject_hash": _digest("publication-owner"),
        "authority_epoch": 3,
        "policy_version": "publication-visitor-policy-v1",
    }
    values.update(overrides)
    return PublicationAuthorizationContext(**values)  # type: ignore[arg-type]


def _principal(**overrides: object) -> PublicationAuthorizationPrincipal:
    context = _context()
    values: dict[str, object] = {
        "kind": PublicationPrincipalKind.OWNER,
        "vault_id": context.vault_id,
        "subject_hash": context.owner_subject_hash,
    }
    values.update(overrides)
    return PublicationAuthorizationPrincipal(**values)  # type: ignore[arg-type]


def _grant(**overrides: object) -> PublicationShareGrant:
    context = _context()
    values: dict[str, object] = {
        "grant_id": GRANT_ID,
        "publication_id": PUBLICATION_ID,
        "publication_version_id": VERSION_ID,
        "vault_id": context.vault_id,
        "owner_subject_hash": context.owner_subject_hash,
        "grantee_subject_hash": _digest("visitor"),
        "grant_credential_hash": _digest("grant-credential"),
        "state": PublicationShareGrantState.ACTIVE,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(days=1),
        "use_limit": 2,
        "use_count": 0,
        "policy_hash": _digest("publication-policy"),
    }
    values.update(overrides)
    return PublicationShareGrant(**values)  # type: ignore[arg-type]


def _visitor(**overrides: object) -> PublicationVisitorIdentity:
    values: dict[str, object] = {
        "subject_hash": _digest("visitor"),
        "adult_verification": PublicationAdultVerificationState.VERIFIED,
        "relationship_origin": PublicationVisitorRelationshipOrigin.DIRECT,
        "emergency_contact_ref_hash": _digest("contact-ref"),
    }
    values.update(overrides)
    return PublicationVisitorIdentity(**values)  # type: ignore[arg-type]


def _command(action: PublicationShareGrantAction = PublicationShareGrantAction.ACCESS) -> PublicationShareGrantCommand:
    return PublicationShareGrantCommand(command_id=COMMAND_ID, action=action)


def _session(**overrides: object) -> PublicationVisitorSessionProposal:
    values: dict[str, object] = {
        "session_id": SESSION_ID,
        "grant_id": GRANT_ID,
        "publication_id": PUBLICATION_ID,
        "publication_version_id": VERSION_ID,
        "visitor_subject_hash": _digest("visitor"),
        "session_credential_hash": _digest("session-credential"),
        "issued_at": NOW,
        "expires_at": NOW + timedelta(hours=1),
        "expected_grant_use_count": 0,
    }
    values.update(overrides)
    return PublicationVisitorSessionProposal(**values)  # type: ignore[arg-type]


class PublicationShareGrantSessionTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_inputs(self) -> None:
        result = evaluate_publication_share_grant_session(
            owner_context=object(),
            owner_principal=object(),
            grant=object(),
            visitor=object(),
            command=object(),
            session=object(),
        )
        self.assertEqual(result.disposition, PublicationShareGrantSessionDisposition.SHADOW_DISABLED)
        self.assertFalse(result.public_query_allowed)

    def test_owner_scope_and_family_auto_grant_fail_closed(self) -> None:
        result = evaluate_publication_share_grant_session(
            owner_context=_context(),
            owner_principal=_principal(vault_id="vault-other"),
            grant=_grant(),
            visitor=_visitor(),
            command=_command(),
            session=_session(),
            enabled=True,
            now=NOW,
        )
        self.assertEqual(result.disposition, PublicationShareGrantSessionDisposition.OWNER_SCOPE_DENIED)

        result = evaluate_publication_share_grant_session(
            owner_context=_context(),
            owner_principal=_principal(),
            grant=_grant(),
            visitor=_visitor(relationship_origin=PublicationVisitorRelationshipOrigin.FAMILY_DERIVED),
            command=_command(),
            session=_session(),
            enabled=True,
            now=NOW,
        )
        self.assertEqual(result.disposition, PublicationShareGrantSessionDisposition.FAMILY_AUTO_GRANT_DENIED)

    def test_minor_unknown_or_failed_adult_verification_denies(self) -> None:
        for state in (
            PublicationAdultVerificationState.MINOR,
            PublicationAdultVerificationState.UNKNOWN,
            PublicationAdultVerificationState.FAILED,
        ):
            with self.subTest(state=state):
                result = evaluate_publication_share_grant_session(
                    owner_context=_context(),
                    owner_principal=_principal(),
                    grant=_grant(),
                    visitor=_visitor(adult_verification=state),
                    command=_command(),
                    session=_session(),
                    enabled=True,
                    now=NOW,
                )
                self.assertEqual(
                    result.disposition,
                    PublicationShareGrantSessionDisposition.ADULT_VERIFICATION_DENIED,
                )

    def test_inactive_expired_and_exhausted_grant_denies(self) -> None:
        for grant, disposition in (
            (_grant(state=PublicationShareGrantState.REVOKED), PublicationShareGrantSessionDisposition.GRANT_INACTIVE),
            (_grant(expires_at=NOW + timedelta(seconds=1)), PublicationShareGrantSessionDisposition.GRANT_EXPIRED),
            (_grant(use_count=2), PublicationShareGrantSessionDisposition.USE_LIMIT_REACHED),
        ):
            with self.subTest(disposition=disposition):
                result = evaluate_publication_share_grant_session(
                    owner_context=_context(), owner_principal=_principal(), grant=grant,
                    visitor=_visitor(), command=_command(), session=_session(), enabled=True,
                    now=NOW + timedelta(days=1),
                )
                if disposition is PublicationShareGrantSessionDisposition.USE_LIMIT_REACHED:
                    result = evaluate_publication_share_grant_session(
                        owner_context=_context(), owner_principal=_principal(), grant=grant,
                        visitor=_visitor(), command=_command(), session=_session(), enabled=True, now=NOW,
                    )
                self.assertEqual(result.disposition, disposition)

    def test_session_version_expiry_and_cas_mismatch_deny(self) -> None:
        for session, disposition in (
            (_session(publication_version_id="da6f7d50-fb84-4c01-a2ca-555d755a3248"), PublicationShareGrantSessionDisposition.GRANT_VERSION_MISMATCH),
            (_session(expires_at=NOW + timedelta(days=2)), PublicationShareGrantSessionDisposition.SESSION_EXPIRED),
            (_session(expected_grant_use_count=1), PublicationShareGrantSessionDisposition.USE_CAS_REQUIRED),
        ):
            with self.subTest(disposition=disposition):
                result = evaluate_publication_share_grant_session(
                    owner_context=_context(), owner_principal=_principal(), grant=_grant(),
                    visitor=_visitor(), command=_command(), session=session, enabled=True, now=NOW,
                )
                self.assertEqual(result.disposition, disposition)

    def test_valid_issue_revoke_and_access_are_still_policy_disabled(self) -> None:
        for action in PublicationShareGrantAction:
            with self.subTest(action=action):
                result = evaluate_publication_share_grant_session(
                    owner_context=_context(), owner_principal=_principal(), grant=_grant(),
                    visitor=_visitor(), command=_command(action), session=_session(), enabled=True, now=NOW,
                )
                self.assertEqual(result.disposition, PublicationShareGrantSessionDisposition.POLICY_DISABLED)
                self.assertFalse(result.grant_issued)
                self.assertFalse(result.grant_revoked)
                self.assertFalse(result.visitor_session_issued)
                self.assertFalse(result.public_query_allowed)
                self.assertFalse(result.use_consumed)

    def test_value_free_summary_does_not_expose_owner_visitor_or_vault_values(self) -> None:
        context = _context(vault_id="vault-private-marker", owner_subject_hash=_digest("owner-private-marker"))
        grant = _grant(vault_id=context.vault_id, owner_subject_hash=context.owner_subject_hash)
        visitor = _visitor(subject_hash=_digest("visitor-private-marker"))
        session = _session(visitor_subject_hash=visitor.subject_hash)
        result = evaluate_publication_share_grant_session(
            owner_context=context,
            owner_principal=PublicationAuthorizationPrincipal(
                kind=PublicationPrincipalKind.OWNER,
                vault_id=context.vault_id,
                subject_hash=context.owner_subject_hash,
            ),
            grant=grant,
            visitor=visitor,
            command=_command(),
            session=session,
            enabled=True,
            now=NOW,
        )
        serialized = json.dumps(result.value_free_summary(), sort_keys=True)
        self.assertNotIn("vault-private-marker", serialized)
        self.assertNotIn(context.owner_subject_hash, serialized)
        self.assertNotIn(visitor.subject_hash, serialized)

    def test_additive_migration_has_no_raw_credential_or_release_flags(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8").lower()
        manifest = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0053")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertEqual(
            manifest["releaseFlags"],
            {
                "publicGatewayV1": False,
                "shareGrantIssueV1": False,
                "visitorSessionV1": False,
            },
        )
        self.assertIn("create table publication.share_grant_authorization_receipts", sql)
        self.assertIn("revoke all on table publication.share_grant_authorization_receipts from public;", sql)
        for forbidden in ("raw_credential", "bearer_value", "source_payload", "object_url", "visitor_body"):
            self.assertNotIn(forbidden, sql)

    def test_migration_loader_accepts_share_grant_metadata(self) -> None:
        migrations = load_migrations(ROOT / "db/migrations")
        metadata = next(item for item in migrations if item.version == "0053")
        self.assertEqual(metadata.name, "publication_share_grant_session_metadata")
        self.assertEqual(metadata.phase, "expand")

    def test_domain_has_no_route_persistence_network_or_worker_imports(self) -> None:
        source = (ROOT / "app/domain/publication/share_grant_session.py").read_text(encoding="utf-8")
        for forbidden in (
            "app.main", "app.services", "app.async_effects", "requests", "httpx", "psycopg", "boto3"
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
