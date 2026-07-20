"""Value-minimized QA-only reads for private Owner Truth interview sessions.

This adapter deliberately exposes only lifecycle and pacing state.  It never
returns messages, topic content, source/candidate identifiers, or any
MemoryVersion authority.  The public Echo surface remains unchanged.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from app.domain.owner_truth.contracts import require_uuid
from app.domain.owner_truth.conversation import OwnerTruthInterviewSessionSnapshot
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext
from app.services.owner_truth_conversation import OwnerTruthConversationService


class OwnerTruthInterviewSessionReadStore(Protocol):
    def request_unit_of_work(
        self,
        *,
        correlation_id: str,
        command_id: str,
    ) -> AbstractContextManager[Any]:
        ...

    def owner_truth_conversation_repository(self) -> Any:
        ...


class OwnerTruthInterviewSessionReadService:
    """Read one private interview session in the store's existing UoW."""

    def __init__(self, store: OwnerTruthInterviewSessionReadStore):
        self._store = store

    def read(
        self,
        *,
        session_id: str,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthInterviewSessionSnapshot:
        normalized_session_id = require_uuid(session_id, field="session_id")
        with self._store.request_unit_of_work(
            correlation_id=(
                "owner-truth-interview-session-state-read-"
                f"{context.vault_id}:{normalized_session_id}"
            ),
            command_id=f"read:{normalized_session_id}",
        ):
            return OwnerTruthConversationService(
                self._store.owner_truth_conversation_repository()
            ).read_session(
                session_id=normalized_session_id,
                context=context,
            )


__all__ = [
    "OwnerTruthInterviewSessionReadService",
    "OwnerTruthInterviewSessionReadStore",
]
