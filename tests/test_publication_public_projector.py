"""G0 tests for the one-way, default-deny public projector boundary."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations
from app.domain.publication.public_projector import (
    PublicationProjectionEvent,
    PublicationProjectionEventKind,
    PublicationProjectionInputSource,
    PublicationProjectorCheckpoint,
    PublicationProjectorDisposition,
    PublicationProjectorState,
    evaluate_publication_projector,
)


ROOT = Path(__file__).parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0052_publication_public_projector.sql"
MIGRATION_JSON = ROOT / "db/migrations/0052_publication_public_projector.json"
PUBLICATION_ID = "d397c4e0-2d8d-466d-9a29-5a4ec336d2a7"
VERSION_ID = "efa96059-8a4a-4f8d-9dd7-2b52cbbeca91"
EVENT_ID = "199c22ff-5b8f-4b76-b1b5-f2e75ecdf1c7"


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _checkpoint(**overrides: object) -> PublicationProjectorCheckpoint:
    values: dict[str, object] = {
        "publication_id": PUBLICATION_ID,
        "vault_id": "vault-publication-owner",
        "last_event_sequence": 0,
        "state": PublicationProjectorState.PENDING_INDEX,
    }
    values.update(overrides)
    return PublicationProjectorCheckpoint(**values)  # type: ignore[arg-type]


def _event(**overrides: object) -> PublicationProjectionEvent:
    values: dict[str, object] = {
        "event_id": EVENT_ID,
        "publication_id": PUBLICATION_ID,
        "publication_version_id": VERSION_ID,
        "vault_id": "vault-publication-owner",
        "event_sequence": 1,
        "kind": PublicationProjectionEventKind.PUBLISHED,
        "version_content_hash": _digest("publication-version"),
        "policy_hash": _digest("publication-policy"),
        "input_source": PublicationProjectionInputSource.PUBLICATION_VERSION_EVENT,
        "external_index_requested": False,
        "object_copy_requested": False,
    }
    values.update(overrides)
    return PublicationProjectionEvent(**values)  # type: ignore[arg-type]


class PublicationPublicProjectorTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_invalid_inputs(self) -> None:
        result = evaluate_publication_projector(checkpoint=object(), event=object())
        self.assertEqual(result.disposition, PublicationProjectorDisposition.SHADOW_DISABLED)
        self.assertFalse(result.projection_write_allowed)

    def test_scope_mismatch_and_private_dependencies_fail_closed(self) -> None:
        result = evaluate_publication_projector(
            checkpoint=_checkpoint(vault_id="vault-other"), event=_event(), enabled=True
        )
        self.assertEqual(result.disposition, PublicationProjectorDisposition.SCOPE_MISMATCH)

        for source in (
            PublicationProjectionInputSource.PRIVATE_MEMORY_REPOSITORY,
            PublicationProjectionInputSource.PRIVATE_SEARCH_PROJECTION,
            PublicationProjectionInputSource.LEGACY_GUEST_INDEX,
        ):
            with self.subTest(source=source):
                result = evaluate_publication_projector(
                    checkpoint=_checkpoint(), event=_event(input_source=source), enabled=True
                )
                self.assertEqual(
                    result.disposition,
                    PublicationProjectorDisposition.PRIVATE_DEPENDENCY_REJECTED,
                )

    def test_external_index_and_object_copy_require_separate_gate(self) -> None:
        for overrides in (
            {"external_index_requested": True},
            {"object_copy_requested": True},
        ):
            with self.subTest(overrides=overrides):
                result = evaluate_publication_projector(
                    checkpoint=_checkpoint(), event=_event(**overrides), enabled=True
                )
                self.assertEqual(
                    result.disposition,
                    PublicationProjectorDisposition.EXTERNAL_PROVIDER_GATE_REQUIRED,
                )

    def test_duplicate_out_of_order_and_gap_events_fail_closed(self) -> None:
        result = evaluate_publication_projector(
            checkpoint=_checkpoint(last_event_sequence=1), event=_event(event_sequence=1), enabled=True
        )
        self.assertEqual(result.disposition, PublicationProjectorDisposition.DUPLICATE_OR_OUT_OF_ORDER)

        result = evaluate_publication_projector(
            checkpoint=_checkpoint(last_event_sequence=1), event=_event(event_sequence=3), enabled=True
        )
        self.assertEqual(result.disposition, PublicationProjectorDisposition.EVENT_GAP)

    def test_suspend_and_withdraw_remain_inaccessible(self) -> None:
        for kind, state in (
            (PublicationProjectionEventKind.SUSPENDED, PublicationProjectorState.SUSPENDED),
            (PublicationProjectionEventKind.WITHDRAWN, PublicationProjectorState.WITHDRAWN),
        ):
            with self.subTest(kind=kind):
                result = evaluate_publication_projector(
                    checkpoint=_checkpoint(), event=_event(kind=kind), enabled=True
                )
                self.assertEqual(result.disposition, PublicationProjectorDisposition.SUSPEND_OR_WITHDRAW)
                self.assertEqual(result.proposed_state, state)
                self.assertFalse(result.public_query_allowed)

    def test_valid_event_is_still_policy_disabled_and_value_minimized(self) -> None:
        result = evaluate_publication_projector(
            checkpoint=_checkpoint(), event=_event(), enabled=True
        )
        self.assertEqual(result.disposition, PublicationProjectorDisposition.POLICY_DISABLED)
        self.assertEqual(result.proposed_state, PublicationProjectorState.PENDING_INDEX)
        self.assertFalse(result.projection_write_allowed)
        self.assertFalse(result.public_query_allowed)
        self.assertFalse(result.external_index_allowed)
        self.assertFalse(result.object_copy_allowed)
        summary = json.dumps(result.value_free_summary(), sort_keys=True)
        self.assertNotIn("vault-publication-owner", summary)
        self.assertNotIn(PUBLICATION_ID, summary)
        self.assertNotIn(VERSION_ID, summary)

    def test_deterministic_candidate_hashes_are_stable_and_event_bound(self) -> None:
        first = evaluate_publication_projector(checkpoint=_checkpoint(), event=_event(), enabled=True)
        replay = evaluate_publication_projector(checkpoint=_checkpoint(), event=_event(), enabled=True)
        changed = evaluate_publication_projector(
            checkpoint=_checkpoint(),
            event=_event(policy_hash=_digest("different-policy")),
            enabled=True,
        )
        self.assertEqual(first.candidate_projection_hash, replay.candidate_projection_hash)
        self.assertEqual(first.candidate_public_citation_hash, replay.candidate_public_citation_hash)
        self.assertNotEqual(first.candidate_projection_hash, changed.candidate_projection_hash)

    def test_additive_migration_has_no_public_payload_or_release_flags(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8").lower()
        manifest = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0052")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertEqual(
            manifest["releaseFlags"],
            {
                "publicIndexProviderV1": False,
                "publicProjectorV1": False,
                "publicStoreQueryV1": False,
            },
        )
        for table in ("projector_checkpoints", "public_projection_candidates"):
            self.assertIn(f"create table publication.{table}", sql)
            self.assertIn(f"revoke all on table publication.{table} from public;", sql)
        for forbidden in ("content_body", "source_payload", "object_url", "preview_url", "search_text"):
            self.assertNotIn(forbidden, sql)

    def test_migration_loader_accepts_projector_metadata(self) -> None:
        migrations = load_migrations(ROOT / "db/migrations")
        projector = next(item for item in migrations if item.version == "0052")
        self.assertEqual(projector.name, "publication_public_projector")
        self.assertEqual(projector.phase, "expand")

    def test_domain_has_no_private_owner_truth_or_runtime_imports(self) -> None:
        source = (ROOT / "app/domain/publication/public_projector.py").read_text(encoding="utf-8")
        for forbidden in (
            "app.main",
            "app.services",
            "app.async_effects",
            "app.domain.owner_truth",
            "requests",
            "httpx",
            "psycopg",
            "boto3",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
