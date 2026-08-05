"""Executable, default-off publication access-deny lifecycle commands.

This module is intentionally narrower than the existing G0 lifecycle planner.
It performs only the local, transactional safety boundary that the product can
truthfully guarantee today: mark a confirmed public projection unavailable,
revoke its ShareGrants and Visitor sessions, and retain a redacted receipt.

It deliberately does *not* claim that an external index, cache, CDN, object
store, or Digital Human provider has been cleared.  Those effects remain
separate worker/provider contracts and are reported as pending or
not-applicable in the receipt.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.contracts import OwnerTruthContractError
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.publication_authority import (
    InMemoryPublicationAuthorityRepository,
    PublicationAuthorityAccessDenied,
    PublicationAuthorityConflict,
    PublicationAuthorityNotPublishable,
)
from app.services.publication_visitor_access import InMemoryPublicationVisitorAccessRepository
from app.async_effects.provider_effect_repository import InMemoryProviderEffectRepository
from app.async_effects.repository import InMemoryEffectKernelRepository
from app.services.publication_external_cleanup import (
    InMemoryPublicationExternalCleanupRepository,
    PublicationExternalCleanupCoordinator,
    PublicationExternalCleanupStatus,
)


PUBLICATION_LIFECYCLE_EXECUTION_SCHEMA_VERSION = "publication-lifecycle-v1"
_NAMESPACE = UUID("9b9a6bde-4db1-44c2-934c-ef4e759d77c4")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class PublicationLifecycleExecutionError(OwnerTruthContractError):
    """A publication lifecycle command cannot be applied safely."""


class PublicationLifecycleExecutionDisabled(PublicationLifecycleExecutionError):
    """The default-off lifecycle route was not admitted by its QA gate."""


class PublicationLifecycleExecutionAccessDenied(PublicationLifecycleExecutionError):
    """The requested publication is outside the active Owner's vault."""


class PublicationLifecycleExecutionUnavailable(PublicationLifecycleExecutionError):
    """The publication is not in a state that can receive this command."""


class PublicationLifecycleExecutionConflict(PublicationLifecycleExecutionError):
    """A replay or authority epoch is inconsistent with the saved command."""


def _uuid(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise PublicationLifecycleExecutionError(f"{field_name} must be a UUID") from exc


def _nonnegative_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicationLifecycleExecutionError(
            f"{field_name} must be a non-negative integer"
        )
    return value


def _digest(value: Mapping[str, object]) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _receipt_id(*, vault_id: str, command_id_hash: str) -> str:
    return str(uuid5(_NAMESPACE, f"publication-lifecycle:{vault_id}:{command_id_hash}"))


@dataclass(frozen=True)
class PublicationLifecycleExecutionCommand:
    command_id: str
    publication_id: str
    expected_authority_epoch: int
    action: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _uuid(self.command_id, field_name="command_id"))
        object.__setattr__(
            self,
            "publication_id",
            _uuid(self.publication_id, field_name="publication_id"),
        )
        object.__setattr__(
            self,
            "expected_authority_epoch",
            _nonnegative_int(self.expected_authority_epoch, field_name="expected_authority_epoch"),
        )
        action = str(self.action or "").strip()
        if action not in {"withdraw", "suspend"}:
            raise PublicationLifecycleExecutionError("action must be withdraw or suspend")
        object.__setattr__(self, "action", action)

    @property
    def command_id_hash(self) -> str:
        return sha256(self.command_id.encode("utf-8")).hexdigest()

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "action": self.action,
                "expectedAuthorityEpoch": self.expected_authority_epoch,
                "publicationId": self.publication_id,
            }
        )


