"""Append-only, value-free audit records for guided-interview policy actions.

The existing orchestrator is deliberately read-only.  This module is the
separate explicit write path that can bind one deterministic policy outcome to
one already-persisted Owner narrative.  It never reads or stores message text,
topic labels, model output, provider payloads, Candidates, or MemoryVersions.
"""

from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, ContextManager, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.contracts import OwnerTruthContractError, require_nonblank, require_uuid
from app.domain.owner_truth.conversation import (
    ConversationMessageAuthor,
    ConversationMessageKind,
    OwnerTruthConversationAccessDenied,
)
from app.domain.owner_truth.interview_orchestration import (
    INTERVIEW_ORCHESTRATION_SCHEMA_VERSION,
    InterviewAction,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_interview_session_orchestration import (
    InterviewSessionOrchestrationSignals,
    OwnerTruthInterviewSessionOrchestrationService,
)


OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_SCHEMA_VERSION = "owner-truth-interview-decision-audit-v1"
OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_UI_SCHEMA_VERSION = "interview-decision-audit-v1"

_AUDIT_NAMESPACE = UUID("b6ea64c4-4395-42d8-bd5f-8f1df146c1d3")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_OPAQUE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


class OwnerTruthInterviewDecisionAuditError(OwnerTruthContractError):
    """A guided-interview decision audit record is malformed or unsafe."""


class OwnerTruthInterviewDecisionAuditAccessDenied(OwnerTruthInterviewDecisionAuditError):
    """The record is not bound to the active Owner's private conversation."""


class OwnerTruthInterviewDecisionAuditConflict(OwnerTruthInterviewDecisionAuditError):
    """A command or message was reused with different decision meaning."""


class OwnerTruthInterviewDecisionAuditStale(OwnerTruthInterviewDecisionAuditConflict):
    """The message/session authority changed before the audit was committed."""


class OwnerTruthInterviewDecisionAuditUnavailable(OwnerTruthInterviewDecisionAuditError):
    """The default-off audit lane has not been enabled for this caller."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise OwnerTruthInterviewDecisionAuditError(
            "interview decision audit payload must be JSON serializable"
        ) from exc


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _hash(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if _HASH_PATTERN.fullmatch(normalized) is None:
        raise OwnerTruthInterviewDecisionAuditError(f"{field} must be a SHA-256 digest")
    return normalized


def _positive_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OwnerTruthInterviewDecisionAuditError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OwnerTruthInterviewDecisionAuditError(f"{field} must be a non-negative integer")
    return value


def _opaque_identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if _OPAQUE_IDENTIFIER.fullmatch(normalized) is None:
        raise OwnerTruthInterviewDecisionAuditError(f"{field} must be an opaque identifier")
    return normalized


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthInterviewDecisionAuditAccessDenied("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthInterviewDecisionAuditAccessDenied(
            "only the Vault Owner may record an interview decision audit"
        )


@dataclass(frozen=True)
class OwnerTruthInterviewDecisionAuditCommand:
    """Idempotent audit request for one already-persisted Owner narrative."""

    command_id: str
    thread_id: str
    session_id: str
    message_id: str
    expected_session_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_nonblank(self.command_id, field="command_id"))
        for field in ("thread_id", "session_id", "message_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        _positive_int(self.expected_session_version, field="expected_session_version")

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)

    @property
    def request_payload_hash(self) -> str:
        return _sha256(
            _canonical_json(
                {
                    "schemaVersion": OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_SCHEMA_VERSION,
                    "threadId": self.thread_id,
                    "sessionId": self.session_id,
                    "messageId": self.message_id,
                    "expectedSessionVersion": self.expected_session_version,
                }
            )
        )


@dataclass(frozen=True)
class OwnerTruthInterviewDecisionAuditResult:
    """Safe summary of an immutable policy decision audit row."""

    outcome: str
    decision_id: str
    thread_id: str
    session_id: str
    message_id: str
    action: InterviewAction
    reason_code: str
    policy_version: str
    policy_schema_version: str
    authority_epoch: int
    session_version: int

    def __post_init__(self) -> None:
        if self.outcome not in {"created", "deduplicated"}:
            raise OwnerTruthInterviewDecisionAuditError("decision audit outcome is not supported")
        for field in ("decision_id", "thread_id", "session_id", "message_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        try:
            object.__setattr__(self, "action", InterviewAction(self.action))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthInterviewDecisionAuditError("decision audit action is not supported") from exc
        object.__setattr__(self, "reason_code", _opaque_identifier(self.reason_code, field="reason_code"))
        object.__setattr__(self, "policy_version", require_nonblank(self.policy_version, field="policy_version"))
        object.__setattr__(
            self,
            "policy_schema_version",
            require_nonblank(self.policy_schema_version, field="policy_schema_version"),
        )
        _nonnegative_int(self.authority_epoch, field="authority_epoch")
        _positive_int(self.session_version, field="session_version")

    def value_free_summary(self) -> dict[str, object]:
        return {
            "action": self.action.value,
            "authorityEpoch": self.authority_epoch,
            "outcome": self.outcome,
            "policySchemaVersion": self.policy_schema_version,
            "policyVersion": self.policy_version,
            "reasonCode": self.reason_code,
            "schemaVersion": OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_SCHEMA_VERSION,
            "sessionVersion": self.session_version,
        }


class OwnerTruthInterviewDecisionAuditRepository(Protocol):
    def find_by_command(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id_hash: str,
        request_payload_hash: str,
    ) -> OwnerTruthInterviewDecisionAuditResult | None:
        ...

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, object],
    ) -> OwnerTruthInterviewDecisionAuditResult:
        ...


class OwnerTruthInterviewDecisionAuditStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[object]:
        ...

    def owner_truth_conversation_repository(self) -> object:
        ...

    def owner_truth_interview_decision_audit_repository(
        self,
    ) -> OwnerTruthInterviewDecisionAuditRepository:
        ...


def _result_from_record(
    record: Mapping[str, object],
    *,
    outcome: str,
) -> OwnerTruthInterviewDecisionAuditResult:
    if not isinstance(record, Mapping):
        raise OwnerTruthInterviewDecisionAuditError("decision audit record is required")
    return OwnerTruthInterviewDecisionAuditResult(
        outcome=outcome,
        decision_id=str(record.get("decisionId") or ""),
        thread_id=str(record.get("threadId") or ""),
        session_id=str(record.get("sessionId") or ""),
        message_id=str(record.get("messageId") or ""),
        action=record.get("action"),
        reason_code=str(record.get("reasonCode") or ""),
        policy_version=str(record.get("policyVersion") or ""),
        policy_schema_version=str(record.get("policySchemaVersion") or ""),
        authority_epoch=_nonnegative_int(record.get("authorityEpoch"), field="authorityEpoch"),
        session_version=_positive_int(record.get("sessionVersion"), field="sessionVersion"),
    )


def _assert_record_context(
    record: Mapping[str, object],
    *,
    context: OwnerTruthCommandContext,
) -> None:
    _assert_owner_context(context)
    if (
        str(record.get("vaultId") or "") != context.vault_id
        or str(record.get("ownerSubjectId") or "") != context.owner_subject_id
        or str(record.get("actorSubjectId") or "") != context.owner_subject_id
    ):
        raise OwnerTruthInterviewDecisionAuditAccessDenied(
            "decision audit record does not match the active Owner context"
        )
    _hash(record.get("commandIdHash"), field="commandIdHash")
    _hash(record.get("requestPayloadHash"), field="requestPayloadHash")
    _hash(record.get("commandPayloadHash"), field="commandPayloadHash")
    schema_version = str(record.get("schemaVersion") or "")
    if schema_version != OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_SCHEMA_VERSION:
        raise OwnerTruthInterviewDecisionAuditError("decision audit schema version is not supported")
    ui_schema_version = str(record.get("uiSchemaVersion") or "")
    if ui_schema_version != OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_UI_SCHEMA_VERSION:
        raise OwnerTruthInterviewDecisionAuditError("decision audit UI schema version is not supported")
    _result_from_record(record, outcome="created")


class InMemoryOwnerTruthInterviewDecisionAuditRepository:
    """Thread-safe semantic double for private append-only decision audits."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records_by_command: dict[tuple[str, str], dict[str, object]] = {}
        self._records_by_message: dict[tuple[str, str], dict[str, object]] = {}

    def find_by_command(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id_hash: str,
        request_payload_hash: str,
    ) -> OwnerTruthInterviewDecisionAuditResult | None:
        _assert_owner_context(context)
        command_hash = _hash(command_id_hash, field="commandIdHash")
        request_hash = _hash(request_payload_hash, field="requestPayloadHash")
        with self._lock:
            existing = self._records_by_command.get((context.vault_id, command_hash))
            if existing is None:
                return None
            if str(existing.get("requestPayloadHash") or "") != request_hash:
                raise OwnerTruthInterviewDecisionAuditConflict(
                    "commandId cannot be reused with different decision audit target"
                )
            return _result_from_record(existing, outcome="deduplicated")

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, object],
    ) -> OwnerTruthInterviewDecisionAuditResult:
        _assert_record_context(record, context=context)
        normalized = deepcopy(dict(record))
        command_hash = str(normalized["commandIdHash"])
        message_id = str(normalized["messageId"])
        with self._lock:
            existing = self._records_by_command.get((context.vault_id, command_hash))
            if existing is not None:
                if str(existing.get("requestPayloadHash") or "") != str(
                    normalized.get("requestPayloadHash") or ""
                ):
                    raise OwnerTruthInterviewDecisionAuditConflict(
                        "commandId cannot be reused with different decision audit target"
                    )
                return _result_from_record(existing, outcome="deduplicated")
            existing_message = self._records_by_message.get((context.vault_id, message_id))
            if existing_message is not None:
                if str(existing_message.get("commandPayloadHash") or "") != str(
                    normalized.get("commandPayloadHash") or ""
                ):
                    raise OwnerTruthInterviewDecisionAuditConflict(
                        "one interview message cannot have multiple decision audit meanings"
                    )
                return _result_from_record(existing_message, outcome="deduplicated")
            self._records_by_command[(context.vault_id, command_hash)] = normalized
            self._records_by_message[(context.vault_id, message_id)] = normalized
            return _result_from_record(normalized, outcome="created")


