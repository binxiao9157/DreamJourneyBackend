"""Private Owner Truth conversation persistence for the M0-A bootstrap.

The service is intentionally route-free and provider-free. It persists a
conversation thread, interview session, and append-only messages, but never
creates Sources, Candidates, DecisionReceipts, or MemoryVersions on its own.
"""

from __future__ import annotations

from copy import deepcopy
import json
from threading import RLock
from typing import Any, Mapping, Protocol

from app.domain.owner_truth.conversation import (
    AppendInterviewMessageCommand,
    AppendInterviewMessageWriteRecord,
    InterviewBoundary,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationConflict,
    OwnerTruthConversationVersionConflict,
    OwnerTruthInterviewSessionResult,
    OwnerTruthInterviewSessionSnapshot,
    OwnerTruthInterviewSessionStateConflict,
    SetInterviewBoundaryCommand,
    SetInterviewBoundaryWriteRecord,
    StartInterviewSessionCommand,
    StartInterviewSessionWriteRecord,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


class OwnerTruthConversationRepository(Protocol):
    def start_interview_session(
        self,
        record: StartInterviewSessionWriteRecord,
    ) -> OwnerTruthInterviewSessionResult:
        ...

    def append_interview_message(
        self,
        record: AppendInterviewMessageWriteRecord,
    ) -> OwnerTruthInterviewSessionResult:
        ...

    def set_interview_boundary(
        self,
        record: SetInterviewBoundaryWriteRecord,
    ) -> OwnerTruthInterviewSessionResult:
        ...

    def get_interview_session(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewSessionSnapshot:
        ...


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthConversationAccessDenied("owner truth command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthConversationAccessDenied(
            "only the Vault Owner may mutate a guided interview session"
        )


class OwnerTruthConversationService:
    """Applies typed M0-A commands through an isolated persistence port."""

    def __init__(self, repository: OwnerTruthConversationRepository):
        self._repository = repository

    def start_session(
        self,
        *,
        command: StartInterviewSessionCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewSessionResult:
        _assert_owner_context(context)
        return self._repository.start_interview_session(command.write_record(context=context))

    def append_message(
        self,
        *,
        command: AppendInterviewMessageCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewSessionResult:
        _assert_owner_context(context)
        return self._repository.append_interview_message(command.write_record(context=context))

    def set_boundary(
        self,
        *,
        command: SetInterviewBoundaryCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewSessionResult:
        _assert_owner_context(context)
        return self._repository.set_interview_boundary(command.write_record(context=context))

    def read_session(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewSessionSnapshot:
        _assert_owner_context(context)
        return self._repository.get_interview_session(session_id=session_id, context=context)


class InMemoryOwnerTruthConversationRepository:
    """Thread-safe semantic double for the M0-A conversation contracts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._vaults: dict[str, dict[str, Any]] = {}
        self._threads: dict[tuple[str, str], dict[str, Any]] = {}
        self._sessions: dict[tuple[str, str], dict[str, Any]] = {}
        self._messages: dict[tuple[str, str], dict[str, Any]] = {}
        self._receipts: dict[tuple[str, str], dict[str, Any]] = {}

    def start_interview_session(
        self,
        record: StartInterviewSessionWriteRecord,
    ) -> OwnerTruthInterviewSessionResult:
        with self._lock:
            vault = self._ensure_active_vault(
                vault_id=record.vault_id,
                owner_subject_id=record.owner_subject_id,
            )
            existing = self._existing_receipt(record)
            if existing is not None:
                return self._replay_result(existing, record=record)
            if record.expected_thread_version != 0:
                raise OwnerTruthConversationVersionConflict(
                    resource="thread",
                    expected_version=record.expected_thread_version,
                    current_version=0,
                )
            thread_key = (record.vault_id, record.thread_id)
            session_key = (record.vault_id, record.session_id)
            if thread_key in self._threads or session_key in self._sessions:
                raise OwnerTruthConversationConflict(
                    "threadId or sessionId already exists without this command receipt"
                )
            if any(
                item["state"] is InterviewSessionState.ACTIVE
                for (vault_id, _), item in self._sessions.items()
                if vault_id == record.vault_id
            ):
                raise OwnerTruthInterviewSessionStateConflict(
                    "only one active interview session is allowed in a Vault"
                )

            authority_epoch = int(vault["authorityEpoch"])
            self._threads[thread_key] = {
                "id": record.thread_id,
                "vaultId": record.vault_id,
                "ownerSubjectId": record.owner_subject_id,
                "authorityEpoch": authority_epoch,
                "rowVersion": 1,
                "state": "active",
                "entryMode": record.entry_mode,
            }
            self._sessions[session_key] = {
                "id": record.session_id,
                "vaultId": record.vault_id,
                "ownerSubjectId": record.owner_subject_id,
                "authorityEpoch": authority_epoch,
                "threadId": record.thread_id,
                "rowVersion": 1,
                "state": InterviewSessionState.ACTIVE,
                "boundary": InterviewBoundary.OPEN,
                "turnCount": 0,
            }
            result = OwnerTruthInterviewSessionResult(
                outcome="created",
                receipt_id=record.receipt_id,
                thread_id=record.thread_id,
                session_id=record.session_id,
                thread_version=1,
                session_version=1,
                state=InterviewSessionState.ACTIVE,
                boundary=InterviewBoundary.OPEN,
            )
            self._store_receipt(record, result)
            return result

    def append_interview_message(
        self,
        record: AppendInterviewMessageWriteRecord,
    ) -> OwnerTruthInterviewSessionResult:
        with self._lock:
            self._ensure_active_vault(
                vault_id=record.vault_id,
                owner_subject_id=record.owner_subject_id,
            )
            existing = self._existing_receipt(record)
            if existing is not None:
                return self._replay_result(existing, record=record)
            session, thread = self._live_session_and_thread(
                vault_id=record.vault_id,
                session_id=record.session_id,
                thread_id=record.thread_id,
                owner_subject_id=record.owner_subject_id,
            )
            if session["state"] is not InterviewSessionState.ACTIVE:
                raise OwnerTruthInterviewSessionStateConflict(
                    "interview session is not active for a new message"
                )
            self._assert_version(
                resource="thread",
                expected=record.expected_thread_version,
                current=int(thread["rowVersion"]),
            )
            self._assert_version(
                resource="interview session",
                expected=record.expected_session_version,
                current=int(session["rowVersion"]),
            )
            message_key = (record.vault_id, record.message_id)
            if message_key in self._messages:
                raise OwnerTruthConversationConflict(
                    "messageId already exists without this command receipt"
                )
            sequence = 1 + sum(
                1
                for message in self._messages.values()
                if message["vaultId"] == record.vault_id and message["threadId"] == record.thread_id
            )
            self._messages[message_key] = {
                "id": record.message_id,
                "vaultId": record.vault_id,
                "ownerSubjectId": record.owner_subject_id,
                "authorityEpoch": int(session["authorityEpoch"]),
                "threadId": record.thread_id,
                "sessionId": record.session_id,
                "sequence": sequence,
                "author": record.author,
                "kind": record.kind,
                "contentHash": record.content_hash,
                "contentPayload": deepcopy(dict(record.content_payload)),
            }
            thread["rowVersion"] += 1
            session["rowVersion"] += 1
            if record.author.value == "owner":
                session["turnCount"] += 1
            result = OwnerTruthInterviewSessionResult(
                outcome="created",
                receipt_id=record.receipt_id,
                thread_id=record.thread_id,
                session_id=record.session_id,
                thread_version=int(thread["rowVersion"]),
                session_version=int(session["rowVersion"]),
                state=session["state"],
                boundary=session["boundary"],
                message_id=record.message_id,
                message_sequence=sequence,
            )
            self._store_receipt(record, result)
            return result

    def set_interview_boundary(
        self,
        record: SetInterviewBoundaryWriteRecord,
    ) -> OwnerTruthInterviewSessionResult:
        with self._lock:
            self._ensure_active_vault(
                vault_id=record.vault_id,
                owner_subject_id=record.owner_subject_id,
            )
            existing = self._existing_receipt(record)
            if existing is not None:
                return self._replay_result(existing, record=record)
            session, thread = self._live_session_and_thread(
                vault_id=record.vault_id,
                session_id=record.session_id,
                thread_id=record.thread_id,
                owner_subject_id=record.owner_subject_id,
            )
            self._assert_version(
                resource="interview session",
                expected=record.expected_session_version,
                current=int(session["rowVersion"]),
            )
            if session["state"] is InterviewSessionState.ENDED:
                raise OwnerTruthInterviewSessionStateConflict("ended interview session cannot change boundary")
            session["boundary"] = record.boundary
            session["state"] = record.state
            session["rowVersion"] += 1
            result = OwnerTruthInterviewSessionResult(
                outcome="created",
                receipt_id=record.receipt_id,
                thread_id=record.thread_id,
                session_id=record.session_id,
                thread_version=int(thread["rowVersion"]),
                session_version=int(session["rowVersion"]),
                state=session["state"],
                boundary=session["boundary"],
            )
            self._store_receipt(record, result)
            return result

    def get_interview_session(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewSessionSnapshot:
        _assert_owner_context(context)
        with self._lock:
            vault = self._ensure_active_vault(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
            )
            session = self._sessions.get((context.vault_id, session_id))
            if session is None or session["ownerSubjectId"] != context.owner_subject_id:
                raise OwnerTruthConversationAccessDenied(
                    "interview session does not belong to this active Owner Vault"
                )
            thread = self._threads.get((context.vault_id, session["threadId"]))
            if thread is None:
                raise OwnerTruthConversationConflict("interview session points to a missing thread")
            return OwnerTruthInterviewSessionSnapshot(
                session_id=str(session["id"]),
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                thread_id=str(session["threadId"]),
                state=session["state"],
                boundary=session["boundary"],
                row_version=int(session["rowVersion"]),
                thread_version=int(thread["rowVersion"]),
                turn_count=int(session["turnCount"]),
                authority_epoch=int(vault["authorityEpoch"]),
            )

    def snapshot(self, *, vault_id: str) -> Mapping[str, Any]:
        """Test-only snapshot. It deliberately reports no authority promotion."""

        with self._lock:
            messages = [
                {
                    "id": item["id"],
                    "threadId": item["threadId"],
                    "sessionId": item["sessionId"],
                    "sequence": item["sequence"],
                    "author": item["author"].value,
                    "kind": item["kind"].value,
                    "text": str(item["contentPayload"].get("text") or ""),
                }
                for item in self._messages.values()
                if item["vaultId"] == vault_id
            ]
            messages.sort(key=lambda item: (item["threadId"], item["sequence"], item["id"]))
            return {
                "threads": [
                    deepcopy(item)
                    for (stored_vault_id, _), item in self._threads.items()
                    if stored_vault_id == vault_id
                ],
                "sessions": [
                    {
                        **deepcopy(item),
                        "state": item["state"].value,
                        "boundary": item["boundary"].value,
                    }
                    for (stored_vault_id, _), item in self._sessions.items()
                    if stored_vault_id == vault_id
                ],
                "messages": messages,
                "candidateCount": 0,
                "memoryVersionCount": 0,
                "authorityEffects": (),
            }

    def _ensure_active_vault(self, *, vault_id: str, owner_subject_id: str) -> Mapping[str, Any]:
        vault = self._vaults.get(vault_id)
        if vault is None:
            vault = {
                "ownerSubjectId": owner_subject_id,
                "authorityEpoch": 0,
                "status": "active",
            }
            self._vaults[vault_id] = vault
        if vault["ownerSubjectId"] != owner_subject_id or vault["status"] != "active":
            raise OwnerTruthConversationAccessDenied("Vault is not active for this Owner")
        return vault

    def _live_session_and_thread(
        self,
        *,
        vault_id: str,
        session_id: str,
        thread_id: str,
        owner_subject_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        session = self._sessions.get((vault_id, session_id))
        thread = self._threads.get((vault_id, thread_id))
        if (
            session is None
            or thread is None
            or session["ownerSubjectId"] != owner_subject_id
            or thread["ownerSubjectId"] != owner_subject_id
            or session["threadId"] != thread_id
        ):
            raise OwnerTruthConversationAccessDenied(
                "interview session or thread does not belong to this active Owner Vault"
            )
        return session, thread

    def _existing_receipt(self, record: Any) -> Mapping[str, Any] | None:
        existing = self._receipts.get((record.vault_id, record.command_id_hash))
        if existing is None:
            return None
        expected = {
            "payloadHash": record.payload_hash,
            "actorSubjectId": record.actor_subject_id,
            "ownerSubjectId": record.owner_subject_id,
            "policyVersion": record.policy_version,
        }
        if any(existing.get(key) != value for key, value in expected.items()):
            raise OwnerTruthConversationConflict(
                "commandId cannot be reused with a different conversation command"
            )
        return existing

    @staticmethod
    def _assert_version(*, resource: str, expected: int, current: int) -> None:
        if expected != current:
            raise OwnerTruthConversationVersionConflict(
                resource=resource,
                expected_version=expected,
                current_version=current,
            )

    def _store_receipt(self, record: Any, result: OwnerTruthInterviewSessionResult) -> None:
        self._receipts[(record.vault_id, record.command_id_hash)] = {
            "payloadHash": record.payload_hash,
            "actorSubjectId": record.actor_subject_id,
            "ownerSubjectId": record.owner_subject_id,
            "policyVersion": record.policy_version,
            "result": result,
        }

    @staticmethod
    def _replay_result(
        existing: Mapping[str, Any],
        *,
        record: Any,
    ) -> OwnerTruthInterviewSessionResult:
        result = existing.get("result")
        if not isinstance(result, OwnerTruthInterviewSessionResult):
            raise OwnerTruthConversationConflict("conversation command receipt has no result")
        if result.thread_id != record.thread_id or result.session_id != record.session_id:
            raise OwnerTruthConversationConflict("conversation command receipt target does not match command")
        return OwnerTruthInterviewSessionResult(
            outcome="deduplicated",
            receipt_id=result.receipt_id,
            thread_id=result.thread_id,
            session_id=result.session_id,
            thread_version=result.thread_version,
            session_version=result.session_version,
            state=result.state,
            boundary=result.boundary,
            message_id=result.message_id,
            message_sequence=result.message_sequence,
            authority_effects=result.authority_effects,
        )


class PostgresOwnerTruthConversationRepository:
    """Persist M0-A conversation commands in one active Postgres UoW.

    The repository owns only the private conversation tables introduced by
    migration 0029. It has no dependency on Source/Candidate/Memory writers.
    """

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def start_interview_session(
        self,
        record: StartInterviewSessionWriteRecord,
    ) -> OwnerTruthInterviewSessionResult:
        with self._cursor() as cursor:
            self._lock(cursor, f"owner-truth-conversation-command:{record.vault_id}:{record.command_id_hash}")
            self._lock(cursor, f"owner-truth-conversation-vault:{record.vault_id}")
            vault = self._ensure_active_vault(cursor, record=record)
            existing = self._receipt_by_command(
                cursor,
                vault_id=record.vault_id,
                command_id_hash=record.command_id_hash,
            )
            if existing is not None:
                return self._deduplicated_result(cursor, existing=existing, record=record)
            if record.expected_thread_version != 0:
                raise OwnerTruthConversationVersionConflict(
                    resource="thread",
                    expected_version=record.expected_thread_version,
                    current_version=0,
                )
            self._assert_thread_absent(cursor, record=record)
            self._assert_session_absent(cursor, record=record)
            cursor.execute(
                """
                INSERT INTO owner_truth.conversation_threads (
                    id, vault_id, owner_subject_id, state, entry_mode,
                    policy_version, authority_epoch, metadata
                ) VALUES (%s, %s, %s, 'active', %s, %s, %s, '{}'::jsonb)
                RETURNING row_version
                """,
                (
                    record.thread_id,
                    record.vault_id,
                    record.owner_subject_id,
                    record.entry_mode,
                    record.policy_version,
                    int(vault["authority_epoch"]),
                ),
            )
            thread = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO owner_truth.interview_sessions (
                    id, vault_id, owner_subject_id, current_thread_id, state,
                    boundary, turn_count, policy_version, authority_epoch, metadata
                ) VALUES (%s, %s, %s, %s, 'active', 'open', 0, %s, %s, '{}'::jsonb)
                RETURNING row_version, state, boundary
                """,
                (
                    record.session_id,
                    record.vault_id,
                    record.owner_subject_id,
                    record.thread_id,
                    record.policy_version,
                    int(vault["authority_epoch"]),
                ),
            )
            session = cursor.fetchone()
            self._insert_receipt(
                cursor,
                record=record,
                authority_epoch=int(vault["authority_epoch"]),
                result_message_id=None,
                expected_thread_version=record.expected_thread_version,
                expected_session_version=None,
            )
        return OwnerTruthInterviewSessionResult(
            outcome="created",
            receipt_id=record.receipt_id,
            thread_id=record.thread_id,
            session_id=record.session_id,
            thread_version=int(thread["row_version"]),
            session_version=int(session["row_version"]),
            state=InterviewSessionState(str(session["state"])),
            boundary=InterviewBoundary(str(session["boundary"])),
        )

    def append_interview_message(
        self,
        record: AppendInterviewMessageWriteRecord,
    ) -> OwnerTruthInterviewSessionResult:
        with self._cursor() as cursor:
            self._lock(cursor, f"owner-truth-conversation-command:{record.vault_id}:{record.command_id_hash}")
            self._lock(cursor, f"owner-truth-conversation-session:{record.vault_id}:{record.session_id}")
            vault = self._active_vault(
                cursor,
                vault_id=record.vault_id,
                owner_subject_id=record.owner_subject_id,
                lock=True,
            )
            existing = self._receipt_by_command(
                cursor,
                vault_id=record.vault_id,
                command_id_hash=record.command_id_hash,
            )
            if existing is not None:
                return self._deduplicated_result(cursor, existing=existing, record=record)
            session, thread = self._locked_session_and_thread(cursor, record=record)
            self._assert_live_session(
                session=session,
                thread=thread,
                record=record,
                authority_epoch=int(vault["authority_epoch"]),
            )
            if str(session["state"]) != InterviewSessionState.ACTIVE.value:
                raise OwnerTruthInterviewSessionStateConflict(
                    "interview session is not active for a new message"
                )
            self._assert_version(
                resource="thread",
                expected=record.expected_thread_version,
                current=int(thread["row_version"]),
            )
            self._assert_version(
                resource="interview session",
                expected=record.expected_session_version,
                current=int(session["row_version"]),
            )
            self._assert_message_absent(cursor, record=record)
            cursor.execute(
                """
                SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_sequence
                FROM owner_truth.conversation_messages
                WHERE vault_id = %s AND thread_id = %s
                """,
                (record.vault_id, record.thread_id),
            )
            sequence = int(cursor.fetchone()["next_sequence"])
            cursor.execute(
                """
                UPDATE owner_truth.conversation_threads
                SET updated_at = NOW()
                WHERE vault_id = %s AND id = %s AND row_version = %s
                RETURNING row_version
                """,
                (record.vault_id, record.thread_id, record.expected_thread_version),
            )
            updated_thread = cursor.fetchone()
            if updated_thread is None:
                raise OwnerTruthConversationVersionConflict(
                    resource="thread",
                    expected_version=record.expected_thread_version,
                    current_version=int(thread["row_version"]),
                )
            cursor.execute(
                """
                UPDATE owner_truth.interview_sessions
                SET turn_count = turn_count + %s, updated_at = NOW()
                WHERE vault_id = %s AND id = %s AND row_version = %s
                RETURNING row_version, state, boundary
                """,
                (
                    1 if record.author.value == "owner" else 0,
                    record.vault_id,
                    record.session_id,
                    record.expected_session_version,
                ),
            )
            updated_session = cursor.fetchone()
            if updated_session is None:
                raise OwnerTruthConversationVersionConflict(
                    resource="interview session",
                    expected_version=record.expected_session_version,
                    current_version=int(session["row_version"]),
                )
            cursor.execute(
                """
                INSERT INTO owner_truth.conversation_messages (
                    id, vault_id, owner_subject_id, thread_id, session_id,
                    sequence_number, author, kind, content_schema_version,
                    content_hash, content_payload, authority_epoch
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                self._adapt_params(
                    (
                        record.message_id,
                        record.vault_id,
                        record.owner_subject_id,
                        record.thread_id,
                        record.session_id,
                        sequence,
                        record.author.value,
                        record.kind.value,
                        "owner-truth-conversation-content-v1",
                        record.content_hash,
                        dict(record.content_payload),
                        int(vault["authority_epoch"]),
                    )
                ),
            )
            self._insert_receipt(
                cursor,
                record=record,
                authority_epoch=int(vault["authority_epoch"]),
                result_message_id=record.message_id,
                expected_thread_version=record.expected_thread_version,
                expected_session_version=record.expected_session_version,
            )
        return OwnerTruthInterviewSessionResult(
            outcome="created",
            receipt_id=record.receipt_id,
            thread_id=record.thread_id,
            session_id=record.session_id,
            thread_version=int(updated_thread["row_version"]),
            session_version=int(updated_session["row_version"]),
            state=InterviewSessionState(str(updated_session["state"])),
            boundary=InterviewBoundary(str(updated_session["boundary"])),
            message_id=record.message_id,
            message_sequence=sequence,
        )

    def set_interview_boundary(
        self,
        record: SetInterviewBoundaryWriteRecord,
    ) -> OwnerTruthInterviewSessionResult:
        with self._cursor() as cursor:
            self._lock(cursor, f"owner-truth-conversation-command:{record.vault_id}:{record.command_id_hash}")
            self._lock(cursor, f"owner-truth-conversation-session:{record.vault_id}:{record.session_id}")
            vault = self._active_vault(
                cursor,
                vault_id=record.vault_id,
                owner_subject_id=record.owner_subject_id,
                lock=True,
            )
            existing = self._receipt_by_command(
                cursor,
                vault_id=record.vault_id,
                command_id_hash=record.command_id_hash,
            )
            if existing is not None:
                return self._deduplicated_result(cursor, existing=existing, record=record)
            session, thread = self._locked_session_and_thread(cursor, record=record)
            self._assert_live_session(
                session=session,
                thread=thread,
                record=record,
                authority_epoch=int(vault["authority_epoch"]),
            )
            self._assert_version(
                resource="interview session",
                expected=record.expected_session_version,
                current=int(session["row_version"]),
            )
            if str(session["state"]) == InterviewSessionState.ENDED.value:
                raise OwnerTruthInterviewSessionStateConflict(
                    "ended interview session cannot change boundary"
                )
            cursor.execute(
                """
                UPDATE owner_truth.interview_sessions
                SET boundary = %s, state = %s, updated_at = NOW()
                WHERE vault_id = %s AND id = %s AND row_version = %s
                RETURNING row_version, state, boundary
                """,
                (
                    record.boundary.value,
                    record.state.value,
                    record.vault_id,
                    record.session_id,
                    record.expected_session_version,
                ),
            )
            updated_session = cursor.fetchone()
            if updated_session is None:
                raise OwnerTruthConversationVersionConflict(
                    resource="interview session",
                    expected_version=record.expected_session_version,
                    current_version=int(session["row_version"]),
                )
            self._insert_receipt(
                cursor,
                record=record,
                authority_epoch=int(vault["authority_epoch"]),
                result_message_id=None,
                expected_thread_version=None,
                expected_session_version=record.expected_session_version,
            )
        return OwnerTruthInterviewSessionResult(
            outcome="created",
            receipt_id=record.receipt_id,
            thread_id=record.thread_id,
            session_id=record.session_id,
            thread_version=int(thread["row_version"]),
            session_version=int(updated_session["row_version"]),
            state=InterviewSessionState(str(updated_session["state"])),
            boundary=InterviewBoundary(str(updated_session["boundary"])),
        )

    def get_interview_session(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewSessionSnapshot:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            vault = self._active_vault(
                cursor,
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                lock=False,
            )
            cursor.execute(
                """
                SELECT s.id, s.vault_id, s.owner_subject_id, s.current_thread_id,
                    s.state, s.boundary, s.row_version, s.turn_count,
                    s.authority_epoch, t.row_version AS thread_row_version
                FROM owner_truth.interview_sessions AS s
                JOIN owner_truth.conversation_threads AS t
                  ON t.vault_id = s.vault_id AND t.id = s.current_thread_id
                WHERE s.vault_id = %s
                  AND s.id = %s
                  AND s.owner_subject_id = %s
                  AND s.authority_epoch = %s
                  AND t.owner_subject_id = %s
                  AND t.authority_epoch = %s
                """,
                (
                    context.vault_id,
                    session_id,
                    context.owner_subject_id,
                    int(vault["authority_epoch"]),
                    context.owner_subject_id,
                    int(vault["authority_epoch"]),
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise OwnerTruthConversationAccessDenied(
                "interview session does not belong to this active Owner Vault"
            )
        return OwnerTruthInterviewSessionSnapshot(
            session_id=str(row["id"]),
            vault_id=str(row["vault_id"]),
            owner_subject_id=str(row["owner_subject_id"]),
            thread_id=str(row["current_thread_id"]),
            state=InterviewSessionState(str(row["state"])),
            boundary=InterviewBoundary(str(row["boundary"])),
            row_version=int(row["row_version"]),
            thread_version=int(row["thread_row_version"]),
            turn_count=int(row["turn_count"]),
            authority_epoch=int(row["authority_epoch"]),
        )

    def _ensure_active_vault(
        self,
        cursor: Any,
        *,
        record: StartInterviewSessionWriteRecord,
    ) -> Mapping[str, Any]:
        cursor.execute(
            """
            INSERT INTO owner_truth.vaults (vault_id, owner_subject_id)
            VALUES (%s, %s)
            ON CONFLICT (vault_id) DO UPDATE
            SET updated_at = NOW()
            WHERE owner_truth.vaults.owner_subject_id = EXCLUDED.owner_subject_id
              AND owner_truth.vaults.status = 'active'
            RETURNING owner_subject_id, authority_epoch, status
            """,
            (record.vault_id, record.owner_subject_id),
        )
        vault = cursor.fetchone()
        if vault is None:
            raise OwnerTruthConversationAccessDenied("Vault is not active for this Owner")
        return vault

    @staticmethod
    def _active_vault(
        cursor: Any,
        *,
        vault_id: str,
        owner_subject_id: str,
        lock: bool,
    ) -> Mapping[str, Any]:
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, status
            FROM owner_truth.vaults
            WHERE vault_id = %s
            """ + ("FOR SHARE" if lock else ""),
            (vault_id,),
        )
        vault = cursor.fetchone()
        if (
            vault is None
            or str(vault["owner_subject_id"]) != owner_subject_id
            or str(vault["status"]) != "active"
        ):
            raise OwnerTruthConversationAccessDenied("Vault is not active for this Owner")
        return vault

    @staticmethod
    def _lock(cursor: Any, key: str) -> None:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
            (key,),
        )

    @staticmethod
    def _command_type(record: Any) -> str:
        if isinstance(record, StartInterviewSessionWriteRecord):
            return "startInterviewSession"
        if isinstance(record, AppendInterviewMessageWriteRecord):
            return "appendInterviewMessage"
        if isinstance(record, SetInterviewBoundaryWriteRecord):
            return "setInterviewBoundary"
        raise TypeError("unsupported owner truth conversation write record")

    def _receipt_by_command(
        self,
        cursor: Any,
        *,
        vault_id: str,
        command_id_hash: str,
    ) -> Mapping[str, Any] | None:
        cursor.execute(
            """
            SELECT id, payload_hash, command_type, target_thread_id,
                target_session_id, result_message_id, actor_subject_id,
                owner_subject_id, authority_epoch, policy_version
            FROM owner_truth.conversation_command_receipts
            WHERE vault_id = %s AND command_id_hash = %s
            FOR UPDATE
            """,
            (vault_id, command_id_hash),
        )
        return cursor.fetchone()

    def _deduplicated_result(
        self,
        cursor: Any,
        *,
        existing: Mapping[str, Any],
        record: Any,
    ) -> OwnerTruthInterviewSessionResult:
        if any(
            (
                str(existing["payload_hash"]) != record.payload_hash,
                str(existing["command_type"]) != self._command_type(record),
                str(existing["target_thread_id"]) != record.thread_id,
                str(existing["target_session_id"]) != record.session_id,
                str(existing["actor_subject_id"]) != record.actor_subject_id,
                str(existing["owner_subject_id"]) != record.owner_subject_id,
                str(existing["policy_version"]) != record.policy_version,
            )
        ):
            raise OwnerTruthConversationConflict(
                "commandId cannot be reused with a different conversation command"
            )
        session, thread = self._locked_session_and_thread(cursor, record=record)
        message_id = existing.get("result_message_id")
        message_sequence = None
        if message_id is not None:
            cursor.execute(
                """
                SELECT sequence_number FROM owner_truth.conversation_messages
                WHERE vault_id = %s AND id = %s
                """,
                (record.vault_id, str(message_id)),
            )
            message = cursor.fetchone()
            if message is None:
                raise OwnerTruthConversationConflict(
                    "conversation command receipt points to a missing message"
                )
            message_sequence = int(message["sequence_number"])
        return OwnerTruthInterviewSessionResult(
            outcome="deduplicated",
            receipt_id=str(existing["id"]),
            thread_id=record.thread_id,
            session_id=record.session_id,
            thread_version=int(thread["row_version"]),
            session_version=int(session["row_version"]),
            state=InterviewSessionState(str(session["state"])),
            boundary=InterviewBoundary(str(session["boundary"])),
            message_id=None if message_id is None else str(message_id),
            message_sequence=message_sequence,
        )

    @staticmethod
    def _assert_version(*, resource: str, expected: int, current: int) -> None:
        if expected != current:
            raise OwnerTruthConversationVersionConflict(
                resource=resource,
                expected_version=expected,
                current_version=current,
            )

    @staticmethod
    def _assert_live_session(
        *,
        session: Mapping[str, Any],
        thread: Mapping[str, Any],
        record: Any,
        authority_epoch: int,
    ) -> None:
        if (
            str(session["owner_subject_id"]) != record.owner_subject_id
            or str(thread["owner_subject_id"]) != record.owner_subject_id
            or str(session["current_thread_id"]) != record.thread_id
            or int(session["authority_epoch"]) != authority_epoch
            or int(thread["authority_epoch"]) != authority_epoch
        ):
            raise OwnerTruthConversationAccessDenied(
                "interview session does not belong to this active Owner Vault"
            )

    @staticmethod
    def _assert_thread_absent(cursor: Any, *, record: StartInterviewSessionWriteRecord) -> None:
        cursor.execute(
            """
            SELECT id FROM owner_truth.conversation_threads
            WHERE vault_id = %s AND id = %s
            FOR UPDATE
            """,
            (record.vault_id, record.thread_id),
        )
        if cursor.fetchone() is not None:
            raise OwnerTruthConversationConflict("threadId already exists without this command receipt")

    @staticmethod
    def _assert_session_absent(cursor: Any, *, record: StartInterviewSessionWriteRecord) -> None:
        cursor.execute(
            """
            SELECT id FROM owner_truth.interview_sessions
            WHERE vault_id = %s AND id = %s
            FOR UPDATE
            """,
            (record.vault_id, record.session_id),
        )
        if cursor.fetchone() is not None:
            raise OwnerTruthConversationConflict("sessionId already exists without this command receipt")

    @staticmethod
    def _assert_message_absent(cursor: Any, *, record: AppendInterviewMessageWriteRecord) -> None:
        cursor.execute(
            """
            SELECT id FROM owner_truth.conversation_messages
            WHERE vault_id = %s AND id = %s
            FOR UPDATE
            """,
            (record.vault_id, record.message_id),
        )
        if cursor.fetchone() is not None:
            raise OwnerTruthConversationConflict("messageId already exists without this command receipt")

    @staticmethod
    def _locked_session_and_thread(cursor: Any, *, record: Any) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        cursor.execute(
            """
            SELECT s.id, s.owner_subject_id, s.current_thread_id, s.state,
                s.boundary, s.turn_count, s.authority_epoch, s.row_version,
                t.id AS thread_id, t.owner_subject_id AS thread_owner_subject_id,
                t.authority_epoch AS thread_authority_epoch,
                t.row_version AS thread_row_version
            FROM owner_truth.interview_sessions AS s
            JOIN owner_truth.conversation_threads AS t
              ON t.vault_id = s.vault_id AND t.id = s.current_thread_id
            WHERE s.vault_id = %s AND s.id = %s AND s.current_thread_id = %s
            FOR UPDATE OF s, t
            """,
            (record.vault_id, record.session_id, record.thread_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise OwnerTruthConversationAccessDenied(
                "interview session or thread does not belong to this active Owner Vault"
            )
        session = {
            "id": str(row["id"]),
            "owner_subject_id": str(row["owner_subject_id"]),
            "current_thread_id": str(row["current_thread_id"]),
            "state": str(row["state"]),
            "boundary": str(row["boundary"]),
            "turn_count": int(row["turn_count"]),
            "authority_epoch": int(row["authority_epoch"]),
            "row_version": int(row["row_version"]),
        }
        thread = {
            "id": str(row["thread_id"]),
            "owner_subject_id": str(row["thread_owner_subject_id"]),
            "authority_epoch": int(row["thread_authority_epoch"]),
            "row_version": int(row["thread_row_version"]),
        }
        return session, thread

    def _insert_receipt(
        self,
        cursor: Any,
        *,
        record: Any,
        authority_epoch: int,
        result_message_id: str | None,
        expected_thread_version: int | None,
        expected_session_version: int | None,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO owner_truth.conversation_command_receipts (
                id, vault_id, command_id_hash, payload_hash, command_type,
                target_thread_id, target_session_id, result_message_id,
                expected_thread_version, expected_session_version,
                actor_subject_id, owner_subject_id, authority_epoch, policy_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.receipt_id,
                record.vault_id,
                record.command_id_hash,
                record.payload_hash,
                self._command_type(record),
                record.thread_id,
                record.session_id,
                result_message_id,
                expected_thread_version,
                expected_session_version,
                record.actor_subject_id,
                record.owner_subject_id,
                authority_epoch,
                record.policy_version,
            ),
        )

    @staticmethod
    def _adapt_params(values: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            return tuple(
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if isinstance(value, Mapping)
                else value
                for value in values
            )
        return tuple(Jsonb(dict(value)) if isinstance(value, Mapping) else value for value in values)

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "InMemoryOwnerTruthConversationRepository",
    "OwnerTruthConversationRepository",
    "OwnerTruthConversationService",
    "PostgresOwnerTruthConversationRepository",
]
