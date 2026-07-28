"""Fail-closed Owner Draft Snapshot contract for a future publication flow.

The contract carries only opaque identifiers, hashes, state and policy
metadata. It never holds draft prose, source payloads, media references,
network credentials or a public projection. Even a fully valid synthetic
request remains denied until the publication policy and later gates approve a
real writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Iterable
from uuid import UUID

from .schema_authz import (
    PublicationAuthorizationContext,
    PublicationAuthorizationPrincipal,
    PublicationPrincipalKind,
)


PUBLICATION_DRAFT_SNAPSHOT_G0_SCHEMA_VERSION = "publication-draft-snapshot-g0-v1"
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class PublicationDraftSnapshotError(ValueError):
    """Raised when a synthetic draft snapshot envelope is malformed."""


class PublicationDraftSourceState(str, Enum):
    ACTIVE = "active"
    REDACTED = "redacted"
    DELETED = "deleted"
    SUSPENDED = "suspended"


class PublicationDraftConsentState(str, Enum):
    GRANTED = "granted"
    MISSING = "missing"
    REVOKED = "revoked"
    THIRD_PARTY_RESTRICTED = "thirdPartyRestricted"


class PublicationDraftDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_CONTEXT = "invalid_context"
    OWNER_SCOPE_DENIED = "owner_scope_denied"
    EMPTY_SNAPSHOT = "empty_snapshot"
    DUPLICATE_MEMORY_VERSION = "duplicate_memory_version"
    STALE_MEMORY_VERSION = "stale_memory_version"
    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_POLICY_BLOCKED = "source_policy_blocked"
    REDACTION_DIFF_REQUIRED = "redaction_diff_required"
    DRAFT_INTEGRITY_MISMATCH = "draft_integrity_mismatch"
    CONFIRMATION_MISMATCH = "confirmation_mismatch"
    SECOND_CONFIRMATION_REQUIRED = "second_confirmation_required"
    AI_DISCLOSURE_REQUIRED = "ai_disclosure_required"
    POLICY_DISABLED = "policy_disabled"


def _opaque_identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise PublicationDraftSnapshotError(f"{field} must be an opaque identifier")
    return normalized


def _uuid(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    try:
        return str(UUID(normalized))
    except (TypeError, ValueError) as exc:
        raise PublicationDraftSnapshotError(f"{field} must be a UUID") from exc


def _digest(value: object, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise PublicationDraftSnapshotError(f"{field} must be a SHA-256 digest")
    return normalized


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PublicationDraftSnapshotError(f"{field} must be a positive integer")
    return value


def _canonical_hash(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class PublicationDraftMemoryVersion:
    """Value-minimized eligibility snapshot for one Owner Truth MemoryVersion."""

    memory_version_id: str
    vault_id: str
    is_current: bool
    source_state: PublicationDraftSourceState
    consent_state: PublicationDraftConsentState
    content_hash: str
    source_citation_hash: str
    requires_redaction: bool = False
    redaction_diff_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "memory_version_id", _uuid(self.memory_version_id, field="memory_version_id"))
        object.__setattr__(self, "vault_id", _opaque_identifier(self.vault_id, field="vault_id"))
        object.__setattr__(self, "is_current", bool(self.is_current))
        object.__setattr__(self, "source_state", PublicationDraftSourceState(self.source_state))
        object.__setattr__(self, "consent_state", PublicationDraftConsentState(self.consent_state))
        object.__setattr__(self, "content_hash", _digest(self.content_hash, field="content_hash"))
        object.__setattr__(
            self,
            "source_citation_hash",
            _digest(self.source_citation_hash, field="source_citation_hash"),
        )
        object.__setattr__(self, "requires_redaction", bool(self.requires_redaction))
        if self.redaction_diff_hash is not None:
            object.__setattr__(
                self,
                "redaction_diff_hash",
                _digest(self.redaction_diff_hash, field="redaction_diff_hash"),
            )

    def hash_material(self) -> dict[str, object]:
        return {
            "consentState": self.consent_state.value,
            "contentHash": self.content_hash,
            "memoryVersionId": self.memory_version_id,
            "redactionDiffHash": self.redaction_diff_hash,
            "requiresRedaction": self.requires_redaction,
            "sourceCitationHash": self.source_citation_hash,
            "sourceState": self.source_state.value,
            "vaultId": self.vault_id,
        }


@dataclass(frozen=True)
class PublicationDraftSnapshot:
    """An Owner-bound draft and preview fingerprint without its readable copy."""

    draft_id: str
    publication_id: str
    vault_id: str
    owner_subject_hash: str
    authority_epoch: int
    policy_version: str
    draft_revision: int
    memory_versions: tuple[PublicationDraftMemoryVersion, ...]
    draft_snapshot_hash: str
    preview_hash: str
    ai_transformation_present: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "draft_id", _uuid(self.draft_id, field="draft_id"))
        object.__setattr__(self, "publication_id", _uuid(self.publication_id, field="publication_id"))
        object.__setattr__(self, "vault_id", _opaque_identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_hash",
            _digest(self.owner_subject_hash, field="owner_subject_hash"),
        )
        if isinstance(self.authority_epoch, bool) or not isinstance(self.authority_epoch, int) or self.authority_epoch < 0:
            raise PublicationDraftSnapshotError("authority_epoch must be a non-negative integer")
        object.__setattr__(self, "policy_version", _opaque_identifier(self.policy_version, field="policy_version"))
        object.__setattr__(self, "draft_revision", _positive_int(self.draft_revision, field="draft_revision"))
        versions = tuple(self.memory_versions)
        if not all(isinstance(item, PublicationDraftMemoryVersion) for item in versions):
            raise PublicationDraftSnapshotError("memory_versions must contain typed snapshots")
        object.__setattr__(self, "memory_versions", versions)
        object.__setattr__(
            self,
            "draft_snapshot_hash",
            _digest(self.draft_snapshot_hash, field="draft_snapshot_hash"),
        )
        object.__setattr__(self, "preview_hash", _digest(self.preview_hash, field="preview_hash"))
        object.__setattr__(self, "ai_transformation_present", bool(self.ai_transformation_present))

    @staticmethod
    def draft_hash_for(
        *,
        draft_id: str,
        publication_id: str,
        vault_id: str,
        owner_subject_hash: str,
        authority_epoch: int,
        policy_version: str,
        draft_revision: int,
        memory_versions: Iterable[PublicationDraftMemoryVersion],
        ai_transformation_present: bool,
    ) -> str:
        return _canonical_hash(
            {
                "aiTransformationPresent": bool(ai_transformation_present),
                "authorityEpoch": authority_epoch,
                "draftId": draft_id,
                "draftRevision": draft_revision,
                "memoryVersions": [item.hash_material() for item in memory_versions],
                "ownerSubjectHash": owner_subject_hash,
                "policyVersion": policy_version,
                "publicationId": publication_id,
                "vaultId": vault_id,
            }
        )

    @staticmethod
    def preview_hash_for(
        *,
        draft_snapshot_hash: str,
        memory_versions: Iterable[PublicationDraftMemoryVersion],
        ai_transformation_present: bool,
    ) -> str:
        return _canonical_hash(
            {
                "aiTransformationPresent": bool(ai_transformation_present),
                "draftSnapshotHash": draft_snapshot_hash,
                "redactionDiffHashes": [
                    item.redaction_diff_hash
                    for item in memory_versions
                    if item.redaction_diff_hash is not None
                ],
            }
        )

    def has_matching_integrity_hashes(self) -> bool:
        expected_snapshot_hash = self.draft_hash_for(
            draft_id=self.draft_id,
            publication_id=self.publication_id,
            vault_id=self.vault_id,
            owner_subject_hash=self.owner_subject_hash,
            authority_epoch=self.authority_epoch,
            policy_version=self.policy_version,
            draft_revision=self.draft_revision,
            memory_versions=self.memory_versions,
            ai_transformation_present=self.ai_transformation_present,
        )
        expected_preview_hash = self.preview_hash_for(
            draft_snapshot_hash=expected_snapshot_hash,
            memory_versions=self.memory_versions,
            ai_transformation_present=self.ai_transformation_present,
        )
        return (
            self.draft_snapshot_hash == expected_snapshot_hash
            and self.preview_hash == expected_preview_hash
        )


@dataclass(frozen=True)
class PublicationDraftConfirmation:
    """Owner's second-confirmation envelope; it cannot write a version at G0."""

    command_id: str
    draft_id: str
    expected_draft_revision: int
    expected_draft_snapshot_hash: str
    expected_preview_hash: str
    expected_policy_version: str
    second_confirmation: bool
    ai_transformation_disclosed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _uuid(self.command_id, field="command_id"))
        object.__setattr__(self, "draft_id", _uuid(self.draft_id, field="draft_id"))
        object.__setattr__(
            self,
            "expected_draft_revision",
            _positive_int(self.expected_draft_revision, field="expected_draft_revision"),
        )
        object.__setattr__(
            self,
            "expected_draft_snapshot_hash",
            _digest(self.expected_draft_snapshot_hash, field="expected_draft_snapshot_hash"),
        )
        object.__setattr__(
            self,
            "expected_preview_hash",
            _digest(self.expected_preview_hash, field="expected_preview_hash"),
        )
        object.__setattr__(
            self,
            "expected_policy_version",
            _opaque_identifier(self.expected_policy_version, field="expected_policy_version"),
        )
        object.__setattr__(self, "second_confirmation", bool(self.second_confirmation))
        object.__setattr__(self, "ai_transformation_disclosed", bool(self.ai_transformation_disclosed))


