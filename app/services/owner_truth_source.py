"""Owner Truth CreateSource application service and legacy Archive facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from uuid import NAMESPACE_URL, uuid5

from app.domain.owner_truth.source_commands import (
    CreateTextSourceCommand,
    OwnerTruthCommandContext,
    OwnerTruthSourceCommandConflict,
    OwnerTruthSourceCommandResult,
    OwnerTruthSourceWriteRecord,
)


class OwnerTruthSourceCommandStore(Protocol):
    def create_owner_truth_source(
        self,
        record: OwnerTruthSourceWriteRecord,
    ) -> OwnerTruthSourceCommandResult:
        ...


class OwnerTruthSourceCommandService:
    def __init__(self, store: OwnerTruthSourceCommandStore):
        self._store = store

    def create_text_source(
        self,
        *,
        command: CreateTextSourceCommand,
        context: OwnerTruthCommandContext,
    ) -> OwnerTruthSourceCommandResult:
        return self._store.create_owner_truth_source(command.write_record(context=context))


@dataclass(frozen=True)
class OwnerTruthArchiveShadowResult:
    status: str
    reason: str | None = None
    source_id: str | None = None
    receipt_id: str | None = None

    def public_contract(self) -> dict[str, str]:
        result = {"status": self.status}
        if self.reason:
            result["reason"] = self.reason
        if self.source_id:
            result["sourceId"] = self.source_id
        if self.receipt_id:
            result["receiptId"] = self.receipt_id
        return result


class ArchiveOwnerTruthCompatibilityFacade:
    """Mirrors eligible legacy text Archive items into non-authoritative Source rows.

    Photos and other media have no server object source in this slice.  They are
    explicitly skipped so metadata sync cannot be mistaken for uploaded media.
    """

    _TEXT_KINDS = frozenset({"text", "textnote"})

    def __init__(self, store: OwnerTruthSourceCommandStore):
        self._service = OwnerTruthSourceCommandService(store)

    def shadow_archive_item(
        self,
        *,
        owner_subject_id: str,
        item: Mapping[str, Any],
    ) -> OwnerTruthArchiveShadowResult:
        raw_kind = str(item.get("kind") or "").strip().lower()
        if raw_kind not in self._TEXT_KINDS:
            return OwnerTruthArchiveShadowResult(status="skipped", reason="localOnlyMedia")

        archive_item_id = str(item.get("id") or "").strip()
        text = str(item.get("note") or "").strip() or str(item.get("title") or "").strip()
        if not archive_item_id or not text:
            return OwnerTruthArchiveShadowResult(status="skipped", reason="emptyText")

        normalized_owner = str(owner_subject_id or "").strip()
        source_id = str(
            uuid5(
                NAMESPACE_URL,
                f"dreamjourney-owner-truth-archive-shadow:{normalized_owner}:{archive_item_id}",
            )
        )
        command = CreateTextSourceCommand(
            command_id=f"archive-shadow:{normalized_owner}:{archive_item_id}",
            source_id=source_id,
            expected_version=0,
            text=text,
            metadata={
                "origin": "archiveCompatibilityFacade",
                "legacyArchiveItemId": archive_item_id,
                "legacyArchiveKind": raw_kind,
                "title": str(item.get("title") or "").strip()[:256],
            },
        )
        context = OwnerTruthCommandContext(
            vault_id=normalized_owner,
            owner_subject_id=normalized_owner,
            actor_subject_id=normalized_owner,
        )
        try:
            result = self._service.create_text_source(command=command, context=context)
        except OwnerTruthSourceCommandConflict:
            return OwnerTruthArchiveShadowResult(
                status="conflicted",
                reason="immutableSourceContent",
                source_id=source_id,
            )
        except Exception:
            # Shadow is observable but not authoritative.  The legacy Archive
            # request remains usable while later reconciliation can retry it.
            return OwnerTruthArchiveShadowResult(status="deferred", reason="shadowWriteFailed")
        return OwnerTruthArchiveShadowResult(
            status=result.outcome,
            source_id=result.source_id,
            receipt_id=result.receipt_id,
        )


__all__ = [
    "ArchiveOwnerTruthCompatibilityFacade",
    "OwnerTruthArchiveShadowResult",
    "OwnerTruthSourceCommandService",
]