class PostgresOwnerTruthInterviewDecisionAuditRepository:
    """Postgres persistence for append-only, owner-scoped policy audits."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def find_by_command(
        self,
        *,
        context: OwnerTruthCommandContext,
        command_id_hash: str,
        request_payload_hash: str,
    ) -> OwnerTruthInterviewDecisionAuditResult | None:
        _assert_owner_context(context)
        command_hash = _hash(command_id_hash, field="commandIdHash")
        request_hash = _hash(request_payload_hash, field="requestPayloadHash")
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT id, vault_id, thread_id, session_id, message_id, action,
                    reason_code, policy_version, policy_schema_version,
                    authority_epoch, session_version, request_payload_hash
                FROM owner_truth.interview_decisions
                WHERE vault_id = %s AND command_id_hash = %s
                FOR SHARE
                """,
                (context.vault_id, command_hash),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        if str(row["request_payload_hash"]) != request_hash:
            raise OwnerTruthInterviewDecisionAuditConflict(
                "commandId cannot be reused with different decision audit target"
            )
        return _result_from_record(self._row_to_record(row), outcome="deduplicated")

    def record(
        self,
        *,
        context: OwnerTruthCommandContext,
        record: Mapping[str, object],
    ) -> OwnerTruthInterviewDecisionAuditResult:
        _assert_record_context(record, context=context)
        normalized = deepcopy(dict(record))
        command_hash = str(normalized["commandIdHash"])
        message_id = str(normalized["messageId"])
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-interview-decision-command:{context.vault_id}:{command_hash}",),
            )
            cursor.fetchone()
            existing = self._find_row_by_command(
                cursor,
                vault_id=context.vault_id,
                command_id_hash=command_hash,
            )
            if existing is not None:
                if str(existing["request_payload_hash"]) != str(
                    normalized["requestPayloadHash"]
                ):
                    raise OwnerTruthInterviewDecisionAuditConflict(
                        "commandId cannot be reused with different decision audit target"
                    )
                return _result_from_record(self._row_to_record(existing), outcome="deduplicated")

            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
                (f"owner-truth-interview-decision-message:{context.vault_id}:{message_id}",),
            )
            cursor.fetchone()
            existing_message = self._find_row_by_message(
                cursor,
                vault_id=context.vault_id,
                message_id=message_id,
            )
            if existing_message is not None:
                if str(existing_message["command_payload_hash"]) != str(
                    normalized["commandPayloadHash"]
                ):
                    raise OwnerTruthInterviewDecisionAuditConflict(
                        "one interview message cannot have multiple decision audit meanings"
                    )
                return _result_from_record(self._row_to_record(existing_message), outcome="deduplicated")

            cursor.execute(
                """
                INSERT INTO owner_truth.interview_decisions (
                    id, vault_id, owner_subject_id, actor_subject_id, authority_epoch,
                    thread_id, session_id, message_id, session_version,
                    action, reason_code, policy_version, policy_schema_version,
                    target_dimension, missing_facet,
                    command_id_hash, request_payload_hash, command_payload_hash,
                    schema_version, ui_schema_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                (
                    normalized["decisionId"],
                    normalized["vaultId"],
                    normalized["ownerSubjectId"],
                    normalized["actorSubjectId"],
                    normalized["authorityEpoch"],
                    normalized["threadId"],
                    normalized["sessionId"],
                    normalized["messageId"],
                    normalized["sessionVersion"],
                    normalized["action"],
                    normalized["reasonCode"],
                    normalized["policyVersion"],
                    normalized["policySchemaVersion"],
                    normalized.get("targetDimension"),
                    normalized.get("missingFacet"),
                    normalized["commandIdHash"],
                    normalized["requestPayloadHash"],
                    normalized["commandPayloadHash"],
                    normalized["schemaVersion"],
                    normalized["uiSchemaVersion"],
                ),
            )
        return _result_from_record(normalized, outcome="created")

    @staticmethod
    def _row_to_record(row: Mapping[str, object]) -> dict[str, object]:
        return {
            "decisionId": str(row["id"]),
            "vaultId": str(row["vault_id"]),
            "threadId": str(row["thread_id"]),
            "sessionId": str(row["session_id"]),
            "messageId": str(row["message_id"]),
            "action": str(row["action"]),
            "reasonCode": str(row["reason_code"]),
            "policyVersion": str(row["policy_version"]),
            "policySchemaVersion": str(row["policy_schema_version"]),
            "authorityEpoch": int(row["authority_epoch"]),
            "sessionVersion": int(row["session_version"]),
        }

    @staticmethod
    def _find_row_by_command(
        cursor: Any,
        *,
        vault_id: str,
        command_id_hash: str,
    ) -> Mapping[str, object] | None:
        cursor.execute(
            """
            SELECT id, vault_id, thread_id, session_id, message_id, action,
                reason_code, policy_version, policy_schema_version,
                authority_epoch, session_version, request_payload_hash, command_payload_hash
            FROM owner_truth.interview_decisions
            WHERE vault_id = %s AND command_id_hash = %s
            FOR UPDATE
            """,
            (vault_id, command_id_hash),
        )
        return cursor.fetchone()

    @staticmethod
    def _find_row_by_message(
        cursor: Any,
        *,
        vault_id: str,
        message_id: str,
    ) -> Mapping[str, object] | None:
        cursor.execute(
            """
            SELECT id, vault_id, thread_id, session_id, message_id, action,
                reason_code, policy_version, policy_schema_version,
                authority_epoch, session_version, request_payload_hash, command_payload_hash
            FROM owner_truth.interview_decisions
            WHERE vault_id = %s AND message_id = %s
            FOR UPDATE
            """,
            (vault_id, message_id),
        )
        return cursor.fetchone()

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


class OwnerTruthInterviewDecisionAuditService:
    """Default-off explicit writer for value-free policy audit records."""

    def __init__(
        self,
        store: OwnerTruthInterviewDecisionAuditStore,
        *,
        enabled: bool = False,
    ) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def decide_and_record(
        self,
        *,
        command: OwnerTruthInterviewDecisionAuditCommand,
        context: OwnerTruthCommandContext,
        signals: InterviewSessionOrchestrationSignals,
    ) -> OwnerTruthInterviewDecisionAuditResult:
        _assert_owner_context(context)
        if not self._enabled:
            raise OwnerTruthInterviewDecisionAuditUnavailable(
                "owner truth interview decision audit is unavailable"
            )
        with self._unit_of_work(
            correlation_id=f"owner-truth-interview-decision-audit-{command.message_id}",
            command_id=command.command_id_hash,
        ):
            repository = self._store.owner_truth_interview_decision_audit_repository()
            replay = repository.find_by_command(
                context=context,
                command_id_hash=command.command_id_hash,
                request_payload_hash=command.request_payload_hash,
            )
            if replay is not None:
                return replay

            conversation = OwnerTruthConversationService(
                self._store.owner_truth_conversation_repository()
            )
            orchestration = OwnerTruthInterviewSessionOrchestrationService(
                conversation_service=conversation
            ).decide(
                session_id=command.session_id,
                context=context,
                signals=signals,
            )
            session = orchestration.persisted_session
            if (
                session.thread_id != command.thread_id
                or session.row_version != command.expected_session_version
            ):
                raise OwnerTruthInterviewDecisionAuditStale(
                    "decision audit must bind the current interview session version"
                )
            message = conversation.read_message_authority(
                message_id=command.message_id,
                context=context,
            )
            if (
                message.thread_id != session.thread_id
                or message.session_id != session.session_id
                or message.owner_subject_id != context.owner_subject_id
                or message.authority_epoch != session.authority_epoch
                or message.author is not ConversationMessageAuthor.OWNER
                or message.kind is not ConversationMessageKind.NARRATIVE
            ):
                raise OwnerTruthInterviewDecisionAuditAccessDenied(
                    "decision audit must bind one current Owner narrative"
                )
            record = self._record(
                command=command,
                context=context,
                action=orchestration.decision.action,
                reason_code=orchestration.decision.reason_code,
                authority_epoch=session.authority_epoch,
                session_version=session.row_version,
            )
            return repository.record(context=context, record=record)

    @staticmethod
    def _record(
        *,
        command: OwnerTruthInterviewDecisionAuditCommand,
        context: OwnerTruthCommandContext,
        action: InterviewAction,
        reason_code: str,
        authority_epoch: int,
        session_version: int,
    ) -> dict[str, object]:
        _nonnegative_int(authority_epoch, field="authority_epoch")
        _positive_int(session_version, field="session_version")
        decision_id = str(
            uuid5(
                _AUDIT_NAMESPACE,
                f"interview-decision:{context.vault_id}:{command.session_id}:{command.message_id}",
            )
        )
        payload = {
            "schemaVersion": OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_SCHEMA_VERSION,
            "threadId": command.thread_id,
            "sessionId": command.session_id,
            "messageId": command.message_id,
            "expectedSessionVersion": command.expected_session_version,
            "action": InterviewAction(action).value,
            "reasonCode": _opaque_identifier(reason_code, field="reason_code"),
            "policyVersion": context.policy_version,
            "policySchemaVersion": INTERVIEW_ORCHESTRATION_SCHEMA_VERSION,
            "authorityEpoch": authority_epoch,
            "sessionVersion": session_version,
            "uiSchemaVersion": OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_UI_SCHEMA_VERSION,
        }
        return {
            "decisionId": decision_id,
            "vaultId": context.vault_id,
            "ownerSubjectId": context.owner_subject_id,
            "actorSubjectId": context.owner_subject_id,
            "authorityEpoch": authority_epoch,
            "threadId": command.thread_id,
            "sessionId": command.session_id,
            "messageId": command.message_id,
            "sessionVersion": session_version,
            "action": InterviewAction(action).value,
            "reasonCode": payload["reasonCode"],
            "policyVersion": context.policy_version,
            "policySchemaVersion": INTERVIEW_ORCHESTRATION_SCHEMA_VERSION,
            "targetDimension": None,
            "missingFacet": None,
            "commandIdHash": command.command_id_hash,
            "requestPayloadHash": command.request_payload_hash,
            "commandPayloadHash": _sha256(_canonical_json(payload)),
            "schemaVersion": OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_SCHEMA_VERSION,
            "uiSchemaVersion": OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_UI_SCHEMA_VERSION,
        }

    def _unit_of_work(self, *, correlation_id: str, command_id: str) -> ContextManager[object]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


__all__ = [
    "InMemoryOwnerTruthInterviewDecisionAuditRepository",
    "OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_SCHEMA_VERSION",
    "OWNER_TRUTH_INTERVIEW_DECISION_AUDIT_UI_SCHEMA_VERSION",
    "OwnerTruthInterviewDecisionAuditAccessDenied",
    "OwnerTruthInterviewDecisionAuditCommand",
    "OwnerTruthInterviewDecisionAuditConflict",
    "OwnerTruthInterviewDecisionAuditError",
    "OwnerTruthInterviewDecisionAuditRepository",
    "OwnerTruthInterviewDecisionAuditResult",
    "OwnerTruthInterviewDecisionAuditService",
    "OwnerTruthInterviewDecisionAuditStale",
    "OwnerTruthInterviewDecisionAuditStore",
    "OwnerTruthInterviewDecisionAuditUnavailable",
    "PostgresOwnerTruthInterviewDecisionAuditRepository",
]
