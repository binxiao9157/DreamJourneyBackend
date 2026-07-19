"""Default-off V4 completion service for delayed Echo replies.

The service intentionally starts *after* a provider result is available.  A
later ProviderEffect worker is responsible for durable accepted/query/unknown
semantics; this boundary only guarantees that a received result becomes a
single private Answer/Message, owner Inbox item, and value-free business
receipt in one transaction.  It never lets a local clock or a notification
create a completed reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, ContextManager, Mapping, Protocol

from app.services.echo_delayed_reply_effects import (
    ECHO_DELAYED_REPLY_SCHEMA_VERSION,
    EchoDelayedReplyCompletion,
    EchoDelayedReplyContractError,
    EchoDelayedReplyGeneratedAnswer,
    EchoDelayedReplyPlan,
    EchoDelayedReplySnapshot,
)


class EchoDelayedReplyAtomicCompletionPersistenceError(RuntimeError):
    """A provider result could not atomically become an Answer and Inbox item."""


class EchoDelayedReplyAtomicCompletionStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> ContextManager[Any]:
        ...

    def effect_kernel_repository(self) -> Any:
        ...

    def async_effect_consumer_repository(self) -> Any:
        ...

    def get_echo_delayed_reply_for_completion(
        self,
        owner_subject_id: str,
        delayed_reply_id: str,
    ) -> Mapping[str, Any] | None:
        ...

    def persist_echo_delayed_reply_answer(
        self,
        owner_subject_id: str,
        snapshot: EchoDelayedReplySnapshot,
        completion: EchoDelayedReplyCompletion,
        payload: Mapping[str, Any],
        completed_at_iso: str,
    ) -> Mapping[str, Any]:
        ...

    def add_mailbox_letter(self, user_id: str, payload: dict[str, Any]) -> Mapping[str, Any]:
        ...

    def update_echo_delayed_reply_completion(
        self,
        owner_subject_id: str,
        delayed_reply_id: str,
        snapshot: EchoDelayedReplySnapshot,
        completion: EchoDelayedReplyCompletion,
        expected_row_version: int,
        completed_at_iso: str,
    ) -> Mapping[str, Any] | None:
        ...


@dataclass(frozen=True)
class EchoDelayedReplyAtomicCompletionResult:
    """Value-free result for one delayed reply completion attempt."""

    outcome: str
    delivery_state: str
    completion: EchoDelayedReplyCompletion | None
    summary: Mapping[str, Any]

    def value_free_summary(self) -> Mapping[str, Any]:
        return {
            "completion": None if self.completion is None else self.completion.value_free_summary(),
            "deliveryState": self.delivery_state,
            "outcome": self.outcome,
            "summary": dict(self.summary),
        }


def _parse_now(now_iso: str) -> datetime:
    normalized = str(now_iso or "").strip()
    if not normalized:
        raise EchoDelayedReplyContractError("now is required")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EchoDelayedReplyContractError("now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _metadata(item: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = item.get("metadata")
    return metadata if isinstance(metadata, Mapping) else {}


def _field(item: Mapping[str, Any], name: str) -> object:
    metadata = _metadata(item)
    return item.get(name) if item.get(name) is not None else metadata.get(name)


def _terminal_state(item: Mapping[str, Any] | None) -> str | None:
    if not isinstance(item, Mapping):
        return None
    state = str(_field(item, "deliveryState") or "").strip()
    return state if state in {"completed", "failed", "unknown", "cancelled", "blocked"} else None


def _live_snapshot_block_reason(
    item: Mapping[str, Any] | None,
    expected: EchoDelayedReplySnapshot,
    *,
    now_iso: str,
) -> str | None:
    if item is None:
        return "delayedReplyNotActive"
    if str(_field(item, "authorityState") or "") != "active":
        return "authorityNotActive"
    try:
        current = EchoDelayedReplySnapshot.from_persisted_reply(item)
    except EchoDelayedReplyContractError:
        return "delayedReplyNotActive"
    if current.owner_subject_id != expected.owner_subject_id or current.vault_id != expected.vault_id:
        return "authorityNotActive"
    if current.delayed_reply_id != expected.delayed_reply_id:
        return "delayedReplyNotActive"
    if current.authority_epoch != expected.authority_epoch:
        return "authorityEpochChanged"
    if current.reply_generation != expected.reply_generation:
        return "replyGenerationChanged"
    if (
        current.context_hash != expected.context_hash
        or current.context_version != expected.context_version
        or current.context_expires_at != expected.context_expires_at
    ):
        return "contextChanged"
    if current.policy_version != expected.policy_version:
        return "policyVersionChanged"
    if current.row_version != expected.row_version:
        return "rowVersionChanged"
    if current.is_context_expired(now_iso):
        return "contextExpired"
    return None


def _answer_payload(
    snapshot: EchoDelayedReplySnapshot,
    completion: EchoDelayedReplyCompletion,
    *,
    completed_at_iso: str,
) -> dict[str, Any]:
    answer = completion.answer
    if answer is None:
        raise EchoDelayedReplyAtomicCompletionPersistenceError("completed delayed reply lacks Answer")
    return {
        "id": completion.answer_id,
        "answerId": completion.answer_id,
        "ownerSubjectId": snapshot.owner_subject_id,
        "vaultId": snapshot.vault_id,
        "delayedReplyId": snapshot.delayed_reply_id,
        "conversationId": snapshot.conversation_id,
        "requestId": snapshot.request_id,
        "responseMessageId": completion.answer_id,
        "replyGeneration": snapshot.reply_generation,
        "contextHash": snapshot.context_hash,
        "contextVersion": snapshot.context_version,
        "policyVersion": snapshot.policy_version,
        "authorityEpoch": snapshot.authority_epoch,
        "citationReceiptHash": answer.citation_receipt_hash,
        "providerResultHash": answer.provider_result_hash,
        "answerHash": answer.answer_hash,
        "answerLength": answer.answer_length,
        "body": answer.answer_text,
        "completedAt": completed_at_iso,
        "schemaVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
    }


def _mailbox_id(snapshot: EchoDelayedReplySnapshot) -> str:
    digest = sha256(f"echoDelayedReplyMailbox:{snapshot.stable_target_key}".encode("utf-8")).hexdigest()
    return f"echo-delayed-v1-{digest[:32]}"


def _mailbox_payload(
    snapshot: EchoDelayedReplySnapshot,
    completion: EchoDelayedReplyCompletion,
    *,
    completed_at_iso: str,
) -> dict[str, Any]:
    """A recipient-facing pointer; it contains no generated reply body."""

    return {
        "id": _mailbox_id(snapshot),
        "kind": "echoDelayedReply",
        "ownerUserId": snapshot.owner_subject_id,
        "sourceDelayedReplyId": snapshot.delayed_reply_id,
        "sourceAnswerId": completion.answer_id,
        "replyGeneration": snapshot.reply_generation,
        "contextHash": snapshot.context_hash,
        "deliveredAt": completed_at_iso,
        "status": "unread",
        "metadataOnly": True,
        "contentRedacted": True,
        "deliveryProtocolVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
        "deliveryTargetKey": snapshot.stable_target_key,
        "title": "一封回响回信已准备好",
        "privacyMetadata": {
            "content": "redacted",
            "scope": "ownerOnly",
        },
    }


class EchoDelayedReplyAtomicCompletionService:
    """Atomically turn one already-generated response into a truthful reply."""

    def __init__(self, store: EchoDelayedReplyAtomicCompletionStore):
        self._store = store

    def complete(
        self,
        plan: EchoDelayedReplyPlan,
        *,
        generated_answer: EchoDelayedReplyGeneratedAnswer,
        now_iso: str,
    ) -> EchoDelayedReplyAtomicCompletionResult:
        if not isinstance(plan, EchoDelayedReplyPlan):
            raise TypeError("echo delayed reply plan is required")
        if not isinstance(generated_answer, EchoDelayedReplyGeneratedAnswer):
            raise TypeError("generated delayed reply Answer is required")
        _parse_now(now_iso)
        if not plan.due or not plan.snapshot.is_due(now_iso):
            return EchoDelayedReplyAtomicCompletionResult(
                outcome="not_due",
                delivery_state="scheduled",
                completion=None,
                summary=plan.value_free_summary(),
            )

        snapshot = plan.snapshot
        command_id = sha256(
            f"{snapshot.stable_target_key}:{snapshot.reply_generation}".encode("utf-8")
        ).hexdigest()
        with self._store.request_unit_of_work(
            correlation_id=f"echo-delayed-reply-{snapshot.delayed_reply_id}-{snapshot.reply_generation}",
            command_id=command_id,
        ):
            live_item = self._store.get_echo_delayed_reply_for_completion(
                snapshot.owner_subject_id,
                snapshot.delayed_reply_id,
            )
            terminal_state = _terminal_state(live_item)
            if terminal_state is not None:
                return EchoDelayedReplyAtomicCompletionResult(
                    outcome="already_terminal",
                    delivery_state=terminal_state,
                    completion=None,
                    summary=self._terminal_summary(live_item, terminal_state),
                )

            kernel = self._store.effect_kernel_repository()
            consumer = self._store.async_effect_consumer_repository()
            kernel.accept(snapshot.effect_intent)

            blocked_reason = _live_snapshot_block_reason(live_item, snapshot, now_iso=now_iso)
            if blocked_reason is not None:
                completion = EchoDelayedReplyCompletion(
                    snapshot=snapshot,
                    outcome="blocked",
                    reason_code=blocked_reason,
                )
                consumer.consume(completion.consumer_command)
                updated = self._store.update_echo_delayed_reply_completion(
                    snapshot.owner_subject_id,
                    snapshot.delayed_reply_id,
                    snapshot,
                    completion,
                    snapshot.row_version,
                    now_iso,
                )
                if updated is None:
                    raise EchoDelayedReplyAtomicCompletionPersistenceError(
                        "delayed reply changed before blocked completion could finalize"
                    )
                return EchoDelayedReplyAtomicCompletionResult(
                    outcome="blocked",
                    delivery_state="blocked",
                    completion=completion,
                    summary=completion.value_free_summary(),
                )

            completion = EchoDelayedReplyCompletion(
                snapshot=snapshot,
                outcome="completed",
                reason_code="answerInboxPersisted",
                answer=generated_answer,
            )
            self._store.persist_echo_delayed_reply_answer(
                snapshot.owner_subject_id,
                snapshot,
                completion,
                _answer_payload(snapshot, completion, completed_at_iso=now_iso),
                now_iso,
            )
            self._store.add_mailbox_letter(
                snapshot.owner_subject_id,
                _mailbox_payload(snapshot, completion, completed_at_iso=now_iso),
            )
            consumer.consume(completion.consumer_command)
            updated = self._store.update_echo_delayed_reply_completion(
                snapshot.owner_subject_id,
                snapshot.delayed_reply_id,
                snapshot,
                completion,
                snapshot.row_version,
                now_iso,
            )
            if updated is None:
                raise EchoDelayedReplyAtomicCompletionPersistenceError(
                    "delayed reply changed before Answer/Inbox completion could finalize"
                )
            return EchoDelayedReplyAtomicCompletionResult(
                outcome="completed",
                delivery_state="completed",
                completion=completion,
                summary=completion.value_free_summary(),
            )

    @staticmethod
    def _terminal_summary(item: Mapping[str, Any] | None, state: str) -> Mapping[str, Any]:
        if not isinstance(item, Mapping):
            return {
                "deliveryState": state,
                "schemaVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
            }
        summary = _field(item, "completionSummary")
        if isinstance(summary, Mapping):
            return dict(summary)
        return {
            "deliveryState": state,
            "legacyTerminal": True,
            "schemaVersion": ECHO_DELAYED_REPLY_SCHEMA_VERSION,
        }


__all__ = [
    "EchoDelayedReplyAtomicCompletionPersistenceError",
    "EchoDelayedReplyAtomicCompletionResult",
    "EchoDelayedReplyAtomicCompletionService",
    "EchoDelayedReplyAtomicCompletionStore",
]
