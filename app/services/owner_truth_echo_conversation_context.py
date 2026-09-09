"""Private, bounded server-side context for text Echo follow-up questions.

This is deliberately not an Owner Truth Source, Candidate or MemoryVersion.
It holds only the most recent completed text exchanges for one authenticated
Owner and product session so a new client request can resolve ordinary
references such as "那一年呢" without trusting a client-supplied history.
Formal facts remain independently selected from the current projection.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from threading import RLock
from typing import Any, Mapping, Protocol, Sequence

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


OWNER_TRUTH_ECHO_CONVERSATION_CONTEXT_SCHEMA_VERSION = (
    "owner-truth-echo-conversation-context-v1"
)
OWNER_TRUTH_ECHO_CONVERSATION_MAX_TURNS = 6
OWNER_TRUTH_ECHO_CONVERSATION_MAX_TURN_CHARS = 500
OWNER_TRUTH_ECHO_CONVERSATION_MAX_TOTAL_CHARS = 2_400
OWNER_TRUTH_ECHO_CONVERSATION_RETENTION_SECONDS = 86_400
_FOLLOW_UP_REFERENCE = re.compile(
    r"^(?:那|这|它|该|刚才|刚刚|上面|前面|然后)(?:个|件|位|所|年|道|段|次|部分|里|是|还|又|怎么|为什么|什么|哪|有|呢|吗|[？?])?"
)


class OwnerTruthEchoConversationContextError(ValueError):
    """The private text Echo context contract cannot be satisfied."""


class OwnerTruthEchoConversationContextAccessDenied(
    OwnerTruthEchoConversationContextError
):
    """A caller tried to read or write another Owner's text context."""


@dataclass(frozen=True)
class OwnerTruthRetrievalQueryResolution:
    """A server-owned query rewrite used only before formal-memory retrieval."""

    retrieval_query: str
    used_history: bool
    source: str


def resolve_owner_truth_retrieval_query(
    *,
    query: str,
    recent_turns: Sequence[Mapping[str, Any]],
) -> OwnerTruthRetrievalQueryResolution:
    """Resolve a narrow follow-up reference from authenticated session history.

    The returned text is a retrieval cue, not evidence: the answer still must
    be grounded solely in the current authorized formal-memory projection.  We
    deliberately use only a prior *user* turn from the same product session,
    never a model answer or client-supplied cross-session history.
    """

    normalized = " ".join(str(query or "").split())
    if not normalized or not _FOLLOW_UP_REFERENCE.match(normalized):
        return OwnerTruthRetrievalQueryResolution(
            retrieval_query=normalized,
            used_history=False,
            source="currentQuery",
        )
    for turn in reversed(recent_turns):
        if not isinstance(turn, Mapping) or str(turn.get("role") or "") != "user":
            continue
        previous = " ".join(str(turn.get("text") or "").split())
        if not previous or _FOLLOW_UP_REFERENCE.match(previous):
            continue
        return OwnerTruthRetrievalQueryResolution(
            retrieval_query=previous,
            used_history=True,
            source="sameProductSessionUserTurn",
        )
    return OwnerTruthRetrievalQueryResolution(
        retrieval_query=normalized,
        used_history=False,
        source="currentQuery",
    )


def _nonblank(value: Any, *, field: str, maximum: int = 128) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise OwnerTruthEchoConversationContextError(f"{field} must be nonblank")
    if len(normalized) > maximum:
        raise OwnerTruthEchoConversationContextError(f"{field} exceeds the supported length")
    return normalized


def _turn_text(value: Any, *, field: str) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise OwnerTruthEchoConversationContextError(f"{field} must be nonblank")
    return normalized[:OWNER_TRUTH_ECHO_CONVERSATION_MAX_TURN_CHARS]


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise OwnerTruthEchoConversationContextAccessDenied(
            "owner truth command context is required"
        )
    if context.actor_subject_id != context.owner_subject_id:
        raise OwnerTruthEchoConversationContextAccessDenied(
            "only the Vault Owner may use private text Echo context"
        )


@dataclass(frozen=True)
class OwnerTruthEchoConversationTurn:
    request_id: str
    role: str
    text: str

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"}:
            raise OwnerTruthEchoConversationContextError("text Echo turn role is invalid")
        object.__setattr__(
            self,
            "request_id",
            _nonblank(self.request_id, field="text Echo request id"),
        )
        object.__setattr__(self, "text", _turn_text(self.text, field="text Echo turn text"))

    def prompt_contract(self) -> dict[str, str]:
        return {"role": self.role, "text": self.text}

    def persistence_contract(self) -> dict[str, str]:
        return {
            "requestId": self.request_id,
            "role": self.role,
            "text": self.text,
        }


