"""Owner-controlled, thread-scoped interview preferences for M0-A/M0-B.

The existing interview-session boundary is intentionally short lived.  This
module adds the separate, value-minimized authority required when an Owner
means "later" or "do not ask about this thread".  It never stores a topic
title, message text, model output, or provider payload.  A ConversationThread
UUID is the only v0 topic identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json
from threading import RLock
from typing import Any, Callable, Mapping, Optional, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.contracts import OwnerTruthContractError, require_nonblank, require_uuid
from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
    OwnerTruthInterviewSessionResult,
    OwnerTruthInterviewSessionSnapshot,
    RestoreDoNotAskInterviewBoundaryCommand,
    SetInterviewBoundaryCommand,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService


OWNER_TRUTH_THREAD_PREFERENCE_SCHEMA_VERSION = "owner-truth-thread-preference-v1"
OWNER_TRUTH_THREAD_PREFERENCE_UI_SCHEMA_VERSION = "thread-preference-v1"
_RECEIPT_NAMESPACE = UUID("25e81d9b-2a61-42b5-b116-b6d76ed0a0cb")


class OwnerTruthThreadPreferenceError(OwnerTruthContractError):
    """A thread preference command cannot be safely applied."""


class OwnerTruthThreadPreferenceAccessDenied(OwnerTruthThreadPreferenceError):
    """The caller does not own the target private interview thread."""


class OwnerTruthThreadPreferenceConflict(OwnerTruthThreadPreferenceError):
    """A command or preference transition conflicts with current authority."""


class OwnerTruthThreadPreferenceUnavailable(OwnerTruthThreadPreferenceError):
    """The default-off QA contract is unavailable."""


class OwnerTruthThreadPreferenceCooldownActive(OwnerTruthThreadPreferenceError):
    """An Owner tried to resume a thread before its cooldown elapsed."""


class ThreadPreferenceState(str, Enum):
    OPEN = "open"
    COOLDOWN = "cooldown"
    DO_NOT_ASK = "doNotAsk"
    STALE = "stale"


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise OwnerTruthThreadPreferenceError("thread preference payload must be serializable") from error


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthThreadPreferenceError(f"{field} is required")
    return sha256(normalized.encode("utf-8")).hexdigest()


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OwnerTruthThreadPreferenceError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _nonnegative_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OwnerTruthThreadPreferenceError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OwnerTruthThreadPreferenceError(f"{field} must be a positive integer")
    return value


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthThreadPreferenceAccessDenied("owner context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthThreadPreferenceAccessDenied(
            "only the Vault Owner may change a thread preference"
        )


def _state(value: object, *, allow_stale: bool = False) -> ThreadPreferenceState:
    try:
        normalized = ThreadPreferenceState(value)
    except (TypeError, ValueError) as error:
        raise OwnerTruthThreadPreferenceError("thread preference is not supported") from error
    if normalized is ThreadPreferenceState.STALE and not allow_stale:
        raise OwnerTruthThreadPreferenceError("stale is not a writable thread preference")
    return normalized


@dataclass(frozen=True)
class OwnerTruthThreadPreferenceSnapshot:
    """The current value-minimized preference for one ConversationThread."""

    vault_id: str
    thread_id: str
    owner_subject_id: str
    authority_epoch: int
    preference: ThreadPreferenceState
    cooldown_until: Optional[datetime]
    row_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "authority_epoch", _nonnegative_int(self.authority_epoch, field="authority_epoch"))
        preference = _state(self.preference, allow_stale=True)
        object.__setattr__(self, "preference", preference)
        if preference is ThreadPreferenceState.COOLDOWN:
            object.__setattr__(
                self,
                "cooldown_until",
                _utc(self.cooldown_until, field="cooldown_until"),
            )
        elif self.cooldown_until is not None:
            raise OwnerTruthThreadPreferenceError(
                "only a cooldown preference may retain cooldown_until"
            )
        object.__setattr__(self, "row_version", _positive_int(self.row_version, field="row_version"))

    @property
    def is_recommendation_eligible(self) -> bool:
        """Expiry does not auto-resume a thread; only explicit OPEN may plan."""

        return self.preference is ThreadPreferenceState.OPEN

    def value_free_summary(self) -> dict[str, object]:
        return {
            "schemaVersion": OWNER_TRUTH_THREAD_PREFERENCE_SCHEMA_VERSION,
            "preference": self.preference.value,
            "cooldownActive": self.preference is ThreadPreferenceState.COOLDOWN,
            "rowVersion": self.row_version,
        }


@dataclass(frozen=True)
class OwnerTruthThreadPreferenceMutationResult:
    outcome: str
    preference: OwnerTruthThreadPreferenceSnapshot

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise OwnerTruthThreadPreferenceError("thread preference outcome is not supported")
        if not isinstance(self.preference, OwnerTruthThreadPreferenceSnapshot):
            raise TypeError("thread preference snapshot is required")


@dataclass(frozen=True)
class OwnerTruthThreadPreferenceBoundaryResult:
    """One session transition plus its independent thread-preference receipt."""

    session: OwnerTruthInterviewSessionResult
    preference: Optional[OwnerTruthThreadPreferenceMutationResult]


@dataclass(frozen=True)
class RestoreCooldownThreadPreferenceCommand:
    command_id: str
    thread_id: str
    session_id: str
    expected_session_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        object.__setattr__(
            self,
            "expected_session_version",
            _positive_int(self.expected_session_version, field="expected_session_version"),
        )


class OwnerTruthThreadPreferenceRepository(Protocol):
    def has_matching_receipt(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id: str,
        thread_id: str,
        session_id: str,
        authority_epoch: int,
        operation: str,
        preference: ThreadPreferenceState,
        previous_preference: Optional[ThreadPreferenceState],
    ) -> bool:
        ...

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthThreadPreferenceMutationResult:
        ...

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        thread_id: str,
    ) -> Optional[OwnerTruthThreadPreferenceSnapshot]:
        ...


class OwnerTruthThreadPreferenceStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> Any:
        ...

    def owner_truth_conversation_repository(self) -> Any:
        ...

    def owner_truth_thread_preference_repository(self) -> OwnerTruthThreadPreferenceRepository:
        ...


def _snapshot_from_record(record: Mapping[str, Any]) -> OwnerTruthThreadPreferenceSnapshot:
    raw_cooldown_until = record.get("cooldownUntil")
    cooldown_until: Optional[datetime]
    if raw_cooldown_until is None:
        cooldown_until = None
    elif isinstance(raw_cooldown_until, datetime):
        cooldown_until = raw_cooldown_until
    else:
        normalized = str(raw_cooldown_until).replace("Z", "+00:00")
        try:
            cooldown_until = datetime.fromisoformat(normalized)
        except ValueError as error:
            raise OwnerTruthThreadPreferenceError("cooldownUntil is not ISO-8601") from error
    return OwnerTruthThreadPreferenceSnapshot(
        vault_id=str(record.get("vaultId") or ""),
        thread_id=str(record.get("threadId") or ""),
        owner_subject_id=str(record.get("ownerSubjectId") or ""),
        authority_epoch=_nonnegative_int(record.get("authorityEpoch"), field="authorityEpoch"),
        preference=_state(record.get("preference"), allow_stale=True),
        cooldown_until=cooldown_until,
        row_version=_positive_int(record.get("rowVersion"), field="rowVersion"),
    )


def _result_from_record(
    record: Mapping[str, Any],
    *,
    outcome: str,
) -> OwnerTruthThreadPreferenceMutationResult:
    return OwnerTruthThreadPreferenceMutationResult(
        outcome=outcome,
        preference=_snapshot_from_record(record),
    )


def _assert_record_context(
    *,
    context: OwnerTruthCommandContext,
    record: Mapping[str, Any],
) -> None:
    _assert_owner_context(context)
    if (
        str(record.get("vaultId") or "") != context.vault_id
        or str(record.get("ownerSubjectId") or "") != context.owner_subject_id
        or str(record.get("actorSubjectId") or "") != context.owner_subject_id
    ):
        raise OwnerTruthThreadPreferenceAccessDenied(
            "thread preference record does not match Owner context"
        )


class InMemoryOwnerTruthThreadPreferenceRepository:
    """Thread-safe semantic double for current preferences and immutable receipts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records_by_thread: dict[tuple[str, str], dict[str, Any]] = {}
        self._records_by_command: dict[tuple[str, str], dict[str, Any]] = {}

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthThreadPreferenceMutationResult:
        _assert_record_context(context=context, record=record)
        normalized = dict(record)
        _snapshot_from_record(normalized)
        command_hash = str(normalized.get("commandIdHash") or "")
        payload_hash = str(normalized.get("payloadHash") or "")
        if len(command_hash) != 64 or len(payload_hash) != 64:
            raise OwnerTruthThreadPreferenceError("thread preference receipt hashes are invalid")
        command_key = (context.vault_id, command_hash)
        thread_key = (context.vault_id, str(normalized.get("threadId") or ""))
        with self._lock:
            existing_receipt = self._records_by_command.get(command_key)
            if existing_receipt is not None:
                if str(existing_receipt.get("payloadHash") or "") != payload_hash:
                    raise OwnerTruthThreadPreferenceConflict(
                        "commandId cannot be reused with different thread preference meaning"
                    )
                current = self._records_by_thread.get(thread_key)
                if current is None:
                    raise OwnerTruthThreadPreferenceConflict(
                        "thread preference receipt cannot outlive its current preference"
                    )
                return _result_from_record(current, outcome="deduplicated")

            current = self._records_by_thread.get(thread_key)
            operation = str(normalized.get("operation") or "")
            requested = _state(normalized.get("preference"))
            if operation == "set":
                if requested not in {ThreadPreferenceState.COOLDOWN, ThreadPreferenceState.DO_NOT_ASK}:
                    raise OwnerTruthThreadPreferenceError("set must choose cooldown or doNotAsk")
                if current is not None and _state(current.get("preference")) is ThreadPreferenceState.DO_NOT_ASK:
                    raise OwnerTruthThreadPreferenceConflict(
                        "doNotAsk thread preference requires an explicit restore"
                    )
            elif operation == "restore":
                if requested is not ThreadPreferenceState.OPEN:
                    raise OwnerTruthThreadPreferenceError("restore must reopen a thread preference")
                previous = _state(normalized.get("previousPreference"))
                if current is None or _state(current.get("preference")) is not previous:
                    raise OwnerTruthThreadPreferenceConflict(
                        "thread preference changed before explicit restore"
                    )
            else:
                raise OwnerTruthThreadPreferenceError("thread preference operation is not supported")

            next_record = dict(normalized)
            next_record["rowVersion"] = 1 if current is None else int(current["rowVersion"]) + 1
            self._records_by_thread[thread_key] = next_record
            self._records_by_command[command_key] = dict(normalized)
            return _result_from_record(next_record, outcome="created")

    def has_matching_receipt(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id: str,
        thread_id: str,
        session_id: str,
        authority_epoch: int,
        operation: str,
        preference: ThreadPreferenceState,
        previous_preference: Optional[ThreadPreferenceState],
    ) -> bool:
        _assert_owner_context(context)
        command_hash = _hash(command_id, field="command_id")
        normalized_thread_id = require_uuid(thread_id, field="thread_id")
        normalized_session_id = require_uuid(session_id, field="session_id")
        normalized_epoch = _nonnegative_int(authority_epoch, field="authority_epoch")
        with self._lock:
            existing = self._records_by_command.get((context.vault_id, command_hash))
            if existing is None:
                return False
            if (
                str(existing.get("ownerSubjectId") or "") != context.owner_subject_id
                or str(existing.get("actorSubjectId") or "") != context.owner_subject_id
                or str(existing.get("threadId") or "") != normalized_thread_id
                or str(existing.get("sessionId") or "") != normalized_session_id
                or _nonnegative_int(
                    existing.get("authorityEpoch"),
                    field="authorityEpoch",
                )
                != normalized_epoch
                or str(existing.get("operation") or "") != operation
                or str(existing.get("preference") or "") != preference.value
                or existing.get("previousPreference")
                != (None if previous_preference is None else previous_preference.value)
            ):
                raise OwnerTruthThreadPreferenceConflict(
                    "commandId cannot be reused with different thread preference meaning"
                )
            return True

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        thread_id: str,
    ) -> Optional[OwnerTruthThreadPreferenceSnapshot]:
        _assert_owner_context(context)
        normalized_thread_id = require_uuid(thread_id, field="thread_id")
        with self._lock:
            record = self._records_by_thread.get((context.vault_id, normalized_thread_id))
            if record is None:
                return None
            result = dict(record)
        if (
            str(result.get("ownerSubjectId") or "") != context.owner_subject_id
            or str(result.get("actorSubjectId") or "") != context.owner_subject_id
        ):
            return OwnerTruthThreadPreferenceSnapshot(
                vault_id=context.vault_id,
                thread_id=normalized_thread_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=_nonnegative_int(result.get("authorityEpoch"), field="authorityEpoch"),
                preference=ThreadPreferenceState.STALE,
                cooldown_until=None,
                row_version=_positive_int(result.get("rowVersion"), field="rowVersion"),
            )
        return _snapshot_from_record(result)


