"""G0 tests for the hidden Owner Draft Snapshot contract."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations
from app.domain.publication.draft_snapshot import (
    PublicationDraftConfirmation,
    PublicationDraftConsentState,
    PublicationDraftDisposition,
    PublicationDraftMemoryVersion,
    PublicationDraftSnapshot,
    PublicationDraftSourceState,
    evaluate_publication_draft_snapshot,
)
from app.domain.publication.schema_authz import (
    PublicationAuthorizationContext,
    PublicationAuthorizationPrincipal,
    PublicationPrincipalKind,
)


ROOT = Path(__file__).parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0051_publication_draft_snapshot.sql"
MIGRATION_JSON = ROOT / "db/migrations/0051_publication_draft_snapshot.json"
DRAFT_ID = "9fa4d187-2a0c-4efb-b351-b2013c0c7b65"
PUBLICATION_ID = "d397c4e0-2d8d-466d-9a29-5a4ec336d2a7"
MEMORY_VERSION_ID = "0a91af9d-f881-4919-835b-b6ba3d3c4e08"
COMMAND_ID = "ba5448eb-5dc0-4208-9622-912de73c8b3f"


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


def _source(**overrides: object) -> PublicationDraftMemoryVersion:
    values: dict[str, object] = {
        "memory_version_id": MEMORY_VERSION_ID,
        "vault_id": "vault-publication-owner",
        "is_current": True,
        "source_state": PublicationDraftSourceState.ACTIVE,
        "consent_state": PublicationDraftConsentState.GRANTED,
        "content_hash": _digest("memory-version"),
        "source_citation_hash": _digest("source-citation"),
        "requires_redaction": False,
        "redaction_diff_hash": None,
    }
    values.update(overrides)
    return PublicationDraftMemoryVersion(**values)  # type: ignore[arg-type]


def _snapshot(
    *,
    sources: tuple[PublicationDraftMemoryVersion, ...] | None = None,
    ai_transformation_present: bool = False,
    **overrides: object,
) -> PublicationDraftSnapshot:
    context = _context()
    sources = sources if sources is not None else (_source(),)
    values: dict[str, object] = {
        "draft_id": DRAFT_ID,
        "publication_id": PUBLICATION_ID,
        "vault_id": context.vault_id,
        "owner_subject_hash": context.owner_subject_hash,
        "authority_epoch": context.authority_epoch,
        "policy_version": context.policy_version,
        "draft_revision": 1,
        "memory_versions": sources,
        "draft_snapshot_hash": "0" * 64,
        "preview_hash": "0" * 64,
        "ai_transformation_present": ai_transformation_present,
    }
    values.update(overrides)
    draft_hash = PublicationDraftSnapshot.draft_hash_for(
        draft_id=str(values["draft_id"]),
        publication_id=str(values["publication_id"]),
        vault_id=str(values["vault_id"]),
        owner_subject_hash=str(values["owner_subject_hash"]),
        authority_epoch=int(values["authority_epoch"]),
        policy_version=str(values["policy_version"]),
        draft_revision=int(values["draft_revision"]),
        memory_versions=tuple(values["memory_versions"]),  # type: ignore[arg-type]
        ai_transformation_present=bool(values["ai_transformation_present"]),
    )
    preview_hash = PublicationDraftSnapshot.preview_hash_for(
        draft_snapshot_hash=draft_hash,
        memory_versions=tuple(values["memory_versions"]),  # type: ignore[arg-type]
        ai_transformation_present=bool(values["ai_transformation_present"]),
    )
    values["draft_snapshot_hash"] = draft_hash
    values["preview_hash"] = preview_hash
    return PublicationDraftSnapshot(**values)  # type: ignore[arg-type]


def _confirmation(snapshot: PublicationDraftSnapshot, **overrides: object) -> PublicationDraftConfirmation:
    values: dict[str, object] = {
        "command_id": COMMAND_ID,
        "draft_id": snapshot.draft_id,
        "expected_draft_revision": snapshot.draft_revision,
        "expected_draft_snapshot_hash": snapshot.draft_snapshot_hash,
        "expected_preview_hash": snapshot.preview_hash,
        "expected_policy_version": snapshot.policy_version,
        "second_confirmation": True,
        "ai_transformation_disclosed": True,
    }
    values.update(overrides)
    return PublicationDraftConfirmation(**values)  # type: ignore[arg-type]


class PublicationDraftSnapshotTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_inputs(self) -> None:
        result = evaluate_publication_draft_snapshot(
            context=object(),
            principal=object(),
            snapshot=object(),
            confirmation=object(),
        )
        self.assertEqual(result.disposition, PublicationDraftDisposition.SHADOW_DISABLED)
        self.assertFalse(result.draft_write_allowed)

    def test_owner_or_epoch_mismatch_fails_closed(self) -> None:
        result = evaluate_publication_draft_snapshot(
            context=_context(),
            principal=_principal(vault_id="vault-another-owner"),
            snapshot=_snapshot(),
            confirmation=None,
            enabled=True,
        )
        self.assertEqual(result.disposition, PublicationDraftDisposition.OWNER_SCOPE_DENIED)

        result = evaluate_publication_draft_snapshot(
            context=_context(),
            principal=_principal(),
            snapshot=_snapshot(authority_epoch=4),
            confirmation=None,
            enabled=True,
        )
        self.assertEqual(result.disposition, PublicationDraftDisposition.OWNER_SCOPE_DENIED)

    def test_stale_deleted_or_policy_blocked_sources_fail_closed(self) -> None:
        for source, disposition in (
            (_source(is_current=False), PublicationDraftDisposition.STALE_MEMORY_VERSION),
            (_source(source_state=PublicationDraftSourceState.DELETED), PublicationDraftDisposition.SOURCE_UNAVAILABLE),
            (_source(consent_state=PublicationDraftConsentState.REVOKED), PublicationDraftDisposition.SOURCE_POLICY_BLOCKED),
        ):
            with self.subTest(disposition=disposition):
                result = evaluate_publication_draft_snapshot(
                    context=_context(),
                    principal=_principal(),
                    snapshot=_snapshot(sources=(source,)),
                    confirmation=None,
                    enabled=True,
                )
                self.assertEqual(result.disposition, disposition)

    def test_redaction_diff_is_required_when_source_requires_redaction(self) -> None:
        result = evaluate_publication_draft_snapshot(
            context=_context(),
            principal=_principal(),
            snapshot=_snapshot(sources=(_source(requires_redaction=True),)),
            confirmation=None,
            enabled=True,
        )
        self.assertEqual(result.disposition, PublicationDraftDisposition.REDACTION_DIFF_REQUIRED)

    def test_hash_and_confirmation_mismatch_fail_closed(self) -> None:
        snapshot = _snapshot()
        invalid = PublicationDraftSnapshot(
            **{**snapshot.__dict__, "preview_hash": "f" * 64}
        )
        result = evaluate_publication_draft_snapshot(
            context=_context(), principal=_principal(), snapshot=invalid, confirmation=None, enabled=True
        )
        self.assertEqual(result.disposition, PublicationDraftDisposition.DRAFT_INTEGRITY_MISMATCH)

        result = evaluate_publication_draft_snapshot(
            context=_context(),
            principal=_principal(),
            snapshot=snapshot,
            confirmation=_confirmation(snapshot, expected_draft_revision=2),
            enabled=True,
        )
        self.assertEqual(result.disposition, PublicationDraftDisposition.CONFIRMATION_MISMATCH)

    def test_second_confirmation_and_ai_disclosure_are_required(self) -> None:
        snapshot = _snapshot(ai_transformation_present=True)
        result = evaluate_publication_draft_snapshot(
            context=_context(), principal=_principal(), snapshot=snapshot, confirmation=None, enabled=True
        )
        self.assertEqual(result.disposition, PublicationDraftDisposition.SECOND_CONFIRMATION_REQUIRED)

        result = evaluate_publication_draft_snapshot(
            context=_context(),
            principal=_principal(),
            snapshot=snapshot,
            confirmation=_confirmation(snapshot, ai_transformation_disclosed=False),
            enabled=True,
        )
        self.assertEqual(result.disposition, PublicationDraftDisposition.AI_DISCLOSURE_REQUIRED)

    def test_fully_matching_snapshot_remains_policy_disabled_without_effect(self) -> None:
        snapshot = _snapshot(
            sources=(
                _source(requires_redaction=True, redaction_diff_hash=_digest("redaction-diff")),
            )
        )
        result = evaluate_publication_draft_snapshot(
            context=_context(),
            principal=_principal(),
            snapshot=snapshot,
            confirmation=_confirmation(snapshot),
            enabled=True,
        )
        self.assertEqual(result.disposition, PublicationDraftDisposition.POLICY_DISABLED)
        self.assertFalse(result.draft_write_allowed)
        self.assertFalse(result.publication_version_created)
        self.assertFalse(result.receipt_created)
        self.assertFalse(result.outbox_enqueued)

    def test_value_free_summary_does_not_expose_owner_or_vault_values(self) -> None:
        context = _context(vault_id="vault-private-marker", owner_subject_hash=_digest("owner-private-marker"))
        snapshot = _snapshot(
            vault_id=context.vault_id,
            owner_subject_hash=context.owner_subject_hash,
            sources=(_source(vault_id=context.vault_id),),
        )
        principal = PublicationAuthorizationPrincipal(
            kind=PublicationPrincipalKind.OWNER,
            vault_id=context.vault_id,
            subject_hash=context.owner_subject_hash,
        )
        result = evaluate_publication_draft_snapshot(
            context=context,
            principal=principal,
            snapshot=snapshot,
            confirmation=_confirmation(snapshot),
            enabled=True,
        )
        serialized = json.dumps(result.value_free_summary(), sort_keys=True)
        self.assertNotIn("vault-private-marker", serialized)
        self.assertNotIn(context.owner_subject_hash, serialized)

    def test_additive_migration_has_no_readable_draft_copy_or_release_flags(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8").lower()
        manifest = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0051")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertEqual(
            manifest["releaseFlags"],
            {
                "publicationDraftPreviewV1": False,
                "publicationDraftSnapshotV1": False,
                "publicationDraftWriterV1": False,
            },
        )
        for table in ("publication_drafts", "publication_draft_memory_versions"):
            self.assertIn(f"create table publication.{table}", sql)
            self.assertIn(f"revoke all on table publication.{table} from public;", sql)
        for forbidden in ("content_body", "source_payload", "object_url", "preview_url", "draft_text"):
            self.assertNotIn(forbidden, sql)

    def test_migration_loader_accepts_draft_snapshot_metadata(self) -> None:
        migrations = load_migrations(ROOT / "db/migrations")
        draft_snapshot = next(item for item in migrations if item.version == "0051")
        self.assertEqual(draft_snapshot.name, "publication_draft_snapshot")
        self.assertEqual(draft_snapshot.phase, "expand")

    def test_domain_has_no_route_persistence_network_or_worker_imports(self) -> None:
        source = (ROOT / "app/domain/publication/draft_snapshot.py").read_text(encoding="utf-8")
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
