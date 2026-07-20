"""Default-off SourceObject admission inventory for future media processing.

This module is intentionally a G0-only, synthetic boundary.  It does not
create SourceObject rows, issue upload intents, touch object storage, enqueue
effects, call a processor, or write Candidate/Memory/Persona state.  It only
answers whether a fully described *future* private object would satisfy the
minimum invariant required before a later processor lane may be considered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping


MEDIA_SOURCE_OBJECT_PROTOCOL_VERSION = "owner-truth-source-object-v1"
MEDIA_SOURCE_OBJECT_ADMISSION_SHADOW_SCHEMA_VERSION = (
    "owner-truth-media-source-object-admission-shadow-v1"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_MEDIA_KIND_MIME_PREFIXES = {
    "image": ("image/",),
    "audio": ("audio/",),
    "video": ("video/",),
    "document": (
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.",
        "text/",
    ),
}
_INACTIVE_STATES = frozenset({"revoked", "deleted", "deletion_pending"})
_UNVERIFIED_STATES = frozenset(
    {
        "local_legacy",
        "awaiting_owner_upload",
        "uploaded_unverified",
        "quarantined",
        "missing",
    }
)
_UNSAFE_LOCATOR_FIELDS = frozenset(
    {
        "fileURL",
        "localPath",
        "objectURL",
        "previewURL",
        "temporaryURL",
        "uploadURL",
        "url",
    }
)
_FORBIDDEN_AUTHORITY_FIELDS = frozenset(
    {
        "candidateDecision",
        "candidateId",
        "confirmedMemoryId",
        "memoryId",
        "personaId",
    }
)


class MediaSourceObjectAdmissionContractError(ValueError):
    """A synthetic future SourceObject envelope is not safe to classify."""


class MediaSourceObjectAdmissionShadowDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    LEGACY_OR_MOCK_OBJECT = "legacy_or_mock_object"
    OWNER_OR_PURPOSE_MISMATCH = "owner_or_purpose_mismatch"
    OBJECT_INACTIVE = "object_inactive"
    OBJECT_NOT_VERIFIED = "object_not_verified"
    UNTRUSTED_STORAGE_LOCATOR = "untrusted_storage_locator"
    INCOMPLETE_VERIFICATION_RECEIPTS = "incomplete_verification_receipts"
    UNSUPPORTED_MEDIA = "unsupported_media"
    WOULD_BE_PROCESSOR_ELIGIBLE = "would_be_processor_eligible"


def _required_text(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise MediaSourceObjectAdmissionContractError(f"{field} is required")
    return normalized


def _identifier(value: object, *, field: str) -> str:
    normalized = _required_text(value, field=field)
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise MediaSourceObjectAdmissionContractError(f"{field} must be an opaque identifier")
    return normalized


def _sha256(value: object, *, field: str) -> str:
    normalized = _required_text(value, field=field).lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise MediaSourceObjectAdmissionContractError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MediaSourceObjectAdmissionContractError("admission envelope must be serializable") from exc


@dataclass(frozen=True)
class MediaSourceObjectAdmissionContext:
    """The authorized target a future processor would have to match."""

    vault_id: str
    owner_subject_id: str
    purpose: str = "candidateExtraction"

    def __post_init__(self) -> None:
        object.__setattr__(self, "vault_id", _identifier(self.vault_id, field="vault_id"))
        object.__setattr__(
            self,
            "owner_subject_id",
            _identifier(self.owner_subject_id, field="owner_subject_id"),
        )
        object.__setattr__(self, "purpose", _identifier(self.purpose, field="purpose"))


@dataclass(frozen=True)
class MediaSourceObjectAdmissionShadow:
    """Value-free result of a non-authoritative media object inventory check."""

    enabled: bool
    disposition: MediaSourceObjectAdmissionShadowDisposition
    reason_code: str
    media_kind: str | None = None
    source_object_fingerprint: str | None = None
    receipt_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise MediaSourceObjectAdmissionContractError("shadow enabled must be a boolean")
        if not isinstance(self.disposition, MediaSourceObjectAdmissionShadowDisposition):
            raise MediaSourceObjectAdmissionContractError("shadow disposition is required")
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))
        if self.media_kind is not None:
            object.__setattr__(self, "media_kind", _identifier(self.media_kind, field="media_kind"))
        if self.source_object_fingerprint is not None:
            object.__setattr__(
                self,
                "source_object_fingerprint",
                _sha256(self.source_object_fingerprint, field="source_object_fingerprint"),
            )
        object.__setattr__(self, "receipt_flags", tuple(sorted(set(self.receipt_flags))))

    @property
    def would_be_processor_eligible(self) -> bool:
        return self.disposition is MediaSourceObjectAdmissionShadowDisposition.WOULD_BE_PROCESSOR_ELIGIBLE

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "candidateProposalPerformed": False,
            "effectAdmissionPerformed": False,
            "enabled": self.enabled,
            "objectStorageOperationPerformed": False,
            "reasonCode": self.reason_code,
            "schemaVersion": MEDIA_SOURCE_OBJECT_ADMISSION_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "sourceObjectCreated": False,
            "status": self.disposition.value,
            "wouldBeProcessorEligible": self.would_be_processor_eligible,
        }
        if self.media_kind is not None:
            summary["mediaKind"] = self.media_kind
        if self.source_object_fingerprint is not None:
            summary["sourceObjectFingerprint"] = self.source_object_fingerprint
        if self.receipt_flags:
            summary["receiptFlags"] = list(self.receipt_flags)
        return summary


def _safe_storage_locator(candidate: Mapping[str, Any]) -> bool:
    if any(field in candidate for field in _UNSAFE_LOCATOR_FIELDS):
        return False
    storage = candidate.get("storage")
    if not isinstance(storage, Mapping):
        return False
    if any(field in storage for field in _UNSAFE_LOCATOR_FIELDS):
        return False
    provider = str(storage.get("provider") or "").strip().lower()
    mode = str(storage.get("mode") or "").strip().lower()
    object_key = str(storage.get("objectKey") or "").strip()
    if not provider or provider.startswith("mock") or provider in {"local", "temporary"}:
        return False
    if mode != "private" or not object_key:
        return False
    if "://" in object_key or ".." in object_key or "?" in object_key or "#" in object_key:
        return False
    return True


def _mime_matches_media_kind(*, media_kind: str, magic_mime: str) -> bool:
    prefixes = _MEDIA_KIND_MIME_PREFIXES.get(media_kind)
    if prefixes is None:
        return False
    normalized_mime = magic_mime.lower()
    return any(normalized_mime.startswith(prefix) for prefix in prefixes)


def _verification_receipt_flags(candidate: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    receipts = candidate.get("verificationReceipts")
    if not isinstance(receipts, Mapping):
        return False, ()
    flags = {
        "checksumVerified": bool(receipts.get("checksumVerified")),
        "headVerified": bool(receipts.get("headVerified")),
        "magicMimeVerified": bool(receipts.get("magicMimeVerified")),
        "scanClean": str(receipts.get("scanStatus") or "").strip().lower() == "clean",
    }
    return all(flags.values()), tuple(flag for flag, present in flags.items() if present)


def _source_object_fingerprint(
    *,
    candidate: Mapping[str, Any],
    media_kind: str,
    sha256_digest: str,
) -> str:
    storage = candidate["storage"]
    assert isinstance(storage, Mapping)
    material = {
        "mediaKind": media_kind,
        "objectKey": str(storage.get("objectKey") or ""),
        "objectVersion": int(candidate["objectVersion"]),
        "ownerSubjectId": str(candidate["ownerSubjectId"]),
        "purpose": str(candidate["purpose"]),
        "sha256": sha256_digest,
        "sourceObjectId": str(candidate["sourceObjectId"]),
        "vaultId": str(candidate["vaultId"]),
    }
    return sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def build_media_source_object_admission_shadow(
    candidate: Mapping[str, Any] | object,
    *,
    context: MediaSourceObjectAdmissionContext,
    enabled: bool = False,
) -> MediaSourceObjectAdmissionShadow:
    """Classify a synthetic future SourceObject without creating or processing it.

    The disabled path intentionally returns before inspecting ``candidate``.
    When enabled for QA, the result is still a value-free preview only.  It is
    not an authorization decision, an upload commit, or a processor admission.
    """

    if not enabled:
        return MediaSourceObjectAdmissionShadow(
            enabled=False,
            disposition=MediaSourceObjectAdmissionShadowDisposition.SHADOW_DISABLED,
            reason_code="shadowDisabled",
        )
    if not isinstance(context, MediaSourceObjectAdmissionContext):
        raise MediaSourceObjectAdmissionContractError("admission context is required")
    if not isinstance(candidate, Mapping):
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.INVALID_ENVELOPE,
            reason_code="invalidEnvelope",
        )
    if str(candidate.get("protocolVersion") or "").strip() != MEDIA_SOURCE_OBJECT_PROTOCOL_VERSION:
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.LEGACY_OR_MOCK_OBJECT,
            reason_code="legacyOrUnknownProtocol",
        )
    if any(field in candidate for field in _FORBIDDEN_AUTHORITY_FIELDS):
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.INVALID_ENVELOPE,
            reason_code="sourceObjectCarriesAuthorityField",
        )

    try:
        source_object_id = _identifier(candidate.get("sourceObjectId"), field="source_object_id")
        vault_id = _identifier(candidate.get("vaultId"), field="vault_id")
        owner_subject_id = _identifier(candidate.get("ownerSubjectId"), field="owner_subject_id")
        purpose = _identifier(candidate.get("purpose"), field="purpose")
        media_kind = _identifier(candidate.get("mediaKind"), field="media_kind").lower()
        state = _identifier(candidate.get("state"), field="state").lower()
        object_version = candidate.get("objectVersion")
        if isinstance(object_version, bool) or not isinstance(object_version, int) or object_version < 1:
            raise MediaSourceObjectAdmissionContractError("object_version must be a positive integer")
        size_bytes = candidate.get("sizeBytes")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 1:
            raise MediaSourceObjectAdmissionContractError("size_bytes must be a positive integer")
        sha256_digest = _sha256(candidate.get("sha256"), field="sha256")
        magic_mime = _required_text(candidate.get("magicMime"), field="magic_mime").lower()
    except MediaSourceObjectAdmissionContractError:
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.INVALID_ENVELOPE,
            reason_code="invalidEnvelope",
        )

    identity_candidate = {
        **candidate,
        "ownerSubjectId": owner_subject_id,
        "purpose": purpose,
        "sourceObjectId": source_object_id,
        "vaultId": vault_id,
        "objectVersion": object_version,
    }
    fingerprint = None
    if _safe_storage_locator(identity_candidate):
        fingerprint = _source_object_fingerprint(
            candidate=identity_candidate,
            media_kind=media_kind,
            sha256_digest=sha256_digest,
        )

    if vault_id != context.vault_id or owner_subject_id != context.owner_subject_id or purpose != context.purpose:
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.OWNER_OR_PURPOSE_MISMATCH,
            reason_code="ownerVaultOrPurposeMismatch",
            media_kind=media_kind,
            source_object_fingerprint=fingerprint,
        )
    if state in _INACTIVE_STATES:
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.OBJECT_INACTIVE,
            reason_code="objectInactive",
            media_kind=media_kind,
            source_object_fingerprint=fingerprint,
        )
    if state in _UNVERIFIED_STATES:
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.OBJECT_NOT_VERIFIED,
            reason_code="objectNotVerified",
            media_kind=media_kind,
            source_object_fingerprint=fingerprint,
        )
    if state != "verified":
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.INVALID_ENVELOPE,
            reason_code="unknownObjectState",
            media_kind=media_kind,
            source_object_fingerprint=fingerprint,
        )
    if not _safe_storage_locator(identity_candidate) or bool(candidate.get("metadataOnly")):
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.UNTRUSTED_STORAGE_LOCATOR,
            reason_code="untrustedStorageLocator",
            media_kind=media_kind,
        )
    if not _mime_matches_media_kind(media_kind=media_kind, magic_mime=magic_mime):
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.UNSUPPORTED_MEDIA,
            reason_code="magicMimeDoesNotMatchMediaKind",
            media_kind=media_kind,
            source_object_fingerprint=fingerprint,
        )
    receipts_complete, receipt_flags = _verification_receipt_flags(candidate)
    if not receipts_complete:
        return MediaSourceObjectAdmissionShadow(
            enabled=True,
            disposition=MediaSourceObjectAdmissionShadowDisposition.INCOMPLETE_VERIFICATION_RECEIPTS,
            reason_code="verificationReceiptIncomplete",
            media_kind=media_kind,
            source_object_fingerprint=fingerprint,
            receipt_flags=receipt_flags,
        )
    return MediaSourceObjectAdmissionShadow(
        enabled=True,
        disposition=MediaSourceObjectAdmissionShadowDisposition.WOULD_BE_PROCESSOR_ELIGIBLE,
        reason_code="verifiedObjectFutureProcessorEligibilityOnly",
        media_kind=media_kind,
        source_object_fingerprint=fingerprint,
        receipt_flags=receipt_flags,
    )


__all__ = [
    "MEDIA_SOURCE_OBJECT_ADMISSION_SHADOW_SCHEMA_VERSION",
    "MEDIA_SOURCE_OBJECT_PROTOCOL_VERSION",
    "MediaSourceObjectAdmissionContext",
    "MediaSourceObjectAdmissionContractError",
    "MediaSourceObjectAdmissionShadow",
    "MediaSourceObjectAdmissionShadowDisposition",
    "build_media_source_object_admission_shadow",
]
