"""Owner-only publication authority for the default-off M2 lane.

The service deliberately treats an Owner Truth MemoryVersion as an authority
anchor, not as a public payload source.  The Owner supplies a dedicated public
copy for a draft; after a second confirmation that copy is written to the
separate publication data plane.  Private projections, KBLite, object URLs,
and raw Source payloads are never inputs to this writer.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid5

from app.domain.owner_truth.contracts import OwnerTruthContractError, require_nonblank, require_uuid
from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


PUBLICATION_AUTHORITY_SCHEMA_VERSION = "publication-authority-v1"
PUBLICATION_AUTHORITY_MULTI_ITEM_SCHEMA_VERSION = "publication-authority-v2"
PUBLICATION_AUTHORITY_REVISION_SCHEMA_VERSION = "publication-authority-v3"
PUBLICATION_AUTHORITY_MAX_ITEMS = 20
PUBLICATION_AI_DISCLOSURE = "该内容由人工智能协助整理，已由发布者确认。"
_NAMESPACE = UUID("cde4f1a3-13f4-47ee-bc01-3cfbdf1e1ac5")
_HASH_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_ID_PATTERN = re.compile(r"(?<!\d)\d{15,18}[0-9Xx](?!\d)")


class PublicationAuthorityError(OwnerTruthContractError):
    """A publication authority command cannot be executed safely."""


class PublicationAuthorityDisabled(PublicationAuthorityError):
    """The default-off publication writer was not admitted by its route gate."""


class PublicationAuthorityAccessDenied(PublicationAuthorityError):
    """A caller is not the active Owner for the selected publication scope."""


class PublicationAuthorityNotPublishable(PublicationAuthorityError):
    """The selected version or draft does not meet the M2 publication policy."""


class PublicationAuthorityConflict(PublicationAuthorityError):
    """A command replay or confirmation did not bind the original draft."""


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise PublicationAuthorityError("publication command must be JSON serializable") from exc


def _digest(value: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _require_digest(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip()
    if _HASH_PATTERN.fullmatch(normalized) is None:
        raise PublicationAuthorityError(f"{field} must be a SHA-256 digest")
    return normalized


def _bounded_text(value: Any, *, field: str, maximum: int) -> str:
    normalized = require_nonblank(str(value or ""), field=field)
    if len(normalized) > maximum:
        raise PublicationAuthorityError(f"{field} exceeds maximum length")
    return normalized


def _assert_owner_context(context: OwnerTruthCommandContext) -> None:
    if not isinstance(context, OwnerTruthCommandContext):
        raise PublicationAuthorityAccessDenied("Owner command context is required")
    if context.actor_subject_id != context.owner_subject_id:
        raise PublicationAuthorityAccessDenied("only the Vault Owner may manage publication drafts")


def _redact_preview(value: str) -> str:
    redacted = _PHONE_PATTERN.sub("***", value)
    redacted = _EMAIL_PATTERN.sub("***", redacted)
    return _ID_PATTERN.sub("***", redacted)


def _assert_public_copy_has_no_direct_identifiers(*, title: str, body: str) -> None:
    """Keep direct identifiers out of the public data plane, not just its preview."""

    combined = f"{title}\n{body}"
    if _PHONE_PATTERN.search(combined) is not None:
        raise PublicationAuthorityNotPublishable(
            "public copy contains a phone number; complete redaction before creating a draft"
        )
    if _EMAIL_PATTERN.search(combined) is not None:
        raise PublicationAuthorityNotPublishable(
            "public copy contains an email address; complete redaction before creating a draft"
        )
    if _ID_PATTERN.search(combined) is not None:
        raise PublicationAuthorityNotPublishable(
            "public copy contains an identity number; complete redaction before creating a draft"
        )


def _int_or_missing(value: Any) -> int:
    """Preserve zero-valued authority epochs while rejecting an absent value."""

    return -1 if value is None else int(value)


@dataclass(frozen=True)
class PublicationAuthorityMemoryVersion:
    """Canonical, command-time facts needed to admit one selected version."""

    memory_version_id: str
    memory_id: str
    vault_id: str
    owner_subject_id: str
    authority_epoch: int
    content_hash: str
    is_current: bool
    memory_state: str
    source_state: str
    decision: str | None
    decision_receipt_id: str | None
    third_party_review_required: bool = False

    def __post_init__(self) -> None:
        for field in ("memory_version_id", "memory_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        object.__setattr__(self, "vault_id", require_nonblank(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            require_nonblank(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "content_hash", _require_digest(self.content_hash, field="content_hash"))
        if not isinstance(self.authority_epoch, int) or self.authority_epoch < 0:
            raise PublicationAuthorityError("authority_epoch must be a non-negative integer")
        if self.decision_receipt_id is not None:
            object.__setattr__(
                self,
                "decision_receipt_id",
                require_uuid(self.decision_receipt_id, field="decision_receipt_id"),
            )


@dataclass(frozen=True)
class PublicationDraftItemCommand:
    memory_version_id: str
    public_title: str
    public_body: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "memory_version_id",
            require_uuid(self.memory_version_id, field="memory_version_id"),
        )
        object.__setattr__(
            self,
            "public_title",
            _bounded_text(self.public_title, field="public_title", maximum=120),
        )
        object.__setattr__(
            self,
            "public_body",
            _bounded_text(self.public_body, field="public_body", maximum=12_000),
        )
        _assert_public_copy_has_no_direct_identifiers(
            title=self.public_title,
            body=self.public_body,
        )

    def payload(self) -> Mapping[str, Any]:
        return {
            "memoryVersionId": self.memory_version_id,
            "publicTitle": self.public_title,
            "publicBody": self.public_body,
        }


@dataclass(frozen=True)
class PublicationDraftCommand:
    command_id: str
    memory_version_id: str | None = None
    public_title: str | None = None
    public_body: str | None = None
    items: tuple[PublicationDraftItemCommand, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", require_uuid(self.command_id, field="command_id"))
        normalized_items = tuple(self.items)
        legacy_values = (self.memory_version_id, self.public_title, self.public_body)
        has_any_legacy_value = any(value is not None for value in legacy_values)
        if normalized_items and has_any_legacy_value:
            raise PublicationAuthorityConflict(
                "publication draft must use either legacy fields or ordered items"
            )
        if not normalized_items:
            if not all(value is not None for value in legacy_values):
                raise PublicationAuthorityError("publication draft items are required")
            normalized_items = (
                PublicationDraftItemCommand(
                    memory_version_id=str(self.memory_version_id),
                    public_title=str(self.public_title),
                    public_body=str(self.public_body),
                ),
            )
        if not 1 <= len(normalized_items) <= PUBLICATION_AUTHORITY_MAX_ITEMS:
            raise PublicationAuthorityError(
                f"publication draft must contain between 1 and {PUBLICATION_AUTHORITY_MAX_ITEMS} items"
            )
        if not all(isinstance(item, PublicationDraftItemCommand) for item in normalized_items):
            raise PublicationAuthorityError("publication draft items are invalid")
        memory_version_ids = [item.memory_version_id for item in normalized_items]
        if len(set(memory_version_ids)) != len(memory_version_ids):
            raise PublicationAuthorityConflict(
                "a MemoryVersion cannot appear more than once in a publication draft"
            )
        object.__setattr__(self, "items", normalized_items)
        first = normalized_items[0]
        object.__setattr__(self, "memory_version_id", first.memory_version_id)
        object.__setattr__(self, "public_title", first.public_title)
        object.__setattr__(self, "public_body", first.public_body)

    @property
    def schema_version(self) -> str:
        return (
            PUBLICATION_AUTHORITY_SCHEMA_VERSION
            if len(self.items) == 1
            else PUBLICATION_AUTHORITY_MULTI_ITEM_SCHEMA_VERSION
        )

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)

    @property
    def payload_hash(self) -> str:
        if self.schema_version == PUBLICATION_AUTHORITY_SCHEMA_VERSION:
            first = self.items[0]
            return _digest(
                {
                    "schemaVersion": PUBLICATION_AUTHORITY_SCHEMA_VERSION,
                    "memoryVersionId": first.memory_version_id,
                    "publicTitle": first.public_title,
                    "publicBody": first.public_body,
                }
            )
        return _digest(
            {
                "schemaVersion": PUBLICATION_AUTHORITY_MULTI_ITEM_SCHEMA_VERSION,
                "items": [item.payload() for item in self.items],
            }
        )


@dataclass(frozen=True)
class PublicationRevisionDraftItemCommand:
    item_index: int
    public_title: str
    public_body: str

    def __post_init__(self) -> None:
        if not isinstance(self.item_index, int) or isinstance(self.item_index, bool) or self.item_index < 0:
            raise PublicationAuthorityError("item_index must be a non-negative integer")
        object.__setattr__(
            self,
            "public_title",
            _bounded_text(self.public_title, field="public_title", maximum=120),
        )
        object.__setattr__(
            self,
            "public_body",
            _bounded_text(self.public_body, field="public_body", maximum=12_000),
        )
        _assert_public_copy_has_no_direct_identifiers(
            title=self.public_title,
            body=self.public_body,
        )

    def payload(self) -> Mapping[str, Any]:
        return {
            "itemIndex": self.item_index,
            "publicTitle": self.public_title,
            "publicBody": self.public_body,
        }


@dataclass(frozen=True)
class PublicationRevisionDraftCommand:
    command_id: str
    publication_id: str
    expected_publication_version_id: str
    expected_publication_version: int
    items: tuple[PublicationRevisionDraftItemCommand, ...]

    def __post_init__(self) -> None:
        for field in ("command_id", "publication_id", "expected_publication_version_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        if (
            not isinstance(self.expected_publication_version, int)
            or isinstance(self.expected_publication_version, bool)
            or self.expected_publication_version < 1
        ):
            raise PublicationAuthorityError(
                "expected_publication_version must be a positive integer"
            )
        normalized_items = tuple(self.items)
        if not 1 <= len(normalized_items) <= PUBLICATION_AUTHORITY_MAX_ITEMS:
            raise PublicationAuthorityError(
                f"publication revision must contain between 1 and {PUBLICATION_AUTHORITY_MAX_ITEMS} items"
            )
        if not all(isinstance(item, PublicationRevisionDraftItemCommand) for item in normalized_items):
            raise PublicationAuthorityError("publication revision items are invalid")
        if [item.item_index for item in normalized_items] != list(range(len(normalized_items))):
            raise PublicationAuthorityConflict(
                "publication revision items must preserve the current contiguous order"
            )
        object.__setattr__(self, "items", normalized_items)

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "schemaVersion": PUBLICATION_AUTHORITY_REVISION_SCHEMA_VERSION,
                "publicationId": self.publication_id,
                "expectedPublicationVersionId": self.expected_publication_version_id,
                "expectedPublicationVersion": self.expected_publication_version,
                "items": [item.payload() for item in self.items],
            }
        )


@dataclass(frozen=True)
class PublicationConfirmCommand:
    command_id: str
    publication_id: str
    draft_id: str
    expected_draft_revision: int
    expected_draft_snapshot_hash: str
    second_confirmation: bool

    def __post_init__(self) -> None:
        for field in ("command_id", "publication_id", "draft_id"):
            object.__setattr__(self, field, require_uuid(getattr(self, field), field=field))
        if not isinstance(self.expected_draft_revision, int) or self.expected_draft_revision < 1:
            raise PublicationAuthorityError("expected_draft_revision must be a positive integer")
        object.__setattr__(
            self,
            "expected_draft_snapshot_hash",
            _require_digest(
                self.expected_draft_snapshot_hash,
                field="expected_draft_snapshot_hash",
            ),
        )
        if self.second_confirmation is not True:
            raise PublicationAuthorityNotPublishable("a second explicit confirmation is required")

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)

    @property
    def payload_hash(self) -> str:
        return _digest(
            {
                "schemaVersion": PUBLICATION_AUTHORITY_SCHEMA_VERSION,
                "publicationId": self.publication_id,
                "draftId": self.draft_id,
                "expectedDraftRevision": self.expected_draft_revision,
                "expectedDraftSnapshotHash": self.expected_draft_snapshot_hash,
                "secondConfirmation": True,
            }
        )


@dataclass(frozen=True)
class PublicationDraftItemResult:
    item_index: int
    memory_version_id: str
    item_snapshot_hash: str
    preview_title: str
    preview_body: str
    third_party_review_required: bool


@dataclass(frozen=True)
class PublicationDraftResult:
    outcome: str
    publication_id: str
    draft_id: str
    expected_draft_revision: int
    draft_snapshot_hash: str
    preview_title: str
    preview_body: str
    state: str
    second_confirmation_required: bool
    third_party_review_required: bool
    items: tuple[PublicationDraftItemResult, ...] = ()
    schema_version: str = PUBLICATION_AUTHORITY_SCHEMA_VERSION
    base_publication_version_id: str | None = None
    target_publication_version: int = 1

    @property
    def item_count(self) -> int:
        return len(self.items) if self.items else 1


@dataclass(frozen=True)
class PublicationConfirmResult:
    outcome: str
    publication_id: str
    draft_id: str
    publication_version_id: str
    publication_version: int
    publication_state: str
    projection_state: str
    public_projection_hash: str
    ai_disclosure_required: bool
    item_count: int = 1
    public_projection_item_hashes: tuple[str, ...] = ()
    schema_version: str = PUBLICATION_AUTHORITY_SCHEMA_VERSION


@dataclass(frozen=True)
class _PreparedPublicationDraftItem:
    item_index: int
    command: PublicationDraftItemCommand
    memory: PublicationAuthorityMemoryVersion
    public_content_hash: str
    preview_title: str
    preview_body: str
    preview_hash: str
    redaction_diff_hash: str | None
    item_snapshot_hash: str

    def result(self) -> PublicationDraftItemResult:
        return PublicationDraftItemResult(
            item_index=self.item_index,
            memory_version_id=self.memory.memory_version_id,
            item_snapshot_hash=self.item_snapshot_hash,
            preview_title=self.preview_title,
            preview_body=self.preview_body,
            third_party_review_required=self.memory.third_party_review_required,
        )


def _prepare_publication_draft_item(
    *,
    item_index: int,
    command: PublicationDraftItemCommand,
    memory: PublicationAuthorityMemoryVersion,
) -> _PreparedPublicationDraftItem:
    public_content_hash = _digest(
        {
            "title": command.public_title,
            "body": command.public_body,
            "aiDisclosure": PUBLICATION_AI_DISCLOSURE,
        }
    )
    preview_title = _redact_preview(command.public_title)
    preview_body = _redact_preview(command.public_body)
    preview_hash = _digest(
        {
            "title": preview_title,
            "body": preview_body,
            "aiDisclosure": PUBLICATION_AI_DISCLOSURE,
        }
    )
    redaction_diff_hash = (
        None
        if preview_title == command.public_title and preview_body == command.public_body
        else _digest(
            {
                "contentHash": public_content_hash,
                "previewHash": preview_hash,
            }
        )
    )
    item_snapshot_hash = _digest(
        {
            "schemaVersion": PUBLICATION_AUTHORITY_MULTI_ITEM_SCHEMA_VERSION,
            "itemIndex": item_index,
            "memoryVersionId": memory.memory_version_id,
            "memoryContentHash": memory.content_hash,
            "publicContentHash": public_content_hash,
            "thirdPartyReviewRequired": memory.third_party_review_required,
        }
    )
    return _PreparedPublicationDraftItem(
        item_index=item_index,
        command=command,
        memory=memory,
        public_content_hash=public_content_hash,
        preview_title=preview_title,
        preview_body=preview_body,
        preview_hash=preview_hash,
        redaction_diff_hash=redaction_diff_hash,
        item_snapshot_hash=item_snapshot_hash,
    )


def _publication_draft_snapshot_hash(
    *,
    vault_id: str,
    schema_version: str,
    items: tuple[_PreparedPublicationDraftItem, ...],
) -> str:
    if schema_version == PUBLICATION_AUTHORITY_SCHEMA_VERSION and len(items) == 1:
        item = items[0]
        return _digest(
            {
                "schemaVersion": PUBLICATION_AUTHORITY_SCHEMA_VERSION,
                "vaultId": vault_id,
                "memoryVersionId": item.memory.memory_version_id,
                "memoryContentHash": item.memory.content_hash,
                "publicContentHash": item.public_content_hash,
                "thirdPartyReviewRequired": item.memory.third_party_review_required,
            }
        )
    return _digest(
        {
            "schemaVersion": schema_version,
            "vaultId": vault_id,
            "items": [
                {
                    "itemIndex": item.item_index,
                    "itemSnapshotHash": item.item_snapshot_hash,
                }
                for item in items
            ],
        }
    )


def _publication_revision_draft_snapshot_hash(
    *,
    vault_id: str,
    publication_id: str,
    base_publication_version_id: str,
    base_publication_version: int,
    items: tuple[_PreparedPublicationDraftItem, ...],
) -> str:
    return _digest(
        {
            "schemaVersion": PUBLICATION_AUTHORITY_REVISION_SCHEMA_VERSION,
            "vaultId": vault_id,
            "publicationId": publication_id,
            "basePublicationVersionId": base_publication_version_id,
            "basePublicationVersion": base_publication_version,
            "items": [
                {
                    "itemIndex": item.item_index,
                    "itemSnapshotHash": item.item_snapshot_hash,
                }
                for item in items
            ],
        }
    )


@dataclass(frozen=True)
class PublicationOwnerPublicationSummary:
    """Owner-only, redacted management facts for one M2 publication.

    This is intentionally a read-model summary rather than a MemoryVersion or
    Source projection. It contains only the independently supplied public
    preview and lifecycle state needed by the closed-beta management surface.
    """

    publication_id: str
    publication_version_id: str | None
    draft_id: str
    draft_revision: int
    lifecycle_authority_epoch: int
    publication_state: str
    projection_state: str | None
    preview_title: str
    preview_body: str
    requires_second_confirmation: bool
    third_party_review_required: bool
    ai_disclosure_required: bool


@dataclass(frozen=True)
class PublicationOwnerVersionItemSummary:
    item_index: int
    public_title: str
    public_body: str
    ai_disclosure_required: bool


@dataclass(frozen=True)
class PublicationOwnerVersionSummary:
    publication_version_id: str
    version_number: int
    confirmed_at: datetime
    projection_state: str | None
    public_snapshot_hash: str
    items: tuple[PublicationOwnerVersionItemSummary, ...]


class PublicationAuthorityRepository(Protocol):
    def create_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationDraftCommand,
    ) -> PublicationDraftResult: ...

    def create_revision_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationRevisionDraftCommand,
    ) -> PublicationDraftResult: ...

    def confirm_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationConfirmCommand,
    ) -> PublicationConfirmResult: ...

    def list_owner_publications(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[PublicationOwnerPublicationSummary, ...]: ...

    def list_owner_publication_versions(
        self,
        *,
        context: OwnerTruthCommandContext,
        publication_id: str,
    ) -> tuple[PublicationOwnerVersionSummary, ...]: ...


class PublicationAuthorityService:
    """Small route-facing facade; UoW ownership stays with the caller."""

    def __init__(self, repository: PublicationAuthorityRepository, *, enabled: bool = False) -> None:
        self._repository = repository
        self._enabled = bool(enabled)

    def create_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationDraftCommand,
    ) -> PublicationDraftResult:
        _assert_owner_context(context)
        if not self._enabled:
            raise PublicationAuthorityDisabled("publication authority is default-off")
        if not isinstance(command, PublicationDraftCommand):
            raise PublicationAuthorityError("publication draft command is required")
        return self._repository.create_draft(context=context, command=command)

    def create_revision_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationRevisionDraftCommand,
    ) -> PublicationDraftResult:
        _assert_owner_context(context)
        if not self._enabled:
            raise PublicationAuthorityDisabled("publication authority is default-off")
        if not isinstance(command, PublicationRevisionDraftCommand):
            raise PublicationAuthorityError("publication revision command is required")
        return self._repository.create_revision_draft(context=context, command=command)

    def confirm_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationConfirmCommand,
    ) -> PublicationConfirmResult:
        _assert_owner_context(context)
        if not self._enabled:
            raise PublicationAuthorityDisabled("publication authority is default-off")
        if not isinstance(command, PublicationConfirmCommand):
            raise PublicationAuthorityError("publication confirmation command is required")
        return self._repository.confirm_draft(context=context, command=command)

    def list_owner_publications(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[PublicationOwnerPublicationSummary, ...]:
        _assert_owner_context(context)
        if not self._enabled:
            raise PublicationAuthorityDisabled("publication authority is default-off")
        return self._repository.list_owner_publications(context=context)

    def list_owner_publication_versions(
        self,
        *,
        context: OwnerTruthCommandContext,
        publication_id: str,
    ) -> tuple[PublicationOwnerVersionSummary, ...]:
        _assert_owner_context(context)
        if not self._enabled:
            raise PublicationAuthorityDisabled("publication authority is default-off")
        return self._repository.list_owner_publication_versions(
            context=context,
            publication_id=require_uuid(publication_id, field="publication_id"),
        )


class InMemoryPublicationAuthorityRepository:
    """Semantic double for route and contract tests.

    Production must use the Postgres counterpart, which re-reads canonical
    Owner Truth rows in the active request UoW.  This double is intentionally
    explicit about test data seeding so it cannot silently become an authority
    source for the app.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._vault_owners: dict[str, str] = {}
        self._memory_versions: dict[str, PublicationAuthorityMemoryVersion] = {}
        self._drafts: dict[str, dict[str, Any]] = {}
        self._command_results: dict[tuple[str, str, str], tuple[str, Any]] = {}
        self._public_projections: dict[str, dict[str, Any]] = {}

    def seed_memory_version(self, value: PublicationAuthorityMemoryVersion) -> None:
        if not isinstance(value, PublicationAuthorityMemoryVersion):
            raise TypeError("PublicationAuthorityMemoryVersion is required")
        with self._lock:
            known_owner = self._vault_owners.get(value.vault_id)
            if known_owner is not None and known_owner != value.owner_subject_id:
                raise PublicationAuthorityAccessDenied(
                    "publication Vault owner cannot change in the in-memory contract double"
                )
            self._vault_owners[value.vault_id] = value.owner_subject_id
            self._memory_versions[value.memory_version_id] = value

    def owner_vault_scope_snapshot(self, vault_id: str) -> Mapping[str, Any] | None:
        """Return only the canonical owner fact needed by adjacent QA doubles."""

        with self._lock:
            owner_subject_id = self._vault_owners.get(vault_id)
            if owner_subject_id is None:
                return None
            return {
                "vaultId": vault_id,
                "ownerSubjectId": owner_subject_id,
                "status": "active",
            }

    def memory_versions(self) -> tuple[PublicationAuthorityMemoryVersion, ...]:
        with self._lock:
            return tuple(deepcopy(item) for item in self._memory_versions.values())

    def public_projection_count(self) -> int:
        with self._lock:
            return len(self._public_projections)

    def public_projection_scope_snapshot(
        self,
        publication_id: str,
        publication_version_id: str,
    ) -> Mapping[str, Any] | None:
        """Expose only QA access scope, never private memory content."""

        with self._lock:
            for projection in self._public_projections.values():
                if (
                    projection.get("publicationId") == publication_id
                    and projection.get("publicationVersionId") == publication_version_id
                ):
                    return deepcopy(projection)
        return None

    def public_projection_content_snapshot(
        self,
        publication_id: str,
        publication_version_id: str,
    ) -> Mapping[str, Any] | None:
        """Return the independently stored public copy, never Owner Truth content."""

        with self._lock:
            for projection in self._public_projections.values():
                if (
                    projection.get("publicationId") == publication_id
                    and projection.get("publicationVersionId") == publication_version_id
                ):
                    return deepcopy(
                        {
                            "publicationId": projection["publicationId"],
                            "publicationVersionId": projection["publicationVersionId"],
                            "projectionState": projection["projectionState"],
                            "displayTitle": projection["displayTitle"],
                            "displayBody": projection["displayBody"],
                            "aiDisclosure": projection["aiDisclosure"],
                            "projectionHash": projection["projectionHash"],
                            "publicCitationHash": projection["publicCitationHash"],
                            "items": deepcopy(projection.get("items") or []),
                        }
                    )
        return None

    def apply_lifecycle_transition(
        self,
        *,
        context: OwnerTruthCommandContext,
        publication_id: str,
        expected_authority_epoch: int,
        action: str,
    ) -> Mapping[str, Any]:
        """Apply the local access-deny state for the lifecycle QA double.

        This is intentionally an internal collaborator for the lifecycle
        execution repository. It exposes only opaque scope and state values;
        public copy and private Memory data remain unavailable to lifecycle
        commands.
        """

        _assert_owner_context(context)
        if action not in {"withdraw", "suspend"}:
            raise PublicationAuthorityConflict("publication lifecycle action is invalid")
        if isinstance(expected_authority_epoch, bool) or not isinstance(
            expected_authority_epoch, int
        ) or expected_authority_epoch < 0:
            raise PublicationAuthorityConflict("publication authority epoch is invalid")
        with self._lock:
            self._assert_active_vault(context)
            projection = next(
                (
                    item
                    for item in self._public_projections.values()
                    if item.get("publicationId") == publication_id
                    and item.get("vaultId") == context.vault_id
                ),
                None,
            )
            if projection is None:
                raise PublicationAuthorityAccessDenied(
                    "publication is not available in this Owner Vault"
                )
            if str(projection.get("ownerSubjectId") or "") != context.owner_subject_id:
                raise PublicationAuthorityAccessDenied(
                    "publication is not available in this Owner Vault"
                )
            authority_epoch = int(projection.get("authorityEpoch") or 0)
            if authority_epoch != expected_authority_epoch:
                raise PublicationAuthorityConflict("publication authority epoch has changed")
            if (
                str(projection.get("publicationState") or "") != "confirmed"
                or str(projection.get("projectionState") or "") != "active"
            ):
                raise PublicationAuthorityNotPublishable("publication is no longer active")
            target_state = "withdrawn" if action == "withdraw" else "suspended"
            projection["publicationState"] = target_state
            projection["projectionState"] = target_state
            projection["conflictHold"] = action == "suspend"
            projection["blockReasonCode"] = (
                "ownerWithdrawal" if action == "withdraw" else "thirdPartyObjection"
            )
            return {
                "publicationVersionId": str(projection["publicationVersionId"]),
                "publicationState": target_state,
                "projectionState": target_state,
                "conflictHold": bool(projection["conflictHold"]),
            }

    def create_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationDraftCommand,
    ) -> PublicationDraftResult:
        _assert_owner_context(context)
        with self._lock:
            self._assert_active_vault(context)
            replay_key = ("draftCreate", context.vault_id, command.command_id_hash)
            replay = self._command_results.get(replay_key)
            if replay is not None:
                payload_hash, result = replay
                if payload_hash != command.payload_hash:
                    raise PublicationAuthorityConflict("commandId cannot be reused with a different draft")
                return PublicationDraftResult(**{**result.__dict__, "outcome": "deduplicated"})
            items = tuple(
                _prepare_publication_draft_item(
                    item_index=item_index,
                    command=item_command,
                    memory=self._publishable_memory(
                        context=context,
                        memory_version_id=item_command.memory_version_id,
                    ),
                )
                for item_index, item_command in enumerate(command.items)
            )
            first_item = items[0]
            publication_id = str(uuid5(_NAMESPACE, f"publication:{context.vault_id}:{command.command_id_hash}"))
            draft_id = str(uuid5(_NAMESPACE, f"publication-draft:{context.vault_id}:{command.command_id_hash}"))
            draft_snapshot_hash = _publication_draft_snapshot_hash(
                vault_id=context.vault_id,
                schema_version=command.schema_version,
                items=items,
            )
            result = PublicationDraftResult(
                outcome="created",
                publication_id=publication_id,
                draft_id=draft_id,
                expected_draft_revision=1,
                draft_snapshot_hash=draft_snapshot_hash,
                preview_title=first_item.preview_title,
                preview_body=first_item.preview_body,
                state="draft",
                second_confirmation_required=True,
                third_party_review_required=any(
                    item.memory.third_party_review_required for item in items
                ),
                items=tuple(item.result() for item in items),
                schema_version=command.schema_version,
            )
            self._drafts[draft_id] = {
                "publicationId": publication_id,
                "draftResult": result,
                "memoryVersionId": first_item.memory.memory_version_id,
                "publicTitle": first_item.command.public_title,
                "publicBody": first_item.command.public_body,
                "publicContentHash": first_item.public_content_hash,
                "thirdPartyReviewRequired": any(
                    item.memory.third_party_review_required for item in items
                ),
                "items": [
                    {
                        "itemIndex": item.item_index,
                        "memoryVersionId": item.memory.memory_version_id,
                        "memoryContentHash": item.memory.content_hash,
                        "publicTitle": item.command.public_title,
                        "publicBody": item.command.public_body,
                        "publicContentHash": item.public_content_hash,
                        "previewTitle": item.preview_title,
                        "previewBody": item.preview_body,
                        "previewHash": item.preview_hash,
                        "redactionDiffHash": item.redaction_diff_hash,
                        "itemSnapshotHash": item.item_snapshot_hash,
                        "thirdPartyReviewRequired": item.memory.third_party_review_required,
                    }
                    for item in items
                ],
                "ownerSubjectId": context.owner_subject_id,
                "vaultId": context.vault_id,
                "state": "draft",
            }
            self._command_results[replay_key] = (command.payload_hash, result)
            return result

    def create_revision_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationRevisionDraftCommand,
    ) -> PublicationDraftResult:
        _assert_owner_context(context)
        with self._lock:
            self._assert_active_vault(context)
            replay_key = ("draftCreate", context.vault_id, command.command_id_hash)
            replay = self._command_results.get(replay_key)
            if replay is not None:
                payload_hash, result = replay
                if payload_hash != command.payload_hash:
                    raise PublicationAuthorityConflict(
                        "commandId cannot be reused with a different draft"
                    )
                return PublicationDraftResult(**{**result.__dict__, "outcome": "deduplicated"})
            active_versions = [
                projection
                for projection in self._public_projections.values()
                if projection.get("publicationId") == command.publication_id
                and projection.get("vaultId") == context.vault_id
                and projection.get("ownerSubjectId") == context.owner_subject_id
                and projection.get("publicationState") == "confirmed"
                and projection.get("projectionState") == "active"
            ]
            if len(active_versions) != 1:
                raise PublicationAuthorityNotPublishable(
                    "publication has no unique active version to revise"
                )
            active_version = active_versions[0]
            if (
                str(active_version["publicationVersionId"])
                != command.expected_publication_version_id
                or int(active_version.get("versionNumber") or 0)
                != command.expected_publication_version
            ):
                raise PublicationAuthorityConflict("publication version has changed")
            source_draft = next(
                (
                    draft
                    for draft in self._drafts.values()
                    if draft.get("publicationId") == command.publication_id
                    and draft.get("publicationVersionId")
                    == command.expected_publication_version_id
                    and draft.get("state") == "confirmed"
                ),
                None,
            )
            if source_draft is None:
                raise PublicationAuthorityConflict(
                    "current publication version has no immutable source snapshot"
                )
            source_items = tuple(source_draft.get("items") or ())
            if len(source_items) != len(command.items):
                raise PublicationAuthorityConflict(
                    "publication revision must preserve the current item set"
                )
            items = tuple(
                _prepare_publication_draft_item(
                    item_index=item_command.item_index,
                    command=PublicationDraftItemCommand(
                        memory_version_id=str(source_item["memoryVersionId"]),
                        public_title=item_command.public_title,
                        public_body=item_command.public_body,
                    ),
                    memory=self._publishable_memory(
                        context=context,
                        memory_version_id=str(source_item["memoryVersionId"]),
                    ),
                )
                for source_item, item_command in zip(source_items, command.items)
            )
            if any(
                str(source_item["memoryContentHash"]) != item.memory.content_hash
                for source_item, item in zip(source_items, items)
            ):
                raise PublicationAuthorityConflict(
                    "publication source authority changed before revision"
                )
            first_item = items[0]
            draft_id = str(
                uuid5(
                    _NAMESPACE,
                    f"publication-revision-draft:{context.vault_id}:{command.command_id_hash}",
                )
            )
            target_version = command.expected_publication_version + 1
            draft_snapshot_hash = _publication_revision_draft_snapshot_hash(
                vault_id=context.vault_id,
                publication_id=command.publication_id,
                base_publication_version_id=command.expected_publication_version_id,
                base_publication_version=command.expected_publication_version,
                items=items,
            )
            result = PublicationDraftResult(
                outcome="created",
                publication_id=command.publication_id,
                draft_id=draft_id,
                expected_draft_revision=1,
                draft_snapshot_hash=draft_snapshot_hash,
                preview_title=first_item.preview_title,
                preview_body=first_item.preview_body,
                state="draft",
                second_confirmation_required=True,
                third_party_review_required=any(
                    item.memory.third_party_review_required for item in items
                ),
                items=tuple(item.result() for item in items),
                schema_version=PUBLICATION_AUTHORITY_REVISION_SCHEMA_VERSION,
                base_publication_version_id=command.expected_publication_version_id,
                target_publication_version=target_version,
            )
            self._drafts[draft_id] = {
                "publicationId": command.publication_id,
                "draftResult": result,
                "memoryVersionId": first_item.memory.memory_version_id,
                "publicTitle": first_item.command.public_title,
                "publicBody": first_item.command.public_body,
                "publicContentHash": first_item.public_content_hash,
                "thirdPartyReviewRequired": result.third_party_review_required,
                "items": [
                    {
                        "itemIndex": item.item_index,
                        "memoryVersionId": item.memory.memory_version_id,
                        "memoryContentHash": item.memory.content_hash,
                        "publicTitle": item.command.public_title,
                        "publicBody": item.command.public_body,
                        "publicContentHash": item.public_content_hash,
                        "previewTitle": item.preview_title,
                        "previewBody": item.preview_body,
                        "previewHash": item.preview_hash,
                        "redactionDiffHash": item.redaction_diff_hash,
                        "itemSnapshotHash": item.item_snapshot_hash,
                        "thirdPartyReviewRequired": item.memory.third_party_review_required,
                    }
                    for item in items
                ],
                "ownerSubjectId": context.owner_subject_id,
                "vaultId": context.vault_id,
                "state": "draft",
                "basePublicationVersionId": command.expected_publication_version_id,
                "targetPublicationVersion": target_version,
            }
            self._command_results[replay_key] = (command.payload_hash, result)
            return result

    def confirm_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationConfirmCommand,
    ) -> PublicationConfirmResult:
        _assert_owner_context(context)
        with self._lock:
            self._assert_active_vault(context)
            replay_key = ("draftConfirm", context.vault_id, command.command_id_hash)
            replay = self._command_results.get(replay_key)
            if replay is not None:
                payload_hash, result = replay
                if payload_hash != command.payload_hash:
                    raise PublicationAuthorityConflict("commandId cannot be reused with a different confirmation")
                return PublicationConfirmResult(**{**result.__dict__, "outcome": "deduplicated"})
            draft = self._drafts.get(command.draft_id)
            if draft is None:
                raise PublicationAuthorityAccessDenied("publication draft is not available in this Owner Vault")
            if (
                draft["vaultId"] != context.vault_id
                or draft["ownerSubjectId"] != context.owner_subject_id
                or draft["publicationId"] != command.publication_id
            ):
                raise PublicationAuthorityAccessDenied("publication draft is not available in this Owner Vault")
            draft_result: PublicationDraftResult = draft["draftResult"]
            if draft["state"] != "draft":
                raise PublicationAuthorityConflict("publication draft is no longer confirmable")
            if (
                command.expected_draft_revision != draft_result.expected_draft_revision
                or command.expected_draft_snapshot_hash != draft_result.draft_snapshot_hash
            ):
                raise PublicationAuthorityConflict("publication draft snapshot has changed")
            draft_items = tuple(draft.get("items") or ())
            memories = tuple(
                self._publishable_memory(
                    context=context,
                    memory_version_id=str(item["memoryVersionId"]),
                )
                for item in draft_items
            )
            if bool(draft["thirdPartyReviewRequired"]) or any(
                memory.third_party_review_required for memory in memories
            ):
                raise PublicationAuthorityNotPublishable(
                    "third-party material requires a separate verified redaction or consent workflow"
                )
            target_version = int(draft.get("targetPublicationVersion") or 1)
            base_version_id = draft.get("basePublicationVersionId")
            if base_version_id is not None:
                active_versions = [
                    projection
                    for projection in self._public_projections.values()
                    if projection.get("publicationId") == command.publication_id
                    and projection.get("vaultId") == context.vault_id
                    and projection.get("ownerSubjectId") == context.owner_subject_id
                    and projection.get("publicationState") == "confirmed"
                    and projection.get("projectionState") == "active"
                ]
                if (
                    len(active_versions) != 1
                    or str(active_versions[0].get("publicationVersionId")) != base_version_id
                    or int(active_versions[0].get("versionNumber") or 0) + 1 != target_version
                ):
                    raise PublicationAuthorityConflict(
                        "publication version changed before revision confirmation"
                    )
            version_id = str(
                uuid5(
                    _NAMESPACE,
                    f"publication-version:{command.draft_id}:{target_version}",
                )
            )
            projection_id = str(uuid5(_NAMESPACE, f"publication-projection:{version_id}"))
            public_items = tuple(
                {
                    "itemIndex": int(item["itemIndex"]),
                    "displayTitle": str(item["publicTitle"]),
                    "displayBody": str(item["publicBody"]),
                    "aiDisclosure": PUBLICATION_AI_DISCLOSURE,
                    "publicCitationHash": _digest(
                        {
                            "publicationVersionId": version_id,
                            "itemIndex": int(item["itemIndex"]),
                            "memoryVersionId": memory.memory_version_id,
                            "memoryContentHash": memory.content_hash,
                            "itemSnapshotHash": str(item["itemSnapshotHash"]),
                            "draftSnapshotHash": draft_result.draft_snapshot_hash,
                        }
                    ),
                    "redactionDiffHash": item.get("redactionDiffHash"),
                }
                for item, memory in zip(draft_items, memories)
            )
            public_items = tuple(
                {
                    **item,
                    "projectionHash": _digest(
                        {
                            "title": item["displayTitle"],
                            "body": item["displayBody"],
                            "aiDisclosure": item["aiDisclosure"],
                            "draftSnapshotHash": draft_result.draft_snapshot_hash,
                            "publicCitationHash": item["publicCitationHash"],
                        }
                    ),
                }
                for item in public_items
            )
            public_citation_hash = _digest(
                {
                    "publicationVersionId": version_id,
                    "itemCitationHashes": [
                        item["publicCitationHash"] for item in public_items
                    ],
                    "draftSnapshotHash": draft_result.draft_snapshot_hash,
                }
            )
            public_projection_hash = _digest(
                {
                    "itemProjectionHashes": [item["projectionHash"] for item in public_items],
                    "draftSnapshotHash": draft_result.draft_snapshot_hash,
                    "publicCitationHash": public_citation_hash,
                }
            )
            result = PublicationConfirmResult(
                outcome="created",
                publication_id=command.publication_id,
                draft_id=command.draft_id,
                publication_version_id=version_id,
                publication_version=target_version,
                publication_state="confirmed",
                projection_state="active",
                public_projection_hash=public_projection_hash,
                ai_disclosure_required=True,
                item_count=len(public_items),
                public_projection_item_hashes=tuple(
                    str(item["projectionHash"]) for item in public_items
                ),
                schema_version=draft_result.schema_version,
            )
            if base_version_id is not None:
                active_versions[0]["projectionState"] = "superseded"
                active_versions[0]["blockReasonCode"] = "newVersionConfirmed"
            draft["state"] = "confirmed"
            draft["publicationVersionId"] = version_id
            self._public_projections[projection_id] = {
                "vaultId": context.vault_id,
                "ownerSubjectId": context.owner_subject_id,
                "authorityEpoch": memories[0].authority_epoch,
                "publicationId": command.publication_id,
                "publicationVersionId": version_id,
                "versionNumber": target_version,
                "confirmedAt": datetime.now(timezone.utc),
                "displayTitle": draft["publicTitle"],
                "displayBody": draft["publicBody"],
                "aiDisclosure": PUBLICATION_AI_DISCLOSURE,
                "projectionHash": public_projection_hash,
                "publicCitationHash": public_citation_hash,
                "items": [deepcopy(item) for item in public_items],
                "projectionState": "active",
                "vaultState": "active",
                "publicationState": "confirmed",
            }
            self._command_results[replay_key] = (command.payload_hash, result)
            return result

    def list_owner_publications(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[PublicationOwnerPublicationSummary, ...]:
        _assert_owner_context(context)
        with self._lock:
            self._assert_active_vault(context)
            summaries: list[PublicationOwnerPublicationSummary] = []
            scoped_drafts = [
                draft
                for draft in self._drafts.values()
                if str(draft.get("vaultId") or "") == context.vault_id
                and str(draft.get("ownerSubjectId") or "") == context.owner_subject_id
            ]
            for publication_id in sorted(
                {str(draft["publicationId"]) for draft in scoped_drafts}
            ):
                publication_drafts = [
                    draft
                    for draft in scoped_drafts
                    if str(draft["publicationId"]) == publication_id
                ]
                projections = [
                    item
                    for item in self._public_projections.values()
                    if item.get("publicationId") == publication_id
                    and item.get("vaultId") == context.vault_id
                    and item.get("ownerSubjectId") == context.owner_subject_id
                ]
                projection = max(
                    projections,
                    key=lambda item: int(item.get("versionNumber") or 0),
                    default=None,
                )
                publication_version_id = (
                    projection.get("publicationVersionId") if projection is not None else None
                )
                draft = next(
                    (
                        item
                        for item in publication_drafts
                        if item.get("publicationVersionId") == publication_version_id
                    ),
                    publication_drafts[-1],
                )
                draft_result = draft["draftResult"]
                if not isinstance(draft_result, PublicationDraftResult):
                    raise PublicationAuthorityConflict("publication draft summary is malformed")
                draft_state = str(draft.get("state") or "draft")
                summaries.append(
                    PublicationOwnerPublicationSummary(
                        publication_id=publication_id,
                        publication_version_id=(
                            str(publication_version_id)
                            if publication_version_id is not None
                            else None
                        ),
                        draft_id=draft_result.draft_id,
                        draft_revision=draft_result.expected_draft_revision,
                        lifecycle_authority_epoch=int(
                            projection.get("authorityEpoch") if projection is not None else 0
                        ),
                        publication_state=(
                            str(projection.get("publicationState"))
                            if projection is not None
                            else draft_state
                        ),
                        projection_state=(
                            str(projection.get("projectionState"))
                            if projection is not None
                            else None
                        ),
                        preview_title=(
                            str(projection["displayTitle"])
                            if projection is not None
                            else draft_result.preview_title
                        ),
                        preview_body=(
                            str(projection["displayBody"])
                            if projection is not None
                            else draft_result.preview_body
                        ),
                        requires_second_confirmation=draft_state == "draft",
                        third_party_review_required=draft_result.third_party_review_required,
                        ai_disclosure_required=True,
                    )
                )
            return tuple(summaries)

    def list_owner_publication_versions(
        self,
        *,
        context: OwnerTruthCommandContext,
        publication_id: str,
    ) -> tuple[PublicationOwnerVersionSummary, ...]:
        _assert_owner_context(context)
        normalized_publication_id = require_uuid(publication_id, field="publication_id")
        with self._lock:
            self._assert_active_vault(context)
            owns_publication = any(
                str(draft.get("publicationId") or "") == normalized_publication_id
                and str(draft.get("vaultId") or "") == context.vault_id
                and str(draft.get("ownerSubjectId") or "") == context.owner_subject_id
                for draft in self._drafts.values()
            )
            if not owns_publication:
                raise PublicationAuthorityAccessDenied(
                    "publication is not available in this Owner Vault"
                )
            versions = [
                projection
                for projection in self._public_projections.values()
                if str(projection.get("publicationId") or "") == normalized_publication_id
                and str(projection.get("vaultId") or "") == context.vault_id
                and str(projection.get("ownerSubjectId") or "") == context.owner_subject_id
            ]
            return tuple(
                PublicationOwnerVersionSummary(
                    publication_version_id=str(projection["publicationVersionId"]),
                    version_number=int(projection.get("versionNumber") or 1),
                    confirmed_at=projection.get("confirmedAt")
                    if isinstance(projection.get("confirmedAt"), datetime)
                    else datetime.now(timezone.utc),
                    projection_state=str(projection.get("projectionState") or "unknown"),
                    public_snapshot_hash=str(projection["projectionHash"]),
                    items=tuple(
                        PublicationOwnerVersionItemSummary(
                            item_index=int(item["itemIndex"]),
                            public_title=str(item["displayTitle"]),
                            public_body=str(item["displayBody"]),
                            ai_disclosure_required=bool(item.get("aiDisclosure")),
                        )
                        for item in projection.get("items") or ()
                    ),
                )
                for projection in sorted(
                    versions,
                    key=lambda value: int(value.get("versionNumber") or 1),
                    reverse=True,
                )
            )

    def _assert_active_vault(self, context: OwnerTruthCommandContext) -> None:
        owner_subject_id = self._vault_owners.get(context.vault_id)
        if owner_subject_id != context.owner_subject_id:
            raise PublicationAuthorityAccessDenied(
                "publication Vault is not available to this Owner"
            )

    def _publishable_memory(
        self,
        *,
        context: OwnerTruthCommandContext,
        memory_version_id: str,
    ) -> PublicationAuthorityMemoryVersion:
        memory = self._memory_versions.get(memory_version_id)
        if memory is None or memory.vault_id != context.vault_id:
            raise PublicationAuthorityAccessDenied("MemoryVersion is not available in this Owner Vault")
        if memory.owner_subject_id != context.owner_subject_id:
            raise PublicationAuthorityAccessDenied("MemoryVersion owner does not match publication owner")
        if (
            not memory.is_current
            or memory.memory_state != "active"
            or memory.source_state != "active"
            or memory.decision not in {"accepted", "corrected"}
            or memory.decision_receipt_id is None
        ):
            raise PublicationAuthorityNotPublishable(
                "MemoryVersion must be active, current and Owner-confirmed before publication"
            )
        return memory


class PostgresPublicationAuthorityRepository:
    """Canonical Owner Truth-backed writer bound to one active request UoW."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def create_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationDraftCommand,
    ) -> PublicationDraftResult:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            self._lock_command(
                cursor,
                vault_id=context.vault_id,
                command_id_hash=command.command_id_hash,
            )
            vault = self._active_vault(cursor, context=context)
            replay = self._draft_replay(
                cursor,
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=int(vault["authority_epoch"]),
                command=command,
            )
            if replay is not None:
                return replay
            items = tuple(
                _prepare_publication_draft_item(
                    item_index=item_index,
                    command=item_command,
                    memory=self._publishable_memory(
                        cursor,
                        context=context,
                        vault_authority_epoch=int(vault["authority_epoch"]),
                        memory_version_id=item_command.memory_version_id,
                    ),
                )
                for item_index, item_command in enumerate(command.items)
            )
            first_item = items[0]
            publication_id = str(uuid5(_NAMESPACE, f"publication:{context.vault_id}:{command.command_id_hash}"))
            draft_id = str(uuid5(_NAMESPACE, f"publication-draft:{context.vault_id}:{command.command_id_hash}"))
            draft_snapshot_hash = _publication_draft_snapshot_hash(
                vault_id=context.vault_id,
                schema_version=command.schema_version,
                items=items,
            )
            third_party_state = (
                "reviewRequired"
                if any(item.memory.third_party_review_required for item in items)
                else "noneDetected"
            )
            cursor.execute(
                """
                INSERT INTO publication.publications (
                    id, vault_id, owner_subject_id, authority_epoch, state
                ) VALUES (%s, %s, %s, %s, 'draft')
                """,
                (
                    publication_id,
                    context.vault_id,
                    context.owner_subject_id,
                    first_item.memory.authority_epoch,
                ),
            )
            cursor.execute(
                """
                INSERT INTO publication.publication_drafts (
                    id, publication_id, vault_id, owner_subject_id, authority_epoch,
                    draft_revision, state, draft_snapshot_hash, preview_hash,
                    redaction_diff_hash, policy_version, ai_transformation_present
                ) VALUES (%s, %s, %s, %s, %s, 1, 'draft', %s, %s, %s, %s, FALSE)
                """,
                (
                    draft_id,
                    publication_id,
                    context.vault_id,
                    context.owner_subject_id,
                    first_item.memory.authority_epoch,
                    draft_snapshot_hash,
                    first_item.preview_hash,
                    first_item.redaction_diff_hash,
                    command.schema_version,
                ),
            )
            for item in items:
                cursor.execute(
                    """
                    INSERT INTO publication.publication_draft_memory_versions (
                        draft_id, vault_id, memory_version_id, source_citation_hash,
                        content_hash, source_state, consent_state, requires_redaction,
                        redaction_diff_hash
                    ) VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, %s)
                    """,
                    (
                        draft_id,
                        context.vault_id,
                        item.memory.memory_version_id,
                        _digest(
                            {
                                "memoryVersionId": item.memory.memory_version_id,
                                "memoryContentHash": item.memory.content_hash,
                            }
                        ),
                        item.memory.content_hash,
                        (
                            "thirdPartyRestricted"
                            if item.memory.third_party_review_required
                            else "granted"
                        ),
                        item.memory.third_party_review_required,
                        item.redaction_diff_hash,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO publication.publication_draft_items (
                        draft_id, vault_id, item_index, memory_version_id,
                        memory_content_hash, item_snapshot_hash, display_title,
                        display_body, content_hash, preview_title, preview_body,
                        preview_hash, redaction_diff_hash,
                        third_party_review_required, ai_disclosure
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        draft_id,
                        context.vault_id,
                        item.item_index,
                        item.memory.memory_version_id,
                        item.memory.content_hash,
                        item.item_snapshot_hash,
                        item.command.public_title,
                        item.command.public_body,
                        item.public_content_hash,
                        item.preview_title,
                        item.preview_body,
                        item.preview_hash,
                        item.redaction_diff_hash,
                        item.memory.third_party_review_required,
                        PUBLICATION_AI_DISCLOSURE,
                    ),
                )
            cursor.execute(
                """
                INSERT INTO publication.publication_draft_public_contents (
                    draft_id, vault_id, display_title, display_body, content_hash,
                    preview_title, preview_body, preview_hash, redaction_diff_hash,
                    third_party_review_required, ai_disclosure
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    draft_id,
                    context.vault_id,
                    first_item.command.public_title,
                    first_item.command.public_body,
                    first_item.public_content_hash,
                    first_item.preview_title,
                    first_item.preview_body,
                    first_item.preview_hash,
                    first_item.redaction_diff_hash,
                    first_item.memory.third_party_review_required,
                    PUBLICATION_AI_DISCLOSURE,
                ),
            )
            cursor.execute(
                """
                INSERT INTO publication.publication_authority_receipts (
                    id, vault_id, publication_id, draft_id, owner_subject_id,
                    authority_epoch, command_kind, command_id_hash, command_payload_hash,
                    memory_version_id, draft_snapshot_hash, preview_hash,
                    redaction_diff_hash, third_party_review_state, policy_version
                ) VALUES (%s, %s, %s, %s, %s, %s, 'draftCreated', %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(uuid5(_NAMESPACE, f"publication-receipt:draft:{context.vault_id}:{command.command_id_hash}")),
                    context.vault_id,
                    publication_id,
                    draft_id,
                    context.owner_subject_id,
                    first_item.memory.authority_epoch,
                    command.command_id_hash,
                    command.payload_hash,
                    first_item.memory.memory_version_id,
                    draft_snapshot_hash,
                    first_item.preview_hash,
                    first_item.redaction_diff_hash,
                    third_party_state,
                    command.schema_version,
                ),
            )
        return PublicationDraftResult(
            outcome="created",
            publication_id=publication_id,
            draft_id=draft_id,
            expected_draft_revision=1,
            draft_snapshot_hash=draft_snapshot_hash,
            preview_title=first_item.preview_title,
            preview_body=first_item.preview_body,
            state="draft",
            second_confirmation_required=True,
            third_party_review_required=any(
                item.memory.third_party_review_required for item in items
            ),
            items=tuple(item.result() for item in items),
            schema_version=command.schema_version,
        )

    def create_revision_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationRevisionDraftCommand,
    ) -> PublicationDraftResult:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            self._lock_command(
                cursor,
                vault_id=context.vault_id,
                command_id_hash=command.command_id_hash,
            )
            vault = self._active_vault(cursor, context=context)
            replay = self._draft_replay(
                cursor,
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=int(vault["authority_epoch"]),
                command=command,
            )
            if replay is not None:
                return replay
            cursor.execute(
                """
                SELECT publication_row.state AS publication_state,
                    version.id AS publication_version_id,
                    version.version_number,
                    projection.state AS projection_state
                FROM publication.publications AS publication_row
                JOIN publication.publication_versions AS version
                  ON version.publication_id = publication_row.id
                 AND version.vault_id = publication_row.vault_id
                JOIN publication.public_projections AS projection
                  ON projection.publication_version_id = version.id
                 AND projection.publication_id = version.publication_id
                 AND projection.vault_id = version.vault_id
                WHERE publication_row.id = %s
                  AND publication_row.vault_id = %s
                  AND publication_row.owner_subject_id = %s
                  AND publication_row.authority_epoch = %s
                ORDER BY version.version_number DESC
                LIMIT 1
                FOR UPDATE OF publication_row, version, projection
                """,
                (
                    command.publication_id,
                    context.vault_id,
                    context.owner_subject_id,
                    int(vault["authority_epoch"]),
                ),
            )
            current = cursor.fetchone()
            if current is None:
                raise PublicationAuthorityAccessDenied(
                    "publication is not available in this Owner Vault"
                )
            if (
                str(current["publication_state"]) != "confirmed"
                or str(current["projection_state"]) != "active"
            ):
                raise PublicationAuthorityNotPublishable(
                    "publication has no active version to revise"
                )
            if (
                str(current["publication_version_id"])
                != command.expected_publication_version_id
                or int(current["version_number"])
                != command.expected_publication_version
            ):
                raise PublicationAuthorityConflict("publication version has changed")
            cursor.execute(
                """
                SELECT item_index, memory_version_id, memory_content_hash
                FROM publication.publication_version_items
                WHERE publication_version_id = %s
                  AND publication_id = %s
                  AND vault_id = %s
                ORDER BY item_index ASC
                """,
                (
                    command.expected_publication_version_id,
                    command.publication_id,
                    context.vault_id,
                ),
            )
            source_items = tuple(cursor.fetchall())
            if (
                len(source_items) != len(command.items)
                or [int(item["item_index"]) for item in source_items]
                != [item.item_index for item in command.items]
            ):
                raise PublicationAuthorityConflict(
                    "publication revision must preserve the current item set"
                )
            items = tuple(
                _prepare_publication_draft_item(
                    item_index=item_command.item_index,
                    command=PublicationDraftItemCommand(
                        memory_version_id=str(source_item["memory_version_id"]),
                        public_title=item_command.public_title,
                        public_body=item_command.public_body,
                    ),
                    memory=self._publishable_memory(
                        cursor,
                        context=context,
                        vault_authority_epoch=int(vault["authority_epoch"]),
                        memory_version_id=str(source_item["memory_version_id"]),
                    ),
                )
                for source_item, item_command in zip(source_items, command.items)
            )
            if any(
                str(source_item["memory_content_hash"]) != item.memory.content_hash
                for source_item, item in zip(source_items, items)
            ):
                raise PublicationAuthorityConflict(
                    "publication source authority changed before revision"
                )
            first_item = items[0]
            target_version = command.expected_publication_version + 1
            draft_id = str(
                uuid5(
                    _NAMESPACE,
                    f"publication-revision-draft:{context.vault_id}:{command.command_id_hash}",
                )
            )
            draft_snapshot_hash = _publication_revision_draft_snapshot_hash(
                vault_id=context.vault_id,
                publication_id=command.publication_id,
                base_publication_version_id=command.expected_publication_version_id,
                base_publication_version=command.expected_publication_version,
                items=items,
            )
            third_party_state = (
                "reviewRequired"
                if any(item.memory.third_party_review_required for item in items)
                else "noneDetected"
            )
            cursor.execute(
                """
                INSERT INTO publication.publication_drafts (
                    id, publication_id, vault_id, owner_subject_id, authority_epoch,
                    draft_revision, state, draft_snapshot_hash, preview_hash,
                    redaction_diff_hash, policy_version, ai_transformation_present,
                    base_publication_version_id, target_version_number
                ) VALUES (%s, %s, %s, %s, %s, 1, 'draft', %s, %s, %s, %s, FALSE, %s, %s)
                """,
                (
                    draft_id,
                    command.publication_id,
                    context.vault_id,
                    context.owner_subject_id,
                    first_item.memory.authority_epoch,
                    draft_snapshot_hash,
                    first_item.preview_hash,
                    first_item.redaction_diff_hash,
                    PUBLICATION_AUTHORITY_REVISION_SCHEMA_VERSION,
                    command.expected_publication_version_id,
                    target_version,
                ),
            )
            for item in items:
                cursor.execute(
                    """
                    INSERT INTO publication.publication_draft_memory_versions (
                        draft_id, vault_id, memory_version_id, source_citation_hash,
                        content_hash, source_state, consent_state, requires_redaction,
                        redaction_diff_hash
                    ) VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, %s)
                    """,
                    (
                        draft_id,
                        context.vault_id,
                        item.memory.memory_version_id,
                        _digest(
                            {
                                "memoryVersionId": item.memory.memory_version_id,
                                "memoryContentHash": item.memory.content_hash,
                            }
                        ),
                        item.memory.content_hash,
                        (
                            "thirdPartyRestricted"
                            if item.memory.third_party_review_required
                            else "granted"
                        ),
                        item.memory.third_party_review_required,
                        item.redaction_diff_hash,
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO publication.publication_draft_items (
                        draft_id, vault_id, item_index, memory_version_id,
                        memory_content_hash, item_snapshot_hash, display_title,
                        display_body, content_hash, preview_title, preview_body,
                        preview_hash, redaction_diff_hash,
                        third_party_review_required, ai_disclosure
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        draft_id,
                        context.vault_id,
                        item.item_index,
                        item.memory.memory_version_id,
                        item.memory.content_hash,
                        item.item_snapshot_hash,
                        item.command.public_title,
                        item.command.public_body,
                        item.public_content_hash,
                        item.preview_title,
                        item.preview_body,
                        item.preview_hash,
                        item.redaction_diff_hash,
                        item.memory.third_party_review_required,
                        PUBLICATION_AI_DISCLOSURE,
                    ),
                )
            cursor.execute(
                """
                INSERT INTO publication.publication_draft_public_contents (
                    draft_id, vault_id, display_title, display_body, content_hash,
                    preview_title, preview_body, preview_hash, redaction_diff_hash,
                    third_party_review_required, ai_disclosure
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    draft_id,
                    context.vault_id,
                    first_item.command.public_title,
                    first_item.command.public_body,
                    first_item.public_content_hash,
                    first_item.preview_title,
                    first_item.preview_body,
                    first_item.preview_hash,
                    first_item.redaction_diff_hash,
                    first_item.memory.third_party_review_required,
                    PUBLICATION_AI_DISCLOSURE,
                ),
            )
            cursor.execute(
                """
                INSERT INTO publication.publication_authority_receipts (
                    id, vault_id, publication_id, draft_id, owner_subject_id,
                    authority_epoch, command_kind, command_id_hash, command_payload_hash,
                    memory_version_id, draft_snapshot_hash, preview_hash,
                    redaction_diff_hash, third_party_review_state, policy_version
                ) VALUES (%s, %s, %s, %s, %s, %s, 'draftCreated', %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(
                        uuid5(
                            _NAMESPACE,
                            f"publication-receipt:revision-draft:{context.vault_id}:{command.command_id_hash}",
                        )
                    ),
                    context.vault_id,
                    command.publication_id,
                    draft_id,
                    context.owner_subject_id,
                    first_item.memory.authority_epoch,
                    command.command_id_hash,
                    command.payload_hash,
                    first_item.memory.memory_version_id,
                    draft_snapshot_hash,
                    first_item.preview_hash,
                    first_item.redaction_diff_hash,
                    third_party_state,
                    PUBLICATION_AUTHORITY_REVISION_SCHEMA_VERSION,
                ),
            )
        return PublicationDraftResult(
            outcome="created",
            publication_id=command.publication_id,
            draft_id=draft_id,
            expected_draft_revision=1,
            draft_snapshot_hash=draft_snapshot_hash,
            preview_title=first_item.preview_title,
            preview_body=first_item.preview_body,
            state="draft",
            second_confirmation_required=True,
            third_party_review_required=any(
                item.memory.third_party_review_required for item in items
            ),
            items=tuple(item.result() for item in items),
            schema_version=PUBLICATION_AUTHORITY_REVISION_SCHEMA_VERSION,
            base_publication_version_id=command.expected_publication_version_id,
            target_publication_version=target_version,
        )

    def confirm_draft(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationConfirmCommand,
    ) -> PublicationConfirmResult:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            self._lock_command(
                cursor,
                vault_id=context.vault_id,
                command_id_hash=command.command_id_hash,
            )
            vault = self._active_vault(cursor, context=context)
            replay = self._confirm_replay(
                cursor,
                vault_id=context.vault_id,
                owner_subject_id=context.owner_subject_id,
                authority_epoch=int(vault["authority_epoch"]),
                command=command,
            )
            if replay is not None:
                return replay
            draft = self._draft_for_confirmation(
                cursor,
                context=context,
                command=command,
                vault_authority_epoch=int(vault["authority_epoch"]),
            )
            draft_items = tuple(draft["items"])
            memories = tuple(
                self._publishable_memory(
                    cursor,
                    context=context,
                    vault_authority_epoch=int(vault["authority_epoch"]),
                    memory_version_id=str(item["memory_version_id"]),
                )
                for item in draft_items
            )
            if any(
                str(item["memory_content_hash"]) != memory.content_hash
                for item, memory in zip(draft_items, memories)
            ):
                raise PublicationAuthorityConflict(
                    "publication draft item authority snapshot has changed"
                )
            if any(
                bool(item["third_party_review_required"])
                or memory.third_party_review_required
                for item, memory in zip(draft_items, memories)
            ):
                raise PublicationAuthorityNotPublishable(
                    "third-party material requires a separate verified redaction or consent workflow"
                )
            target_version = int(draft.get("target_version_number") or 1)
            base_version_id = (
                str(draft["base_publication_version_id"])
                if draft.get("base_publication_version_id") is not None
                else None
            )
            if base_version_id is not None:
                cursor.execute(
                    """
                    SELECT version.id AS publication_version_id,
                        version.version_number,
                        projection.state AS projection_state
                    FROM publication.publication_versions AS version
                    JOIN publication.public_projections AS projection
                      ON projection.publication_version_id = version.id
                     AND projection.publication_id = version.publication_id
                     AND projection.vault_id = version.vault_id
                    WHERE version.publication_id = %s
                      AND version.vault_id = %s
                    ORDER BY version.version_number DESC
                    LIMIT 1
                    FOR UPDATE OF version, projection
                    """,
                    (command.publication_id, context.vault_id),
                )
                current = cursor.fetchone()
                if (
                    current is None
                    or str(current["publication_version_id"]) != base_version_id
                    or int(current["version_number"]) + 1 != target_version
                    or str(current["projection_state"]) != "active"
                ):
                    raise PublicationAuthorityConflict(
                        "publication version changed before revision confirmation"
                    )
            version_id = str(
                uuid5(
                    _NAMESPACE,
                    f"publication-version:{command.draft_id}:{target_version}",
                )
            )
            projection_id = str(uuid5(_NAMESPACE, f"publication-projection:{version_id}"))
            projected_items = tuple(
                {
                    **item,
                    "public_citation_hash": _digest(
                        {
                            "publicationVersionId": version_id,
                            "itemIndex": int(item["item_index"]),
                            "memoryVersionId": memory.memory_version_id,
                            "memoryContentHash": memory.content_hash,
                            "itemSnapshotHash": str(item["item_snapshot_hash"]),
                            "draftSnapshotHash": str(draft["draft_snapshot_hash"]),
                        }
                    ),
                }
                for item, memory in zip(draft_items, memories)
            )
            projected_items = tuple(
                {
                    **item,
                    "projection_hash": _digest(
                        {
                            "title": str(item["display_title"]),
                            "body": str(item["display_body"]),
                            "aiDisclosure": str(item["ai_disclosure"]),
                            "draftSnapshotHash": str(draft["draft_snapshot_hash"]),
                            "publicCitationHash": str(item["public_citation_hash"]),
                        }
                    ),
                }
                for item in projected_items
            )
            first_item = projected_items[0]
            first_memory = memories[0]
            if len(projected_items) == 1:
                public_citation_hash = _digest(
                    {
                        "publicationVersionId": version_id,
                        "memoryVersionId": first_memory.memory_version_id,
                        "memoryContentHash": first_memory.content_hash,
                        "draftSnapshotHash": str(draft["draft_snapshot_hash"]),
                    }
                )
                projection_hash = _digest(
                    {
                        "title": str(first_item["display_title"]),
                        "body": str(first_item["display_body"]),
                        "aiDisclosure": str(first_item["ai_disclosure"]),
                        "draftSnapshotHash": str(draft["draft_snapshot_hash"]),
                        "publicCitationHash": public_citation_hash,
                    }
                )
            else:
                public_citation_hash = _digest(
                    {
                        "publicationVersionId": version_id,
                        "itemCitationHashes": [
                            item["public_citation_hash"] for item in projected_items
                        ],
                        "draftSnapshotHash": str(draft["draft_snapshot_hash"]),
                    }
                )
                projection_hash = _digest(
                    {
                        "itemProjectionHashes": [
                            item["projection_hash"] for item in projected_items
                        ],
                        "draftSnapshotHash": str(draft["draft_snapshot_hash"]),
                        "publicCitationHash": public_citation_hash,
                    }
                )
            now = datetime.now(timezone.utc)
            cursor.execute(
                """
                INSERT INTO publication.publication_versions (
                    id, publication_id, vault_id, pinned_memory_version_id,
                    version_number, content_hash, policy_version, confirmed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    version_id,
                    command.publication_id,
                    context.vault_id,
                    first_memory.memory_version_id,
                    target_version,
                    projection_hash,
                    str(draft["policy_version"]),
                    now,
                ),
            )
            if base_version_id is not None:
                cursor.execute(
                    """
                    UPDATE publication.public_projections
                    SET state = 'superseded',
                        blocked_at = %s,
                        block_reason_code = 'newVersionConfirmed',
                        updated_at = %s
                    WHERE publication_version_id = %s
                      AND publication_id = %s
                      AND vault_id = %s
                      AND state = 'active'
                    """,
                    (
                        now,
                        now,
                        base_version_id,
                        command.publication_id,
                        context.vault_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PublicationAuthorityConflict(
                        "publication active projection changed before revision confirmation"
                    )
            cursor.execute(
                """
                INSERT INTO publication.public_projections (
                    id, vault_id, publication_id, publication_version_id, state,
                    display_title, display_body, ai_disclosure, projection_hash,
                    public_citation_hash, redaction_diff_hash
                ) VALUES (%s, %s, %s, %s, 'active', %s, %s, %s, %s, %s, %s)
                """,
                (
                    projection_id,
                    context.vault_id,
                    command.publication_id,
                    version_id,
                    str(first_item["display_title"]),
                    str(first_item["display_body"]),
                    str(first_item["ai_disclosure"]),
                    projection_hash,
                    public_citation_hash,
                    first_item.get("redaction_diff_hash"),
                ),
            )
            for item, memory in zip(projected_items, memories):
                cursor.execute(
                    """
                    INSERT INTO publication.publication_version_items (
                        publication_version_id, publication_id, vault_id,
                        item_index, memory_version_id, memory_content_hash,
                        item_snapshot_hash, display_title, display_body,
                        ai_disclosure, projection_hash, public_citation_hash,
                        redaction_diff_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        version_id,
                        command.publication_id,
                        context.vault_id,
                        int(item["item_index"]),
                        memory.memory_version_id,
                        memory.content_hash,
                        str(item["item_snapshot_hash"]),
                        str(item["display_title"]),
                        str(item["display_body"]),
                        str(item["ai_disclosure"]),
                        str(item["projection_hash"]),
                        str(item["public_citation_hash"]),
                        item.get("redaction_diff_hash"),
                    ),
                )
                cursor.execute(
                    """
                    INSERT INTO publication.public_projection_items (
                        public_projection_id, publication_version_id,
                        publication_id, vault_id, item_index, display_title,
                        display_body, ai_disclosure, projection_hash,
                        public_citation_hash, redaction_diff_hash
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        projection_id,
                        version_id,
                        command.publication_id,
                        context.vault_id,
                        int(item["item_index"]),
                        str(item["display_title"]),
                        str(item["display_body"]),
                        str(item["ai_disclosure"]),
                        str(item["projection_hash"]),
                        str(item["public_citation_hash"]),
                        item.get("redaction_diff_hash"),
                    ),
                )
            cursor.execute(
                """
                UPDATE publication.publications
                SET state = 'confirmed', updated_at = %s
                WHERE id = %s AND vault_id = %s
                  AND state = %s
                """,
                (
                    now,
                    command.publication_id,
                    context.vault_id,
                    "confirmed" if base_version_id is not None else "draft",
                ),
            )
            if cursor.rowcount != 1:
                raise PublicationAuthorityConflict("publication state changed before confirmation")
            cursor.execute(
                """
                UPDATE publication.publication_drafts
                SET state = 'confirmed', confirmed_at = %s, updated_at = %s
                WHERE id = %s AND vault_id = %s AND state = 'draft'
                """,
                (now, now, command.draft_id, context.vault_id),
            )
            if cursor.rowcount != 1:
                raise PublicationAuthorityConflict("publication draft state changed before confirmation")
            cursor.execute(
                """
                INSERT INTO publication.publication_authority_receipts (
                    id, vault_id, publication_id, draft_id, publication_version_id,
                    owner_subject_id, authority_epoch, command_kind, command_id_hash,
                    command_payload_hash, memory_version_id, draft_snapshot_hash,
                    preview_hash, redaction_diff_hash, third_party_review_state,
                    policy_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'publicationConfirmed', %s, %s, %s, %s, %s, %s, 'noneDetected', %s)
                """,
                (
                    str(uuid5(_NAMESPACE, f"publication-receipt:confirm:{context.vault_id}:{command.command_id_hash}")),
                    context.vault_id,
                    command.publication_id,
                    command.draft_id,
                    version_id,
                    context.owner_subject_id,
                    first_memory.authority_epoch,
                    command.command_id_hash,
                    command.payload_hash,
                    first_memory.memory_version_id,
                    str(draft["draft_snapshot_hash"]),
                    str(draft["preview_hash"]),
                    draft.get("redaction_diff_hash"),
                    str(draft["policy_version"]),
                ),
            )
        return PublicationConfirmResult(
            outcome="created",
            publication_id=command.publication_id,
            draft_id=command.draft_id,
            publication_version_id=version_id,
            publication_version=target_version,
            publication_state="confirmed",
            projection_state="active",
            public_projection_hash=projection_hash,
            ai_disclosure_required=True,
            item_count=len(projected_items),
            public_projection_item_hashes=tuple(
                str(item["projection_hash"]) for item in projected_items
            ),
            schema_version=str(draft["policy_version"]),
        )

    def list_owner_publications(
        self,
        *,
        context: OwnerTruthCommandContext,
    ) -> tuple[PublicationOwnerPublicationSummary, ...]:
        _assert_owner_context(context)
        with self._cursor() as cursor:
            vault = self._active_vault(cursor, context=context)
            cursor.execute(
                """
                SELECT
                    publication_row.id AS publication_id,
                    publication_row.state AS publication_state,
                    draft.id AS draft_id,
                    draft.draft_revision,
                    draft.state AS draft_state,
                    content.preview_title,
                    content.preview_body,
                    content.third_party_review_required,
                    version.id AS publication_version_id,
                    projection.state AS projection_state
                FROM publication.publications AS publication_row
                LEFT JOIN LATERAL (
                    SELECT id, version_number
                    FROM publication.publication_versions
                    WHERE publication_id = publication_row.id
                      AND vault_id = publication_row.vault_id
                    ORDER BY version_number DESC
                    LIMIT 1
                ) AS version ON TRUE
                JOIN LATERAL (
                    SELECT draft_row.id, draft_row.draft_revision,
                        draft_row.state, draft_row.created_at,
                        content_row.preview_title, content_row.preview_body,
                        content_row.third_party_review_required
                    FROM publication.publication_drafts AS draft_row
                    JOIN publication.publication_draft_public_contents AS content_row
                      ON content_row.draft_id = draft_row.id
                     AND content_row.vault_id = draft_row.vault_id
                    LEFT JOIN publication.publication_authority_receipts AS receipt
                      ON receipt.draft_id = draft_row.id
                     AND receipt.vault_id = draft_row.vault_id
                     AND receipt.publication_version_id = version.id
                    WHERE draft_row.publication_id = publication_row.id
                      AND draft_row.vault_id = publication_row.vault_id
                    ORDER BY (receipt.publication_version_id IS NOT NULL) DESC,
                        draft_row.created_at DESC
                    LIMIT 1
                ) AS draft ON TRUE
                JOIN publication.publication_draft_public_contents AS content
                  ON content.draft_id = draft.id
                 AND content.vault_id = publication_row.vault_id
                LEFT JOIN publication.public_projections AS projection
                  ON projection.publication_version_id = version.id
                 AND projection.publication_id = publication_row.id
                 AND projection.vault_id = publication_row.vault_id
                WHERE publication_row.vault_id = %s
                  AND publication_row.owner_subject_id = %s
                  AND publication_row.authority_epoch = %s
                ORDER BY draft.created_at DESC, publication_row.id ASC
                """,
                (
                    context.vault_id,
                    context.owner_subject_id,
                    int(vault["authority_epoch"]),
                ),
            )
            return tuple(
                PublicationOwnerPublicationSummary(
                    publication_id=str(row["publication_id"]),
                    publication_version_id=(
                        str(row["publication_version_id"])
                        if row.get("publication_version_id") is not None
                        else None
                    ),
                    draft_id=str(row["draft_id"]),
                    draft_revision=int(row["draft_revision"]),
                    lifecycle_authority_epoch=int(vault["authority_epoch"]),
                    publication_state=str(row["publication_state"]),
                    projection_state=(
                        str(row["projection_state"])
                        if row.get("projection_state") is not None
                        else None
                    ),
                    preview_title=str(row["preview_title"]),
                    preview_body=str(row["preview_body"]),
                    requires_second_confirmation=str(row["draft_state"]) == "draft",
                    third_party_review_required=bool(row["third_party_review_required"]),
                    ai_disclosure_required=True,
                )
                for row in cursor.fetchall()
            )

    def list_owner_publication_versions(
        self,
        *,
        context: OwnerTruthCommandContext,
        publication_id: str,
    ) -> tuple[PublicationOwnerVersionSummary, ...]:
        _assert_owner_context(context)
        normalized_publication_id = require_uuid(publication_id, field="publication_id")
        with self._cursor() as cursor:
            vault = self._active_vault(cursor, context=context)
            cursor.execute(
                """
                SELECT id
                FROM publication.publications
                WHERE id = %s
                  AND vault_id = %s
                  AND owner_subject_id = %s
                  AND authority_epoch = %s
                """,
                (
                    normalized_publication_id,
                    context.vault_id,
                    context.owner_subject_id,
                    int(vault["authority_epoch"]),
                ),
            )
            if cursor.fetchone() is None:
                raise PublicationAuthorityAccessDenied(
                    "publication is not available in this Owner Vault"
                )
            cursor.execute(
                """
                SELECT version.id AS publication_version_id,
                    version.version_number,
                    version.confirmed_at,
                    version.content_hash AS public_snapshot_hash,
                    projection.state AS projection_state
                FROM publication.publication_versions AS version
                LEFT JOIN publication.public_projections AS projection
                  ON projection.publication_version_id = version.id
                 AND projection.publication_id = version.publication_id
                 AND projection.vault_id = version.vault_id
                WHERE version.publication_id = %s
                  AND version.vault_id = %s
                ORDER BY version.version_number DESC, version.confirmed_at DESC
                """,
                (normalized_publication_id, context.vault_id),
            )
            version_rows = tuple(cursor.fetchall())
            summaries: list[PublicationOwnerVersionSummary] = []
            for version in version_rows:
                cursor.execute(
                    """
                    SELECT item_index, display_title, display_body, ai_disclosure
                    FROM publication.publication_version_items
                    WHERE publication_version_id = %s
                      AND publication_id = %s
                      AND vault_id = %s
                    ORDER BY item_index ASC
                    """,
                    (
                        version["publication_version_id"],
                        normalized_publication_id,
                        context.vault_id,
                    ),
                )
                items = tuple(
                    PublicationOwnerVersionItemSummary(
                        item_index=int(item["item_index"]),
                        public_title=str(item["display_title"]),
                        public_body=str(item["display_body"]),
                        ai_disclosure_required=bool(item.get("ai_disclosure")),
                    )
                    for item in cursor.fetchall()
                )
                if not items:
                    raise PublicationAuthorityConflict(
                        "publication version has no immutable public items"
                    )
                summaries.append(
                    PublicationOwnerVersionSummary(
                        publication_version_id=str(version["publication_version_id"]),
                        version_number=int(version["version_number"]),
                        confirmed_at=version["confirmed_at"],
                        projection_state=(
                            str(version["projection_state"])
                            if version.get("projection_state") is not None
                            else None
                        ),
                        public_snapshot_hash=str(version["public_snapshot_hash"]),
                        items=items,
                    )
                )
            return tuple(summaries)

    @staticmethod
    def _third_party_review_required(payload: Any, *, sensitivity: str) -> bool:
        if str(sensitivity) != "standard":
            return True
        if not isinstance(payload, Mapping):
            return False
        stack: list[Any] = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, Mapping):
                for key, nested in value.items():
                    normalized = str(key).replace("_", "").replace("-", "").lower()
                    if normalized in {
                        "thirdparty",
                        "thirdpartycontent",
                        "thirdpartymentions",
                        "thirdpartyrestricted",
                    } and bool(nested):
                        return True
                    if normalized == "consentstate" and str(nested) in {
                        "missing",
                        "revoked",
                        "thirdpartyrestricted",
                    }:
                        return True
                    stack.append(nested)
            elif isinstance(value, (tuple, list)):
                stack.extend(value)
        return False

    def _active_vault(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
    ) -> Mapping[str, Any]:
        cursor.execute(
            """
            SELECT owner_subject_id, authority_epoch, status
            FROM owner_truth.vaults
            WHERE vault_id = %s
            FOR UPDATE
            """,
            (context.vault_id,),
        )
        vault = cursor.fetchone()
        if vault is None or str(vault["owner_subject_id"]) != context.owner_subject_id:
            raise PublicationAuthorityAccessDenied("publication Vault is not available to this Owner")
        if str(vault["status"]) != "active":
            raise PublicationAuthorityNotPublishable("publication Vault is no longer active")
        return vault

    def _publishable_memory(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        vault_authority_epoch: int,
        memory_version_id: str,
    ) -> PublicationAuthorityMemoryVersion:
        cursor.execute(
            """
            SELECT
                version.id AS memory_version_id,
                version.memory_id,
                version.vault_id,
                version.is_current,
                version.content_hash,
                version.payload,
                version.source_id,
                version.source_version,
                version.decision_receipt_id,
                memory.owner_subject_id,
                memory.authority_epoch AS memory_authority_epoch,
                memory.status AS memory_state,
                memory.sensitivity,
                source.owner_subject_id AS source_owner_subject_id,
                source.authority_epoch AS source_authority_epoch,
                source.source_version AS live_source_version,
                source.state AS source_state,
                receipt.decision AS receipt_decision,
                receipt.authority_epoch AS receipt_authority_epoch,
                candidate.owner_subject_id AS candidate_owner_subject_id,
                candidate.authority_epoch AS candidate_authority_epoch,
                candidate.decision_status AS candidate_decision_status
            FROM owner_truth.memory_versions AS version
            JOIN owner_truth.memories AS memory
              ON memory.vault_id = version.vault_id AND memory.id = version.memory_id
            JOIN owner_truth.sources AS source
              ON source.vault_id = version.vault_id AND source.id = version.source_id
            JOIN owner_truth.decision_receipts AS receipt
              ON receipt.vault_id = version.vault_id AND receipt.id = version.decision_receipt_id
            JOIN owner_truth.memory_candidates AS candidate
              ON candidate.vault_id = receipt.vault_id AND candidate.id = receipt.candidate_id
            WHERE version.id = %s AND version.vault_id = %s
            FOR UPDATE OF version, memory, source, receipt, candidate
            """,
            (memory_version_id, context.vault_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise PublicationAuthorityAccessDenied("MemoryVersion is not available in this Owner Vault")
        if str(row["owner_subject_id"]) != context.owner_subject_id:
            raise PublicationAuthorityAccessDenied("MemoryVersion owner does not match publication owner")
        if (
            row["source_id"] is None
            or row["decision_receipt_id"] is None
            or row["is_current"] is not True
            or str(row["memory_state"]) != "active"
            or int(row["memory_authority_epoch"]) != vault_authority_epoch
            or str(row.get("source_owner_subject_id") or "") != context.owner_subject_id
            or str(row.get("source_state") or "") != "active"
            or _int_or_missing(row.get("source_authority_epoch")) != vault_authority_epoch
            or _int_or_missing(row.get("live_source_version")) != int(row["source_version"])
            or str(row.get("receipt_decision") or "") not in {"accepted", "corrected"}
            or str(row.get("candidate_decision_status") or "") != str(row.get("receipt_decision") or "")
            or str(row.get("candidate_owner_subject_id") or "") != context.owner_subject_id
            or _int_or_missing(row.get("candidate_authority_epoch")) != vault_authority_epoch
            or _int_or_missing(row.get("receipt_authority_epoch")) != vault_authority_epoch
        ):
            raise PublicationAuthorityNotPublishable(
                "MemoryVersion must be active, current and Owner-confirmed before publication"
            )
        payload = row.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        return PublicationAuthorityMemoryVersion(
            memory_version_id=str(row["memory_version_id"]),
            memory_id=str(row["memory_id"]),
            vault_id=str(row["vault_id"]),
            owner_subject_id=str(row["owner_subject_id"]),
            authority_epoch=vault_authority_epoch,
            content_hash=str(row["content_hash"]),
            is_current=True,
            memory_state="active",
            source_state="active",
            decision=str(row["receipt_decision"]),
            decision_receipt_id=str(row["decision_receipt_id"]),
            third_party_review_required=self._third_party_review_required(
                payload,
                sensitivity=str(row["sensitivity"]),
            ),
        )

    def _draft_replay(
        self,
        cursor: Any,
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int,
        command: PublicationDraftCommand | PublicationRevisionDraftCommand,
    ) -> PublicationDraftResult | None:
        cursor.execute(
            """
            SELECT receipt.command_kind, receipt.command_payload_hash,
                receipt.owner_subject_id, receipt.authority_epoch,
                draft.id AS draft_id, draft.publication_id, draft.draft_revision,
                draft.draft_snapshot_hash, draft.policy_version,
                draft.base_publication_version_id, draft.target_version_number,
                content.preview_title, content.preview_body,
                content.third_party_review_required
            FROM publication.publication_authority_receipts AS receipt
            JOIN publication.publication_drafts AS draft
              ON draft.id = receipt.draft_id AND draft.vault_id = receipt.vault_id
            JOIN publication.publication_draft_public_contents AS content
              ON content.draft_id = draft.id AND content.vault_id = draft.vault_id
            WHERE receipt.vault_id = %s AND receipt.command_id_hash = %s
            FOR UPDATE OF receipt
            """,
            (vault_id, command.command_id_hash),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        if (
            str(row["owner_subject_id"]) != owner_subject_id
            or int(row["authority_epoch"]) != authority_epoch
        ):
            raise PublicationAuthorityAccessDenied(
                "publication command cannot be replayed after Vault authority changed"
            )
        if str(row["command_kind"]) != "draftCreated" or str(row["command_payload_hash"]) != command.payload_hash:
            raise PublicationAuthorityConflict("commandId cannot be reused with a different publication action")
        cursor.execute(
            """
            SELECT item_index, memory_version_id, item_snapshot_hash,
                preview_title, preview_body, third_party_review_required
            FROM publication.publication_draft_items
            WHERE draft_id = %s AND vault_id = %s
            ORDER BY item_index ASC
            """,
            (row["draft_id"], vault_id),
        )
        items = tuple(
            PublicationDraftItemResult(
                item_index=int(item["item_index"]),
                memory_version_id=str(item["memory_version_id"]),
                item_snapshot_hash=str(item["item_snapshot_hash"]),
                preview_title=str(item["preview_title"]),
                preview_body=str(item["preview_body"]),
                third_party_review_required=bool(item["third_party_review_required"]),
            )
            for item in cursor.fetchall()
        )
        return PublicationDraftResult(
            outcome="deduplicated",
            publication_id=str(row["publication_id"]),
            draft_id=str(row["draft_id"]),
            expected_draft_revision=int(row["draft_revision"]),
            draft_snapshot_hash=str(row["draft_snapshot_hash"]),
            preview_title=str(row["preview_title"]),
            preview_body=str(row["preview_body"]),
            state="draft",
            second_confirmation_required=True,
            third_party_review_required=bool(row["third_party_review_required"]),
            items=items,
            schema_version=str(row["policy_version"]),
            base_publication_version_id=(
                str(row["base_publication_version_id"])
                if row.get("base_publication_version_id") is not None
                else None
            ),
            target_publication_version=int(row["target_version_number"]),
        )

    def _confirm_replay(
        self,
        cursor: Any,
        *,
        vault_id: str,
        owner_subject_id: str,
        authority_epoch: int,
        command: PublicationConfirmCommand,
    ) -> PublicationConfirmResult | None:
        cursor.execute(
            """
            SELECT receipt.command_kind, receipt.command_payload_hash,
                receipt.owner_subject_id, receipt.authority_epoch,
                receipt.publication_id, receipt.draft_id, receipt.publication_version_id,
                version.version_number, version.policy_version,
                publication.state AS publication_state,
                projection.state AS projection_state, projection.projection_hash
            FROM publication.publication_authority_receipts AS receipt
            JOIN publication.publication_versions AS version
              ON version.id = receipt.publication_version_id
             AND version.publication_id = receipt.publication_id
             AND version.vault_id = receipt.vault_id
            JOIN publication.publications AS publication
              ON publication.id = receipt.publication_id AND publication.vault_id = receipt.vault_id
            JOIN publication.public_projections AS projection
              ON projection.publication_version_id = version.id
            WHERE receipt.vault_id = %s AND receipt.command_id_hash = %s
            FOR UPDATE OF receipt
            """,
            (vault_id, command.command_id_hash),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        if (
            str(row["owner_subject_id"]) != owner_subject_id
            or int(row["authority_epoch"]) != authority_epoch
        ):
            raise PublicationAuthorityAccessDenied(
                "publication command cannot be replayed after Vault authority changed"
            )
        if str(row["command_kind"]) != "publicationConfirmed" or str(row["command_payload_hash"]) != command.payload_hash:
            raise PublicationAuthorityConflict("commandId cannot be reused with a different publication action")
        if str(row["projection_state"]) not in {"active", "superseded"}:
            raise PublicationAuthorityNotPublishable(
                "publication projection is no longer available after an authority change"
            )
        cursor.execute(
            """
            SELECT projection_hash
            FROM publication.public_projection_items
            WHERE publication_version_id = %s
            ORDER BY item_index ASC
            """,
            (row["publication_version_id"],),
        )
        item_hashes = tuple(str(item["projection_hash"]) for item in cursor.fetchall())
        return PublicationConfirmResult(
            outcome="deduplicated",
            publication_id=str(row["publication_id"]),
            draft_id=str(row["draft_id"]),
            publication_version_id=str(row["publication_version_id"]),
            publication_version=int(row["version_number"]),
            publication_state=str(row["publication_state"]),
            projection_state=str(row["projection_state"]),
            public_projection_hash=str(row["projection_hash"]),
            ai_disclosure_required=True,
            item_count=len(item_hashes) if item_hashes else 1,
            public_projection_item_hashes=item_hashes,
            schema_version=str(row.get("policy_version") or PUBLICATION_AUTHORITY_SCHEMA_VERSION),
        )

    def _draft_for_confirmation(
        self,
        cursor: Any,
        *,
        context: OwnerTruthCommandContext,
        command: PublicationConfirmCommand,
        vault_authority_epoch: int,
    ) -> Mapping[str, Any]:
        cursor.execute(
            """
            SELECT
                draft.id, draft.publication_id, draft.vault_id, draft.owner_subject_id,
                draft.authority_epoch, draft.draft_revision, draft.state,
                draft.draft_snapshot_hash, draft.preview_hash,
                draft.redaction_diff_hash, draft.policy_version,
                draft.base_publication_version_id, draft.target_version_number
            FROM publication.publication_drafts AS draft
            WHERE draft.id = %s AND draft.vault_id = %s
            FOR UPDATE OF draft
            """,
            (command.draft_id, context.vault_id),
        )
        draft = cursor.fetchone()
        if draft is None:
            raise PublicationAuthorityAccessDenied("publication draft is not available in this Owner Vault")
        if (
            str(draft["publication_id"]) != command.publication_id
            or str(draft["owner_subject_id"]) != context.owner_subject_id
            or int(draft["authority_epoch"]) != vault_authority_epoch
        ):
            raise PublicationAuthorityAccessDenied("publication draft is not available in this Owner Vault")
        if str(draft["state"]) != "draft":
            raise PublicationAuthorityConflict("publication draft is no longer confirmable")
        if (
            int(draft["draft_revision"]) != command.expected_draft_revision
            or str(draft["draft_snapshot_hash"]) != command.expected_draft_snapshot_hash
        ):
            raise PublicationAuthorityConflict("publication draft snapshot has changed")
        cursor.execute(
            """
            SELECT item_index, memory_version_id, memory_content_hash,
                item_snapshot_hash, display_title, display_body, ai_disclosure,
                redaction_diff_hash, third_party_review_required
            FROM publication.publication_draft_items
            WHERE draft_id = %s AND vault_id = %s
            ORDER BY item_index ASC
            """,
            (command.draft_id, context.vault_id),
        )
        items = tuple(cursor.fetchall())
        if not items or [int(item["item_index"]) for item in items] != list(range(len(items))):
            raise PublicationAuthorityConflict(
                "publication draft must bind a contiguous ordered item snapshot"
            )
        return {**draft, "items": items}

    @staticmethod
    def _lock_command(cursor: Any, *, vault_id: str, command_id_hash: str) -> None:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0)) AS locked",
            (f"publication-authority-command:{vault_id}:{command_id_hash}",),
        )
        cursor.fetchone()

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)


__all__ = [
    "InMemoryPublicationAuthorityRepository",
    "PostgresPublicationAuthorityRepository",
    "PublicationAuthorityAccessDenied",
    "PublicationAuthorityConflict",
    "PublicationAuthorityDisabled",
    "PublicationAuthorityError",
    "PublicationAuthorityMemoryVersion",
    "PublicationAuthorityNotPublishable",
    "PublicationOwnerPublicationSummary",
    "PublicationOwnerVersionItemSummary",
    "PublicationOwnerVersionSummary",
    "PublicationAuthorityRepository",
    "PublicationAuthorityService",
    "PublicationConfirmCommand",
    "PublicationConfirmResult",
    "PublicationDraftCommand",
    "PublicationDraftItemCommand",
    "PublicationDraftItemResult",
    "PublicationDraftResult",
    "PublicationRevisionDraftCommand",
    "PublicationRevisionDraftItemCommand",
    "PUBLICATION_AI_DISCLOSURE",
    "PUBLICATION_AUTHORITY_MAX_ITEMS",
    "PUBLICATION_AUTHORITY_MULTI_ITEM_SCHEMA_VERSION",
    "PUBLICATION_AUTHORITY_REVISION_SCHEMA_VERSION",
    "PUBLICATION_AUTHORITY_SCHEMA_VERSION",
]
