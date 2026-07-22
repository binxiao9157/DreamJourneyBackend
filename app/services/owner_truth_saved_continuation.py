"""Explicit, value-free Owner continuation cues for M0-B QA planning.

An active interview session alone is not evidence that an Owner wants to
continue a topic later. This module records that intent only after an explicit
Owner action binds one current, confirmed MemoryVersion and one still-missing
facet to the current open session. It never stores transcript text, question
text, model summaries, or provider output.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, ContextManager, Iterable, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.conversation import (
    InterviewBoundary,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationThreadAuthoritySnapshot,
)
from app.domain.owner_truth.knowledge_dimension_read import (
    OwnerTruthKnowledgeDimensionReadService,
    OwnerTruthKnowledgeDimensionReadState,
)
from app.domain.owner_truth.knowledge_recommendations import (
    KnowledgeDimension,
    ServerPlannedContinuationCue,
    knowledge_dimension_facets,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.domain.owner_truth.contracts import OwnerTruthContractError, require_nonblank, require_uuid


OWNER_TRUTH_SAVED_CONTINUATION_CUE_SCHEMA_VERSION = "owner-truth-saved-continuation-cue-v1"
OWNER_TRUTH_SAVED_CONTINUATION_CUE_UI_SCHEMA_VERSION = "saved-continuation-cue-v1"

_CUE_NAMESPACE = UUID("c1fc7831-35e8-4582-80a6-e320a34dcf29")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class OwnerTruthSavedContinuationCueError(OwnerTruthContractError):
    """An explicit continuation cue is malformed or unsafe to persist."""


class OwnerTruthSavedContinuationCueAccessDenied(OwnerTruthSavedContinuationCueError):
    """Only the current Vault Owner may create or read a cue."""


class OwnerTruthSavedContinuationCueConflict(OwnerTruthSavedContinuationCueError):
    """A replay, session version, or one-cue-per-session rule conflicted."""


class OwnerTruthSavedContinuationCueStale(OwnerTruthSavedContinuationCueConflict):
    """The referenced session or MemoryVersion is no longer current."""


class OwnerTruthSavedContinuationCueUnavailable(OwnerTruthSavedContinuationCueError):
    """The default-off QA lane cannot safely derive a cue target."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthSavedContinuationCueError(
            "saved continuation cue payload must be JSON serializable"
        ) from exc


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _digest(value: Mapping[str, Any]) -> str:
    return _sha256(_canonical_json(value))


def _nonnegative_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OwnerTruthSavedContinuationCueError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OwnerTruthSavedContinuationCueError(f"{field} must be a positive integer")
    return value


def _hash(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if _HASH_PATTERN.fullmatch(normalized) is None:
        raise OwnerTruthSavedContinuationCueError(f"{field} must be a SHA-256 digest")
    return normalized


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthSavedContinuationCueAccessDenied("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthSavedContinuationCueAccessDenied(
            "only the Vault Owner may save a continuation cue"
        )


@dataclass(frozen=True)
class OwnerTruthSavedContinuationCueCommand:
    """A narrow explicit Owner instruction to continue one confirmed gap later."""

    command_id: str
    thread_id: str
    session_id: str
    expected_session_version: int
    memory_version_id: str
    target_dimension: KnowledgeDimension | str
    missing_facet: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        object.__setattr__(self, "thread_id", require_uuid(self.thread_id, field="thread_id"))
        object.__setattr__(self, "session_id", require_uuid(self.session_id, field="session_id"))
        object.__setattr__(
            self,
            "memory_version_id",
            require_uuid(self.memory_version_id, field="memory_version_id"),
        )
        _positive_int(self.expected_session_version, field="expected_session_version")
        try:
            dimension = KnowledgeDimension(self.target_dimension)
        except (TypeError, ValueError) as exc:
            raise OwnerTruthSavedContinuationCueError("target_dimension is not supported") from exc
        object.__setattr__(self, "target_dimension", dimension)
        missing_facet = require_nonblank(self.missing_facet, field="missing_facet")
        if missing_facet not in knowledge_dimension_facets(dimension):
            raise OwnerTruthSavedContinuationCueError(
                "missing_facet is not valid for target_dimension"
            )
        object.__setattr__(self, "missing_facet", missing_facet)

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "schemaVersion": OWNER_TRUTH_SAVED_CONTINUATION_CUE_SCHEMA_VERSION,
                "threadId": self.thread_id,
                "sessionId": self.session_id,
                "expectedSessionVersion": self.expected_session_version,
                "memoryVersionId": self.memory_version_id,
                "targetDimension": self.target_dimension.value,
                "missingFacet": self.missing_facet,
                "uiSchemaVersion": OWNER_TRUTH_SAVED_CONTINUATION_CUE_UI_SCHEMA_VERSION,
            }
        )


