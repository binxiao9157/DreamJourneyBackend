from __future__ import annotations

import unittest

from app.async_effects.owner_inbox_account_resolver import (
    OwnerInboxAccountResolutionError,
    PostgresOwnerInboxAccountResolver,
)


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.parameters = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _query, parameters):
        self.parameters = parameters

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.cursor_instance = _Cursor(row)

    def cursor(self, **_kwargs):
        return self.cursor_instance


class OwnerInboxAccountResolverTests(unittest.TestCase):
    def test_canonical_owner_account_resolves_without_legacy_alias(self) -> None:
        connection = _Connection(
            {
                "account_payload": {
                    "accessState": "active",
                    "deletionState": "active",
                    "authEpoch": 3,
                },
                "owner_subject_id": "owner-canonical",
                "vault_id": "vault-canonical",
                "vault_state": "active",
            }
        )

        resolved = PostgresOwnerInboxAccountResolver(connection).resolve_active(
            "owner-canonical",
            "vault-canonical",
        )

        self.assertEqual(resolved.source, "canonicalOwner")
        self.assertEqual(resolved.snapshot.inbox_subject_id, "owner-canonical")
        self.assertEqual(resolved.snapshot.inbox_vault_id, "vault-canonical")
        self.assertEqual(resolved.snapshot.account_epoch, 3)
        self.assertEqual(
            connection.cursor_instance.parameters,
            ("owner-canonical", "vault-canonical"),
        )

    def test_inactive_canonical_account_fails_closed_without_legacy_fallback(self) -> None:
        connection = _Connection(
            {
                "account_payload": {
                    "accessState": "suspended_restorable",
                    "deletionState": "softDeleted",
                    "authEpoch": 4,
                },
                "owner_subject_id": "owner-canonical",
                "vault_id": "vault-canonical",
                "vault_state": "active",
            }
        )

        with self.assertRaises(OwnerInboxAccountResolutionError):
            PostgresOwnerInboxAccountResolver(connection).resolve_active(
                "owner-canonical",
                "vault-canonical",
            )

    def test_canonical_account_requires_authentication_epoch(self) -> None:
        connection = _Connection(
            {
                "account_payload": {
                    "accessState": "active",
                    "deletionState": "active",
                },
                "owner_subject_id": "owner-canonical",
                "vault_id": "vault-canonical",
                "vault_state": "active",
            }
        )

        with self.assertRaises(OwnerInboxAccountResolutionError):
            PostgresOwnerInboxAccountResolver(connection).resolve_active(
                "owner-canonical",
                "vault-canonical",
            )


if __name__ == "__main__":
    unittest.main()