@dataclass(frozen=True)
class PublicationLifecycleExecutionResult:
    outcome: str
    vault_id: str
    publication_id: str
    publication_version_id: str
    publication_state: str
    projection_state: str
    conflict_hold: bool
    revoked_grant_count: int
    revoked_visitor_session_count: int
    receipt_id: str
    reason_code: str
    access_deny_state: str = "completed"
    public_index_cleanup_state: str = "pending"
    runtime_cleanup_state: str = "notApplicable"
    # P2-S4C may attach post-commit, redacted external effect status here.
    # Empty means that local denial succeeded but no async effect coordinates
    # were accepted in the same response path; it never means cleanup is done.
    external_cleanup: tuple[PublicationExternalCleanupStatus, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome not in {"withdrawn", "suspended", "deduplicated"}:
            raise PublicationLifecycleExecutionError("lifecycle outcome is invalid")
        if self.publication_state not in {"withdrawn", "suspended"}:
            raise PublicationLifecycleExecutionError("publication lifecycle state is invalid")
        if self.projection_state not in {"withdrawn", "suspended"}:
            raise PublicationLifecycleExecutionError("projection lifecycle state is invalid")
        expected_state = "withdrawn" if self.outcome == "withdrawn" else None
        if expected_state is not None and (
            self.publication_state != expected_state or self.projection_state != expected_state
        ):
            raise PublicationLifecycleExecutionError("withdrawal result state is inconsistent")
        if self.outcome == "suspended" and (
            self.publication_state != "suspended" or self.projection_state != "suspended"
        ):
            raise PublicationLifecycleExecutionError("suspension result state is inconsistent")
        for field_name in (
            "publication_id",
            "publication_version_id",
            "receipt_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _uuid(getattr(self, field_name), field_name=field_name),
            )
        vault_id = str(self.vault_id or "").strip()
        if not _IDENTIFIER_PATTERN.fullmatch(vault_id):
            raise PublicationLifecycleExecutionError("vault_id must be an opaque identifier")
        object.__setattr__(self, "vault_id", vault_id)
        reason_code = str(self.reason_code or "").strip()
        if not _IDENTIFIER_PATTERN.fullmatch(reason_code):
            raise PublicationLifecycleExecutionError("reason_code must be an opaque identifier")
        object.__setattr__(self, "reason_code", reason_code)
        for field_name in ("revoked_grant_count", "revoked_visitor_session_count"):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_int(getattr(self, field_name), field_name=field_name),
            )
        if self.access_deny_state != "completed":
            raise PublicationLifecycleExecutionError("access deny receipt must be completed")
        if self.public_index_cleanup_state != "pending":
            raise PublicationLifecycleExecutionError("public index cleanup cannot be claimed complete")
        if self.runtime_cleanup_state != "notApplicable":
            raise PublicationLifecycleExecutionError("runtime cleanup is not applicable without runtime binding")
        if not isinstance(self.external_cleanup, tuple) or not all(
            isinstance(item, PublicationExternalCleanupStatus) for item in self.external_cleanup
        ):
            raise PublicationLifecycleExecutionError("external cleanup summary is invalid")


class PublicationLifecycleExecutionRepository(Protocol):
    def execute(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationLifecycleExecutionCommand,
        now: datetime,
    ) -> PublicationLifecycleExecutionResult:
        ...


class PublicationLifecycleExecutionService:
    """Route-facing facade; caller owns the request transaction."""

    def __init__(
        self,
        repository: PublicationLifecycleExecutionRepository,
        *,
        enabled: bool = False,
    ) -> None:
        self._repository = repository
        self._enabled = bool(enabled)

    def execute(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationLifecycleExecutionCommand,
        now: datetime | None = None,
    ) -> PublicationLifecycleExecutionResult:
        if not isinstance(context, OwnerTruthCommandContext):
            raise PublicationLifecycleExecutionAccessDenied("Owner context is required")
        if context.actor_subject_id != context.owner_subject_id:
            raise PublicationLifecycleExecutionAccessDenied(
                "only the Vault Owner may execute publication lifecycle commands"
            )
        if not self._enabled:
            raise PublicationLifecycleExecutionDisabled("publication lifecycle is default-off")
        if not isinstance(command, PublicationLifecycleExecutionCommand):
            raise PublicationLifecycleExecutionError("publication lifecycle command is required")
        return self._repository.execute(
            context=context,
            command=command,
            now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc),
        )


