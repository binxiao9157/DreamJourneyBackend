"""G0 tests for the disabled publication schema and authorization contract."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations
from app.domain.publication.schema_authz import (
    PublicationAccessAction,
    PublicationAuthorizationContext,
    PublicationAuthorizationDisposition,
    PublicationAuthorizationPrincipal,
    PublicationDataPlane,
    PublicationPrincipalKind,
    evaluate_publication_schema_authz,
)


ROOT = Path(__file__).parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0050_publication_visitor_schema.sql"
MIGRATION_JSON = ROOT / "db/migrations/0050_publication_visitor_schema.json"


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(**overrides: object) -> PublicationAuthorizationContext:
    values: dict[str, object] = {
        "vault_id": "vault-publication-owner",
        "owner_subject_hash": _digest("owner-publication-subject"),
        "authority_epoch": 7,
        "policy_version": "publication-visitor-policy-v1",
    }
    values.update(overrides)
    return PublicationAuthorizationContext(**values)  # type: ignore[arg-type]


def _principal(**overrides: object) -> PublicationAuthorizationPrincipal:
    values: dict[str, object] = {
        "kind": PublicationPrincipalKind.OWNER,
        "vault_id": "vault-publication-owner",
        "subject_hash": _digest("owner-publication-subject"),
    }
    values.update(overrides)
    return PublicationAuthorizationPrincipal(**values)  # type: ignore[arg-type]


class PublicationSchemaAuthorizationTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_invalid_inputs(self) -> None:
        result = evaluate_publication_schema_authz(
            context=object(),
            principal=object(),
            data_plane=object(),
            action=object(),
        )

        self.assertEqual(result.disposition, PublicationAuthorizationDisposition.SHADOW_DISABLED)
        self.assertFalse(result.private_authority_read_allowed)
        self.assertFalse(result.publication_writer_allowed)
        self.assertFalse(result.public_store_read_allowed)

    def test_public_principals_can_never_read_private_authority(self) -> None:
        for kind in (
            PublicationPrincipalKind.VISITOR,
            PublicationPrincipalKind.PUBLIC_GATEWAY,
        ):
            with self.subTest(kind=kind):
                principal = PublicationAuthorizationPrincipal(
                    kind=kind,
                    vault_id=None,
                    subject_hash=None,
                )
                result = evaluate_publication_schema_authz(
                    context=_context(),
                    principal=principal,
                    data_plane=PublicationDataPlane.PRIVATE_AUTHORITY,
                    action=PublicationAccessAction.PUBLIC_STORE_READ,
                    enabled=True,
                )
                self.assertEqual(
                    result.disposition,
                    PublicationAuthorizationDisposition.PRIVATE_AUTHORITY_DENIED,
                )
                self.assertFalse(result.private_authority_read_allowed)

    def test_owner_cross_vault_or_subject_mismatch_fails_closed(self) -> None:
        for principal in (
            _principal(vault_id="vault-another-owner"),
            _principal(subject_hash=_digest("another-owner")),
        ):
            with self.subTest(principal=principal):
                result = evaluate_publication_schema_authz(
                    context=_context(),
                    principal=principal,
                    data_plane=PublicationDataPlane.PRIVATE_AUTHORITY,
                    action=PublicationAccessAction.PUBLICATION_WRITE,
                    enabled=True,
                )
                self.assertEqual(
                    result.disposition,
                    PublicationAuthorizationDisposition.CROSS_VAULT_DENIED,
                )

    def test_matching_owner_is_still_denied_until_policy_approval(self) -> None:
        result = evaluate_publication_schema_authz(
            context=_context(),
            principal=_principal(),
            data_plane=PublicationDataPlane.PRIVATE_AUTHORITY,
            action=PublicationAccessAction.PUBLICATION_WRITE,
            enabled=True,
        )

        self.assertEqual(result.disposition, PublicationAuthorizationDisposition.POLICY_DISABLED)
        self.assertIn("publicationVisitorPolicyDefaultDeny", result.reason_codes)
        self.assertFalse(result.publication_writer_allowed)
        self.assertFalse(result.share_grant_issued)
        self.assertFalse(result.visitor_session_issued)

    def test_value_free_summary_does_not_leak_owner_or_vault_values(self) -> None:
        context = _context(vault_id="vault-private-marker")
        owner_hash = _digest("owner-private-marker")
        result = evaluate_publication_schema_authz(
            context=PublicationAuthorizationContext(
                vault_id=context.vault_id,
                owner_subject_hash=owner_hash,
                authority_epoch=context.authority_epoch,
                policy_version=context.policy_version,
            ),
            principal=PublicationAuthorizationPrincipal(
                kind=PublicationPrincipalKind.OWNER,
                vault_id=context.vault_id,
                subject_hash=owner_hash,
            ),
            data_plane=PublicationDataPlane.PUBLIC_STORE,
            action=PublicationAccessAction.PUBLIC_STORE_READ,
            enabled=True,
        )
        serialized = json.dumps(result.value_free_summary(), sort_keys=True)

        self.assertNotIn("vault-private-marker", serialized)
        self.assertNotIn(owner_hash, serialized)

    def test_migration_is_additive_private_and_contains_no_public_content_column(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0050")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["publicationSchemaV1"])
        self.assertFalse(manifest["releaseFlags"]["publicationWriterV1"])
        self.assertFalse(manifest["releaseFlags"]["visitorGatewayV1"])
        self.assertIn("CREATE SCHEMA IF NOT EXISTS publication;", sql)
        self.assertIn("REVOKE ALL ON SCHEMA publication FROM PUBLIC;", sql)
        for table in (
            "publications",
            "publication_versions",
            "share_grants",
            "visitor_sessions",
            "visitor_feedback",
        ):
            self.assertIn(f"CREATE TABLE publication.{table}", sql)
            self.assertIn(f"REVOKE ALL ON TABLE publication.{table} FROM PUBLIC;", sql)
        self.assertIn("REFERENCES owner_truth.memory_versions(id)", sql)
        self.assertIn("pinned memory version belongs to another vault", sql)
        self.assertIn("token_hash TEXT NOT NULL CHECK", sql)
        self.assertIn("session_token_hash TEXT NOT NULL CHECK", sql)
        for forbidden in (
            "content_body",
            "object_url",
            "preview_url",
            "temporary_url",
            "source_payload",
        ):
            self.assertNotIn(forbidden, sql.lower())

    def test_migration_loader_accepts_publication_schema_metadata(self) -> None:
        migrations = load_migrations(ROOT / "db/migrations")
        publication = next(item for item in migrations if item.version == "0050")

        self.assertEqual(publication.name, "publication_visitor_schema")
        self.assertEqual(publication.phase, "expand")
        self.assertEqual(publication.compatibility, "additive")

    def test_domain_has_no_route_persistence_network_or_worker_imports(self) -> None:
        source = (
            ROOT / "app/domain/publication/schema_authz.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "app.main",
            "app.services.postgres_store",
            "app.services.in_memory_store",
            "app.async_effects",
            "requests",
            "httpx",
            "psycopg",
            "boto3",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