class PostgresOwnerTruthThreadPreferenceRepository:
    """Postgres persistence for thread preferences and append-only receipts."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthThreadPreferenceMutationResult:
        _assert_record_context(context=context, record=record)
        normalized = dict(record)
        _snapshot_from_record(normalized)
        command_hash = str(normalized.get("commandIdHash") or "")
        payload_hash = str(normalized.get("payloadHash") or "")
        if len(command_hash) != 64 or len(payload_hash) != 64:
            raise OwnerTruthThreadPreferenceError("thread preference receipt hashes are invalid")
        with self._cursor() as cursor:
            self._lock(cursor, f"owner-truth-thread-preference-command:{context.vault_id}:{command_hash}")
            self._lock(
                cursor,
                "owner-truth-thread-preference-thread:"
                f"{context.vault_id}:{normalized['threadId']}",
            )
            cursor.execute(
                """
                SELECT command_payload_hash
                FROM owner_truth.thread_preference_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing["command_payload_hash"]) != payload_hash:
                    raise OwnerTruthThreadPreferenceConflict(
                        "commandId cannot be reused with different thread preference meaning"
                    )
                current = self._read_current(cursor, context=context, thread_id=normalized["threadId"])
                if current is None:
                    raise OwnerTruthThreadPreferenceConflict(
                        "thread preference receipt cannot outlive its current preference"
                    )
                return OwnerTruthThreadPreferenceMutationResult(
                    outcome="deduplicated",
                    preference=current,
                )

            self._assert_post_transition_session(cursor, context=context, record=normalized)
            current = self._read_current(cursor, context=context, thread_id=normalized["threadId"], lock=True)
            operation = str(normalized.get("operation") or "")
            requested = _state(normalized.get("preference"))
            if operation == "set":
                if requested not in {ThreadPreferenceState.COOLDOWN, ThreadPreferenceState.DO_NOT_ASK}:
                    raise OwnerTruthThreadPreferenceError("set must choose cooldown or doNotAsk")
                if current is not None and current.preference is ThreadPreferenceState.DO_NOT_ASK:
                    raise OwnerTruthThreadPreferenceConflict(
                        "doNotAsk thread preference requires an explicit restore"
                    )
            elif operation == "restore":
                if requested is not ThreadPreferenceState.OPEN:
                    raise OwnerTruthThreadPreferenceError("restore must reopen a thread preference")
                previous = _state(normalized.get("previousPreference"))
                if current is None or current.preference is not previous:
                    raise OwnerTruthThreadPreferenceConflict(
                        "thread preference changed before explicit restore"
                    )
                if (
                    previous is ThreadPreferenceState.COOLDOWN
                    and current.cooldown_until is not None
                    and datetime.now(timezone.utc) < current.cooldown_until
                ):
                    raise OwnerTruthThreadPreferenceCooldownActive(
                        "thread cooldown has not yet elapsed"
                    )
            else:
                raise OwnerTruthThreadPreferenceError("thread preference operation is not supported")

            cursor.execute(
                """
                INSERT INTO owner_truth.thread_preferences (
                    id, vault_id, thread_id, owner_subject_id, actor_subject_id,
                    preference, cooldown_until, policy_version, authority_epoch
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (vault_id, thread_id)
                DO UPDATE SET
                    actor_subject_id = EXCLUDED.actor_subject_id,
                    preference = EXCLUDED.preference,
                    cooldown_until = EXCLUDED.cooldown_until,
                    policy_version = EXCLUDED.policy_version,
                    updated_at = NOW()
                RETURNING vault_id, thread_id, owner_subject_id, authority_epoch,
                    preference, cooldown_until, row_version
                """,
                (
                    normalized["preferenceId"],
                    normalized["vaultId"],
                    normalized["threadId"],
                    normalized["ownerSubjectId"],
                    normalized["actorSubjectId"],
                    normalized["preference"],
                    normalized.get("cooldownUntil"),
                    normalized["policyVersion"],
                    normalized["authorityEpoch"],
                ),
            )
            row = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO owner_truth.thread_preference_receipts (
                    id, vault_id, thread_id, session_id, owner_subject_id, actor_subject_id,
                    authority_epoch, operation, preference, previous_preference,
                    cooldown_until, expected_session_version, command_id_hash,
                    command_payload_hash, schema_version, ui_schema_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    normalized["receiptId"],
                    normalized["vaultId"],
                    normalized["threadId"],
                    normalized["sessionId"],
                    normalized["ownerSubjectId"],
                    normalized["actorSubjectId"],
                    normalized["authorityEpoch"],
                    normalized["operation"],
                    normalized["preference"],
                    normalized.get("previousPreference"),
                    normalized.get("cooldownUntil"),
                    normalized["expectedSessionVersion"],
                    command_hash,
                    payload_hash,
                    normalized["schemaVersion"],
                    normalized["uiSchemaVersion"],
                ),
            )
        return OwnerTruthThreadPreferenceMutationResult(
            outcome="created",
            preference=self._snapshot_from_row(row),
        )

    def has_matching_receipt(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id: str,
        thread_id: str,
        session_id: str,
        authority_epoch: int,
        operation: str,
        preference: ThreadPreferenceState,
        previous_preference: Optional[ThreadPreferenceState],
    ) -> bool:
        _assert_owner_context(context)
        command_hash = _hash(command_id, field="command_id")
        normalized_thread_id = require_uuid(thread_id, field="thread_id")
        normalized_session_id = require_uuid(session_id, field="session_id")
        normalized_epoch = _nonnegative_int(authority_epoch, field="authority_epoch")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT thread_id, session_id, owner_subject_id, actor_subject_id,
                    authority_epoch, operation, preference, previous_preference
                FROM owner_truth.thread_preference_receipts
                WHERE vault_id = %s AND command_id_hash = %s
                FOR SHARE
                """,
                (context.vault_id, command_hash),
            )
            existing = cursor.fetchone()
        if existing is None:
            return False
        if (
            str(existing["thread_id"]) != normalized_thread_id
            or str(existing["session_id"]) != normalized_session_id
            or str(existing["owner_subject_id"]) != context.owner_subject_id
            or str(existing["actor_subject_id"]) != context.owner_subject_id
            or int(existing["authority_epoch"]) != normalized_epoch
            or str(existing["operation"]) != operation
            or str(existing["preference"]) != preference.value
            or existing["previous_preference"]
            != (None if previous_preference is None else previous_preference.value)
        ):
            raise OwnerTruthThreadPreferenceConflict(
                "commandId cannot be reused with different thread preference meaning"
            )
        return True

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        thread_id: str,
    ) -> Optional[OwnerTruthThreadPreferenceSnapshot]:
        _assert_owner_context(context)
        normalized_thread_id = require_uuid(thread_id, field="thread_id")
        with self._cursor() as cursor:
            return self._read_current(cursor, context=context, thread_id=normalized_thread_id)

    def _read_current(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        thread_id: str,
        lock: bool = False,
    ) -> Optional[OwnerTruthThreadPreferenceSnapshot]:
        lock_clause = " FOR SHARE" if lock else ""
        cursor.execute(
            """
            SELECT preference.vault_id, preference.thread_id, preference.owner_subject_id,
                preference.authority_epoch, preference.preference, preference.cooldown_until,
                preference.row_version, preference.actor_subject_id,
                vault.owner_subject_id AS vault_owner_subject_id,
                vault.authority_epoch AS vault_authority_epoch, vault.status AS vault_status,
                thread.owner_subject_id AS thread_owner_subject_id,
                thread.authority_epoch AS thread_authority_epoch, thread.state AS thread_state
            FROM owner_truth.thread_preferences AS preference
            JOIN owner_truth.vaults AS vault
              ON vault.vault_id = preference.vault_id
            JOIN owner_truth.conversation_threads AS thread
              ON thread.vault_id = preference.vault_id AND thread.id = preference.thread_id
            WHERE preference.vault_id = %s AND preference.thread_id = %s
            """ + lock_clause,
            (context.vault_id, thread_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        snapshot = self._snapshot_from_row(row)
        if (
            str(row["actor_subject_id"]) != context.owner_subject_id
            or str(row["vault_owner_subject_id"]) != context.owner_subject_id
            or str(row["vault_status"]) != "active"
            or int(row["vault_authority_epoch"]) != snapshot.authority_epoch
            or str(row["thread_owner_subject_id"]) != context.owner_subject_id
            or int(row["thread_authority_epoch"]) != snapshot.authority_epoch
            or str(row["thread_state"]) != "active"
        ):
            return OwnerTruthThreadPreferenceSnapshot(
                vault_id=snapshot.vault_id,
                thread_id=snapshot.thread_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=snapshot.authority_epoch,
                preference=ThreadPreferenceState.STALE,
                cooldown_until=None,
                row_version=snapshot.row_version,
            )
        return snapshot

    def _assert_post_transition_session(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> None:
        cursor.execute(
            """
            SELECT vault.owner_subject_id, vault.authority_epoch, vault.status,
                thread.owner_subject_id AS thread_owner_subject_id,
                thread.authority_epoch AS thread_authority_epoch, thread.state AS thread_state,
                session.owner_subject_id AS session_owner_subject_id,
                session.authority_epoch AS session_authority_epoch,
                session.current_thread_id, session.state AS session_state,
                session.boundary AS session_boundary, session.row_version AS session_row_version
            FROM owner_truth.vaults AS vault
            JOIN owner_truth.conversation_threads AS thread
              ON thread.vault_id = vault.vault_id AND thread.id = %s
            JOIN owner_truth.interview_sessions AS session
              ON session.vault_id = vault.vault_id AND session.id = %s
            WHERE vault.vault_id = %s
            FOR SHARE OF vault, thread, session
            """,
            (record["threadId"], record["sessionId"], context.vault_id),
        )
        row = cursor.fetchone()
        expected_state = "paused" if str(record["operation"]) == "set" else "active"
        expected_boundary = (
            str(record["preference"])
            if str(record["operation"]) == "set"
            else InterviewBoundary.OPEN.value
        )
        if (
            row is None
            or str(row["owner_subject_id"]) != context.owner_subject_id
            or str(row["status"]) != "active"
            or int(row["authority_epoch"]) != int(record["authorityEpoch"])
            or str(row["thread_owner_subject_id"]) != context.owner_subject_id
            or int(row["thread_authority_epoch"]) != int(record["authorityEpoch"])
            or str(row["thread_state"]) != "active"
            or str(row["session_owner_subject_id"]) != context.owner_subject_id
            or int(row["session_authority_epoch"]) != int(record["authorityEpoch"])
            or str(row["current_thread_id"]) != str(record["threadId"])
            or str(row["session_state"]) != expected_state
            or str(row["session_boundary"]) != expected_boundary
            or int(row["session_row_version"]) != int(record["expectedSessionVersion"])
        ):
            raise OwnerTruthThreadPreferenceConflict(
                "thread preference must bind the current post-transition session"
            )

    @staticmethod
    def _snapshot_from_row(row: Mapping[str, Any]) -> OwnerTruthThreadPreferenceSnapshot:
        return OwnerTruthThreadPreferenceSnapshot(
            vault_id=str(row["vault_id"]),
            thread_id=str(row["thread_id"]),
            owner_subject_id=str(row["owner_subject_id"]),
            authority_epoch=int(row["authority_epoch"]),
            preference=_state(row["preference"]),
            cooldown_until=row["cooldown_until"],
            row_version=int(row["row_version"]),
        )

    def _lock(self, cursor: Any, key: str) -> None:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
        cursor.fetchone()

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthThreadPreferenceService:
    """Default-off composite for session boundary + persistent thread control."""

    def __init__(
        self,
        store: OwnerTruthThreadPreferenceStore,
        *,
        enabled: bool = False,
        cooldown_seconds: int = 7 * 24 * 60 * 60,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not isinstance(cooldown_seconds, int) or isinstance(cooldown_seconds, bool):
            raise ValueError("cooldown_seconds must be an integer")
        self._store = store
        self._enabled = bool(enabled)
        self._cooldown_seconds = cooldown_seconds
        self._now = now

    def set_boundary(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: SetInterviewBoundaryCommand,
    ) -> OwnerTruthThreadPreferenceBoundaryResult:
        _assert_owner_context(context)
        if not isinstance(command, SetInterviewBoundaryCommand):
            raise TypeError("SetInterviewBoundaryCommand is required")
        if command.boundary is InterviewBoundary.SKIP_ONCE:
            return OwnerTruthThreadPreferenceBoundaryResult(
                session=self._conversation().set_boundary(command=command, context=context),
                preference=None,
            )
        if command.boundary not in {InterviewBoundary.COOLDOWN, InterviewBoundary.DO_NOT_ASK}:
            raise OwnerTruthThreadPreferenceError("only cooldown or doNotAsk may become a thread preference")
        self._require_enabled(requires_cooldown=command.boundary is InterviewBoundary.COOLDOWN)
        with self._request_unit_of_work(command_id=command.command_id, context=context, session_id=command.session_id):
            before = self._conversation().read_session(
                session_id=command.session_id,
                context=context,
            )
            preference = self._preference_for_boundary(command.boundary)
            if self._repository().has_matching_receipt(
                context=context,
                command_id=command.command_id,
                thread_id=command.thread_id,
                session_id=command.session_id,
                authority_epoch=before.authority_epoch,
                operation="set",
                preference=preference,
                previous_preference=None,
            ):
                replayed = self._conversation().set_boundary(command=command, context=context)
                current = self._repository().read(context=context, thread_id=command.thread_id)
                if current is None:
                    raise OwnerTruthThreadPreferenceConflict(
                        "thread preference receipt cannot outlive its current preference"
                    )
                return OwnerTruthThreadPreferenceBoundaryResult(
                    session=replayed,
                    preference=OwnerTruthThreadPreferenceMutationResult(
                        outcome="deduplicated",
                        preference=current,
                    ),
                )
            self._require_current_open_session(context=context, command=command)
            cooldown_until = self._cooldown_until(command.boundary)
            session_result = self._conversation().set_boundary(command=command, context=context)
            preference_result = self._repository().record(
                context=context,
                record=self._record(
                    context=context,
                    command_id=command.command_id,
                    thread_id=command.thread_id,
                    session_id=command.session_id,
                    expected_session_version=session_result.session_version,
                    operation="set",
                    preference=preference,
                    previous_preference=None,
                    cooldown_until=cooldown_until,
                    authority_epoch=before.authority_epoch,
                ),
            )
        return OwnerTruthThreadPreferenceBoundaryResult(
            session=session_result,
            preference=preference_result,
        )

    def restore_do_not_ask(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: RestoreDoNotAskInterviewBoundaryCommand,
    ) -> OwnerTruthThreadPreferenceBoundaryResult:
        _assert_owner_context(context)
        if not isinstance(command, RestoreDoNotAskInterviewBoundaryCommand):
            raise TypeError("RestoreDoNotAskInterviewBoundaryCommand is required")
        if not self._enabled:
            return OwnerTruthThreadPreferenceBoundaryResult(
                session=self._conversation().restore_do_not_ask_boundary(command=command, context=context),
                preference=None,
            )
        with self._request_unit_of_work(command_id=command.command_id, context=context, session_id=command.session_id):
            session_before = self._conversation().read_session(
                session_id=command.session_id,
                context=context,
            )
            if self._repository().has_matching_receipt(
                context=context,
                command_id=command.command_id,
                thread_id=command.thread_id,
                session_id=command.session_id,
                authority_epoch=session_before.authority_epoch,
                operation="restore",
                preference=ThreadPreferenceState.OPEN,
                previous_preference=ThreadPreferenceState.DO_NOT_ASK,
            ):
                replayed = self._conversation().restore_do_not_ask_boundary(
                    command=command,
                    context=context,
                )
                current = self._repository().read(context=context, thread_id=command.thread_id)
                if current is None:
                    raise OwnerTruthThreadPreferenceConflict(
                        "thread preference receipt cannot outlive its current preference"
                    )
                return OwnerTruthThreadPreferenceBoundaryResult(
                    session=replayed,
                    preference=OwnerTruthThreadPreferenceMutationResult(
                        outcome="deduplicated",
                        preference=current,
                    ),
                )
            existing = self._repository().read(context=context, thread_id=command.thread_id)
            if existing is None:
                return OwnerTruthThreadPreferenceBoundaryResult(
                    session=self._conversation().restore_do_not_ask_boundary(
                        command=command,
                        context=context,
                    ),
                    preference=None,
                )
            if existing.preference is not ThreadPreferenceState.DO_NOT_ASK:
                raise OwnerTruthThreadPreferenceConflict(
                    "only a doNotAsk thread preference can use this restore command"
                )
            self._require_paused_session(
                context=context,
                thread_id=command.thread_id,
                session_id=command.session_id,
                expected_session_version=command.expected_session_version,
                boundary=InterviewBoundary.DO_NOT_ASK,
            )
            session_result = self._conversation().restore_do_not_ask_boundary(
                command=command,
                context=context,
            )
            preference_result = self._repository().record(
                context=context,
                record=self._record(
                    context=context,
                    command_id=command.command_id,
                    thread_id=command.thread_id,
                    session_id=command.session_id,
                    expected_session_version=session_result.session_version,
                    operation="restore",
                    preference=ThreadPreferenceState.OPEN,
                    previous_preference=ThreadPreferenceState.DO_NOT_ASK,
                    cooldown_until=None,
                    authority_epoch=existing.authority_epoch,
                ),
            )
        return OwnerTruthThreadPreferenceBoundaryResult(
            session=session_result,
            preference=preference_result,
        )

    def restore_cooldown(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: RestoreCooldownThreadPreferenceCommand,
    ) -> OwnerTruthThreadPreferenceBoundaryResult:
        _assert_owner_context(context)
        if not isinstance(command, RestoreCooldownThreadPreferenceCommand):
            raise TypeError("RestoreCooldownThreadPreferenceCommand is required")
        self._require_enabled(requires_cooldown=True)
        reopen = SetInterviewBoundaryCommand(
            command_id=command.command_id,
            thread_id=command.thread_id,
            session_id=command.session_id,
            expected_session_version=command.expected_session_version,
            boundary=InterviewBoundary.OPEN,
        )
        with self._request_unit_of_work(command_id=command.command_id, context=context, session_id=command.session_id):
            session_before = self._conversation().read_session(
                session_id=command.session_id,
                context=context,
            )
            if self._repository().has_matching_receipt(
                context=context,
                command_id=command.command_id,
                thread_id=command.thread_id,
                session_id=command.session_id,
                authority_epoch=session_before.authority_epoch,
                operation="restore",
                preference=ThreadPreferenceState.OPEN,
                previous_preference=ThreadPreferenceState.COOLDOWN,
            ):
                replayed = self._conversation().set_boundary(command=reopen, context=context)
                current = self._repository().read(context=context, thread_id=command.thread_id)
                if current is None:
                    raise OwnerTruthThreadPreferenceConflict(
                        "thread preference receipt cannot outlive its current preference"
                    )
                return OwnerTruthThreadPreferenceBoundaryResult(
                    session=replayed,
                    preference=OwnerTruthThreadPreferenceMutationResult(
                        outcome="deduplicated",
                        preference=current,
                    ),
                )
            existing = self._repository().read(context=context, thread_id=command.thread_id)
            if existing is None or existing.preference is not ThreadPreferenceState.COOLDOWN:
                raise OwnerTruthThreadPreferenceConflict("thread is not in a cooldown preference")
            now = _utc(self._now(), field="now")
            if existing.cooldown_until is None or now < existing.cooldown_until:
                raise OwnerTruthThreadPreferenceCooldownActive("thread cooldown has not yet elapsed")
            self._require_paused_session(
                context=context,
                thread_id=command.thread_id,
                session_id=command.session_id,
                expected_session_version=command.expected_session_version,
                boundary=InterviewBoundary.COOLDOWN,
            )
            session_result = self._conversation().set_boundary(command=reopen, context=context)
            preference_result = self._repository().record(
                context=context,
                record=self._record(
                    context=context,
                    command_id=command.command_id,
                    thread_id=command.thread_id,
                    session_id=command.session_id,
                    expected_session_version=session_result.session_version,
                    operation="restore",
                    preference=ThreadPreferenceState.OPEN,
                    previous_preference=ThreadPreferenceState.COOLDOWN,
                    cooldown_until=None,
                    authority_epoch=existing.authority_epoch,
                ),
            )
        return OwnerTruthThreadPreferenceBoundaryResult(
            session=session_result,
            preference=preference_result,
        )

    def permits_recommendation(
        self,
        *,
        context: OwnerTruthCommandContext,
        thread_id: str,
    ) -> bool:
        _assert_owner_context(context)
        preference = self._repository().read(context=context, thread_id=thread_id)
        return preference is None or preference.is_recommendation_eligible

    def _conversation(self) -> OwnerTruthConversationService:
        return OwnerTruthConversationService(self._store.owner_truth_conversation_repository())

    def _repository(self) -> OwnerTruthThreadPreferenceRepository:
        return self._store.owner_truth_thread_preference_repository()

    def _require_enabled(self, *, requires_cooldown: bool = False) -> None:
        if not self._enabled:
            raise OwnerTruthThreadPreferenceUnavailable(
                "thread preference QA contract is disabled"
            )
        if requires_cooldown and self._cooldown_seconds <= 0:
            raise OwnerTruthThreadPreferenceUnavailable(
                "thread preference cooldown policy is not configured"
            )

    def _require_current_open_session(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: SetInterviewBoundaryCommand,
    ) -> OwnerTruthInterviewSessionSnapshot:
        session = self._conversation().read_session(session_id=command.session_id, context=context)
        if (
            session.thread_id != command.thread_id
            or session.row_version != command.expected_session_version
            or session.state is not InterviewSessionState.ACTIVE
            or session.boundary is not InterviewBoundary.OPEN
        ):
            raise OwnerTruthThreadPreferenceConflict(
                "thread preference requires a current active open interview session"
            )
        return session

    def _require_paused_session(
        self,
        *,
        context: OwnerTruthCommandContext,
        thread_id: str,
        session_id: str,
        expected_session_version: int,
        boundary: InterviewBoundary,
    ) -> OwnerTruthInterviewSessionSnapshot:
        session = self._conversation().read_session(session_id=session_id, context=context)
        if (
            session.thread_id != thread_id
            or session.row_version != expected_session_version
            or session.state is not InterviewSessionState.PAUSED
            or session.boundary is not boundary
        ):
            raise OwnerTruthThreadPreferenceConflict(
                "thread preference restore requires its current paused interview session"
            )
        return session

    def _cooldown_until(self, boundary: InterviewBoundary) -> Optional[datetime]:
        if boundary is not InterviewBoundary.COOLDOWN:
            return None
        return _utc(self._now(), field="now") + timedelta(seconds=self._cooldown_seconds)

    @staticmethod
    def _preference_for_boundary(boundary: InterviewBoundary) -> ThreadPreferenceState:
        if boundary is InterviewBoundary.COOLDOWN:
            return ThreadPreferenceState.COOLDOWN
        if boundary is InterviewBoundary.DO_NOT_ASK:
            return ThreadPreferenceState.DO_NOT_ASK
        raise OwnerTruthThreadPreferenceError("boundary does not map to a thread preference")

    @staticmethod
    def _record(
        *,
        context: OwnerTruthCommandContext,
        command_id: str,
        thread_id: str,
        session_id: str,
        expected_session_version: int,
        operation: str,
        preference: ThreadPreferenceState,
        previous_preference: Optional[ThreadPreferenceState],
        cooldown_until: Optional[datetime],
        authority_epoch: int,
    ) -> dict[str, Any]:
        command_hash = _hash(command_id, field="command_id")
        cooldown_value = None if cooldown_until is None else _utc(cooldown_until, field="cooldown_until")
        normalized_authority_epoch = _nonnegative_int(
            authority_epoch,
            field="authority_epoch",
        )
        payload = {
            "schemaVersion": OWNER_TRUTH_THREAD_PREFERENCE_SCHEMA_VERSION,
            "operation": operation,
            "threadId": thread_id,
            "sessionId": session_id,
            "preference": preference.value,
            "previousPreference": None if previous_preference is None else previous_preference.value,
            "authorityEpoch": normalized_authority_epoch,
        }
        return {
            "receiptId": str(uuid5(_RECEIPT_NAMESPACE, f"{context.vault_id}:{command_hash}:{operation}")),
            "preferenceId": str(uuid5(_RECEIPT_NAMESPACE, f"{context.vault_id}:{thread_id}")),
            "vaultId": context.vault_id,
            "ownerSubjectId": context.owner_subject_id,
            "actorSubjectId": context.actor_subject_id,
            "authorityEpoch": normalized_authority_epoch,
            "threadId": thread_id,
            "sessionId": session_id,
            "expectedSessionVersion": expected_session_version,
            "operation": operation,
            "preference": preference.value,
            "previousPreference": None if previous_preference is None else previous_preference.value,
            "cooldownUntil": cooldown_value,
            "commandIdHash": command_hash,
            "payloadHash": _hash(_canonical_json(payload), field="payload"),
            "schemaVersion": OWNER_TRUTH_THREAD_PREFERENCE_SCHEMA_VERSION,
            "uiSchemaVersion": OWNER_TRUTH_THREAD_PREFERENCE_UI_SCHEMA_VERSION,
            "rowVersion": 1,
            "policyVersion": context.policy_version,
        }

    def _request_unit_of_work(
        self,
        *,
        command_id: str,
        context: OwnerTruthCommandContext,
        session_id: str,
    ) -> Any:
        return self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-thread-preference:"
                f"{context.vault_id}:{session_id}"
            ),
            command_id=command_id,
        )


__all__ = [
    "InMemoryOwnerTruthThreadPreferenceRepository",
    "OWNER_TRUTH_THREAD_PREFERENCE_SCHEMA_VERSION",
    "OWNER_TRUTH_THREAD_PREFERENCE_UI_SCHEMA_VERSION",
    "OwnerTruthThreadPreferenceAccessDenied",
    "OwnerTruthThreadPreferenceBoundaryResult",
    "OwnerTruthThreadPreferenceConflict",
    "OwnerTruthThreadPreferenceCooldownActive",
    "OwnerTruthThreadPreferenceError",
    "OwnerTruthThreadPreferenceMutationResult",
    "OwnerTruthThreadPreferenceService",
    "OwnerTruthThreadPreferenceSnapshot",
    "OwnerTruthThreadPreferenceUnavailable",
    "PostgresOwnerTruthThreadPreferenceRepository",
    "RestoreCooldownThreadPreferenceCommand",
    "ThreadPreferenceState",
]
