from __future__ import annotations

import unittest

from app.async_effects.owner_inbox_account_resolver import (
    OwnerInboxAccountResolutionError,
    PostgresOwnerInboxAccountResolver,
)


class _Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.parameters = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _query, parameters):
        self.parameters = parameters

    def fetchone(self):
        return self.connection.rows.pop(0)


class _Connection:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.cursor_instances = []

    def cursor(self, **_kwargs):
        cursor = _Cursor(self)
        self.cursor_instances.append(cursor)
        return cursor


class OwnerInboxAccountResolverTests(unittest.TestCase):
    def test_native_v4_subject_resolves_without_legacy_users_row(self) -> None:
        connection = _Connection(
            {
                "subject_state": "active",
                "active_identity_binding": True,
                "owner_subject_id": "subject-canonical",
                "vault_id": "subject-canonical",
                "vault_state": "active",
            }
        )

        resolved = PostgresOwnerInboxAccountResolver(connection).resolve_active(
            "subject-canonical",
            "subject-canonical",
        )

        self.assertEqual(resolved.source, "canonicalOwner")
        self.assertEqual(resolved.snapshot.inbox_subject_id, "subject-canonical")
        self.assertEqual(resolved.snapshot.inbox_vault_id, "subject-canonical")
        self.assertEqual(resolved.snapshot.account_epoch, 0)
        self.assertEqual(
            connection.cursor_instances[0].parameters,
            ("subject-canonical", "subject-canonical"),
        )

    def test_native_v4_subject_requires_active_identity_binding(self) -> None:
        connection = _Connection(
            {
                "subject_state": "active",
                "active_identity_binding": False,
                "owner_subject_id": "subject-canonical",
                "vault_id": "subject-canonical",
                "vault_state": "active",
            }
        )

        with self.assertRaises(OwnerInboxAccountResolutionError):
            PostgresOwnerInboxAccountResolver(connection).resolve_active(
                "subject-canonical",
                "subject-canonical",
            )

    def test_canonical_owner_account_resolves_without_legacy_alias(self) -> None:
        connection = _Connection(
            None,
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
            connection.cursor_instances[1].parameters,
            ("owner-canonical", "vault-canonical"),
        )

    def test_inactive_canonical_account_fails_closed_without_legacy_fallback(self) -> None:
        connection = _Connection(
            None,
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
            None,
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
