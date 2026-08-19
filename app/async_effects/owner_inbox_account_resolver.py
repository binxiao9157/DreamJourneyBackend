"""Resolve the current private inbox for one canonical or migrated owner.

New V4 accounts use the authenticated account identifier directly as their
Owner Subject and personal Vault identifier. Migrated accounts may still use
the verified legacy identity bridge. This resolver keeps both paths behind one
fail-closed contract so producers and workers validate the same account epoch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.async_effects.business_message_projection_repository import (
    InboxAccountSnapshot,
)
from app.async_effects.legacy_identity_inbox_bridge import (
    LegacyInboxAccountResolutionError,
    PostgresLegacyInboxAccountResolver,
)


class OwnerInboxAccountResolutionError(RuntimeError):
    """The requested Owner inbox is absent, inactive, or ambiguous."""


@dataclass(frozen=True)
class ResolvedOwnerInboxAccount:
    snapshot: InboxAccountSnapshot
    source: str

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, InboxAccountSnapshot):
            raise OwnerInboxAccountResolutionError("owner inbox snapshot is invalid")
        if self.source not in {"canonicalOwner", "legacyBridge"}:
            raise OwnerInboxAccountResolutionError("owner inbox source is invalid")


class PostgresOwnerInboxAccountResolver:
    """Resolve a canonical account first, then a verified migration bridge."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def resolve_active(self, subject_id: str, vault_id: str) -> ResolvedOwnerInboxAccount:
        normalized_subject_id = _identifier(subject_id, field="subject_id")
        normalized_vault_id = _identifier(vault_id, field="vault_id")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT account.payload AS account_payload,
                       vault.owner_subject_id, vault.vault_id, vault.status AS vault_state
                FROM users AS account
                INNER JOIN owner_truth.vaults AS vault
                    ON vault.owner_subject_id = account.id
                WHERE account.id = %s AND vault.vault_id = %s
                FOR SHARE OF account, vault
                """,
                (normalized_subject_id, normalized_vault_id),
            )
            row = cursor.fetchone()
        if row is not None:
            return ResolvedOwnerInboxAccount(
                snapshot=self._canonical_snapshot(
                    row,
                    subject_id=normalized_subject_id,
                    vault_id=normalized_vault_id,
                ),
                source="canonicalOwner",
            )

        try:
            legacy = PostgresLegacyInboxAccountResolver(self._connection).resolve_active(
                normalized_subject_id
            )
        except LegacyInboxAccountResolutionError as exc:
            raise OwnerInboxAccountResolutionError(
                "active owner inbox account is missing"
            ) from exc
        if legacy.snapshot.inbox_vault_id != normalized_vault_id:
            raise OwnerInboxAccountResolutionError(
                "legacy owner inbox does not match the requested vault"
            )
        return ResolvedOwnerInboxAccount(
            snapshot=legacy.snapshot,
            source="legacyBridge",
        )

    @staticmethod
    def _canonical_snapshot(
        row: Mapping[str, object],
        *,
        subject_id: str,
        vault_id: str,
    ) -> InboxAccountSnapshot:
        payload = row.get("account_payload")
        if not isinstance(payload, Mapping):
            raise OwnerInboxAccountResolutionError("owner account payload is malformed")
        if (
            str(row.get("owner_subject_id") or "") != subject_id
            or str(row.get("vault_id") or "") != vault_id
            or str(row.get("vault_state") or "") != "active"
            or str(payload.get("accessState") or "") != "active"
            or str(payload.get("deletionState") or "") != "active"
        ):
            raise OwnerInboxAccountResolutionError("owner inbox account is inactive")
        try:
            account_epoch = int(payload["authEpoch"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OwnerInboxAccountResolutionError(
                "owner account payload lacks an authentication epoch"
            ) from exc
        return InboxAccountSnapshot(
            inbox_subject_id=subject_id,
            inbox_vault_id=vault_id,
            account_epoch=account_epoch,
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


def resolve_owner_inbox_account(
    store: Any,
    *,
    subject_id: str,
    vault_id: str,
) -> ResolvedOwnerInboxAccount:
    """Resolve through the canonical seam, with legacy-only doubles supported."""

    resolver_factory = getattr(store, "async_effect_owner_inbox_account_resolver", None)
    if callable(resolver_factory):
        return resolver_factory().resolve_active(subject_id, vault_id)

    legacy_factory = getattr(store, "async_effect_legacy_inbox_account_resolver", None)
    if not callable(legacy_factory):
        raise OwnerInboxAccountResolutionError("owner inbox resolver is unavailable")
    try:
        legacy = legacy_factory().resolve_active(subject_id)
    except LegacyInboxAccountResolutionError as exc:
        raise OwnerInboxAccountResolutionError("active owner inbox account is missing") from exc
    if legacy.snapshot.inbox_vault_id != vault_id:
        raise OwnerInboxAccountResolutionError(
            "legacy owner inbox does not match the requested vault"
        )
    return ResolvedOwnerInboxAccount(snapshot=legacy.snapshot, source="legacyBridge")


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 127:
        raise OwnerInboxAccountResolutionError(f"{field} is invalid")
    return normalized


__all__ = [
    "OwnerInboxAccountResolutionError",
    "PostgresOwnerInboxAccountResolver",
    "ResolvedOwnerInboxAccount",
    "resolve_owner_inbox_account",
]