@dataclass(frozen=True)
class OwnerTruthEchoConversationContextSnapshot:
    vault_id: str
    owner_subject_id: str
    product_session_id: str
    turns: tuple[OwnerTruthEchoConversationTurn, ...]
    revision: int
    state: str

    @property
    def prompt_turns(self) -> list[dict[str, str]]:
        return [item.prompt_contract() for item in self.turns]

    def public_summary(self, *, source: str) -> dict[str, Any]:
        return {
            "schemaVersion": OWNER_TRUTH_ECHO_CONVERSATION_CONTEXT_SCHEMA_VERSION,
            "source": source,
            "state": self.state,
            "productSessionId": self.product_session_id,
            "turnCount": len(self.turns),
            "revision": self.revision,
        }


class OwnerTruthEchoConversationContextRepository(Protocol):
    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        product_session_id: str,
    ) -> OwnerTruthEchoConversationContextSnapshot:
        ...

    def append_exchange(
        self,
        *,
        context: OwnerTruthCommandContext,
        product_session_id: str,
        request_id: str,
        user_text: str,
        assistant_text: str,
    ) -> OwnerTruthEchoConversationContextSnapshot:
        ...


class OwnerTruthEchoConversationContextStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def owner_truth_echo_conversation_context_repository(
        self,
    ) -> OwnerTruthEchoConversationContextRepository:
        ...


def _empty_snapshot(
    *,
    context: OwnerTruthCommandContext,
    product_session_id: str,
    state: str = "empty",
) -> OwnerTruthEchoConversationContextSnapshot:
    return OwnerTruthEchoConversationContextSnapshot(
        vault_id=context.vault_id,
        owner_subject_id=context.owner_subject_id,
        product_session_id=product_session_id,
        turns=(),
        revision=0,
        state=state,
    )