class InMemoryPublicationLifecycleExecutionRepository:
    """Semantic double for API tests; Postgres is the production authority."""

    def __init__(
        self,
        authority_repository: InMemoryPublicationAuthorityRepository,
        visitor_access_repository: InMemoryPublicationVisitorAccessRepository,
        external_cleanup_coordinator: PublicationExternalCleanupCoordinator | None = None,
    ) -> None:
        self._authority_repository = authority_repository
        self._visitor_access_repository = visitor_access_repository
        self._external_cleanup_coordinator = external_cleanup_coordinator or (
            PublicationExternalCleanupCoordinator(
                effect_repository=InMemoryEffectKernelRepository(),
                provider_effect_repository=InMemoryProviderEffectRepository(),
                cleanup_repository=InMemoryPublicationExternalCleanupRepository(),
            )
        )
        self._lock = RLock()
        self._commands: dict[tuple[str, str], tuple[str, str, PublicationLifecycleExecutionResult]] = {}

    def execute(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationLifecycleExecutionCommand,
        now: datetime,
    ) -> PublicationLifecycleExecutionResult:
        with self._lock:
            replay_key = (context.vault_id, command.command_id_hash)
            replay = self._commands.get(replay_key)
            if replay is not None:
                payload_hash, owner_subject_id, result = replay
                if payload_hash != command.payload_hash or owner_subject_id != context.owner_subject_id:
                    raise PublicationLifecycleExecutionConflict(
                        "commandId cannot be reused with a different lifecycle command"
                    )
                return replace(
                    result,
                    outcome="deduplicated",
                    revoked_grant_count=0,
                    revoked_visitor_session_count=0,
                )

            try:
                target = self._authority_repository.apply_lifecycle_transition(
                    context=context,
                    publication_id=command.publication_id,
                    expected_authority_epoch=command.expected_authority_epoch,
                    action=command.action,
                )
            except PublicationAuthorityAccessDenied as exc:
                raise PublicationLifecycleExecutionAccessDenied(str(exc)) from exc
            except PublicationAuthorityConflict as exc:
                raise PublicationLifecycleExecutionConflict(str(exc)) from exc
            except PublicationAuthorityNotPublishable as exc:
                raise PublicationLifecycleExecutionUnavailable(str(exc)) from exc
            revoked_grants, revoked_sessions = (
                self._visitor_access_repository.revoke_publication_access(
                    vault_id=context.vault_id,
                    publication_id=command.publication_id,
                    command_id_hash=command.command_id_hash,
                    now=now,
                )
            )
            result = PublicationLifecycleExecutionResult(
                outcome="withdrawn" if command.action == "withdraw" else "suspended",
                vault_id=context.vault_id,
                publication_id=command.publication_id,
                publication_version_id=str(target["publicationVersionId"]),
                publication_state=str(target["publicationState"]),
                projection_state=str(target["projectionState"]),
                conflict_hold=bool(target["conflictHold"]),
                revoked_grant_count=revoked_grants,
                revoked_visitor_session_count=revoked_sessions,
                receipt_id=_receipt_id(
                    vault_id=context.vault_id,
                    command_id_hash=command.command_id_hash,
                ),
                reason_code=(
                    "ownerWithdrawal" if command.action == "withdraw" else "thirdPartyObjection"
                ),
            )
            result = replace(
                result,
                external_cleanup=self._external_cleanup_coordinator.enqueue_after_access_deny(
                    lifecycle_receipt_id=result.receipt_id,
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    publication_id=result.publication_id,
                    publication_version_id=result.publication_version_id,
                    authority_epoch=command.expected_authority_epoch,
                    action=command.action,
                    reason_code=result.reason_code,
                    observed_at=now,
                ),
            )
            self._commands[replay_key] = (
                command.payload_hash,
                context.owner_subject_id,
                result,
            )
            return result


