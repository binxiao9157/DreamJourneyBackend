"""Default-off inventory that prevents legacy Archive media from promotion.

The current Archive media path is metadata-only and may carry local, mock or
temporary transport details. This G0 helper classifies those representations
without reading files, calling storage, adding an API route, or creating any
SourceObject/Candidate/Memory/Persona authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Iterator, Mapping

from app.services.owner_truth_media_source_object_shadow import MediaSourceObjectAdmissionContext


LEGACY_ARCHIVE_MEDIA_NON_PROMOTION_SHADOW_SCHEMA_VERSION = (
    "owner-truth-legacy-archive-media-non-promotion-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_MEDIA_KIND_MAP = {
    "album": "image",
    "audio": "audio",
    "image": "image",
    "photo": "image",
    "video": "video",
}
_LOCAL_REFERENCE_KEYS = frozenset(
    {
        "absolutePath",
        "assetLocalIdentifier",
        "fileURL",
        "imageLocalPath",
        "localPath",
        "sampleLocalPath",
    }
)
_TEMPORARY_URL_KEYS = frozenset({"objectURL", "previewURL", "temporaryURL", "uploadURL", "url"})
_LEGACY_OBJECT_AUTHORITY_KEYS = frozenset(
    {
        "objectReceipt",
        "objectVersion",
        "sourceObjectId",
        "sourceObjectReceipt",
        "verificationReceipts",
    }
)
_CLOUD_CLAIM_KEYS = frozenset({"cloudStatus", "fileStatus", "uploadStatus"})


class LegacyArchiveMediaNonPromotionContractError(ValueError):
    """The caller did not provide a valid synthetic owner/vault context."""


class LegacyArchiveMediaNonPromotionDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_ENVELOPE = "invalid_envelope"
    NOT_MEDIA = "not_media"
    OWNER_OR_VAULT_MISMATCH = "owner_or_vault_mismatch"
    LOCAL_OR_DEVICE_ONLY = "local_or_device_only"
    MOCK_OR_METADATA_UPLOAD = "mock_or_metadata_upload"
    TEMPORARY_OR_PUBLIC_LOCATOR = "temporary_or_public_locator"
    LEGACY_AUTHORITY_TAINT = "legacy_authority_taint"
    METADATA_ONLY = "metadata_only"
    UNBACKED_CLOUD_CLAIM = "unbacked_cloud_claim"
    LEGACY_MEDIA_UNCLASSIFIED = "legacy_media_unclassified"


def _identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise LegacyArchiveMediaNonPromotionContractError(f"{field} must be an opaque identifier")
    return normalized


def _mapping_views(item: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    yield item
    metadata = item.get("metadata")
    if isinstance(metadata, Mapping):
        yield metadata


def _first_text(item: Mapping[str, Any], field: str) -> str:
    for view in _mapping_views(item):
        value = view.get(field)
        if value is not None:
            normalized = str(value).strip()
            if normalized:
                return normalized
    return ""


def _has_key(item: Mapping[str, Any], fields: frozenset[str]) -> bool:
    return any(any(field in view for field in fields) for view in _mapping_views(item))


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _has_local_reference(item: Mapping[str, Any]) -> bool:
    return any(
        any(str(view.get(field) or "").strip() for field in _LOCAL_REFERENCE_KEYS)
        for view in _mapping_views(item)
    )


def _has_mock_transport(item: Mapping[str, Any]) -> bool:
    for view in _mapping_views(item):
        for field in ("storageProvider", "provider", "providerMode", "uploadURLScheme"):
            value = str(view.get(field) or "").strip().lower()
            if value.startswith("mock") or value in {"local", "metadataonly", "temporary"}:
                return True
        action = str(view.get("clientUploadAction") or "").strip().lower()
        if action == "metadataonly":
            return True
        for field in _TEMPORARY_URL_KEYS:
            value = str(view.get(field) or "").strip().lower()
            if value.startswith("mock://"):
                return True
    return False


def _has_temporary_or_public_locator(item: Mapping[str, Any]) -> bool:
    for view in _mapping_views(item):
        for field in _TEMPORARY_URL_KEYS:
            value = str(view.get(field) or "").strip().lower()
            if value.startswith(("http://", "https://", "file://")):
                return True
        if str(view.get("providerMode") or "").strip().lower() == "temporary":
            return True
    return False


def _has_metadata_only_marker(item: Mapping[str, Any]) -> bool:
    return any(_is_truthy(view.get("metadataOnly")) for view in _mapping_views(item))


def _has_unbacked_cloud_claim(item: Mapping[str, Any]) -> bool:
    claim_values = {
        str(view.get(field) or "").strip().lower()
        for view in _mapping_views(item)
        for field in _CLOUD_CLAIM_KEYS
    }
    return bool(claim_values & {"cloud", "synced", "uploaded", "uploading"})


def _fingerprint(item: Mapping[str, Any], media_kind: str) -> str:
    material = {
        "archiveItemId": str(item.get("id") or ""),
        "kind": media_kind,
        "owner": _first_text(item, "userId")
        or _first_text(item, "ownerId")
        or _first_text(item, "ownerUserId"),
        "vault": _first_text(item, "vaultId"),
    }
    return sha256(
        json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class LegacyArchiveMediaNonPromotionShadow:
    enabled: bool
    disposition: LegacyArchiveMediaNonPromotionDisposition
    reason_code: str
    media_kind: str | None = None
    archive_media_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise LegacyArchiveMediaNonPromotionContractError("shadow enabled must be a boolean")
        if not isinstance(self.disposition, LegacyArchiveMediaNonPromotionDisposition):
            raise LegacyArchiveMediaNonPromotionContractError("shadow disposition is required")
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))
        if self.media_kind is not None:
            object.__setattr__(self, "media_kind", _identifier(self.media_kind, field="media_kind"))
        if self.archive_media_fingerprint is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", self.archive_media_fingerprint):
                raise LegacyArchiveMediaNonPromotionContractError(
                    "archive_media_fingerprint must be a SHA-256 digest"
                )

    @property
    def would_be_verified_source_object(self) -> bool:
        return False

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "candidateProposalPerformed": False,
            "enabled": self.enabled,
            "legacyMediaPromoted": False,
            "objectStorageOperationPerformed": False,
            "processorAdmissionPerformed": False,
            "reasonCode": self.reason_code,
            "schemaVersion": LEGACY_ARCHIVE_MEDIA_NON_PROMOTION_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "sourceObjectCreated": False,
            "status": self.disposition.value,
            "wouldBeVerifiedSourceObject": self.would_be_verified_source_object,
        }
        if self.media_kind is not None:
            summary["mediaKind"] = self.media_kind
        if self.archive_media_fingerprint is not None:
            summary["archiveMediaFingerprint"] = self.archive_media_fingerprint
        return summary


def inventory_legacy_archive_media_non_promotion(
    item: Mapping[str, Any] | object,
    *,
    context: MediaSourceObjectAdmissionContext,
    enabled: bool = False,
) -> LegacyArchiveMediaNonPromotionShadow:
    """Classify current Archive media as non-authoritative without promoting it.

    The disabled path returns before it looks at the archive envelope. Enabled
    runs are QA-only and no disposition is an access or processor decision.
    """

    if not enabled:
        return LegacyArchiveMediaNonPromotionShadow(
            enabled=False,
            disposition=LegacyArchiveMediaNonPromotionDisposition.SHADOW_DISABLED,
            reason_code="shadowDisabled",
        )
    if not isinstance(context, MediaSourceObjectAdmissionContext):
        raise LegacyArchiveMediaNonPromotionContractError("admission context is required")
    if not isinstance(item, Mapping):
        return LegacyArchiveMediaNonPromotionShadow(
            enabled=True,
            disposition=LegacyArchiveMediaNonPromotionDisposition.INVALID_ENVELOPE,
            reason_code="invalidEnvelope",
        )

    media_kind = _MEDIA_KIND_MAP.get(str(item.get("kind") or "").strip().lower())
    if media_kind is None:
        return LegacyArchiveMediaNonPromotionShadow(
            enabled=True,
            disposition=LegacyArchiveMediaNonPromotionDisposition.NOT_MEDIA,
            reason_code="notMediaArchiveKind",
        )

    fingerprint = _fingerprint(item, media_kind)
    owner = _first_text(item, "userId") or _first_text(item, "ownerId") or _first_text(item, "ownerUserId")
    vault = _first_text(item, "vaultId")
    if (owner and owner != context.owner_subject_id) or (vault and vault != context.vault_id):
        return LegacyArchiveMediaNonPromotionShadow(
            enabled=True,
            disposition=LegacyArchiveMediaNonPromotionDisposition.OWNER_OR_VAULT_MISMATCH,
            reason_code="ownerOrVaultMismatch",
            media_kind=media_kind,
            archive_media_fingerprint=fingerprint,
        )
    if _has_local_reference(item):
        disposition = LegacyArchiveMediaNonPromotionDisposition.LOCAL_OR_DEVICE_ONLY
        reason = "localOrDeviceReference"
    elif _has_mock_transport(item):
        disposition = LegacyArchiveMediaNonPromotionDisposition.MOCK_OR_METADATA_UPLOAD
        reason = "mockOrMetadataTransport"
    elif _has_temporary_or_public_locator(item):
        disposition = LegacyArchiveMediaNonPromotionDisposition.TEMPORARY_OR_PUBLIC_LOCATOR
        reason = "temporaryOrPublicLocator"
    elif _has_key(item, _LEGACY_OBJECT_AUTHORITY_KEYS):
        disposition = LegacyArchiveMediaNonPromotionDisposition.LEGACY_AUTHORITY_TAINT
        reason = "legacyArchiveCarriesObjectAuthority"
    elif _has_metadata_only_marker(item):
        disposition = LegacyArchiveMediaNonPromotionDisposition.METADATA_ONLY
        reason = "metadataOnlyArchiveMedia"
    elif _has_unbacked_cloud_claim(item):
        disposition = LegacyArchiveMediaNonPromotionDisposition.UNBACKED_CLOUD_CLAIM
        reason = "unbackedCloudClaim"
    else:
        disposition = LegacyArchiveMediaNonPromotionDisposition.LEGACY_MEDIA_UNCLASSIFIED
        reason = "legacyMediaRequiresOwnerReupload"

    return LegacyArchiveMediaNonPromotionShadow(
        enabled=True,
        disposition=disposition,
        reason_code=reason,
        media_kind=media_kind,
        archive_media_fingerprint=fingerprint,
    )


__all__ = [
    "LEGACY_ARCHIVE_MEDIA_NON_PROMOTION_SHADOW_SCHEMA_VERSION",
    "LegacyArchiveMediaNonPromotionContractError",
    "LegacyArchiveMediaNonPromotionDisposition",
    "LegacyArchiveMediaNonPromotionShadow",
    "inventory_legacy_archive_media_non_promotion",
]