def _normalize_persisted_turns(value: Any) -> tuple[OwnerTruthEchoConversationTurn, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise OwnerTruthEchoConversationContextError(
                "persisted text Echo context is not valid JSON"
            ) from error
    if not isinstance(value, list):
        raise OwnerTruthEchoConversationContextError("persisted text Echo turns must be an array")
    turns: list[OwnerTruthEchoConversationTurn] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise OwnerTruthEchoConversationContextError("persisted text Echo turn is invalid")
        turns.append(
            OwnerTruthEchoConversationTurn(
                request_id=str(item.get("requestId") or ""),
                role=str(item.get("role") or ""),
                text=str(item.get("text") or ""),
            )
        )
    return _bounded_turns(turns)


def _bounded_turns(
    turns: Sequence[OwnerTruthEchoConversationTurn],
) -> tuple[OwnerTruthEchoConversationTurn, ...]:
    bounded = list(turns)
    while bounded and (
        len(bounded) > OWNER_TRUTH_ECHO_CONVERSATION_MAX_TURNS
        or sum(len(item.text) for item in bounded)
        > OWNER_TRUTH_ECHO_CONVERSATION_MAX_TOTAL_CHARS
    ):
        bounded.pop(0)
    return tuple(bounded)


class InMemoryOwnerTruthEchoConversationContextRepository:
    """Thread-safe semantic double for text Echo context persistence."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[tuple[str, str, str], dict[str, Any]] = {}

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        product_session_id: str,
    ) -> OwnerTruthEchoConversationContextSnapshot:
        _assert_owner_context(context)
        product_session_id = _nonblank(
            product_session_id,
            field="text Echo product session id",
        )
        key = (context.vault_id, context.owner_subject_id, product_session_id)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return _empty_snapshot(context=context, product_session_id=product_session_id)
            expires_at = record.get("expiresAt")
            if isinstance(expires_at, datetime) and expires_at <= datetime.now(timezone.utc):
                del self._records[key]
                return _empty_snapshot(
                    context=context,
                    product_session_id=product_session_id,
                    state="expired",
                )
            turns = _normalize_persisted_turns(record.get("turns"))
            return OwnerTruthEchoConversationContextSnapshot(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                product_session_id=product_session_id,
                turns=turns,
                revision=int(record.get("revision") or 0),
                state="ready",
            )

    def append_exchange(
        self,
        *,
        context: OwnerTruthCommandContext,
        product_session_id: str,
        request_id: str,
        user_text: str,
        assistant_text: str,
    ) -> OwnerTruthEchoConversationContextSnapshot:
        _assert_owner_context(context)
        product_session_id = _nonblank(
            product_session_id,
            field="text Echo product session id",
        )
        request_id = _nonblank(request_id, field="text Echo request id")
        user_turn = OwnerTruthEchoConversationTurn(
            request_id=request_id,
            role="user",
            text=user_text,
        )
        assistant_turn = OwnerTruthEchoConversationTurn(
            request_id=request_id,
            role="assistant",
            text=assistant_text,
        )
        key = (context.vault_id, context.owner_subject_id, product_session_id)
        with self._lock:
            existing = self.read(context=context, product_session_id=product_session_id)
            if any(item.request_id == request_id for item in existing.turns):
                return OwnerTruthEchoConversationContextSnapshot(
                    vault_id=existing.vault_id,
                    owner_subject_id=existing.owner_subject_id,
                    product_session_id=existing.product_session_id,
                    turns=existing.turns,
                    revision=existing.revision,
                    state="deduplicated",
                )
            turns = _bounded_turns((*existing.turns, user_turn, assistant_turn))
            revision = existing.revision + 1
            self._records[key] = {
                "turns": [item.persistence_contract() for item in turns],
                "revision": revision,
                "expiresAt": datetime.now(timezone.utc)
                + timedelta(seconds=OWNER_TRUTH_ECHO_CONVERSATION_RETENTION_SECONDS),
            }
            return OwnerTruthEchoConversationContextSnapshot(
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                product_session_id=product_session_id,
                turns=turns,
                revision=revision,
                state="ready",
            )


class PostgresOwnerTruthEchoConversationContextRepository:
    """PostgreSQL persistence for bounded, expiring text Echo context."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def read(
        self,
        *,
        context: OwnerTruthCommandContext,
        product_session_id: str,
    ) -> OwnerTruthEchoConversationContextSnapshot:
        _assert_owner_context(context)
        product_session_id = _nonblank(
            product_session_id,
            field="text Echo product session id",
        )
        with self._cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM owner_truth.echo_conversation_contexts
                WHERE vault_id = %s AND owner_subject_id = %s
                  AND product_session_id = %s AND expires_at <= NOW()
                """,
                (context.vault_id, context.owner_subject_id, product_session_id),
            )
            cursor.execute(
                """
                SELECT turns, revision
                FROM owner_truth.echo_conversation_contexts
                WHERE vault_id = %s AND owner_subject_id = %s
                  AND product_session_id = %s AND expires_at > NOW()
                """,
                (context.vault_id, context.owner_subject_id, product_session_id),
            )
            row = cursor.fetchone()
        if row is None:
            return _empty_snapshot(context=context, product_session_id=product_session_id)
        turns = _normalize_persisted_turns(row["turns"])
        return OwnerTruthEchoConversationContextSnapshot(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            product_session_id=product_session_id,
            turns=turns,
            revision=int(row["revision"]),
            state="ready",
        )

    def append_exchange(
        self,
        *,
        context: OwnerTruthCommandContext,
        product_session_id: str,
        request_id: str,
        user_text: str,
        assistant_text: str,
    ) -> OwnerTruthEchoConversationContextSnapshot:
        _assert_owner_context(context)
        product_session_id = _nonblank(
            product_session_id,
            field="text Echo product session id",
        )
        request_id = _nonblank(request_id, field="text Echo request id")
        new_turns = (
            OwnerTruthEchoConversationTurn(
                request_id=request_id,
                role="user",
                text=user_text,
            ),
            OwnerTruthEchoConversationTurn(
                request_id=request_id,
                role="assistant",
                text=assistant_text,
            ),
        )
        with self._cursor() as cursor:
            # An expired row must not be revived as if it were a current
            # conversation. Delete it before locking the active record so a
            # new exchange starts a fresh, bounded context.
            cursor.execute(
                """
                DELETE FROM owner_truth.echo_conversation_contexts
                WHERE vault_id = %s AND owner_subject_id = %s
                  AND product_session_id = %s AND expires_at <= NOW()
                """,
                (context.vault_id, context.owner_subject_id, product_session_id),
            )
            cursor.execute(
                """
                SELECT turns, revision
                FROM owner_truth.echo_conversation_contexts
                WHERE vault_id = %s AND owner_subject_id = %s
                  AND product_session_id = %s
                FOR UPDATE
                """,
                (context.vault_id, context.owner_subject_id, product_session_id),
            )
            row = cursor.fetchone()
            existing = () if row is None else _normalize_persisted_turns(row["turns"])
            if any(item.request_id == request_id for item in existing):
                return OwnerTruthEchoConversationContextSnapshot(
                    vault_id=context.vault_id,
                    owner_subject_id=context.owner_subject_id,
                    product_session_id=product_session_id,
                    turns=existing,
                    revision=int(row["revision"]),
                    state="deduplicated",
                )
            turns = _bounded_turns((*existing, *new_turns))
            revision = 1 if row is None else int(row["revision"]) + 1
            payload = [item.persistence_contract() for item in turns]
            if row is None:
                cursor.execute(
                    """
                    INSERT INTO owner_truth.echo_conversation_contexts (
                        vault_id, owner_subject_id, product_session_id,
                        turns, revision, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, NOW() + INTERVAL '24 hours')
                    """,
                    self._adapt_params(
                        (
                            context.vault_id,
                            context.owner_subject_id,
                            product_session_id,
                            payload,
                            revision,
                        )
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE owner_truth.echo_conversation_contexts
                    SET turns = %s, revision = %s,
                        expires_at = NOW() + INTERVAL '24 hours', updated_at = NOW()
                    WHERE vault_id = %s AND owner_subject_id = %s
                      AND product_session_id = %s
                    """,
                    self._adapt_params(
                        (
                            payload,
                            revision,
                            context.vault_id,
                            context.owner_subject_id,
                            product_session_id,
                        )
                    ),
                )
        return OwnerTruthEchoConversationContextSnapshot(
            vault_id=context.vault_id,
            owner_subject_id=context.owner_subject_id,
            product_session_id=product_session_id,
            turns=turns,
            revision=revision,
            state="ready",
        )

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)

    @staticmethod
    def _adapt_params(values: tuple[Any, ...]) -> tuple[Any, ...]:
        try:
            from psycopg.types.json import Jsonb
        except ImportError:  # pragma: no cover - production dependency
            return tuple(
                json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if isinstance(value, (dict, list))
                else value
                for value in values
            )
        return tuple(Jsonb(value) if isinstance(value, (dict, list)) else value for value in values)