@dataclass(frozen=True)
class PublicationDraftEvaluationResult:
    disposition: PublicationDraftDisposition
    reason_codes: tuple[str, ...]
    scope_hash: str | None = None
    source_count: int = 0
    redaction_required_count: int = 0
    ai_transformation_present: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "disposition", PublicationDraftDisposition(self.disposition))
        reasons = tuple(sorted({_opaque_identifier(value, field="reason_code") for value in self.reason_codes}))
        if not reasons:
            raise PublicationDraftSnapshotError("at least one reason code is required")
        object.__setattr__(self, "reason_codes", reasons)
        if self.scope_hash is not None:
            object.__setattr__(self, "scope_hash", _digest(self.scope_hash, field="scope_hash"))
        if self.source_count < 0 or self.redaction_required_count < 0:
            raise PublicationDraftSnapshotError("summary counts must not be negative")

    @property
    def draft_write_allowed(self) -> bool:
        return False

    @property
    def publication_version_created(self) -> bool:
        return False

    @property
    def receipt_created(self) -> bool:
        return False

    @property
    def outbox_enqueued(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "aiTransformationPresent": self.ai_transformation_present,
            "draftWriteAllowed": self.draft_write_allowed,
            "outboxEnqueued": self.outbox_enqueued,
            "publicationVersionCreated": self.publication_version_created,
            "reasonCodes": list(self.reason_codes),
            "receiptCreated": self.receipt_created,
            "redactionRequiredCount": self.redaction_required_count,
            "releaseVisible": False,
            "schemaVersion": PUBLICATION_DRAFT_SNAPSHOT_G0_SCHEMA_VERSION,
            "sourceCount": self.source_count,
            "status": self.disposition.value,
        }
        if self.scope_hash is not None:
            summary["scopeHash"] = self.scope_hash
        return summary


