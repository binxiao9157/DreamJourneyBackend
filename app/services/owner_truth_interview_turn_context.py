"""Private Owner Truth interview-turn Context preparation.

This is the first server-side bridge between the persisted M0-A interview lane
and confirmed MemoryVersion Context materialization.  It deliberately remains
default-off and provider-free: the service only proves that one current owner
narrative, one active interview session, and one current Projection authority
epoch agree before bounded personal-memory text can exist in process.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from copy import deepcopy
from hashlib import sha256
from typing import Any, Mapping, Protocol

from app.domain.owner_truth.contracts import require_uuid
from app.domain.owner_truth.conversation import (
    ConversationMessageAuthor,
    ConversationMessageKind,
    InterviewSessionState,
    OwnerTruthConversationAccessDenied,
    OwnerTruthConversationError,
    OwnerTruthConversationMessageAuthoritySnapshot,
    OwnerTruthConversationVersionConflict,
    OwnerTruthInterviewSessionSnapshot,
    OwnerTruthInterviewSessionStateConflict,
)
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_context_materialization import (
    OwnerTruthContextMaterializationError,
    OwnerTruthContextMaterializationService,
    context_materialization_summary,
)
from app.services.owner_truth_conversation import OwnerTruthConversationService
from app.services.owner_truth_memory_projection import OwnerTruthMemoryProjectionStore


OWNER_TRUTH_INTERVIEW_TURN_CONTEXT_SCHEMA_VERSION = "owner-truth-interview-turn-context-v1"
OWNER_TRUTH_INTERVIEW_TURN_CONTEXT_POLICY_VERSION = "owner-truth-interview-turn-context-policy-v1"

_ALLOWED_PAYLOAD_FIELDS = frozenset(
    {
        "expectedSessionVersion",
        "intent",
        "messageId",
        "query",
        "selectionMode",
    }
)
_FALLBACK_AUTHORITY_EPOCH_MISMATCH = "interview_session_authority_epoch_mismatch"


class OwnerTruthInterviewTurnContextError(OwnerTruthConversationError):
    """The private interview-turn Context envelope is malformed."""


class OwnerTruthInterviewTurnContextStore(OwnerTruthMemoryProjectionStore, Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def owner_truth_conversation_repository(self) -> Any:
        ...


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise OwnerTruthInterviewTurnContextError(f"{field} must be a positive integer")
    return value


def _payload(payload: Mapping[str, Any] | None) -> tuple[str, int, dict[str, Any]]:
    if payload is None or not isinstance(payload, Mapping):
        raise OwnerTruthInterviewTurnContextError("interview turn Context payload must be an object")
    unsupported = sorted(set(payload).difference(_ALLOWED_PAYLOAD_FIELDS))
    if unsupported:
        raise OwnerTruthInterviewTurnContextError("interview turn Context contains unsupported fields")
    message_id = require_uuid(payload.get("messageId"), field="message_id")
    expected_session_version = _positive_int(
        payload.get("expectedSessionVersion"),
        field="expected_session_version",
    )
    materialization_payload = {
        key: payload[key]
        for key in ("intent", "query", "selectionMode")
        if key in payload
    }
    return message_id, expected_session_version, materialization_payload


def _empty_generation_context(materialization: Mapping[str, Any]) -> dict[str, Any]:
    existing = materialization.get("generationContext")
    generation = existing if isinstance(existing, Mapping) else {}
    text = ""
    return {
        "version": str(generation.get("version") or ""),
        "text": text,
        "contentHash": "sha256:" + sha256(text.encode("utf-8")).hexdigest(),
        "sourceCount": 0,
        "maxChars": int(generation.get("maxChars") or 0),
        "truncated": False,
    }


class OwnerTruthInterviewTurnContextService:
    """Prepare bounded confirmed-memory Context for one persisted owner turn.

    The result is intentionally private.  Its ``generationContext.text`` can
    be consumed only by a future server-side reply adapter after this binding
    succeeds.  This service does not call a model, write a response, mutate an
    interview, or alter the public Echo Context route.
    """

    def __init__(self, store: OwnerTruthInterviewTurnContextStore, *, enabled: bool = False) -> None:
        self._store = store
        self._enabled = bool(enabled)

    def prepare(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
        payload: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if not self._enabled:
            raise OwnerTruthInterviewTurnContextError("interview turn Context is unavailable")
        normalized_session_id = require_uuid(session_id, field="session_id")
        message_id, expected_session_version, materialization_payload = _payload(payload)

        with self._unit_of_work(
            correlation_id=(
                "owner-truth-interview-turn-context-prepare:"
                f"{context.vault_id}:{normalized_session_id}"
            ),
            command_id=f"prepare:{normalized_session_id}:{message_id}",
        ):
            conversation = OwnerTruthConversationService(
                self._store.owner_truth_conversation_repository()
            )
            session = conversation.read_session(
                session_id=normalized_session_id,
                context=context,
            )
            self._assert_current_session(
                session=session,
                context=context,
                expected_session_version=expected_session_version,
            )
            message = conversation.read_message_authority(
                message_id=message_id,
                context=context,
            )
            self._assert_current_owner_narrative(session=session, message=message, context=context)

            materialization = OwnerTruthContextMaterializationService(
                self._store,
                enabled=True,
            ).build(
                context=context,
                payload=materialization_payload,
            )
            if not self._materialization_matches_session(
                materialization=materialization,
                session=session,
                context=context,
            ):
                materialization = self._blocked_materialization(materialization)
                return self._result(
                    state="authorityMismatch",
                    session=session,
                    message=message,
                    materialization=materialization,
                    ready_for_server_turn=False,
                )

            materialization_state = str(materialization.get("state") or "")
            return self._result(
                state="ready" if materialization_state == "ready" else "projectionUnavailable",
                session=session,
                message=message,
                materialization=materialization,
                ready_for_server_turn=materialization_state == "ready",
            )

    @staticmethod
    def _assert_current_session(
        *,
        session: OwnerTruthInterviewSessionSnapshot,
        context: OwnerTruthCommandContext,
        expected_session_version: int,
    ) -> None:
        if (
            session.vault_id != context.vault_id
            or session.owner_subject_id != context.owner_subject_id
            or context.actor_subject_id != context.owner_subject_id
        ):
            raise OwnerTruthConversationAccessDenied(
                "interview turn Context does not belong to the active Vault Owner"
            )
        if session.state is not InterviewSessionState.ACTIVE:
            raise OwnerTruthInterviewSessionStateConflict(
                "interview turn Context requires an active session"
            )
        if session.row_version != expected_session_version:
            raise OwnerTruthConversationVersionConflict(
                resource="session",
                expected_version=expected_session_version,
                current_version=session.row_version,
            )

    @staticmethod
    def _assert_current_owner_narrative(
        *,
        session: OwnerTruthInterviewSessionSnapshot,
        message: OwnerTruthConversationMessageAuthoritySnapshot,
        context: OwnerTruthCommandContext,
    ) -> None:
        if (
            message.vault_id != context.vault_id
            or message.owner_subject_id != context.owner_subject_id
            or message.thread_id != session.thread_id
            or message.session_id != session.session_id
            or message.authority_epoch != session.authority_epoch
            or message.author is not ConversationMessageAuthor.OWNER
            or message.kind is not ConversationMessageKind.NARRATIVE
        ):
            raise OwnerTruthConversationAccessDenied(
                "interview turn Context requires one current Owner narrative"
            )

    @staticmethod
    def _materialization_matches_session(
        *,
        materialization: Mapping[str, Any],
        session: OwnerTruthInterviewSessionSnapshot,
        context: OwnerTruthCommandContext,
    ) -> bool:
        authority = materialization.get("authority")
        if not isinstance(authority, Mapping):
            raise OwnerTruthContextMaterializationError("materialization authority is invalid")
        return (
            str(authority.get("vaultId") or "") == context.vault_id
            and authority.get("authorityEpoch") == session.authority_epoch
        )

    @staticmethod
    def _blocked_materialization(materialization: Mapping[str, Any]) -> dict[str, Any]:
        blocked = deepcopy(dict(materialization))
        fallbacks = list(blocked.get("fallbacks") or [])
        if _FALLBACK_AUTHORITY_EPOCH_MISMATCH not in fallbacks:
            fallbacks.append(_FALLBACK_AUTHORITY_EPOCH_MISMATCH)
        blocked["state"] = "authorityMismatch"
        blocked["selectedContext"] = []
        blocked["typedCitations"] = []
        blocked["generationContext"] = _empty_generation_context(blocked)
        blocked["fallbacks"] = fallbacks
        trace = dict(blocked.get("trace") or {})
        trace.update(
            {
                "selectedContextCount": 0,
                "typedCitationCount": 0,
                "generationContextSourceCount": 0,
                "generationContextLength": 0,
                "generationContextTruncated": False,
                "fallbackCount": len(fallbacks),
            }
        )
        blocked["trace"] = trace
        return blocked

    @staticmethod
    def _result(
        *,
        state: str,
        session: OwnerTruthInterviewSessionSnapshot,
        message: OwnerTruthConversationMessageAuthoritySnapshot,
        materialization: Mapping[str, Any],
        ready_for_server_turn: bool,
    ) -> dict[str, Any]:
        generation_context = materialization.get("generationContext")
        if not isinstance(generation_context, Mapping):
            raise OwnerTruthContextMaterializationError("materialization generationContext is invalid")
        return {
            "schemaVersion": OWNER_TRUTH_INTERVIEW_TURN_CONTEXT_SCHEMA_VERSION,
            "policyVersion": OWNER_TRUTH_INTERVIEW_TURN_CONTEXT_POLICY_VERSION,
            "state": state,
            "readyForServerTurn": ready_for_server_turn,
            "providerDispatchAllowed": False,
            "publicEchoUnchanged": True,
            "session": session,
            "messageAuthority": message,
            "materialization": deepcopy(dict(materialization)),
            "generationContext": deepcopy(dict(generation_context)),
            "typedCitations": deepcopy(list(materialization.get("typedCitations") or [])),
            "fallbacks": list(materialization.get("fallbacks") or []),
        }

    def _unit_of_work(self, *, correlation_id: str, command_id: str) -> AbstractContextManager[Any]:
        factory = getattr(self._store, "request_unit_of_work", None)
        if callable(factory):
            return factory(correlation_id=correlation_id, command_id=command_id)
        return nullcontext()


def interview_turn_context_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """Export only value-free preparation evidence; never personal text."""

    if not isinstance(result, Mapping):
        raise OwnerTruthInterviewTurnContextError("interview turn Context result must be an object")
    session = result.get("session")
    message = result.get("messageAuthority")
    materialization = result.get("materialization")
    if not isinstance(session, OwnerTruthInterviewSessionSnapshot):
        raise OwnerTruthInterviewTurnContextError("interview turn Context session is invalid")
    if not isinstance(message, OwnerTruthConversationMessageAuthoritySnapshot):
        raise OwnerTruthInterviewTurnContextError("interview turn Context message binding is invalid")
    if not isinstance(materialization, Mapping):
        raise OwnerTruthInterviewTurnContextError("interview turn Context materialization is invalid")
    return {
        "schemaVersion": str(result.get("schemaVersion") or ""),
        "policyVersion": str(result.get("policyVersion") or ""),
        "state": str(result.get("state") or ""),
        "readyForServerTurn": bool(result.get("readyForServerTurn")),
        "providerDispatchAllowed": bool(result.get("providerDispatchAllowed")),
        "publicEchoUnchanged": bool(result.get("publicEchoUnchanged")),
        "session": {
            "state": session.state.value,
            "boundary": session.boundary.value,
            "sessionVersion": session.row_version,
            "threadVersion": session.thread_version,
            "authorityEpoch": session.authority_epoch,
        },
        "messageAuthority": {
            "author": message.author.value,
            "kind": message.kind.value,
            "sequenceNumber": message.sequence_number,
        },
        "contextMaterialization": context_materialization_summary(materialization),
        "fallbacks": list(result.get("fallbacks") or []),
    }


__all__ = [
    "OWNER_TRUTH_INTERVIEW_TURN_CONTEXT_POLICY_VERSION",
    "OWNER_TRUTH_INTERVIEW_TURN_CONTEXT_SCHEMA_VERSION",
    "OwnerTruthInterviewTurnContextError",
    "OwnerTruthInterviewTurnContextService",
    "OwnerTruthInterviewTurnContextStore",
    "interview_turn_context_summary",
]
