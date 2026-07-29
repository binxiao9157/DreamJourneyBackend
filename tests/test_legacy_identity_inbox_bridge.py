from __future__ import annotations

from hashlib import sha256
import unittest

from app.async_effects.legacy_identity_inbox_bridge import (
    InMemoryLegacyInboxAccountResolver,
    LegacyAliasClaimState,
    LegacyInboxAccountBinding,
    LegacyInboxAccountResolutionError,
    PostgresLegacyInboxAccountResolver,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _binding(**changes: object) -> LegacyInboxAccountBinding:
    values: dict[str, object] = {
        "legacy_user_id": "user_legacy_owner",
        "legacy_alias_hash": _digest("legacy-owner-a"),
        "subject_id": "sub_owner_a",
        "vault_id": "vault_owner_a",
        "claim_state": LegacyAliasClaimState.VERIFIED,
        "identity_proof_subject_id": "sub_owner_a",
        "subject_state": "active",
        "vault_owner_subject_id": "sub_owner_a",
        "vault_state": "active",
        "account_access_state": "active",
        "account_deletion_state": "active",
        "account_auth_epoch": 7,
        "bridge_row_version": 1,
    }
    values.update(changes)
    return LegacyInboxAccountBinding(**values)  # type: ignore[arg-type]


class _Cursor:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.query = ""
        self.params: tuple[object, ...] = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.query = query
        self.params = params

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.cursor_value = _Cursor(rows)

    def cursor(self, **_kwargs):
        return self.cursor_value


class LegacyIdentityInboxBridgeTests(unittest.TestCase):
    def test_verified_active_binding_resolves_to_explicit_inbox_snapshot(self) -> None:
        result = InMemoryLegacyInboxAccountResolver([_binding()]).resolve_active("sub_owner_a")

        self.assertEqual(result.snapshot.inbox_subject_id, "sub_owner_a")
        self.assertEqual(result.snapshot.inbox_vault_id, "vault_owner_a")
        self.assertEqual(result.snapshot.account_epoch, 7)

    def test_value_free_summary_does_not_leak_legacy_user_identifier(self) -> None:
        result = InMemoryLegacyInboxAccountResolver([_binding()]).resolve_active("sub_owner_a")
        summary = result.value_free_summary()

        self.assertNotIn("user_legacy_owner", str(summary))
        self.assertNotIn("sub_owner_a", str(summary))
        self.assertNotIn("vault_owner_a", str(summary))
        self.assertEqual(summary["binding"]["legacyAliasHash"], _digest("legacy-owner-a"))

    def test_missing_or_duplicate_subject_binding_fails_closed(self) -> None:
        resolver = InMemoryLegacyInboxAccountResolver()
        with self.assertRaisesRegex(LegacyInboxAccountResolutionError, "missing"):
            resolver.resolve_active("sub_owner_a")

        duplicate = _binding(legacy_user_id="user_legacy_second")
        with self.assertRaisesRegex(LegacyInboxAccountResolutionError, "multiple"):
            InMemoryLegacyInboxAccountResolver([_binding(), duplicate])

    def test_non_verified_or_mismatched_identity_evidence_cannot_resolve(self) -> None:
        for changes, reason in (
            ({"claim_state": LegacyAliasClaimState.CLAIM_PENDING, "identity_proof_subject_id": None}, "legacyAliasNotVerified"),
            ({"identity_proof_subject_id": "sub_owner_b"}, "identityProofSubjectMismatch"),
            ({"subject_state": "suspended"}, "subjectNotActive"),
            ({"vault_state": "suspended"}, "vaultNotActive"),
            ({"vault_owner_subject_id": "sub_owner_b"}, "vaultOwnerSubjectMismatch"),
        ):
            with self.subTest(reason=reason):
                resolver = InMemoryLegacyInboxAccountResolver([_binding(**changes)])
                with self.assertRaisesRegex(LegacyInboxAccountResolutionError, reason):
                    resolver.resolve_active("sub_owner_a")

    def test_account_lifecycle_or_epoch_malformed_fails_closed(self) -> None:
        for changes, reason in (
            ({"account_access_state": "suspended_restorable"}, "accountAccessNotActive"),
            ({"account_deletion_state": "softDeleted"}, "accountDeletionNotActive"),
            ({"account_auth_epoch": True}, "account_auth_epoch"),
            ({"account_auth_epoch": -1}, "account_auth_epoch"),
        ):
            with self.subTest(reason=reason):
                if reason == "account_auth_epoch":
                    with self.assertRaisesRegex(LegacyInboxAccountResolutionError, reason):
                        _binding(**changes)
                    continue
                resolver = InMemoryLegacyInboxAccountResolver([_binding(**changes)])
                with self.assertRaisesRegex(LegacyInboxAccountResolutionError, reason):
                    resolver.resolve_active("sub_owner_a")

    def test_postgres_resolver_reads_only_verified_bridge_coordinates(self) -> None:
        row = {
            "legacy_account_user_id": "user_legacy_owner",
            "legacy_alias_hash": _digest("legacy-owner-a"),
            "subject_id": "sub_owner_a",
            "vault_id": "vault_owner_a",
            "claim_state": "verified",
            "identity_proof_subject_id": "sub_owner_a",
            "subject_state": "active",
            "vault_owner_subject_id": "sub_owner_a",
            "vault_state": "active",
            "account_payload": {
                "accessState": "active",
                "deletionState": "active",
                "authEpoch": 7,
            },
            "bridge_row_version": 1,
        }
        connection = _Connection([row])
        result = PostgresLegacyInboxAccountResolver(connection).resolve_active("sub_owner_a")

        self.assertEqual(result.snapshot.inbox_vault_id, "vault_owner_a")
        self.assertEqual(connection.cursor_value.params, ("sub_owner_a",))
        self.assertIn("FOR SHARE", connection.cursor_value.query)
        self.assertNotIn("family_relationships", connection.cursor_value.query)
        self.assertNotIn("access_grants", connection.cursor_value.query)

    def test_postgres_resolver_rejects_missing_lifecycle_fences(self) -> None:
        row = {
            "legacy_account_user_id": "user_legacy_owner",
            "legacy_alias_hash": _digest("legacy-owner-a"),
            "subject_id": "sub_owner_a",
            "vault_id": "vault_owner_a",
            "claim_state": "verified",
            "identity_proof_subject_id": "sub_owner_a",
            "subject_state": "active",
            "vault_owner_subject_id": "sub_owner_a",
            "vault_state": "active",
            "account_payload": {"authEpoch": 7},
            "bridge_row_version": 1,
        }
        with self.assertRaisesRegex(LegacyInboxAccountResolutionError, "lacks lifecycle fencing"):
            PostgresLegacyInboxAccountResolver(_Connection([row])).resolve_active("sub_owner_a")


if __name__ == "__main__":
    unittest.main()