class OwnerTruthEchoConversationContextService:
    """Server-owned bounded context used only to disambiguate text Echo."""

    def __init__(self, store: OwnerTruthEchoConversationContextStore) -> None:
        self._store = store

    def read_recent(
        self,
        *,
        context: OwnerTruthCommandContext,
        product_session_id: str,
    ) -> OwnerTruthEchoConversationContextSnapshot:
        _assert_owner_context(context)
        with self._store.request_unit_of_work(
            correlation_id=f"owner-truth-echo-context-read-{context.vault_id}",
            command_id="ownerTruthEchoConversationContextRead",
        ):
            return self._store.owner_truth_echo_conversation_context_repository().read(
                context=context,
                product_session_id=product_session_id,
            )

    def record_answered_exchange(
        self,
        *,
        context: OwnerTruthCommandContext,
        product_session_id: str,
        request_id: str,
        user_text: str,
        assistant_text: str,
    ) -> OwnerTruthEchoConversationContextSnapshot:
        _assert_owner_context(context)
        with self._store.request_unit_of_work(
            correlation_id=f"owner-truth-echo-context-write-{context.vault_id}",
            command_id="ownerTruthEchoConversationContextWrite",
        ):
            return self._store.owner_truth_echo_conversation_context_repository().append_exchange(
                context=context,
                product_session_id=product_session_id,
                request_id=request_id,
                user_text=user_text,
                assistant_text=assistant_text,
            )


__all__ = [
    "InMemoryOwnerTruthEchoConversationContextRepository",
    "OWNER_TRUTH_ECHO_CONVERSATION_CONTEXT_SCHEMA_VERSION",
    "OWNER_TRUTH_ECHO_CONVERSATION_MAX_TOTAL_CHARS",
    "OWNER_TRUTH_ECHO_CONVERSATION_MAX_TURN_CHARS",
    "OWNER_TRUTH_ECHO_CONVERSATION_MAX_TURNS",
    "OWNER_TRUTH_ECHO_CONVERSATION_RETENTION_SECONDS",
    "OwnerTruthEchoConversationContextAccessDenied",
    "OwnerTruthEchoConversationContextError",
    "OwnerTruthEchoConversationContextService",
    "OwnerTruthEchoConversationContextSnapshot",
    "OwnerTruthEchoConversationContextStore",
    "OwnerTruthEchoConversationContextRepository",
    "OwnerTruthEchoConversationTurn",
    "OwnerTruthRetrievalQueryResolution",
    "PostgresOwnerTruthEchoConversationContextRepository",
    "resolve_owner_truth_retrieval_query",
]
