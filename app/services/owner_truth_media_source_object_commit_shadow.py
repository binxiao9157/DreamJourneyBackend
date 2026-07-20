"""Default-off intent/commit contract shadow for future private SourceObjects.

This G0 module validates only synthetic metadata.  It does not issue an
upload URL, receive bytes, call HEAD, create a database row, or delete an
orphan.  Its output is a value-free preview of what a future object port would
need to prove before it could persist a quarantined SourceObject.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from app.services.owner_truth_media_source_object_shadow import MediaSourceObjectAdmissionContext


MEDIA_SOURCE_OBJECT_INTENT_PROTOCOL_VERSION = "owner-truth-source-object-intent-v1"
MEDIA_SOURCE_OBJECT_COMMIT_PROTOCOL_VERSION = "owner-truth-source-object-commit-v1"
MEDIA_SOURCE_OBJECT_COMMIT_SHADOW_SCHEMA_VERSION = (
    "owner-truth-media-source-object-commit-shadow-v1"
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class MediaSourceObjectCommitShadowContractError(ValueError):
    """A synthetic intent/commit envelope cannot enter the G0 shadow."""


class MediaSourceObjectCommitShadowDisposition(str, Enum):
    SHADOW_DISABLED = "shadow_disabled"
    INVALID_INTENT = "invalid_intent"
    LEGACY_OR_MOCK_INTENT = "legacy_or_mock_intent"
    INTENT_EXPIRED = "intent_expired"
    COMMIT_MISSING = "commit_missing"
    ORPHAN_CANDIDATE = "orphan_candidate"
    INTENT_BINDING_MISMATCH = "intent_binding_mismatch"
    UNSAFE_OBJECT_KEY = "unsafe_object_key"
    COMMIT_VERIFICATION_MISMATCH = "commit_verification_mismatch"
    DUPLICATE_CONFLICT = "duplicate_conflict"
    WOULD_DEDUPLICATE = "would_deduplicate"
    WOULD_COMMIT_QUARANTINED = "would_commit_quarantined"


def _required_text(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise MediaSourceObjectCommitShadowContractError(f"{field} is required")
    return normalized


def _identifier(value: object, *, field: str) -> str:
    normalized = _required_text(value, field=field)
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise MediaSourceObjectCommitShadowContractError(f"{field} must be an opaque identifier")
    return normalized


def _sha256(value: object, *, field: str) -> str:
    normalized = _required_text(value, field=field).lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise MediaSourceObjectCommitShadowContractError(f"{field} must be a lowercase SHA-256 digest")
    return normalized


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MediaSourceObjectCommitShadowContractError(f"{field} must be a positive integer")
    return value


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise MediaSourceObjectCommitShadowContractError("intent/commit values must be serializable") from exc


def _parse_iso(value: object, *, field: str) -> datetime:
    normalized = _required_text(value, field=field)
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MediaSourceObjectCommitShadowContractError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise MediaSourceObjectCommitShadowContractError(f"{field} must include an offset")
    return parsed.astimezone(timezone.utc)


def _safe_object_key(value: object) -> str:
    key = _required_text(value, field="object_key")
    if (
        key.startswith("/")
        or "://" in key
        or "?" in key
        or "#" in key
        or any(segment in {"", ".", ".."} for segment in key.split("/"))
    ):
        raise MediaSourceObjectCommitShadowContractError("object_key is not a private normalized key")
    return key


def _matches_media_kind(*, media_kind: str, magic_mime: str) -> bool:
    prefixes = {
        "image": ("image/",),
        "audio": ("audio/",),
        "video": ("video/",),
        "document": (
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.",
            "text/",
        ),
    }.get(media_kind)
    return prefixes is not None and any(magic_mime.startswith(prefix) for prefix in prefixes)


@dataclass(frozen=True)
class _Intent:
    intent_id: str
    source_object_id: str
    vault_id: str
    owner_subject_id: str
    purpose: str
    media_kind: str
    object_key: str
    max_size_bytes: int
    declared_size_bytes: int
    declared_sha256: str
    expires_at: datetime

    @property
    def fingerprint(self) -> str:
        return sha256(
            _canonical_json(
                {
                    "declaredSha256": self.declared_sha256,
                    "declaredSizeBytes": self.declared_size_bytes,
                    "intentId": self.intent_id,
                    "mediaKind": self.media_kind,
                    "objectKey": self.object_key,
                    "ownerSubjectId": self.owner_subject_id,
                    "purpose": self.purpose,
                    "sourceObjectId": self.source_object_id,
                    "vaultId": self.vault_id,
                }
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class MediaSourceObjectCommitShadow:
    enabled: bool
    disposition: MediaSourceObjectCommitShadowDisposition
    reason_code: str
    intent_fingerprint: str | None = None
    commit_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise MediaSourceObjectCommitShadowContractError("shadow enabled must be a boolean")
        if not isinstance(self.disposition, MediaSourceObjectCommitShadowDisposition):
            raise MediaSourceObjectCommitShadowContractError("shadow disposition is required")
        object.__setattr__(self, "reason_code", _identifier(self.reason_code, field="reason_code"))
        for field in ("intent_fingerprint", "commit_fingerprint"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _sha256(value, field=field))

    @property
    def would_commit_quarantined(self) -> bool:
        return self.disposition is MediaSourceObjectCommitShadowDisposition.WOULD_COMMIT_QUARANTINED

    def value_free_summary(self) -> dict[str, object]:
        summary: dict[str, object] = {
            "candidateProposalPerformed": False,
            "effectAdmissionPerformed": False,
            "enabled": self.enabled,
            "objectStorageOperationPerformed": False,
            "orphanCleanupPerformed": False,
            "providerHeadPerformed": False,
            "reasonCode": self.reason_code,
            "schemaVersion": MEDIA_SOURCE_OBJECT_COMMIT_SHADOW_SCHEMA_VERSION,
            "shadowOnly": True,
            "sourceObjectCreated": False,
            "status": self.disposition.value,
            "wouldCommitQuarantined": self.would_commit_quarantined,
        }
        if self.intent_fingerprint is not None:
            summary["intentFingerprint"] = self.intent_fingerprint
        if self.commit_fingerprint is not None:
            summary["commitFingerprint"] = self.commit_fingerprint
        return summary


def _parse_intent(intent: Mapping[str, Any]) -> _Intent:
    if str(intent.get("protocolVersion") or "").strip() != MEDIA_SOURCE_OBJECT_INTENT_PROTOCOL_VERSION:
        raise MediaSourceObjectCommitShadowContractError("legacy or unknown intent protocol")
    storage = intent.get("storage")
    if not isinstance(storage, Mapping):
        raise MediaSourceObjectCommitShadowContractError("intent storage is required")
    provider = str(storage.get("provider") or "").strip().lower()
    mode = str(storage.get("mode") or "").strip().lower()
    if not provider or provider.startswith("mock") or provider in {"local", "temporary"} or mode != "private":
        raise MediaSourceObjectCommitShadowContractError("intent storage is not private")
    return _Intent(
        intent_id=_identifier(intent.get("intentId"), field="intent_id"),
        source_object_id=_identifier(intent.get("sourceObjectId"), field="source_object_id"),
        vault_id=_identifier(intent.get("vaultId"), field="vault_id"),
        owner_subject_id=_identifier(intent.get("ownerSubjectId"), field="owner_subject_id"),
        purpose=_identifier(intent.get("purpose"), field="purpose"),
        media_kind=_identifier(intent.get("mediaKind"), field="media_kind").lower(),
        object_key=_safe_object_key(storage.get("objectKey")),
        max_size_bytes=_positive_int(intent.get("maxSizeBytes"), field="max_size_bytes"),
        declared_size_bytes=_positive_int(intent.get("declaredSizeBytes"), field="declared_size_bytes"),
        declared_sha256=_sha256(intent.get("declaredSha256"), field="declared_sha256"),
        expires_at=_parse_iso(intent.get("expiresAt"), field="expires_at"),
    )


def _commit_fingerprint(intent: _Intent, commit: Mapping[str, Any]) -> str:
    return sha256(
        _canonical_json(
            {
                "intentFingerprint": intent.fingerprint,
                "magicMime": str(commit.get("magicMime") or "").strip().lower(),
                "objectVersion": int(commit["objectVersion"]),
                "sha256": str(commit.get("sha256") or "").strip().lower(),
                "sizeBytes": int(commit["sizeBytes"]),
            }
        ).encode("utf-8")
    ).hexdigest()


def _orphan_fingerprint(intent: _Intent, observed_object: Mapping[str, Any]) -> str | None:
    try:
        object_key = _safe_object_key(observed_object.get("objectKey"))
        object_version = _positive_int(observed_object.get("objectVersion"), field="object_version")
        digest = _sha256(observed_object.get("sha256"), field="sha256")
    except MediaSourceObjectCommitShadowContractError:
        return None
    if object_key != intent.object_key:
        return None
    return sha256(
        _canonical_json(
            {
                "intentFingerprint": intent.fingerprint,
                "objectVersion": object_version,
                "sha256": digest,
            }
        ).encode("utf-8")
    ).hexdigest()


def _parsed_commit_matches_intent(
    *,
    intent: _Intent,
    commit: Mapping[str, Any],
    context: MediaSourceObjectAdmissionContext,
) -> tuple[str | None, str | None]:
    if str(commit.get("protocolVersion") or "").strip() != MEDIA_SOURCE_OBJECT_COMMIT_PROTOCOL_VERSION:
        return "intentBindingMismatch", None
    try:
        if (
            _identifier(commit.get("intentId"), field="intent_id") != intent.intent_id
            or _identifier(commit.get("sourceObjectId"), field="source_object_id") != intent.source_object_id
            or _identifier(commit.get("vaultId"), field="vault_id") != intent.vault_id
            or _identifier(commit.get("ownerSubjectId"), field="owner_subject_id") != intent.owner_subject_id
            or _identifier(commit.get("purpose"), field="purpose") != intent.purpose
            or intent.vault_id != context.vault_id
            or intent.owner_subject_id != context.owner_subject_id
            or intent.purpose != context.purpose
        ):
            return "ownerVaultOrPurposeMismatch", None
        if _safe_object_key(commit.get("objectKey")) != intent.object_key:
            return "unsafeObjectKey", None
        object_version = _positive_int(commit.get("objectVersion"), field="object_version")
        size_bytes = _positive_int(commit.get("sizeBytes"), field="size_bytes")
        digest = _sha256(commit.get("sha256"), field="sha256")
        magic_mime = _required_text(commit.get("magicMime"), field="magic_mime").lower()
    except MediaSourceObjectCommitShadowContractError:
        return "commitVerificationMismatch", None
    if (
        size_bytes != intent.declared_size_bytes
        or size_bytes > intent.max_size_bytes
        or digest != intent.declared_sha256
        or not _matches_media_kind(media_kind=intent.media_kind, magic_mime=magic_mime)
    ):
        return "commitVerificationMismatch", None
    observed_head = commit.get("observedHead")
    if not isinstance(observed_head, Mapping):
        return "providerHeadEvidenceMissing", None
    try:
        if (
            _safe_object_key(observed_head.get("objectKey")) != intent.object_key
            or _positive_int(observed_head.get("objectVersion"), field="head_object_version") != object_version
            or _positive_int(observed_head.get("sizeBytes"), field="head_size_bytes") != size_bytes
            or _sha256(observed_head.get("sha256"), field="head_sha256") != digest
            or _required_text(observed_head.get("magicMime"), field="head_magic_mime").lower() != magic_mime
        ):
            return "providerHeadVerificationMismatch", None
    except MediaSourceObjectCommitShadowContractError:
        return "providerHeadVerificationMismatch", None
    return None, _commit_fingerprint(
        intent,
        {
            "magicMime": magic_mime,
            "objectVersion": object_version,
            "sha256": digest,
            "sizeBytes": size_bytes,
        },
    )


def build_media_source_object_intent_commit_shadow(
    intent: Mapping[str, Any] | object,
    commit: Mapping[str, Any] | None | object,
    *,
    context: MediaSourceObjectAdmissionContext,
    now_iso: str,
    prior_receipt: Mapping[str, Any] | None = None,
    observed_uncommitted_object: Mapping[str, Any] | None = None,
    enabled: bool = False,
) -> MediaSourceObjectCommitShadow:
    """Preview private intent/commit validation without issuing or consuming it."""

    if not enabled:
        return MediaSourceObjectCommitShadow(
            enabled=False,
            disposition=MediaSourceObjectCommitShadowDisposition.SHADOW_DISABLED,
            reason_code="shadowDisabled",
        )
    if not isinstance(context, MediaSourceObjectAdmissionContext):
        raise MediaSourceObjectCommitShadowContractError("admission context is required")
    if not isinstance(intent, Mapping):
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.INVALID_INTENT,
            reason_code="invalidIntent",
        )
    protocol = str(intent.get("protocolVersion") or "").strip()
    if protocol != MEDIA_SOURCE_OBJECT_INTENT_PROTOCOL_VERSION:
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.LEGACY_OR_MOCK_INTENT,
            reason_code="legacyOrMockIntent",
        )
    storage = intent.get("storage")
    if not isinstance(storage, Mapping):
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.INVALID_INTENT,
            reason_code="invalidIntent",
        )
    storage_provider = str(storage.get("provider") or "").strip().lower()
    storage_mode = str(storage.get("mode") or "").strip().lower()
    if (
        not storage_provider
        or storage_provider.startswith("mock")
        or storage_provider in {"local", "temporary"}
        or storage_mode != "private"
    ):
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.LEGACY_OR_MOCK_INTENT,
            reason_code="legacyOrMockIntent",
        )
    try:
        _safe_object_key(storage.get("objectKey"))
    except MediaSourceObjectCommitShadowContractError:
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.UNSAFE_OBJECT_KEY,
            reason_code="unsafeObjectKey",
        )
    try:
        parsed_intent = _parse_intent(intent)
        now = _parse_iso(now_iso, field="now_iso")
    except MediaSourceObjectCommitShadowContractError:
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.INVALID_INTENT,
            reason_code="invalidIntent",
        )
    if (
        parsed_intent.vault_id != context.vault_id
        or parsed_intent.owner_subject_id != context.owner_subject_id
        or parsed_intent.purpose != context.purpose
    ):
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.INTENT_BINDING_MISMATCH,
            reason_code="ownerVaultOrPurposeMismatch",
            intent_fingerprint=parsed_intent.fingerprint,
        )
    if parsed_intent.declared_size_bytes > parsed_intent.max_size_bytes:
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.INVALID_INTENT,
            reason_code="declaredSizeExceedsLimit",
            intent_fingerprint=parsed_intent.fingerprint,
        )
    if parsed_intent.expires_at <= now:
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.INTENT_EXPIRED,
            reason_code="intentExpired",
            intent_fingerprint=parsed_intent.fingerprint,
        )
    if commit is None:
        orphan_fingerprint = (
            _orphan_fingerprint(parsed_intent, observed_uncommitted_object)
            if isinstance(observed_uncommitted_object, Mapping)
            else None
        )
        if orphan_fingerprint is not None:
            return MediaSourceObjectCommitShadow(
                enabled=True,
                disposition=MediaSourceObjectCommitShadowDisposition.ORPHAN_CANDIDATE,
                reason_code="uncommittedPrivateObjectWouldNeedReconcile",
                intent_fingerprint=parsed_intent.fingerprint,
                commit_fingerprint=orphan_fingerprint,
            )
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.COMMIT_MISSING,
            reason_code="commitMissing",
            intent_fingerprint=parsed_intent.fingerprint,
        )
    if not isinstance(commit, Mapping):
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.COMMIT_VERIFICATION_MISMATCH,
            reason_code="invalidCommit",
            intent_fingerprint=parsed_intent.fingerprint,
        )
    reason_code, commit_fingerprint = _parsed_commit_matches_intent(
        intent=parsed_intent,
        commit=commit,
        context=context,
    )
    if reason_code is not None:
        disposition = (
            MediaSourceObjectCommitShadowDisposition.UNSAFE_OBJECT_KEY
            if reason_code == "unsafeObjectKey"
            else MediaSourceObjectCommitShadowDisposition.INTENT_BINDING_MISMATCH
            if reason_code in {"intentBindingMismatch", "ownerVaultOrPurposeMismatch"}
            else MediaSourceObjectCommitShadowDisposition.COMMIT_VERIFICATION_MISMATCH
        )
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=disposition,
            reason_code=reason_code,
            intent_fingerprint=parsed_intent.fingerprint,
        )
    assert commit_fingerprint is not None
    if prior_receipt is not None:
        existing_fingerprint = str(prior_receipt.get("commitFingerprint") or "").strip().lower()
        if existing_fingerprint == commit_fingerprint:
            return MediaSourceObjectCommitShadow(
                enabled=True,
                disposition=MediaSourceObjectCommitShadowDisposition.WOULD_DEDUPLICATE,
                reason_code="sameCommitWouldReturnExistingQuarantinedReceipt",
                intent_fingerprint=parsed_intent.fingerprint,
                commit_fingerprint=commit_fingerprint,
            )
        return MediaSourceObjectCommitShadow(
            enabled=True,
            disposition=MediaSourceObjectCommitShadowDisposition.DUPLICATE_CONFLICT,
            reason_code="existingReceiptFingerprintConflict",
            intent_fingerprint=parsed_intent.fingerprint,
            commit_fingerprint=commit_fingerprint,
        )
    return MediaSourceObjectCommitShadow(
        enabled=True,
        disposition=MediaSourceObjectCommitShadowDisposition.WOULD_COMMIT_QUARANTINED,
        reason_code="futureCommitWouldCreateQuarantinedObjectOnly",
        intent_fingerprint=parsed_intent.fingerprint,
        commit_fingerprint=commit_fingerprint,
    )


__all__ = [
    "MEDIA_SOURCE_OBJECT_COMMIT_PROTOCOL_VERSION",
    "MEDIA_SOURCE_OBJECT_COMMIT_SHADOW_SCHEMA_VERSION",
    "MEDIA_SOURCE_OBJECT_INTENT_PROTOCOL_VERSION",
    "MediaSourceObjectCommitShadow",
    "MediaSourceObjectCommitShadowContractError",
    "MediaSourceObjectCommitShadowDisposition",
    "build_media_source_object_intent_commit_shadow",
]
