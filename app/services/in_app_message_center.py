"""Principal-scoped, metadata-only in-app message center.

The immutable business-message projection remains the source event. This
module owns only user lifecycle state and idempotent commands; it never stores
message bodies, grants access to a resource, or dispatches a notification.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Mapping
from uuid import UUID


IN_APP_MESSAGE_CENTER_SCHEMA_VERSION = "in-app-message-center-v1"
IN_APP_MESSAGE_COMMAND_SCHEMA_VERSION = "in-app-message-command-v1"


class InAppMessageKind(str, Enum):
    CANDIDATE_READY = "candidateReady"
    PROJECTION_STATUS = "projectionStatus"
    EXPORT_STATUS = "exportStatus"
    FAMILY_INVITATION = "familyInvitation"
    FAMILY_CONTRIBUTION = "familyContribution"
    AUTHORIZATION_REVOKED = "authorizationRevoked"
    ACCOUNT_SECURITY = "accountSecurity"
    TASK_RETRY_REQUIRED = "taskRetryRequired"
    CARE_SIGNAL = "careSignal"
    SYSTEM_NOTICE = "systemNotice"
    TIME_LETTER = "timeLetter"
    ECHO_REPLY = "echoReply"


PUBLIC_MESSAGE_KINDS = frozenset(
    {
        InAppMessageKind.CANDIDATE_READY,
        InAppMessageKind.PROJECTION_STATUS,
        InAppMessageKind.EXPORT_STATUS,
        InAppMessageKind.FAMILY_INVITATION,
        InAppMessageKind.FAMILY_CONTRIBUTION,
        InAppMessageKind.AUTHORIZATION_REVOKED,
        InAppMessageKind.ACCOUNT_SECURITY,
        InAppMessageKind.TASK_RETRY_REQUIRED,
        InAppMessageKind.CARE_SIGNAL,
        InAppMessageKind.SYSTEM_NOTICE,
    }
)


class InAppMessageState(str, Enum):
    UNREAD = "unread"
    READ = "read"
    DELETED = "deleted"


class InAppMessageCommandKind(str, Enum):
    MARK_READ = "markRead"
    MARK_ALL_READ = "markAllRead"
    DELETE_READ = "deleteRead"


class InAppMessageCenterError(ValueError):
    pass


class InAppMessageCenterNotFound(InAppMessageCenterError):
    pass


class InAppMessageCenterCommandConflict(InAppMessageCenterError):
    pass


def _nonempty(value: object, *, field: str, max_length: int = 255) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > max_length:
        raise InAppMessageCenterError(f"{field} is invalid")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as exc:
        raise InAppMessageCenterError(f"{field} must be a UUID") from exc


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise InAppMessageCenterError(f"{field} must be a datetime")
    if value.tzinfo is None:
        raise InAppMessageCenterError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True)
class InAppMessageCenterMessage:
    message_id: str
    kind: InAppMessageKind
    inbox_subject_id: str
    inbox_vault_id: str
    resource_type: str
    resource_id: str
    resource_version: int
    created_at: datetime
    state: InAppMessageState = InAppMessageState.UNREAD
    read_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "message_id", _uuid(self.message_id, field="message_id"))
        if not isinstance(self.kind, InAppMessageKind):
            raise InAppMessageCenterError("kind is invalid")
        object.__setattr__(
            self,
            "inbox_subject_id",
            _nonempty(self.inbox_subject_id, field="inbox_subject_id"),
        )
        object.__setattr__(
            self,
            "inbox_vault_id",
            _nonempty(self.inbox_vault_id, field="inbox_vault_id"),
        )
        object.__setattr__(self, "resource_type", _nonempty(self.resource_type, field="resource_type"))
        object.__setattr__(self, "resource_id", _nonempty(self.resource_id, field="resource_id"))
        if isinstance(self.resource_version, bool) or self.resource_version < 0:
            raise InAppMessageCenterError("resource_version is invalid")
        object.__setattr__(self, "created_at", _utc(self.created_at, field="created_at"))
        if not isinstance(self.state, InAppMessageState):
            raise InAppMessageCenterError("state is invalid")
        if self.read_at is not None:
            object.__setattr__(self, "read_at", _utc(self.read_at, field="read_at"))

    def public_payload(self) -> dict[str, object]:
        return {
            "id": self.message_id,
            "kind": self.kind.value,
            "state": self.state.value,
            "createdAt": _iso(self.created_at),
            "readAt": _iso(self.read_at),
            "resource": {
                "type": self.resource_type,
                "id": self.resource_id,
                "version": self.resource_version,
            },
            "metadataOnly": True,
            "requiresReauthorization": True,
        }


@dataclass(frozen=True)
class InAppMessageCenterPage:
    messages: tuple[InAppMessageCenterMessage, ...]
    unread_count: int
    next_cursor: str | None


@dataclass(frozen=True)
class InAppMessageCommandResult:
    command_id: str
    command_kind: InAppMessageCommandKind
    outcome: str
    affected_count: int
    unread_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _uuid(self.command_id, field="command_id"))
        if self.outcome not in {"applied", "deduplicated"}:
            raise InAppMessageCenterError("command outcome is invalid")
        if self.affected_count < 0 or self.unread_count < 0:
            raise InAppMessageCenterError("command counts are invalid")


@dataclass(frozen=True)
class _CommandReceipt:
    result: InAppMessageCommandResult
    request_hash: str


def _encode_cursor(message: InAppMessageCenterMessage) -> str:
    payload = json.dumps(
        {
            "createdAt": _iso(message.created_at),
            "messageId": message.message_id,
            "schemaVersion": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        if payload.get("schemaVersion") != 1:
            raise ValueError("unsupported cursor")
        created_at = datetime.fromisoformat(str(payload["createdAt"]).replace("Z", "+00:00"))
        return _utc(created_at, field="cursor.createdAt"), _uuid(
            payload["messageId"], field="cursor.messageId"
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InAppMessageCenterError("cursor is invalid") from exc


class InMemoryInAppMessageCenterRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._messages: dict[str, InAppMessageCenterMessage] = {}
        self._receipts: dict[str, _CommandReceipt] = {}

    def seed(self, message: InAppMessageCenterMessage) -> None:
        if not isinstance(message, InAppMessageCenterMessage):
            raise InAppMessageCenterError("message is required")
        with self._lock:
            self._messages[message.message_id] = message

    def list_messages(
        self,
        inbox_subject_id: str,
        *,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> tuple[tuple[InAppMessageCenterMessage, ...], bool, int]:
        with self._lock:
            visible = [
                message
                for message in self._messages.values()
                if message.inbox_subject_id == inbox_subject_id
                and message.kind in PUBLIC_MESSAGE_KINDS
                and message.state is not InAppMessageState.DELETED
            ]
            visible.sort(key=lambda item: (item.created_at, item.message_id), reverse=True)
            if cursor is not None:
                visible = [
                    item
                    for item in visible
                    if (item.created_at, item.message_id) < cursor
                ]
            page = visible[: limit + 1]
            unread_count = sum(
                1
                for item in self._messages.values()
                if item.inbox_subject_id == inbox_subject_id
                and item.kind in PUBLIC_MESSAGE_KINDS
                and item.state is InAppMessageState.UNREAD
            )
            return tuple(page[:limit]), len(page) > limit, unread_count

    def execute(
        self,
        *,
        inbox_subject_id: str,
        command_id: str,
        command_kind: InAppMessageCommandKind,
        message_id: str | None,
        occurred_at: datetime,
        request_hash: str,
    ) -> InAppMessageCommandResult:
        with self._lock:
            existing = self._receipts.get(command_id)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise InAppMessageCenterCommandConflict(
                        "command_id is already bound to a different message command"
                    )
                return replace(existing.result, outcome="deduplicated")

            affected_count = 0
            if command_kind is InAppMessageCommandKind.MARK_READ:
                target = self._messages.get(str(message_id))
                if (
                    target is None
                    or target.inbox_subject_id != inbox_subject_id
                    or target.kind not in PUBLIC_MESSAGE_KINDS
                    or target.state is InAppMessageState.DELETED
                ):
                    raise InAppMessageCenterNotFound("message not found")
                if target.state is InAppMessageState.UNREAD:
                    self._messages[target.message_id] = replace(
                        target,
                        state=InAppMessageState.READ,
                        read_at=occurred_at,
                    )
                    affected_count = 1
            elif command_kind is InAppMessageCommandKind.MARK_ALL_READ:
                for target in tuple(self._messages.values()):
                    if (
                        target.inbox_subject_id == inbox_subject_id
                        and target.kind in PUBLIC_MESSAGE_KINDS
                        and target.state is InAppMessageState.UNREAD
                        and target.created_at <= occurred_at
                    ):
                        self._messages[target.message_id] = replace(
                            target,
                            state=InAppMessageState.READ,
                            read_at=occurred_at,
                        )
                        affected_count += 1
            elif command_kind is InAppMessageCommandKind.DELETE_READ:
                for target in tuple(self._messages.values()):
                    if (
                        target.inbox_subject_id == inbox_subject_id
                        and target.kind in PUBLIC_MESSAGE_KINDS
                        and target.state is InAppMessageState.READ
                        and target.read_at is not None
                        and target.read_at <= occurred_at
                    ):
                        self._messages[target.message_id] = replace(
                            target,
                            state=InAppMessageState.DELETED,
                        )
                        affected_count += 1
            else:  # pragma: no cover - guarded by the enum
                raise InAppMessageCenterError("command kind is unsupported")

            unread_count = sum(
                1
                for item in self._messages.values()
                if item.inbox_subject_id == inbox_subject_id
                and item.kind in PUBLIC_MESSAGE_KINDS
                and item.state is InAppMessageState.UNREAD
            )
            result = InAppMessageCommandResult(
                command_id=command_id,
                command_kind=command_kind,
                outcome="applied",
                affected_count=affected_count,
                unread_count=unread_count,
            )
            self._receipts[command_id] = _CommandReceipt(result, request_hash)
            return result


class PostgresInAppMessageCenterRepository:
    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def list_messages(
        self,
        inbox_subject_id: str,
        *,
        limit: int,
        cursor: tuple[datetime, str] | None,
    ) -> tuple[tuple[InAppMessageCenterMessage, ...], bool, int]:
        kind_values = tuple(kind.value for kind in sorted(PUBLIC_MESSAGE_KINDS, key=lambda item: item.value))
        placeholders = ", ".join(["%s"] * len(kind_values))
        cursor_clause = ""
        params: list[object] = [inbox_subject_id, *kind_values]
        if cursor is not None:
            cursor_clause = "AND (projection.created_at, projection.message_id) < (%s, %s)"
            params.extend([cursor[0], cursor[1]])
        params.append(limit + 1)
        with self._cursor() as cursor_handle:
            cursor_handle.execute(
                f"""
                SELECT projection.message_id, projection.message_kind,
                       projection.inbox_subject_id, projection.inbox_vault_id,
                       projection.resource_type, projection.resource_id,
                       projection.resource_version, projection.created_at,
                       COALESCE(lifecycle.state, 'unread') AS lifecycle_state,
                       lifecycle.read_at
                FROM async_effects.business_message_projections AS projection
                LEFT JOIN async_effects.in_app_message_lifecycle AS lifecycle
                  ON lifecycle.message_id = projection.message_id
                WHERE projection.inbox_subject_id = %s
                  AND projection.message_kind IN ({placeholders})
                  AND COALESCE(lifecycle.state, 'unread') <> 'deleted'
                  {cursor_clause}
                ORDER BY projection.created_at DESC, projection.message_id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            rows = cursor_handle.fetchall()
            cursor_handle.execute(
                f"""
                SELECT COUNT(*) AS unread_count
                FROM async_effects.business_message_projections AS projection
                LEFT JOIN async_effects.in_app_message_lifecycle AS lifecycle
                  ON lifecycle.message_id = projection.message_id
                WHERE projection.inbox_subject_id = %s
                  AND projection.message_kind IN ({placeholders})
                  AND lifecycle.message_id IS NULL
                """,
                (inbox_subject_id, *kind_values),
            )
            unread_count = int(cursor_handle.fetchone()["unread_count"])
        messages = tuple(self._message_from_row(row) for row in rows[:limit])
        return messages, len(rows) > limit, unread_count

    def execute(
        self,
        *,
        inbox_subject_id: str,
        command_id: str,
        command_kind: InAppMessageCommandKind,
        message_id: str | None,
        occurred_at: datetime,
        request_hash: str,
    ) -> InAppMessageCommandResult:
        kind_values = tuple(kind.value for kind in sorted(PUBLIC_MESSAGE_KINDS, key=lambda item: item.value))
        placeholders = ", ".join(["%s"] * len(kind_values))
        with self._cursor() as cursor_handle:
            cursor_handle.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (command_id,))
            cursor_handle.execute(
                """
                SELECT command_kind, request_hash, affected_count
                FROM async_effects.in_app_message_commands
                WHERE command_id = %s
                """,
                (command_id,),
            )
            existing = cursor_handle.fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise InAppMessageCenterCommandConflict(
                        "command_id is already bound to a different message command"
                    )
                return InAppMessageCommandResult(
                    command_id=command_id,
                    command_kind=InAppMessageCommandKind(str(existing["command_kind"])),
                    outcome="deduplicated",
                    affected_count=int(existing["affected_count"]),
                    unread_count=self._unread_count(cursor_handle, inbox_subject_id, kind_values),
                )

            if command_kind is InAppMessageCommandKind.MARK_READ:
                cursor_handle.execute(
                    f"""
                    SELECT projection.message_id, lifecycle.state
                    FROM async_effects.business_message_projections AS projection
                    LEFT JOIN async_effects.in_app_message_lifecycle AS lifecycle
                      ON lifecycle.message_id = projection.message_id
                    WHERE projection.message_id = %s
                      AND projection.inbox_subject_id = %s
                      AND projection.message_kind IN ({placeholders})
                      AND COALESCE(lifecycle.state, 'unread') <> 'deleted'
                    FOR UPDATE OF projection
                    """,
                    (message_id, inbox_subject_id, *kind_values),
                )
                target = cursor_handle.fetchone()
                if target is None:
                    raise InAppMessageCenterNotFound("message not found")
                affected_count = 0
                if target["state"] is None:
                    cursor_handle.execute(
                        """
                        INSERT INTO async_effects.in_app_message_lifecycle (
                            message_id, inbox_subject_id, inbox_vault_id,
                            state, read_at, state_version
                        )
                        SELECT message_id, inbox_subject_id, inbox_vault_id,
                               'read', %s, 1
                        FROM async_effects.business_message_projections
                        WHERE message_id = %s
                        ON CONFLICT DO NOTHING
                        RETURNING message_id
                        """,
                        (occurred_at, message_id),
                    )
                    affected_count = 1 if cursor_handle.fetchone() is not None else 0
            elif command_kind is InAppMessageCommandKind.MARK_ALL_READ:
                cursor_handle.execute(
                    f"""
                    INSERT INTO async_effects.in_app_message_lifecycle (
                        message_id, inbox_subject_id, inbox_vault_id,
                        state, read_at, state_version
                    )
                    SELECT projection.message_id, projection.inbox_subject_id,
                           projection.inbox_vault_id, 'read', %s, 1
                    FROM async_effects.business_message_projections AS projection
                    LEFT JOIN async_effects.in_app_message_lifecycle AS lifecycle
                      ON lifecycle.message_id = projection.message_id
                    WHERE projection.inbox_subject_id = %s
                      AND projection.message_kind IN ({placeholders})
                      AND lifecycle.message_id IS NULL
                      AND projection.created_at <= %s
                    ON CONFLICT DO NOTHING
                    RETURNING message_id
                    """,
                    (occurred_at, inbox_subject_id, *kind_values, occurred_at),
                )
                affected_count = len(cursor_handle.fetchall())
            elif command_kind is InAppMessageCommandKind.DELETE_READ:
                cursor_handle.execute(
                    f"""
                    UPDATE async_effects.in_app_message_lifecycle AS lifecycle
                    SET state = 'deleted', deleted_at = %s,
                        state_version = lifecycle.state_version + 1,
                        updated_at = NOW()
                    FROM async_effects.business_message_projections AS projection
                    WHERE lifecycle.message_id = projection.message_id
                      AND projection.inbox_subject_id = %s
                      AND projection.message_kind IN ({placeholders})
                      AND lifecycle.state = 'read'
                      AND lifecycle.read_at <= %s
                    RETURNING lifecycle.message_id
                    """,
                    (occurred_at, inbox_subject_id, *kind_values, occurred_at),
                )
                affected_count = len(cursor_handle.fetchall())
            else:  # pragma: no cover - guarded by the enum
                raise InAppMessageCenterError("command kind is unsupported")

            cursor_handle.execute(
                """
                INSERT INTO async_effects.in_app_message_commands (
                    command_id, inbox_subject_id, command_kind, message_id,
                    request_hash, affected_count, schema_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    command_id,
                    inbox_subject_id,
                    command_kind.value,
                    message_id,
                    request_hash,
                    affected_count,
                    IN_APP_MESSAGE_COMMAND_SCHEMA_VERSION,
                ),
            )
            return InAppMessageCommandResult(
                command_id=command_id,
                command_kind=command_kind,
                outcome="applied",
                affected_count=affected_count,
                unread_count=self._unread_count(cursor_handle, inbox_subject_id, kind_values),
            )

    @staticmethod
    def _unread_count(cursor_handle: Any, inbox_subject_id: str, kind_values: tuple[str, ...]) -> int:
        placeholders = ", ".join(["%s"] * len(kind_values))
        cursor_handle.execute(
            f"""
            SELECT COUNT(*) AS unread_count
            FROM async_effects.business_message_projections AS projection
            LEFT JOIN async_effects.in_app_message_lifecycle AS lifecycle
              ON lifecycle.message_id = projection.message_id
            WHERE projection.inbox_subject_id = %s
              AND projection.message_kind IN ({placeholders})
              AND lifecycle.message_id IS NULL
            """,
            (inbox_subject_id, *kind_values),
        )
        return int(cursor_handle.fetchone()["unread_count"])

    @staticmethod
    def _message_from_row(row: Mapping[str, object]) -> InAppMessageCenterMessage:
        state = InAppMessageState(str(row["lifecycle_state"]))
        return InAppMessageCenterMessage(
            message_id=str(row["message_id"]),
            kind=InAppMessageKind(str(row["message_kind"])),
            inbox_subject_id=str(row["inbox_subject_id"]),
            inbox_vault_id=str(row["inbox_vault_id"]),
            resource_type=str(row["resource_type"]),
            resource_id=str(row["resource_id"]),
            resource_version=int(row["resource_version"]),
            created_at=row["created_at"],
            state=state,
            read_at=row.get("read_at"),
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class InAppMessageCenterService:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def list_messages(
        self,
        inbox_subject_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> InAppMessageCenterPage:
        subject_id = _nonempty(inbox_subject_id, field="inbox_subject_id")
        if isinstance(limit, bool) or limit < 1 or limit > 100:
            raise InAppMessageCenterError("limit must be between 1 and 100")
        decoded_cursor = _decode_cursor(cursor) if cursor else None
        messages, has_more, unread_count = self._repository.list_messages(
            subject_id,
            limit=limit,
            cursor=decoded_cursor,
        )
        next_cursor = _encode_cursor(messages[-1]) if has_more and messages else None
        return InAppMessageCenterPage(messages, unread_count, next_cursor)

    def mark_read(
        self,
        inbox_subject_id: str,
        message_id: str,
        *,
        command_id: str,
        occurred_at: datetime,
    ) -> InAppMessageCommandResult:
        return self._execute(
            inbox_subject_id,
            command_id=command_id,
            command_kind=InAppMessageCommandKind.MARK_READ,
            message_id=_uuid(message_id, field="message_id"),
            occurred_at=occurred_at,
        )

    def mark_all_read(
        self,
        inbox_subject_id: str,
        *,
        command_id: str,
        occurred_at: datetime,
    ) -> InAppMessageCommandResult:
        return self._execute(
            inbox_subject_id,
            command_id=command_id,
            command_kind=InAppMessageCommandKind.MARK_ALL_READ,
            message_id=None,
            occurred_at=occurred_at,
        )

    def delete_read(
        self,
        inbox_subject_id: str,
        *,
        command_id: str,
        occurred_at: datetime,
    ) -> InAppMessageCommandResult:
        return self._execute(
            inbox_subject_id,
            command_id=command_id,
            command_kind=InAppMessageCommandKind.DELETE_READ,
            message_id=None,
            occurred_at=occurred_at,
        )

    def _execute(
        self,
        inbox_subject_id: str,
        *,
        command_id: str,
        command_kind: InAppMessageCommandKind,
        message_id: str | None,
        occurred_at: datetime,
    ) -> InAppMessageCommandResult:
        subject_id = _nonempty(inbox_subject_id, field="inbox_subject_id")
        normalized_command_id = _uuid(command_id, field="command_id")
        normalized_time = _utc(occurred_at, field="occurred_at")
        request_hash = _canonical_hash(
            {
                "commandKind": command_kind.value,
                "inboxSubjectId": subject_id,
                "messageId": message_id,
                "schemaVersion": IN_APP_MESSAGE_COMMAND_SCHEMA_VERSION,
            }
        )
        return self._repository.execute(
            inbox_subject_id=subject_id,
            command_id=normalized_command_id,
            command_kind=command_kind,
            message_id=message_id,
            occurred_at=normalized_time,
            request_hash=request_hash,
        )


__all__ = [
    "IN_APP_MESSAGE_CENTER_SCHEMA_VERSION",
    "InAppMessageCenterCommandConflict",
    "InAppMessageCenterError",
    "InAppMessageCenterMessage",
    "InAppMessageCenterNotFound",
    "InAppMessageCenterPage",
    "InAppMessageCenterService",
    "InAppMessageCommandKind",
    "InAppMessageCommandResult",
    "InAppMessageKind",
    "InAppMessageState",
    "InMemoryInAppMessageCenterRepository",
    "PostgresInAppMessageCenterRepository",
    "PUBLIC_MESSAGE_KINDS",
]