@dataclass(frozen=True)
class OwnerTruthSavedContinuationCueResult:
    outcome: str
    cue_id: str
    thread_id: str
    session_id: str
    memory_version_id: str
    target_dimension: KnowledgeDimension
    missing_facet: str
    authority_epoch: int

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise OwnerTruthSavedContinuationCueError("saved continuation cue outcome is not supported")
        for field in ("cue_id", "thread_id", "session_id", "memory_version_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        try:
            object.__setattr__(self, "target_dimension", KnowledgeDimension(self.target_dimension))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthSavedContinuationCueError("target_dimension is not supported") from exc
        missing_facet = require_nonblank(self.missing_facet, field="missing_facet")
        if missing_facet not in knowledge_dimension_facets(self.target_dimension):
            raise OwnerTruthSavedContinuationCueError(
                "missing_facet is not valid for target_dimension"
            )
        object.__setattr__(self, "missing_facet", missing_facet)
        _nonnegative_int(self.authority_epoch, field="authority_epoch")


class OwnerTruthSavedContinuationCueRepository(Protocol):
    def replay(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthSavedContinuationCueCommand,
    ) -> OwnerTruthSavedContinuationCueResult | None:
        """Return a matching append-only receipt without revalidating live state."""
        ...

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthSavedContinuationCueResult:
        ...

    def list_for_recommendation(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[ServerPlannedContinuationCue, ...]:
        ...


class OwnerTruthSavedContinuationCueStore(Protocol):
    def owner_truth_memory_projection_repository(self) -> Any:
        ...

    def owner_truth_knowledge_dimension_confirmation_repository(self) -> Any:
        ...

    def owner_truth_conversation_repository(self) -> Any:
        ...

    def owner_truth_saved_continuation_cue_repository(self) -> OwnerTruthSavedContinuationCueRepository:
        ...


def _result_from_record(
    record: Mapping[str, Any],
    *,
    outcome: str,
) -> OwnerTruthSavedContinuationCueResult:
    if not isinstance(record, Mapping):
        raise OwnerTruthSavedContinuationCueError("saved continuation cue record is required")
    return OwnerTruthSavedContinuationCueResult(
        outcome=outcome,
        cue_id=str(record.get("cueId") or ""),
        thread_id=str(record.get("threadId") or ""),
        session_id=str(record.get("sessionId") or ""),
        memory_version_id=str(record.get("memoryVersionId") or ""),
        target_dimension=record.get("targetDimension"),
        missing_facet=str(record.get("missingFacet") or ""),
        authority_epoch=_nonnegative_int(record.get("authorityEpoch"), field="authorityEpoch"),
    )


def _cue_from_record(record: Mapping[str, Any]) -> ServerPlannedContinuationCue:
    if not isinstance(record, Mapping):
        raise OwnerTruthSavedContinuationCueError("saved continuation cue record is required")
    return ServerPlannedContinuationCue(
        cue_id=str(record.get("cueId") or ""),
        owner_subject_id=str(record.get("ownerSubjectId") or ""),
        vault_id=str(record.get("vaultId") or ""),
        authority_epoch=_nonnegative_int(record.get("authorityEpoch"), field="authorityEpoch"),
        thread_id=str(record.get("threadId") or ""),
        session_id=str(record.get("sessionId") or ""),
        expected_session_version=_positive_int(
            record.get("expectedSessionVersion"),
            field="expectedSessionVersion",
        ),
        memory_version_id=str(record.get("memoryVersionId") or ""),
        target_dimension=record.get("targetDimension"),
        missing_facet=str(record.get("missingFacet") or ""),
    )


class InMemoryOwnerTruthSavedContinuationCueRepository:
    """Thread-safe semantic double for append-only continuation cue receipts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records_by_command: dict[tuple[str, str], dict[str, Any]] = {}
        self._records_by_session: dict[tuple[str, str], dict[str, Any]] = {}

    def replay(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthSavedContinuationCueCommand,
    ) -> OwnerTruthSavedContinuationCueResult | None:
        _assert_owner_context(context)
        command_key = (context.vault_id, command.command_id_hash)
        with self._lock:
            existing = self._records_by_command.get(command_key)
            if existing is None:
                return None
            if str(existing.get("payloadHash") or "") != command.payload_hash:
                raise OwnerTruthSavedContinuationCueConflict(
                    "commandId cannot be reused with different saved continuation meaning"
                )
            return _result_from_record(existing, outcome="deduplicated")

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthSavedContinuationCueResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        _result_from_record(normalized, outcome="created")
        if (
            str(normalized.get("vaultId") or "") != context.vault_id
            or str(normalized.get("ownerSubjectId") or "") != context.owner_subject_id
            or str(normalized.get("actorSubjectId") or "") != context.owner_subject_id
        ):
            raise OwnerTruthSavedContinuationCueAccessDenied(
                "saved continuation cue does not match Owner context"
            )
        command_hash = _hash(normalized.get("commandIdHash"), field="commandIdHash")
        payload_hash = _hash(normalized.get("payloadHash"), field="payloadHash")
        session_id = str(normalized.get("sessionId") or "")
        command_key = (context.vault_id, command_hash)
        session_key = (context.vault_id, session_id)
        with self._lock:
            existing = self._records_by_command.get(command_key)
            if existing is not None:
                if str(existing.get("payloadHash") or "") != payload_hash:
                    raise OwnerTruthSavedContinuationCueConflict(
                        "commandId cannot be reused with different saved continuation meaning"
                    )
                return _result_from_record(existing, outcome="deduplicated")
            if session_key in self._records_by_session:
                raise OwnerTruthSavedContinuationCueConflict(
                    "interview session already has an immutable saved continuation cue"
                )
            self._records_by_command[command_key] = normalized
            self._records_by_session[session_key] = normalized
            return _result_from_record(normalized, outcome="created")

    def list_for_recommendation(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[ServerPlannedContinuationCue, ...]:
        _assert_owner_context(context)
        with self._lock:
            rows = [
                deepcopy(record)
                for record in self._records_by_command.values()
                if str(record.get("vaultId") or "") == context.vault_id
                and str(record.get("ownerSubjectId") or "") == context.owner_subject_id
                and str(record.get("actorSubjectId") or "") == context.owner_subject_id
            ]
        return tuple(
            _cue_from_record(record)
            for record in sorted(rows, key=lambda item: str(item.get("cueId") or ""))
        )


class PostgresOwnerTruthSavedContinuationCueRepository:
    """Postgres persistence for append-only, owner-scoped continuation cues."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def replay(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthSavedContinuationCueCommand,
    ) -> OwnerTruthSavedContinuationCueResult | None:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-saved-continuation-command:"
                    f"{context.vault_id}:{command.command_id_hash}",
                ),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    thread_id, session_id, expected_session_version, memory_version_id, target_dimension,
                    missing_facet, command_id_hash, command_payload_hash
                FROM owner_truth.saved_continuation_cues
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command.command_id_hash),
            )
            existing = cursor.fetchone()
        if existing is None:
            return None
        if str(existing["command_payload_hash"]) != command.payload_hash:
            raise OwnerTruthSavedContinuationCueConflict(
                "commandId cannot be reused with different saved continuation meaning"
            )
        return _result_from_record(self._row_to_record(existing), outcome="deduplicated")

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, Any],
    ) -> OwnerTruthSavedContinuationCueResult:
        _assert_owner_context(context)
        normalized = deepcopy(dict(record))
        _result_from_record(normalized, outcome="created")
        command_hash = _hash(normalized.get("commandIdHash"), field="commandIdHash")
        payload_hash = _hash(normalized.get("payloadHash"), field="payloadHash")
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-saved-continuation-command:{context.vault_id}:{command_hash}",),
            )
            cursor.fetchone()
            cursor.execute(
                """
                SELECT id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    thread_id, session_id, expected_session_version, memory_version_id, target_dimension,
                    missing_facet, command_id_hash, command_payload_hash
                FROM owner_truth.saved_continuation_cues
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing["command_payload_hash"]) != payload_hash:
                    raise OwnerTruthSavedContinuationCueConflict(
                        "commandId cannot be reused with different saved continuation meaning"
                    )
                return _result_from_record(self._row_to_record(existing), outcome="deduplicated")

            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (
                    "owner-truth-saved-continuation-session:"
                    f"{context.vault_id}:{normalized['sessionId']}",
                ),
            )
            cursor.fetchone()
            self._assert_current_target(cursor, context=context, record=normalized)
            cursor.execute(
                """
                SELECT id
                FROM owner_truth.saved_continuation_cues
                WHERE vault_id = %s AND session_id = %s
                FOR UPDATE
                """,
                (context.vault_id, normalized["sessionId"]),
            )
            if cursor.fetchone() is not None:
                raise OwnerTruthSavedContinuationCueConflict(
                    "interview session already has an immutable saved continuation cue"
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.saved_continuation_cues (
                    id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    thread_id, session_id, expected_session_version,
                    knowledge_confirmation_id, memory_id, memory_version_id,
                    bound_content_hash, target_dimension, missing_facet,
                    command_id_hash, command_payload_hash, schema_version,
                    ui_schema_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    normalized["cueId"],
                    normalized["vaultId"],
                    normalized["ownerSubjectId"],
                    normalized["actorSubjectId"],
                    normalized["authorityEpoch"],
                    normalized["threadId"],
                    normalized["sessionId"],
                    normalized["expectedSessionVersion"],
                    normalized["knowledgeConfirmationId"],
                    normalized["memoryId"],
                    normalized["memoryVersionId"],
                    normalized["boundContentHash"],
                    normalized["targetDimension"],
                    normalized["missingFacet"],
                    command_hash,
                    payload_hash,
                    normalized["schemaVersion"],
                    normalized["uiSchemaVersion"],
                ),
            )
        return _result_from_record(normalized, outcome="created")

    def list_for_recommendation(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[ServerPlannedContinuationCue, ...]:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT cue.id, cue.vault_id, cue.owner_subject_id, cue.actor_subject_id,
                    cue.authority_epoch, cue.thread_id, cue.session_id,
                    cue.expected_session_version, cue.memory_version_id, cue.target_dimension, cue.missing_facet,
                    cue.command_id_hash, cue.command_payload_hash
                FROM owner_truth.saved_continuation_cues AS cue
                JOIN owner_truth.vaults AS vault
                  ON vault.vault_id = cue.vault_id
                JOIN owner_truth.conversation_threads AS thread
                  ON thread.vault_id = cue.vault_id AND thread.id = cue.thread_id
                JOIN owner_truth.interview_sessions AS session
                  ON session.vault_id = cue.vault_id AND session.id = cue.session_id
                WHERE cue.vault_id = %s
                  AND cue.owner_subject_id = %s
                  AND cue.actor_subject_id = %s
                  AND vault.owner_subject_id = %s
                  AND vault.status = 'active'
                  AND cue.authority_epoch = vault.authority_epoch
                  AND thread.owner_subject_id = vault.owner_subject_id
                  AND thread.authority_epoch = vault.authority_epoch
                  AND thread.state = 'active'
                  AND session.owner_subject_id = vault.owner_subject_id
                  AND session.authority_epoch = vault.authority_epoch
                  AND session.current_thread_id = cue.thread_id
                  AND (
                    (
                      session.state = 'active'
                      AND session.boundary = 'open'
                      AND session.row_version = cue.expected_session_version
                    )
                    OR (
                      session.state = 'paused'
                      AND session.boundary = 'cooldown'
                      AND session.row_version = cue.expected_session_version + 1
                    )
                  )
                ORDER BY cue.id ASC
                """,
                (
                    context.vault_id,
                    context.owner_subject_id,
                    context.owner_subject_id,
                    context.owner_subject_id,
                ),
            )
            rows = cursor.fetchall()
        return tuple(_cue_from_record(self._row_to_record(row)) for row in rows)

    def _assert_current_target(
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
        session = cursor.fetchone()
        if (
            session is None
            or str(session["owner_subject_id"]) != context.owner_subject_id
            or str(session["status"]) != "active"
            or int(session["authority_epoch"]) != int(record["authorityEpoch"])
            or str(session["thread_owner_subject_id"]) != context.owner_subject_id
            or int(session["thread_authority_epoch"]) != int(record["authorityEpoch"])
            or str(session["thread_state"]) != "active"
            or str(session["session_owner_subject_id"]) != context.owner_subject_id
            or int(session["session_authority_epoch"]) != int(record["authorityEpoch"])
            or str(session["current_thread_id"]) != str(record["threadId"])
            or str(session["session_state"]) != "active"
            or str(session["session_boundary"]) != "open"
            or int(session["session_row_version"]) != int(record["expectedSessionVersion"])
        ):
            raise OwnerTruthSavedContinuationCueStale(
                "saved continuation cue must bind the current active open interview session"
            )
        cursor.execute(
            """
            SELECT receipt.id, receipt.vault_id, receipt.owner_subject_id,
                receipt.actor_subject_id, receipt.authority_epoch, receipt.memory_id,
                receipt.memory_version_id, receipt.bound_content_hash, receipt.dimension,
                memory.owner_subject_id AS memory_owner_subject_id,
                memory.status AS memory_status, memory.authority_epoch AS memory_authority_epoch,
                version.memory_id AS version_memory_id, version.is_current, version.content_hash
            FROM owner_truth.knowledge_dimension_confirmation_receipts AS receipt
            JOIN owner_truth.memories AS memory
              ON memory.vault_id = receipt.vault_id AND memory.id = receipt.memory_id
            JOIN owner_truth.memory_versions AS version
              ON version.vault_id = receipt.vault_id AND version.id = receipt.memory_version_id
            WHERE receipt.vault_id = %s AND receipt.id = %s
            FOR SHARE OF receipt, memory, version
            """,
            (context.vault_id, record["knowledgeConfirmationId"]),
        )
        confirmation = cursor.fetchone()
        if (
            confirmation is None
            or str(confirmation["owner_subject_id"]) != context.owner_subject_id
            or str(confirmation["actor_subject_id"]) != context.owner_subject_id
            or int(confirmation["authority_epoch"]) != int(record["authorityEpoch"])
            or str(confirmation["memory_id"]) != str(record["memoryId"])
            or str(confirmation["memory_version_id"]) != str(record["memoryVersionId"])
            or str(confirmation["bound_content_hash"]) != str(record["boundContentHash"])
            or str(confirmation["dimension"]) != str(record["targetDimension"])
            or str(confirmation["memory_owner_subject_id"]) != context.owner_subject_id
            or str(confirmation["memory_status"]) != "active"
            or int(confirmation["memory_authority_epoch"]) != int(record["authorityEpoch"])
            or str(confirmation["version_memory_id"]) != str(record["memoryId"])
            or confirmation["is_current"] is not True
            or str(confirmation["content_hash"]) != str(record["boundContentHash"])
        ):
            raise OwnerTruthSavedContinuationCueStale(
                "saved continuation cue must bind a current Owner-confirmed MemoryVersion"
            )

    @staticmethod
    def _row_to_record(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "cueId": str(row["id"]),
            "vaultId": str(row["vault_id"]),
            "ownerSubjectId": str(row["owner_subject_id"]),
            "actorSubjectId": str(row["actor_subject_id"]),
            "authorityEpoch": int(row["authority_epoch"]),
            "threadId": str(row["thread_id"]),
            "sessionId": str(row["session_id"]),
            "expectedSessionVersion": int(row["expected_session_version"]),
            "memoryVersionId": str(row["memory_version_id"]),
            "targetDimension": str(row["target_dimension"]),
            "missingFacet": str(row["missing_facet"]),
            "commandIdHash": str(row["command_id_hash"]),
            "payloadHash": str(row["command_payload_hash"]),
        }

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthSavedContinuationCueService:
    """Default-off service for one Owner-authored M0-B continuity pointer."""

    def __init__(self, store: OwnerTruthSavedContinuationCueStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def create(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: OwnerTruthSavedContinuationCueCommand,
    ) -> OwnerTruthSavedContinuationCueResult:
        _assert_owner_context(context)
        if not isinstance(command, OwnerTruthSavedContinuationCueCommand):
            raise OwnerTruthSavedContinuationCueError("saved continuation cue command is required")
        if not self._enabled:
            raise OwnerTruthSavedContinuationCueUnavailable(
                "saved continuation cue QA contract is disabled"
            )
        with self._request_unit_of_work(
            correlation_id=(
                "owner-truth-saved-continuation-cue-"
                f"{context.vault_id}:{command.session_id}"
            ),
            command_id=command.command_id_hash,
        ):
            repository = self._store.owner_truth_saved_continuation_cue_repository()
            replayed = repository.replay(context=context, command=command)
            if replayed is not None:
                return replayed
            conversation = self._store.owner_truth_conversation_repository()
            try:
                session = conversation.get_interview_session(
                    session_id=command.session_id,
                    context=context,
                )
                thread = conversation.get_interview_thread_authority(
                    thread_id=command.thread_id,
                    context=context,
                )
            except OwnerTruthConversationAccessDenied as error:
                raise OwnerTruthSavedContinuationCueAccessDenied(str(error)) from error
            if (
                session.thread_id != command.thread_id
                or session.row_version != command.expected_session_version
                or session.state is not InterviewSessionState.ACTIVE
                or session.boundary is not InterviewBoundary.OPEN
                or thread.session_id != command.session_id
                or not thread.is_recommendation_eligible
            ):
                raise OwnerTruthSavedContinuationCueStale(
                    "saved continuation cue must bind the current active open interview session"
                )
            dimension_read = OwnerTruthKnowledgeDimensionReadService(
                self._store.owner_truth_memory_projection_repository(),
                self._store.owner_truth_knowledge_dimension_confirmation_repository(),
            ).read(context=context)
            if dimension_read.state is not OwnerTruthKnowledgeDimensionReadState.READY:
                raise OwnerTruthSavedContinuationCueUnavailable(
                    "current Owner-confirmed knowledge coverage is unavailable"
                )
            assert dimension_read.coverage is not None
            if thread.authority_epoch != dimension_read.authority_epoch:
                raise OwnerTruthSavedContinuationCueStale(
                    "saved continuation cue authority epoch is stale"
                )
            coverage = dimension_read.coverage.for_dimension(command.target_dimension)
            if command.memory_version_id not in coverage.memory_version_ids:
                raise OwnerTruthSavedContinuationCueStale(
                    "saved continuation cue memory is not current Owner-confirmed evidence for its dimension"
                )
            if command.missing_facet not in coverage.missing_facets:
                raise OwnerTruthSavedContinuationCueStale(
                    "saved continuation cue facet is already covered"
                )
            confirmations = tuple(
                self._store.owner_truth_knowledge_dimension_confirmation_repository().list_for_projection(
                    context=context,
                    memory_version_ids=dimension_read.included_memory_version_ids,
                )
            )
            matching = tuple(
                record
                for record in confirmations
                if str(record.get("memoryVersionId") or "") == command.memory_version_id
                and str(record.get("dimension") or "") == command.target_dimension.value
                and _confirmation_authority_epoch(record) == dimension_read.authority_epoch
            )
            if len(matching) != 1:
                raise OwnerTruthSavedContinuationCueStale(
                    "saved continuation cue requires exactly one current Owner dimension confirmation"
                )
            confirmation = matching[0]
            record = {
                "cueId": str(uuid5(_CUE_NAMESPACE, f"{context.vault_id}:{command.command_id_hash}")),
                "vaultId": context.vault_id,
                "ownerSubjectId": context.owner_subject_id,
                "actorSubjectId": context.actor_subject_id,
                "authorityEpoch": dimension_read.authority_epoch,
                "threadId": command.thread_id,
                "sessionId": command.session_id,
                "expectedSessionVersion": command.expected_session_version,
                "knowledgeConfirmationId": require_uuid(
                    str(confirmation.get("confirmationId") or ""),
                    field="confirmationId",
                ),
                "memoryId": require_uuid(str(confirmation.get("memoryId") or ""), field="memoryId"),
                "memoryVersionId": command.memory_version_id,
                "boundContentHash": _hash(
                    confirmation.get("boundContentHash"), field="boundContentHash"
                ),
                "targetDimension": command.target_dimension.value,
                "missingFacet": command.missing_facet,
                "commandIdHash": command.command_id_hash,
                "payloadHash": command.payload_hash,
                "schemaVersion": OWNER_TRUTH_SAVED_CONTINUATION_CUE_SCHEMA_VERSION,
                "uiSchemaVersion": OWNER_TRUTH_SAVED_CONTINUATION_CUE_UI_SCHEMA_VERSION,
            }
            return repository.record(
                context=context,
                record=record,
            )

    def _request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


def _confirmation_authority_epoch(record: Mapping[str, Any]) -> int:
    """Read epoch zero as a valid authority epoch, never as a missing value."""

    raw_epoch = record.get("authorityEpoch")
    if raw_epoch is None:
        return -1
    try:
        return int(raw_epoch)
    except (TypeError, ValueError):
        return -1


def saved_continuation_cue_summary(
    result: OwnerTruthSavedContinuationCueResult,
) -> dict[str, Any]:
    if not isinstance(result, OwnerTruthSavedContinuationCueResult):
        raise OwnerTruthSavedContinuationCueError("saved continuation cue result is required")
    return {
        "schemaVersion": OWNER_TRUTH_SAVED_CONTINUATION_CUE_SCHEMA_VERSION,
        "status": result.outcome,
        "cueId": result.cue_id,
        "threadId": result.thread_id,
        "sessionId": result.session_id,
        "memoryVersionId": result.memory_version_id,
        "targetDimension": result.target_dimension.value,
        "missingFacet": result.missing_facet,
        "authorityEpoch": result.authority_epoch,
    }


__all__ = [
    "InMemoryOwnerTruthSavedContinuationCueRepository",
    "OWNER_TRUTH_SAVED_CONTINUATION_CUE_SCHEMA_VERSION",
    "OWNER_TRUTH_SAVED_CONTINUATION_CUE_UI_SCHEMA_VERSION",
    "OwnerTruthSavedContinuationCueAccessDenied",
    "OwnerTruthSavedContinuationCueCommand",
    "OwnerTruthSavedContinuationCueConflict",
    "OwnerTruthSavedContinuationCueError",
    "OwnerTruthSavedContinuationCueRepository",
    "OwnerTruthSavedContinuationCueResult",
    "OwnerTruthSavedContinuationCueService",
    "OwnerTruthSavedContinuationCueStale",
    "OwnerTruthSavedContinuationCueUnavailable",
    "PostgresOwnerTruthSavedContinuationCueRepository",
    "saved_continuation_cue_summary",
]