class PostgresPublicationLifecycleExecutionRepository:
    """Canonical lifecycle execution port bound to one active Postgres UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def execute(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationLifecycleExecutionCommand,
        now: datetime,
    ) -> PublicationLifecycleExecutionResult:
        with self._cursor() as cursor:
            self._lock_command(
                cursor,
                vault_id=context.vault_id,
                command_id_hash=command.command_id_hash,
            )
            cursor.execute(
                """
                SELECT publication_id, publication_version_id, owner_subject_id,
                    command_payload_hash, publication_state, projection_state,
                    conflict_hold, revoked_grant_count, revoked_visitor_session_count,
                    id, reason_code
                FROM publication.publication_lifecycle_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command.command_id_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if (
                    str(existing["publication_id"]) != command.publication_id
                    or str(existing["owner_subject_id"]) != context.owner_subject_id
                    or str(existing["command_payload_hash"]) != command.payload_hash
                ):
                    raise PublicationLifecycleExecutionConflict(
                        "commandId cannot be reused with a different lifecycle command"
                    )
                return PublicationLifecycleExecutionResult(
                    outcome="deduplicated",
                    vault_id=context.vault_id,
                    publication_id=str(existing["publication_id"]),
                    publication_version_id=str(existing["publication_version_id"]),
                    publication_state=str(existing["publication_state"]),
                    projection_state=str(existing["projection_state"]),
                    conflict_hold=bool(existing["conflict_hold"]),
                    revoked_grant_count=0,
                    revoked_visitor_session_count=0,
                    receipt_id=str(existing["id"]),
                    reason_code=str(existing["reason_code"]),
                )

            target = self._active_owner_target(
                cursor,
                context=context,
                command=command,
            )
            target_state = "withdrawn" if command.action == "withdraw" else "suspended"
            reason_code = "ownerWithdrawal" if command.action == "withdraw" else "thirdPartyObjection"
            conflict_hold = command.action == "suspend"
            cursor.execute(
                """
                UPDATE publication.public_projections
                SET state = %s,
                    blocked_at = %s,
                    block_reason_code = %s,
                    updated_at = %s
                WHERE publication_version_id = %s
                  AND publication_id = %s
                  AND vault_id = %s
                  AND state = 'active'
                """,
                (
                    target_state,
                    now,
                    reason_code,
                    now,
                    target["publication_version_id"],
                    command.publication_id,
                    context.vault_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PublicationLifecycleExecutionUnavailable(
                    "publication projection is no longer active"
                )
            cursor.execute(
                """
                UPDATE publication.publications
                SET state = %s,
                    conflict_hold = %s,
                    updated_at = %s
                WHERE id = %s AND vault_id = %s AND state = 'confirmed'
                """,
                (
                    target_state,
                    conflict_hold,
                    now,
                    command.publication_id,
                    context.vault_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PublicationLifecycleExecutionUnavailable(
                    "publication is no longer confirmed"
                )
            cursor.execute(
                """
                UPDATE publication.visitor_sessions
                SET state = 'revoked', updated_at = %s
                WHERE vault_id = %s
                  AND publication_id = %s
                  AND publication_version_id = %s
                  AND state = 'active'
                """,
                (
                    now,
                    context.vault_id,
                    command.publication_id,
                    target["publication_version_id"],
                ),
            )
            revoked_visitor_session_count = int(cursor.rowcount)
            cursor.execute(
                """
                UPDATE publication.share_grants
                SET state = 'revoked',
                    revoked_at = %s,
                    revocation_command_hash = %s,
                    updated_at = %s
                WHERE vault_id = %s
                  AND publication_id = %s
                  AND publication_version_id = %s
                  AND state = 'active'
                """,
                (
                    now,
                    command.command_id_hash,
                    now,
                    context.vault_id,
                    command.publication_id,
                    target["publication_version_id"],
                ),
            )
            revoked_grant_count = int(cursor.rowcount)
            receipt_id = _receipt_id(
                vault_id=context.vault_id,
                command_id_hash=command.command_id_hash,
            )
            cursor.execute(
                """
                INSERT INTO publication.publication_lifecycle_receipts (
                    id, vault_id, publication_id, publication_version_id,
                    owner_subject_id, authority_epoch, action, origin, reason_code,
                    command_id_hash, command_payload_hash, publication_state,
                    projection_state, conflict_hold, revoked_grant_count,
                    revoked_visitor_session_count, access_deny_state,
                    public_index_cleanup_state, runtime_cleanup_state, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'ownerCommand', %s,
                    %s, %s, %s, %s, %s, %s, %s, 'completed', 'pending',
                    'notApplicable', %s
                )
                """,
                (
                    receipt_id,
                    context.vault_id,
                    command.publication_id,
                    target["publication_version_id"],
                    context.owner_subject_id,
                    target["authority_epoch"],
                    command.action,
                    reason_code,
                    command.command_id_hash,
                    command.payload_hash,
                    target_state,
                    target_state,
                    conflict_hold,
                    revoked_grant_count,
                    revoked_visitor_session_count,
                    now,
                ),
            )
            return PublicationLifecycleExecutionResult(
                outcome="withdrawn" if command.action == "withdraw" else "suspended",
                vault_id=context.vault_id,
                publication_id=command.publication_id,
                publication_version_id=str(target["publication_version_id"]),
                publication_state=target_state,
                projection_state=target_state,
                conflict_hold=conflict_hold,
                revoked_grant_count=revoked_grant_count,
                revoked_visitor_session_count=revoked_visitor_session_count,
                receipt_id=receipt_id,
                reason_code=reason_code,
            )

    @staticmethod
    def _lock_command(cursor: Any, *, vault_id: str, command_id_hash: str) -> None:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
            (f"publication-lifecycle-command:{vault_id}:{command_id_hash}",),
        )
        cursor.fetchone()

    @staticmethod
    def _active_owner_target(
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationLifecycleExecutionCommand,
    ) -> Mapping[str, Any]:
        cursor.execute(
            """
            SELECT publication.owner_subject_id AS publication_owner_subject_id,
                publication.authority_epoch AS publication_authority_epoch,
                publication.state AS publication_state,
                vault.owner_subject_id AS vault_owner_subject_id,
                vault.authority_epoch AS vault_authority_epoch,
                vault.status AS vault_state,
                projection.publication_version_id,
                projection.state AS projection_state
            FROM publication.publications AS publication
            JOIN owner_truth.vaults AS vault
              ON vault.vault_id = publication.vault_id
            JOIN publication.public_projections AS projection
              ON projection.publication_id = publication.id
             AND projection.vault_id = publication.vault_id
            WHERE publication.id = %s
              AND publication.vault_id = %s
              AND projection.state = 'active'
            ORDER BY projection.created_at DESC
            LIMIT 1
            FOR UPDATE OF publication, vault, projection
            """,
            (command.publication_id, context.vault_id),
        )
        target = cursor.fetchone()
        if target is None:
            raise PublicationLifecycleExecutionUnavailable("publication projection is unavailable")
        if (
            str(target["vault_owner_subject_id"]) != context.owner_subject_id
            or str(target["publication_owner_subject_id"]) != context.owner_subject_id
        ):
            raise PublicationLifecycleExecutionAccessDenied(
                "publication is not available in this Owner Vault"
            )
        if str(target["vault_state"]) != "active" or str(target["publication_state"]) != "confirmed":
            raise PublicationLifecycleExecutionUnavailable("publication is not active")
        authority_epoch = int(target["vault_authority_epoch"])
        if int(target["publication_authority_epoch"]) != authority_epoch:
            raise PublicationLifecycleExecutionUnavailable("publication authority epoch is unavailable")
        if authority_epoch != command.expected_authority_epoch:
            raise PublicationLifecycleExecutionConflict("publication authority epoch has changed")
        return {
            "publication_version_id": str(target["publication_version_id"]),
            "authority_epoch": authority_epoch,
        }

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "InMemoryPublicationLifecycleExecutionRepository",
    "PostgresPublicationLifecycleExecutionRepository",
    "PublicationLifecycleExecutionAccessDenied",
    "PublicationLifecycleExecutionCommand",
    "PublicationLifecycleExecutionConflict",
    "PublicationLifecycleExecutionDisabled",
    "PublicationLifecycleExecutionError",
    "PublicationLifecycleExecutionRepository",
    "PublicationLifecycleExecutionResult",
    "PublicationLifecycleExecutionService",
    "PublicationLifecycleExecutionUnavailable",
    "PUBLICATION_LIFECYCLE_EXECUTION_SCHEMA_VERSION",
]
