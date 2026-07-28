"""G0 tests for Publication update, suspend/withdraw and propagation contracts."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import unittest

from app.db.migrator import load_migrations
from app.domain.publication.lifecycle_propagation import (
    PublicationLifecycleAction,
    PublicationLifecycleCommand,
    PublicationLifecycleDisposition,
    PublicationLifecycleState,
    PublicationLifecycleTrigger,
    PublicationPropagationLayer,
    evaluate_publication_lifecycle_propagation,
)
from app.domain.publication.schema_authz import (
    PublicationAuthorizationContext,
    PublicationAuthorizationPrincipal,
    PublicationPrincipalKind,
)


ROOT = Path(__file__).parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0055_publication_lifecycle_propagation.sql"
MIGRATION_JSON = ROOT / "db/migrations/0055_publication_lifecycle_propagation.json"
PUBLICATION_ID = "1614fc46-55f6-471b-af80-1e882c12f742"
VERSION_ID = "78469531-3479-4f57-ae3f-64b02e0af579"
NEW_VERSION_ID = "94621311-ddd6-416e-80b8-60fa6b4f50d5"
COMMAND_ID = "f77ddc7f-6950-4c5e-ac74-393f01e2c42f"


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _context(**overrides: object) -> PublicationAuthorizationContext:
    values: dict[str, object] = {
        "vault_id": "vault-publication-owner",
        "owner_subject_hash": _digest("owner"),
        "authority_epoch": 7,
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


def _command(**overrides: object) -> PublicationLifecycleCommand:
    context = _context()
    values: dict[str, object] = {
        "command_id": COMMAND_ID,
        "publication_id": PUBLICATION_ID,
        "publication_version_id": VERSION_ID,
        "vault_id": context.vault_id,
        "owner_subject_hash": context.owner_subject_hash,
        "authority_epoch": context.authority_epoch,
        "action": PublicationLifecycleAction.WITHDRAW,
        "trigger": PublicationLifecycleTrigger.OWNER_ACTION,
        "current_state": PublicationLifecycleState.PUBLISHED,
        "transition_sequence": 2,
        "previous_transition_sequence": 1,
        "request_hash": _digest("lifecycle-request"),
        "policy_hash": _digest("lifecycle-policy"),
        "propagation_layers": tuple(PublicationPropagationLayer),
        "active_access_observation_count": 0,
    }
    values.update(overrides)
    return PublicationLifecycleCommand(**values)  # type: ignore[arg-type]


def _evaluate(**overrides: object):
    values: dict[str, object] = {
        "context": _context(),
        "principal": _principal(),
        "command": _command(),
        "enabled": True,
    }
    values.update(overrides)
    return evaluate_publication_lifecycle_propagation(**values)


class PublicationLifecyclePropagationTests(unittest.TestCase):
    def test_disabled_path_does_not_inspect_inputs(self) -> None:
        result = evaluate_publication_lifecycle_propagation(
            context=object(), principal=object(), command=object()
        )
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.SHADOW_DISABLED)
        self.assertFalse(result.publication_mutated)
        self.assertFalse(result.gateway_access_denied)
        self.assertFalse(result.propagation_receipt_persisted)

    def test_owner_scope_and_sequence_fail_closed(self) -> None:
        result = _evaluate(principal=_principal(vault_id="vault-other"))
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.OWNER_SCOPE_DENIED)

        result = _evaluate(command=_command(transition_sequence=1, previous_transition_sequence=1))
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.DUPLICATE_OR_OUT_OF_ORDER)

    def test_private_triggers_cannot_silently_update_and_withdrawn_cannot_republish(self) -> None:
        result = _evaluate(
            command=_command(
                action=PublicationLifecycleAction.UPDATE,
                trigger=PublicationLifecycleTrigger.MEMORY_CORRECTION,
            )
        )
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.PRIVATE_TRIGGER_SUSPEND_REQUIRED)

        result = _evaluate(
            command=_command(
                action=PublicationLifecycleAction.UPDATE,
                current_state=PublicationLifecycleState.WITHDRAWN,
            )
        )
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.WITHDRAWN_REPUBLISH_DENIED)

    def test_update_requires_new_version_and_second_confirmation(self) -> None:
        result = _evaluate(command=_command(action=PublicationLifecycleAction.UPDATE))
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.UPDATE_REQUIRES_NEW_VERSION)

        result = _evaluate(
            command=_command(
                action=PublicationLifecycleAction.UPDATE,
                new_publication_version_id=NEW_VERSION_ID,
                new_pinned_memory_version_hash=_digest("new-memory-version"),
            )
        )
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.UPDATE_CONFIRMATION_REQUIRED)

        result = _evaluate(
            command=_command(
                action=PublicationLifecycleAction.UPDATE,
                new_publication_version_id=NEW_VERSION_ID,
                new_pinned_memory_version_hash=_digest("new-memory-version"),
                second_confirmation_hash=_digest("second-confirmation"),
            )
        )
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.POLICY_DISABLED)
        self.assertFalse(result.publication_mutated)

    def test_suspend_withdraw_requires_immediate_deny_plan(self) -> None:
        result = _evaluate(
            command=_command(propagation_layers=(PublicationPropagationLayer.PUBLIC_GATEWAY,))
        )
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.ACCESS_DENY_PLAN_REQUIRED)
        self.assertIn(PublicationPropagationLayer.VISITOR_SESSION, result.required_deny_layers)
        self.assertFalse(result.gateway_access_denied)

        result = _evaluate(command=_command(active_access_observation_count=3))
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.EXTERNAL_CLEANUP_GATES_REQUIRED)
        self.assertEqual(result.active_access_observation_count, 3)
        self.assertTrue(result.external_cleanup_required)

    def test_external_copy_requires_external_gates(self) -> None:
        internal_layers = tuple(
            layer for layer in PublicationPropagationLayer if layer not in {
                PublicationPropagationLayer.EXTERNAL_INDEX,
                PublicationPropagationLayer.OBJECT_STORE,
                PublicationPropagationLayer.CDN,
            }
        )
        result = _evaluate(
            command=_command(propagation_layers=internal_layers, external_copy_observed=True)
        )
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.EXTERNAL_CLEANUP_GATES_REQUIRED)
        self.assertTrue(result.external_cleanup_required)
        self.assertFalse(result.external_cleanup_performed)

    def test_internal_deny_plan_still_remains_policy_disabled(self) -> None:
        layers = tuple(
            layer for layer in PublicationPropagationLayer if layer not in {
                PublicationPropagationLayer.EXTERNAL_INDEX,
                PublicationPropagationLayer.OBJECT_STORE,
                PublicationPropagationLayer.CDN,
            }
        )
        result = _evaluate(command=_command(propagation_layers=layers))
        self.assertEqual(result.disposition, PublicationLifecycleDisposition.POLICY_DISABLED)
        self.assertFalse(result.grant_revoked)
        self.assertFalse(result.visitor_session_closed)
        self.assertFalse(result.index_or_cache_cleared)

    def test_value_free_summary_does_not_expose_owner_or_vault_values(self) -> None:
        context = _context(vault_id="vault-private-marker", owner_subject_hash=_digest("owner-private-marker"))
        command = _command(vault_id=context.vault_id, owner_subject_hash=context.owner_subject_hash)
        principal = PublicationAuthorizationPrincipal(
            kind=PublicationPrincipalKind.OWNER,
            vault_id=context.vault_id,
            subject_hash=context.owner_subject_hash,
        )
        result = _evaluate(context=context, principal=principal, command=command)
        serialized = json.dumps(result.value_free_summary(), sort_keys=True)
        self.assertNotIn("vault-private-marker", serialized)
        self.assertNotIn(context.owner_subject_hash, serialized)

    def test_additive_migration_and_manifest_are_default_off(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8").lower()
        manifest = json.loads(MIGRATION_JSON.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0055")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(
            manifest["releaseFlags"],
            {
                "publicationExternalCleanupV1": False,
                "publicationLifecycleCommandV1": False,
                "publicationRevokePropagationV1": False,
            },
        )
        self.assertIn("create table publication.lifecycle_transition_receipts", sql)
        self.assertIn("create table publication.propagation_cleanup_candidates", sql)
        self.assertIn("revoke all on table publication.lifecycle_transition_receipts from public;", sql)
        for forbidden in ("content_body", "source_payload", "object_url", "preview_url", "search_text"):
            self.assertNotIn(forbidden, sql)

    def test_migration_loader_and_no_route_persistence_network_dependency(self) -> None:
        migrations = load_migrations(ROOT / "db/migrations")
        metadata = next(item for item in migrations if item.version == "0055")
        self.assertEqual(metadata.name, "publication_lifecycle_propagation")

        source = (ROOT / "app/domain/publication/lifecycle_propagation.py").read_text(encoding="utf-8")
        for forbidden in (
            "app.main", "app.services", "app.async_effects", "app.domain.owner_truth", "requests", "httpx", "psycopg", "boto3"
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