def _scope_hash(
    *,
    context: PublicationAuthorizationContext,
    principal: PublicationAuthorizationPrincipal,
    snapshot: PublicationDraftSnapshot,
) -> str:
    return _canonical_hash(
        {
            "authorityEpoch": context.authority_epoch,
            "draftId": snapshot.draft_id,
            "draftRevision": snapshot.draft_revision,
            "ownerSubjectHash": context.owner_subject_hash,
            "policyVersion": snapshot.policy_version,
            "principalKind": principal.kind.value,
            "publicationId": snapshot.publication_id,
            "vaultId": context.vault_id,
        }
    )


def _result(
    disposition: PublicationDraftDisposition,
    reason: str,
    *,
    scope_hash: str | None = None,
    source_count: int = 0,
    redaction_required_count: int = 0,
    ai_transformation_present: bool = False,
) -> PublicationDraftEvaluationResult:
    return PublicationDraftEvaluationResult(
        disposition=disposition,
        reason_codes=(reason,),
        scope_hash=scope_hash,
        source_count=source_count,
        redaction_required_count=redaction_required_count,
        ai_transformation_present=ai_transformation_present,
    )


def evaluate_publication_draft_snapshot(
    *,
    context: PublicationAuthorizationContext | object,
    principal: PublicationAuthorizationPrincipal | object,
    snapshot: PublicationDraftSnapshot | object,
    confirmation: PublicationDraftConfirmation | object | None,
    enabled: bool = False,
) -> PublicationDraftEvaluationResult:
    """Evaluate a synthetic Owner Draft Snapshot without creating any effect."""

    if enabled is not True:
        return _result(
            PublicationDraftDisposition.SHADOW_DISABLED,
            "publicationDraftSnapshotShadowDisabled",
        )
    if not isinstance(context, PublicationAuthorizationContext) or not isinstance(
        principal, PublicationAuthorizationPrincipal
    ) or not isinstance(snapshot, PublicationDraftSnapshot):
        return _result(
            PublicationDraftDisposition.INVALID_CONTEXT,
            "invalidPublicationDraftSnapshotContext",
        )
    if (
        principal.kind is not PublicationPrincipalKind.OWNER
        or principal.vault_id != context.vault_id
        or principal.subject_hash != context.owner_subject_hash
        or snapshot.vault_id != context.vault_id
        or snapshot.owner_subject_hash != context.owner_subject_hash
        or snapshot.authority_epoch != context.authority_epoch
    ):
        return _result(
            PublicationDraftDisposition.OWNER_SCOPE_DENIED,
            "ownerVaultSubjectOrEpochMismatch",
        )

    source_count = len(snapshot.memory_versions)
    redaction_required_count = sum(
        1 for item in snapshot.memory_versions if item.requires_redaction
    )
    scope_hash = _scope_hash(context=context, principal=principal, snapshot=snapshot)
    summary_kwargs = {
        "scope_hash": scope_hash,
        "source_count": source_count,
        "redaction_required_count": redaction_required_count,
        "ai_transformation_present": snapshot.ai_transformation_present,
    }
    if source_count == 0:
        return _result(
            PublicationDraftDisposition.EMPTY_SNAPSHOT,
            "publicationDraftRequiresAtLeastOneMemoryVersion",
            **summary_kwargs,
        )
    version_ids = [item.memory_version_id for item in snapshot.memory_versions]
    if len(version_ids) != len(set(version_ids)):
        return _result(
            PublicationDraftDisposition.DUPLICATE_MEMORY_VERSION,
            "duplicatePublicationDraftMemoryVersion",
            **summary_kwargs,
        )
    if any(item.vault_id != context.vault_id for item in snapshot.memory_versions):
        return _result(
            PublicationDraftDisposition.OWNER_SCOPE_DENIED,
            "draftMemoryVersionCrossVault",
            **summary_kwargs,
        )
    if any(not item.is_current for item in snapshot.memory_versions):
        return _result(
            PublicationDraftDisposition.STALE_MEMORY_VERSION,
            "publicationDraftMemoryVersionIsNotCurrent",
            **summary_kwargs,
        )
    if any(item.source_state is not PublicationDraftSourceState.ACTIVE for item in snapshot.memory_versions):
        return _result(
            PublicationDraftDisposition.SOURCE_UNAVAILABLE,
            "publicationDraftSourceUnavailable",
            **summary_kwargs,
        )
    if any(item.consent_state is not PublicationDraftConsentState.GRANTED for item in snapshot.memory_versions):
        return _result(
            PublicationDraftDisposition.SOURCE_POLICY_BLOCKED,
            "publicationDraftSourceConsentOrThirdPartyPolicyBlocked",
            **summary_kwargs,
        )
    if any(
        item.requires_redaction and item.redaction_diff_hash is None
        for item in snapshot.memory_versions
    ):
        return _result(
            PublicationDraftDisposition.REDACTION_DIFF_REQUIRED,
            "publicationDraftRedactionDiffRequired",
            **summary_kwargs,
        )
    if not snapshot.has_matching_integrity_hashes():
        return _result(
            PublicationDraftDisposition.DRAFT_INTEGRITY_MISMATCH,
            "publicationDraftOrPreviewHashMismatch",
            **summary_kwargs,
        )
    if confirmation is None:
        return _result(
            PublicationDraftDisposition.SECOND_CONFIRMATION_REQUIRED,
            "publicationDraftSecondConfirmationRequired",
            **summary_kwargs,
        )
    if not isinstance(confirmation, PublicationDraftConfirmation):
        return _result(
            PublicationDraftDisposition.INVALID_CONTEXT,
            "invalidPublicationDraftConfirmation",
            **summary_kwargs,
        )
    if (
        confirmation.draft_id != snapshot.draft_id
        or confirmation.expected_draft_revision != snapshot.draft_revision
        or confirmation.expected_draft_snapshot_hash != snapshot.draft_snapshot_hash
        or confirmation.expected_preview_hash != snapshot.preview_hash
        or confirmation.expected_policy_version != snapshot.policy_version
    ):
        return _result(
            PublicationDraftDisposition.CONFIRMATION_MISMATCH,
            "publicationDraftConfirmationExpectationMismatch",
            **summary_kwargs,
        )
    if not confirmation.second_confirmation:
        return _result(
            PublicationDraftDisposition.SECOND_CONFIRMATION_REQUIRED,
            "publicationDraftSecondConfirmationRequired",
            **summary_kwargs,
        )
    if snapshot.ai_transformation_present and not confirmation.ai_transformation_disclosed:
        return _result(
            PublicationDraftDisposition.AI_DISCLOSURE_REQUIRED,
            "publicationDraftAiTransformationDisclosureRequired",
            **summary_kwargs,
        )
    return _result(
        PublicationDraftDisposition.POLICY_DISABLED,
        "publicationDraftWriterPolicyDisabled",
        **summary_kwargs,
    )


__all__ = [
    "PUBLICATION_DRAFT_SNAPSHOT_G0_SCHEMA_VERSION",
    "PublicationDraftConfirmation",
    "PublicationDraftConsentState",
    "PublicationDraftDisposition",
    "PublicationDraftEvaluationResult",
    "PublicationDraftMemoryVersion",
    "PublicationDraftSnapshot",
    "PublicationDraftSnapshotError",
    "PublicationDraftSourceState",
    "evaluate_publication_draft_snapshot",
]
