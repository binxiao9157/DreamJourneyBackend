"""Fail-closed legacy identity bridge for internal message inbox resolution.

The V4 target model separates legacy ``users.id`` from verified Subjects and
Vaults.  This module consumes only an explicitly persisted bridge row; it
does not create a claim, infer a relationship, authorize a resource, issue a
session, or expose a public inbox.  It is intentionally usable only by an
internal async-effect caller after that caller has independently established
resource authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import re
from typing import Any, Mapping, Sequence

from app.async_effects.business_message_projection_repository import InboxAccountSnapshot


LEGACY_IDENTITY_INBOX_BRIDGE_SCHEMA_VERSION = "legacy-identity-inbox-bridge-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class LegacyInboxAccountResolutionError(ValueError):
    """An inbox bridge record is absent, malformed, or not currently active."""


class LegacyAliasClaimState(str, Enum):
    CLAIM_PENDING = "claim_pending"
    VERIFIED = "verified"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise LegacyInboxAccountResolutionError(f"{field} must be an opaque identifier")
    return normalized


def _sha256_hex(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise LegacyInboxAccountResolutionError(f"{field} must be a SHA-256 digest")
    return normalized


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LegacyInboxAccountResolutionError(f"{field} must be a non-negative integer")
    return value


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LegacyInboxAccountBinding:
    """One persisted legacy alias bridge plus its current authority snapshot.

    ``legacy_user_id`` is retained only for an internal database join.  It is
    never returned by value-free summaries or the public `InboxAccountSnapshot`.
    """

    legacy_user_id: str
    legacy_alias_hash: str
    subject_id: str
    vault_id: str
    claim_state: LegacyAliasClaimState
    identity_proof_subject_id: str | None
    subject_state: str
    vault_owner_subject_id: str
    vault_state: str
    account_access_state: str
    account_deletion_state: str
    account_auth_epoch: int
    bridge_row_version: int

    def __post_init__(self) -> None:
        for field in (
            "legacy_user_id",
            "subject_id",
            "vault_id",
            "vault_owner_subject_id",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field=field))
        object.__setattr__(
            self,
            "legacy_alias_hash",
            _sha256_hex(self.legacy_alias_hash, field="legacy_alias_hash"),
        )
        object.__setattr__(self, "claim_state", LegacyAliasClaimState(self.claim_state))
        if self.identity_proof_subject_id is not None:
            object.__setattr__(
                self,
                "identity_proof_subject_id",
                _identifier(
                    self.identity_proof_subject_id,
                    field="identity_proof_subject_id",
                ),
            )
        for field, allowed in (
            ("subject_state", {"active", "suspended", "retired"}),
            ("vault_state", {"active", "suspended", "closed"}),
            (
                "account_access_state",
                {"active", "suspended_restorable", "purged"},
            ),
            ("account_deletion_state", {"active", "softDeleted", "purged"}),
        ):
            value = str(getattr(self, field) or "").strip()
            if value not in allowed:
                raise LegacyInboxAccountResolutionError(f"{field} is invalid")
            object.__setattr__(self, field, value)
        object.__setattr__(
            self,
            "account_auth_epoch",
            _non_negative_int(self.account_auth_epoch, field="account_auth_epoch"),
        )
        bridge_version = _non_negative_int(
            self.bridge_row_version,
            field="bridge_row_version",
        )
        if bridge_version < 1:
            raise LegacyInboxAccountResolutionError("bridge_row_version must be positive")
        object.__setattr__(self, "bridge_row_version", bridge_version)

    def resolve_active_snapshot(self) -> InboxAccountSnapshot:
        reasons: list[str] = []
        if self.claim_state is not LegacyAliasClaimState.VERIFIED:
            reasons.append("legacyAliasNotVerified")
        if self.identity_proof_subject_id != self.subject_id:
            reasons.append("identityProofSubjectMismatch")
        if self.subject_state != "active":
            reasons.append("subjectNotActive")
        if self.vault_state != "active":
            reasons.append("vaultNotActive")
        if self.vault_owner_subject_id != self.subject_id:
            reasons.append("vaultOwnerSubjectMismatch")
        if self.account_access_state != "active":
            reasons.append("accountAccessNotActive")
        if self.account_deletion_state != "active":
            reasons.append("accountDeletionNotActive")
        if reasons:
            raise LegacyInboxAccountResolutionError(
                "active inbox account binding is unavailable: " + ",".join(sorted(reasons))
            )
        return InboxAccountSnapshot(
            inbox_subject_id=self.subject_id,
            inbox_vault_id=self.vault_id,
            account_epoch=self.account_auth_epoch,
        )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "accountAuthEpoch": self.account_auth_epoch,
            "bridgeRowVersion": self.bridge_row_version,
            "claimState": self.claim_state.value,
            "legacyAliasHash": self.legacy_alias_hash,
            "schemaVersion": LEGACY_IDENTITY_INBOX_BRIDGE_SCHEMA_VERSION,
            "subjectDigest": _digest(self.subject_id),
            "vaultDigest": _digest(self.vault_id),
        }


@dataclass(frozen=True)
class ResolvedLegacyInboxAccount:
    binding: LegacyInboxAccountBinding
    snapshot: InboxAccountSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.binding, LegacyInboxAccountBinding):
            raise LegacyInboxAccountResolutionError("legacy inbox account binding is required")
        if not isinstance(self.snapshot, InboxAccountSnapshot):
            raise LegacyInboxAccountResolutionError("inbox account snapshot is required")
        if (
            self.snapshot.inbox_subject_id != self.binding.subject_id
            or self.snapshot.inbox_vault_id != self.binding.vault_id
            or self.snapshot.account_epoch != self.binding.account_auth_epoch
        ):
            raise LegacyInboxAccountResolutionError(
                "inbox account snapshot does not match the verified bridge"
            )

    def value_free_summary(self) -> Mapping[str, object]:
        return {
            "binding": self.binding.value_free_summary(),
            "snapshot": self.snapshot.value_free_summary(),
            "schemaVersion": LEGACY_IDENTITY_INBOX_BRIDGE_SCHEMA_VERSION,
        }


class InMemoryLegacyInboxAccountResolver:
    """Small deterministic resolver for contract tests and QA-only callers."""

    def __init__(self, bindings: Sequence[LegacyInboxAccountBinding] = ()) -> None:
        by_subject: dict[str, LegacyInboxAccountBinding] = {}
        for binding in bindings:
            if not isinstance(binding, LegacyInboxAccountBinding):
                raise TypeError("legacy inbox account bindings must be typed")
            if binding.subject_id in by_subject:
                raise LegacyInboxAccountResolutionError(
                    "multiple legacy inbox bindings exist for the same subject"
                )
            by_subject[binding.subject_id] = binding
        self._by_subject = by_subject

    def resolve_active(self, subject_id: str) -> ResolvedLegacyInboxAccount:
        normalized_subject_id = _identifier(subject_id, field="subject_id")
        binding = self._by_subject.get(normalized_subject_id)
        if binding is None:
            raise LegacyInboxAccountResolutionError("active inbox account binding is missing")
        return ResolvedLegacyInboxAccount(binding=binding, snapshot=binding.resolve_active_snapshot())


class PostgresLegacyInboxAccountResolver:
    """Read-only bridge resolver bound to an existing Postgres unit of work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def resolve_active(self, subject_id: str) -> ResolvedLegacyInboxAccount:
        normalized_subject_id = _identifier(subject_id, field="subject_id")
        with self._cursor() as cursor:
            cursor.execute(self._select_sql(), (normalized_subject_id,))
            rows = cursor.fetchall()
        if len(rows) != 1:
            raise LegacyInboxAccountResolutionError("active inbox account binding is missing")
        binding = self._binding_from_row(rows[0])
        return ResolvedLegacyInboxAccount(binding=binding, snapshot=binding.resolve_active_snapshot())

    @staticmethod
    def _select_sql() -> str:
        return """
            SELECT alias.legacy_account_user_id, alias.legacy_alias_hash,
                   alias.subject_id, alias.vault_id, alias.claim_state,
                   proof.subject_id AS identity_proof_subject_id,
                   subject.status AS subject_state,
                   vault.owner_subject_id AS vault_owner_subject_id,
                   vault.status AS vault_state,
                   account.payload AS account_payload,
                   alias.row_version AS bridge_row_version
            FROM legacy_identity_aliases AS alias
            INNER JOIN users AS account
                ON account.id = alias.legacy_account_user_id
            INNER JOIN subjects AS subject
                ON subject.id = alias.subject_id
            INNER JOIN owner_truth.vaults AS vault
                ON vault.vault_id = alias.vault_id
            LEFT JOIN identity_proofs AS proof
                ON proof.id = alias.identity_proof_id
            WHERE alias.subject_id = %s
            FOR SHARE OF alias, account, subject, vault
        """

    @staticmethod
    def _binding_from_row(row: Mapping[str, object]) -> LegacyInboxAccountBinding:
        try:
            payload = row["account_payload"]
            if not isinstance(payload, Mapping):
                raise LegacyInboxAccountResolutionError("legacy account payload is malformed")
            if "accessState" not in payload or "deletionState" not in payload or "authEpoch" not in payload:
                raise LegacyInboxAccountResolutionError(
                    "legacy account payload lacks lifecycle fencing"
                )
            return LegacyInboxAccountBinding(
                legacy_user_id=str(row["legacy_account_user_id"]),
                legacy_alias_hash=str(row["legacy_alias_hash"]),
                subject_id=str(row["subject_id"]),
                vault_id=str(row["vault_id"]),
                claim_state=LegacyAliasClaimState(str(row["claim_state"])),
                identity_proof_subject_id=(
                    None
                    if row.get("identity_proof_subject_id") is None
                    else str(row["identity_proof_subject_id"])
                ),
                subject_state=str(row["subject_state"]),
                vault_owner_subject_id=str(row["vault_owner_subject_id"]),
                vault_state=str(row["vault_state"]),
                account_access_state=str(payload["accessState"]),
                account_deletion_state=str(payload["deletionState"]),
                account_auth_epoch=payload["authEpoch"],
                bridge_row_version=row["bridge_row_version"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, LegacyInboxAccountResolutionError):
                raise
            raise LegacyInboxAccountResolutionError(
                "legacy inbox account bridge row is malformed"
            ) from exc

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "InMemoryLegacyInboxAccountResolver",
    "LEGACY_IDENTITY_INBOX_BRIDGE_SCHEMA_VERSION",
    "LegacyAliasClaimState",
    "LegacyInboxAccountBinding",
    "LegacyInboxAccountResolutionError",
    "PostgresLegacyInboxAccountResolver",
    "ResolvedLegacyInboxAccount",
]
