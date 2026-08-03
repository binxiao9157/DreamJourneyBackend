"""Private Owner Truth media-source ingestion contracts.

This module is the first non-shadow media boundary for V4 Stage 2.  It keeps
bytes in a server-owned private store, persists only metadata and hashes in
Postgres, and refuses to treat an unscanned upload as verified input for any
downstream processor.  It deliberately does not expose public object URLs or
promote a media object into a Memory/Candidate by itself.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
from threading import RLock
from typing import Any, Callable, Mapping, Optional, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5
import zipfile

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


OWNER_TRUTH_MEDIA_SOURCE_OBJECT_SCHEMA_VERSION = "owner-truth-media-source-object-v1"
OWNER_TRUTH_MEDIA_UPLOAD_INTENT_SCHEMA_VERSION = "owner-truth-media-upload-intent-v1"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PURPOSE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,79}$")
_MEDIA_KINDS = frozenset({"image", "audio", "video", "document"})
_MIME_TYPES_BY_KIND: dict[str, frozenset[str]] = {
    "image": frozenset({"image/jpeg", "image/png", "image/webp"}),
    "audio": frozenset(
        {
            "audio/mpeg",
            "audio/wav",
            "audio/x-wav",
            "audio/mp4",
            "audio/m4a",
        }
    ),
    "video": frozenset({"video/mp4", "video/quicktime"}),
    "document": frozenset(
        {
            "text/plain",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
    ),
}


class OwnerTruthMediaIngestionError(RuntimeError):
    """Base error for the private media-ingestion boundary."""

    code = "ownerTruthMediaIngestionInvalid"


class OwnerTruthMediaCaptureUnavailable(OwnerTruthMediaIngestionError):
    code = "ownerTruthMediaCaptureUnavailable"


class OwnerTruthMediaUploadInvalid(OwnerTruthMediaIngestionError):
    code = "ownerTruthMediaUploadInvalid"


class OwnerTruthMediaUploadNotFound(OwnerTruthMediaIngestionError):
    code = "ownerTruthMediaUploadNotFound"


class OwnerTruthMediaVaultNotFound(OwnerTruthMediaIngestionError):
    code = "ownerTruthMediaVaultNotFound"


class OwnerTruthMediaAuthorityEpochConflict(OwnerTruthMediaIngestionError):
    code = "ownerTruthMediaAuthorityEpochConflict"

    def __init__(self, *, expected_epoch: int, current_epoch: int) -> None:
        super().__init__("owner truth media authority epoch is stale")
        self.expected_epoch = expected_epoch
        self.current_epoch = current_epoch


class OwnerTruthMediaUploadConflict(OwnerTruthMediaIngestionError):
    code = "ownerTruthMediaUploadConflict"


class OwnerTruthMediaUploadExpired(OwnerTruthMediaIngestionError):
    code = "ownerTruthMediaUploadExpired"


class OwnerTruthMediaUploadTokenInvalid(OwnerTruthMediaIngestionError):
    code = "ownerTruthMediaUploadTokenInvalid"


class OwnerTruthMediaContentSafetyUnavailable(OwnerTruthMediaIngestionError):
    code = "ownerTruthMediaContentSafetyUnavailable"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: object) -> datetime:
    normalized = str(value or "").strip()
    if not normalized:
        raise OwnerTruthMediaUploadInvalid("client created time is required")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OwnerTruthMediaUploadInvalid("client created time is invalid") from exc
    if parsed.tzinfo is None:
        raise OwnerTruthMediaUploadInvalid("client created time must include timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return sha256(payload).hexdigest()


def _safe_file_name(value: object) -> str:
    name = str(value or "").strip()
    if (
        not name
        or len(name) > 255
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise OwnerTruthMediaUploadInvalid("file name is invalid")
    return name


def _normalize_content_type(value: object) -> str:
    content_type = str(value or "").strip().lower().split(";", 1)[0].strip()
    if not content_type or len(content_type) > 160:
        raise OwnerTruthMediaUploadInvalid("content type is invalid")
    return content_type


def _normalize_media_kind(value: object) -> str:
    kind = str(value or "").strip().lower()
    if kind not in _MEDIA_KINDS:
        raise OwnerTruthMediaUploadInvalid("media kind is invalid")
    return kind


def _normalize_sha256(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise OwnerTruthMediaUploadInvalid("content sha256 is invalid")
    return normalized


def _normalize_identifier(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 128:
        raise OwnerTruthMediaUploadInvalid(f"{field} is invalid")
    return normalized


@dataclass(frozen=True)
class MediaUploadIntentCommand:
    command_id: str
    expected_authority_epoch: int
    media_kind: str
    file_name: str
    content_type: str
    file_size_bytes: int
    content_sha256: str
    purpose: str
    client_created_at: datetime
    external_processing_allowed: bool = False

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "MediaUploadIntentCommand":
        required_fields = {
            "commandId",
            "expectedAuthorityEpoch",
            "mediaKind",
            "fileName",
            "contentType",
            "fileSizeBytes",
            "contentSha256",
            "purpose",
            "clientCreatedAt",
        }
        allowed_fields = required_fields | {"allowExternalProcessing"}
        if not required_fields.issubset(payload) or not set(payload).issubset(allowed_fields):
            raise OwnerTruthMediaUploadInvalid("media upload intent payload is invalid")
        raw_command_id = payload.get("commandId")
        try:
            command_id = str(UUID(str(raw_command_id)))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthMediaUploadInvalid("command id is invalid") from exc
        expected_epoch = payload.get("expectedAuthorityEpoch")
        if type(expected_epoch) is not int or expected_epoch < 0:
            raise OwnerTruthMediaUploadInvalid("authority epoch is invalid")
        file_size_bytes = payload.get("fileSizeBytes")
        if type(file_size_bytes) is not int or file_size_bytes < 1:
            raise OwnerTruthMediaUploadInvalid("file size is invalid")
        media_kind = _normalize_media_kind(payload.get("mediaKind"))
        external_processing_allowed = payload.get("allowExternalProcessing", False)
        if type(external_processing_allowed) is not bool:
            raise OwnerTruthMediaUploadInvalid("external processing permission is invalid")
        if external_processing_allowed and media_kind not in {"image", "audio"}:
            raise OwnerTruthMediaUploadInvalid("external processing is not supported for media kind")
        content_type = _normalize_content_type(payload.get("contentType"))
        if content_type not in _MIME_TYPES_BY_KIND[media_kind]:
            raise OwnerTruthMediaUploadInvalid("content type is not supported for media kind")
        purpose = str(payload.get("purpose") or "").strip()
        if not _PURPOSE_PATTERN.fullmatch(purpose):
            raise OwnerTruthMediaUploadInvalid("purpose is invalid")
        return cls(
            command_id=command_id,
            expected_authority_epoch=expected_epoch,
            media_kind=media_kind,
            file_name=_safe_file_name(payload.get("fileName")),
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            content_sha256=_normalize_sha256(payload.get("contentSha256")),
            purpose=purpose,
            external_processing_allowed=external_processing_allowed,
            client_created_at=_parse_iso(payload.get("clientCreatedAt")),
        )

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)

    def source_object_id(self, *, vault_id: str) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                f"dreamjourney-owner-truth-media-source-object-v1:{vault_id}:{self.command_id}",
            )
        )

    def upload_intent_id(self, *, vault_id: str) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                f"dreamjourney-owner-truth-media-upload-intent-v1:{vault_id}:{self.command_id}",
            )
        )

    def payload_hash(self, *, vault_id: str) -> str:
        return _sha256(
            _canonical_json(
                {
                    "vaultId": vault_id,
                    "expectedAuthorityEpoch": self.expected_authority_epoch,
                    "mediaKind": self.media_kind,
                    "fileName": self.file_name,
                    "contentType": self.content_type,
                    "fileSizeBytes": self.file_size_bytes,
                    "contentSha256": self.content_sha256,
                    "purpose": self.purpose,
                    "clientCreatedAt": _utc_iso(self.client_created_at),
                    **(
                        {"allowExternalProcessing": True}
                        if self.external_processing_allowed
                        else {}
                    ),
                }
            )
        )


@dataclass(frozen=True)
class MediaSafetyVerdict:
    status: str
    provider: str
    reason_code: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in {"clean", "blocked", "unavailable"}:
            raise ValueError("unsupported media safety verdict")
        if not str(self.provider or "").strip():
            raise ValueError("media safety provider is required")
        if self.status == "clean" and self.reason_code is not None:
            raise ValueError("clean media safety verdict cannot carry a reason")
        if self.status != "clean" and not str(self.reason_code or "").strip():
            raise ValueError("non-clean media safety verdict requires a reason")


class MediaContentSafetyScanner(Protocol):
    def inspect(self, *, media_kind: str, content_type: str, payload: bytes) -> MediaSafetyVerdict:
        ...


class DisabledMediaContentSafetyScanner:
    """Fail closed until a real scanner is configured."""

    def inspect(self, *, media_kind: str, content_type: str, payload: bytes) -> MediaSafetyVerdict:
        del media_kind, content_type, payload
        return MediaSafetyVerdict(
            status="unavailable",
            provider="disabled",
            reason_code="contentSafetyScannerUnavailable",
        )


class TestOnlyCleanMediaContentSafetyScanner:
    """Deterministic test scanner. Never instantiate it for production traffic."""

    def inspect(self, *, media_kind: str, content_type: str, payload: bytes) -> MediaSafetyVerdict:
        del media_kind, content_type, payload
        return MediaSafetyVerdict(status="clean", provider="testOnlyClean")


class ClamAVMediaContentSafetyScanner:
    """Uses a locally managed ClamAV binary without sending bytes to a provider."""

    def __init__(self, *, executable: str = "clamscan", timeout_seconds: int = 30) -> None:
        self._executable = str(executable or "clamscan").strip() or "clamscan"
        self._timeout_seconds = max(1, int(timeout_seconds))

    def inspect(self, *, media_kind: str, content_type: str, payload: bytes) -> MediaSafetyVerdict:
        del media_kind, content_type
        executable = shutil.which(self._executable)
        if executable is None:
            return MediaSafetyVerdict(
                status="unavailable",
                provider="clamav",
                reason_code="contentSafetyScannerUnavailable",
            )
        try:
            completed = subprocess.run(
                [executable, "--no-summary", "-"],
                input=payload,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=self._timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            return MediaSafetyVerdict(
                status="unavailable",
                provider="clamav",
                reason_code="contentSafetyScannerUnavailable",
            )
        if completed.returncode == 0:
            return MediaSafetyVerdict(status="clean", provider="clamav")
        if completed.returncode == 1:
            return MediaSafetyVerdict(
                status="blocked",
                provider="clamav",
                reason_code="contentSafetyScanBlocked",
            )
        return MediaSafetyVerdict(
            status="unavailable",
            provider="clamav",
            reason_code="contentSafetyScannerUnavailable",
        )


class PrivateMediaObjectStore(Protocol):
    provider_name: str

    def write(self, *, storage_key: str, payload: bytes) -> None:
        ...

    def delete(self, *, storage_key: str) -> None:
        ...

    def read(self, *, storage_key: str) -> bytes:
        ...


class DisabledPrivateMediaObjectStore:
    provider_name = "disabled"

    def write(self, *, storage_key: str, payload: bytes) -> None:
        del storage_key, payload
        raise OwnerTruthMediaCaptureUnavailable("private media storage is not configured")

    def delete(self, *, storage_key: str) -> None:
        del storage_key

    def read(self, *, storage_key: str) -> bytes:
        del storage_key
        raise OwnerTruthMediaCaptureUnavailable("private media storage is not configured")


class FilesystemPrivateMediaObjectStore:
    """Durable, non-public object adapter backed by a mounted server volume."""

    provider_name = "filesystem"

    def __init__(self, *, root: str | Path) -> None:
        candidate = Path(root).expanduser()
        candidate.mkdir(parents=True, exist_ok=True)
        self._root = candidate.resolve()

    def write(self, *, storage_key: str, payload: bytes) -> None:
        target = self._resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def delete(self, *, storage_key: str) -> None:
        self._resolve(storage_key).unlink(missing_ok=True)

    def read(self, *, storage_key: str) -> bytes:
        return self._resolve(storage_key).read_bytes()

    def _resolve(self, storage_key: str) -> Path:
        normalized = _private_storage_key(storage_key)
        path = PurePosixPath(normalized)
        target = (self._root / Path(*path.parts)).resolve()
        if target != self._root and self._root not in target.parents:
            raise OwnerTruthMediaUploadInvalid("storage key escapes private root")
        return target


class S3PrivateMediaObjectStore:
    """Private S3-compatible object adapter for the Stage 2 media boundary.

    This works with an S3 API compatible bucket, including a private Tencent
    COS bucket configured through its S3 endpoint. Public ACLs and presigned
    URLs are deliberately not part of this adapter: the API owns all byte
    transfer and keeps the bucket/key implementation detail server-side.
    """

    provider_name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "dreamjourney/private-media",
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        server_side_encryption: Optional[str] = None,
        kms_key_id: Optional[str] = None,
        client: Any = None,
    ) -> None:
        self._bucket = _private_bucket_name(bucket)
        self._prefix = _private_storage_prefix(prefix)
        self._server_side_encryption = _optional_identifier(
            server_side_encryption,
            field="server side encryption",
        )
        self._kms_key_id = _optional_identifier(kms_key_id, field="kms key id")
        if self._server_side_encryption not in {None, "AES256", "aws:kms"}:
            raise OwnerTruthMediaUploadInvalid("server side encryption is invalid")
        if self._kms_key_id is not None and self._server_side_encryption != "aws:kms":
            raise OwnerTruthMediaUploadInvalid("kms key requires aws:kms encryption")
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - dependency is packaged in production
                raise OwnerTruthMediaCaptureUnavailable("s3 media storage client is unavailable") from exc
            client = boto3.client(
                "s3",
                region_name=str(region or "").strip() or None,
                endpoint_url=str(endpoint_url or "").strip() or None,
                aws_access_key_id=str(access_key_id or "").strip() or None,
                aws_secret_access_key=str(secret_access_key or "").strip() or None,
            )
        self._client = client

    def write(self, *, storage_key: str, payload: bytes) -> None:
        request: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": self._object_key(storage_key),
            "Body": payload,
        }
        if self._server_side_encryption is not None:
            request["ServerSideEncryption"] = self._server_side_encryption
        if self._kms_key_id is not None:
            request["SSEKMSKeyId"] = self._kms_key_id
        try:
            self._client.put_object(**request)
        except Exception as exc:
            raise OwnerTruthMediaCaptureUnavailable("private media object write is unavailable") from exc

    def delete(self, *, storage_key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=self._object_key(storage_key))
        except Exception:
            # Cleanup is best effort; callers are already handling the original
            # metadata transition failure and must not receive provider details.
            return

    def read(self, *, storage_key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._object_key(storage_key))
            body = response["Body"]
            payload = body.read()
        except Exception as exc:
            raise OwnerTruthMediaCaptureUnavailable("private media object read is unavailable") from exc
        if not isinstance(payload, bytes):
            raise OwnerTruthMediaCaptureUnavailable("private media object read is unavailable")
        return payload

    def _object_key(self, storage_key: str) -> str:
        normalized = _private_storage_key(storage_key)
        return f"{self._prefix}/{normalized}" if self._prefix else normalized


def _private_storage_key(value: object) -> str:
    normalized = str(value or "").strip()
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise OwnerTruthMediaUploadInvalid("storage key is invalid")
    return normalized


def _private_storage_prefix(value: object) -> str:
    normalized = str(value or "").strip().strip("/")
    if not normalized:
        return ""
    return _private_storage_key(normalized)


def _private_bucket_name(value: object) -> str:
    bucket = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{1,126}[A-Za-z0-9]", bucket):
        raise OwnerTruthMediaUploadInvalid("private media bucket is invalid")
    return bucket


def _optional_identifier(value: object, *, field: str) -> Optional[str]:
    normalized = str(value or "").strip() or None
    if normalized is not None and (len(normalized) > 512 or any(character.isspace() for character in normalized)):
        raise OwnerTruthMediaUploadInvalid(f"{field} is invalid")
    return normalized


def build_private_media_object_store(
    *,
    provider: str,
    root: str,
    s3_bucket: Optional[str] = None,
    s3_prefix: str = "dreamjourney/private-media",
    s3_region: Optional[str] = None,
    s3_endpoint_url: Optional[str] = None,
    s3_access_key_id: Optional[str] = None,
    s3_secret_access_key: Optional[str] = None,
    s3_server_side_encryption: Optional[str] = None,
    s3_kms_key_id: Optional[str] = None,
) -> PrivateMediaObjectStore:
    normalized = str(provider or "").strip().lower()
    if normalized == "filesystem":
        return FilesystemPrivateMediaObjectStore(root=root)
    if normalized in {"s3", "cos"}:
        if not str(s3_bucket or "").strip():
            return DisabledPrivateMediaObjectStore()
        try:
            return S3PrivateMediaObjectStore(
                bucket=str(s3_bucket),
                prefix=s3_prefix,
                region=s3_region,
                endpoint_url=s3_endpoint_url,
                access_key_id=s3_access_key_id,
                secret_access_key=s3_secret_access_key,
                server_side_encryption=s3_server_side_encryption,
                kms_key_id=s3_kms_key_id,
            )
        except OwnerTruthMediaIngestionError:
            return DisabledPrivateMediaObjectStore()
    return DisabledPrivateMediaObjectStore()


def build_media_content_safety_scanner(
    *,
    provider: str,
    environment: str,
) -> MediaContentSafetyScanner:
    normalized = str(provider or "").strip().lower()
    if normalized == "clamav":
        return ClamAVMediaContentSafetyScanner()
    if normalized == "testclean" and str(environment or "").strip().lower() not in {
        "production",
        "prod",
    }:
        return TestOnlyCleanMediaContentSafetyScanner()
    return DisabledMediaContentSafetyScanner()


def inspect_magic_mime(*, media_kind: str, payload: bytes) -> str:
    """Accept only a small, explicit MIME matrix before private persistence."""

    kind = _normalize_media_kind(media_kind)
    if not payload:
        raise OwnerTruthMediaUploadInvalid("uploaded media is empty")
    if kind == "document":
        if payload.startswith(b"%PDF-"):
            return "application/pdf"
        if b"\x00" not in payload:
            try:
                payload.decode("utf-8")
            except UnicodeDecodeError:
                pass
            else:
                return "text/plain"
        try:
            with zipfile.ZipFile(BytesIO(payload)) as archive:
                names = set(archive.namelist())
        except (OSError, zipfile.BadZipFile):
            names = set()
        if {"[Content_Types].xml", "word/document.xml"}.issubset(names):
            return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif kind == "image":
        if payload.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
            return "image/webp"
    elif kind == "audio":
        if payload.startswith(b"RIFF") and payload[8:12] == b"WAVE":
            return "audio/wav"
        if payload.startswith(b"ID3") or payload.startswith(b"\xff\xfb") or payload.startswith(b"\xff\xf3"):
            return "audio/mpeg"
        if len(payload) >= 12 and payload[4:8] == b"ftyp":
            return "audio/mp4"
    elif kind == "video":
        if len(payload) >= 12 and payload[4:8] == b"ftyp":
            brand = payload[8:12]
            return "video/quicktime" if brand == b"qt  " else "video/mp4"
    raise OwnerTruthMediaUploadInvalid("media magic mime is not supported")


def _content_type_matches(*, expected: str, observed: str, media_kind: str) -> bool:
    if expected == observed:
        return True
    aliases = {
        ("audio/m4a", "audio/mp4"),
        ("audio/x-wav", "audio/wav"),
    }
    return media_kind == "audio" and (expected, observed) in aliases


@dataclass(frozen=True)
class MediaUploadIntentCreateResult:
    outcome: str
    source_object: Mapping[str, Any]
    upload_intent: Mapping[str, Any]
    upload_token: Optional[str]


class OwnerTruthMediaSourceObjectRepository(Protocol):
    def create_upload_intent(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: MediaUploadIntentCommand,
        upload_token_hash: str,
        expires_at: datetime,
    ) -> MediaUploadIntentCreateResult:
        ...

    def load_upload_intent(
        self,
        *,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
    ) -> Mapping[str, Any]:
        ...

    def complete_upload(
        self,
        *,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
        upload_token_hash: str,
        magic_mime: str,
        storage_provider: str,
        storage_key: str,
        safety_verdict: MediaSafetyVerdict,
    ) -> tuple[str, Mapping[str, Any]]:
        ...

    def reject_upload(
        self,
        *,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
        upload_token_hash: str,
        safety_verdict: MediaSafetyVerdict,
    ) -> Mapping[str, Any]:
        ...

    def get_source_object(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
    ) -> Mapping[str, Any]:
        ...

    def queue_processing(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
    ) -> Mapping[str, Any]:
        ...

    def begin_processing(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        expected_authority_epoch: int,
        expected_processing_generation: int,
        attempt: int,
    ) -> Mapping[str, Any]:
        ...

    def record_processing_outcome(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        processing_generation: int,
        attempt: int,
        processor_id: str,
        processor_version: str,
        outcome: str,
        result_hash: str,
        extracted_text_sha256: Optional[str] = None,
        derived_source_id: Optional[str] = None,
        failure_code: Optional[str] = None,
    ) -> Mapping[str, Any]:
        ...


def _object_public_receipt(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sourceObjectId": str(record["sourceObjectId"]),
        "mediaKind": str(record["mediaKind"]),
        "state": str(record["state"]),
        "contentType": str(record["contentType"]),
        "magicMime": record.get("magicMime"),
        "fileName": str(record["fileName"]),
        "fileSizeBytes": int(record["fileSizeBytes"]),
        "contentSha256": str(record["contentSha256"]),
        "safetyStatus": str(record["safetyStatus"]),
        "safetyProvider": record.get("safetyProvider"),
        "processingStatus": str(record["processingStatus"]),
        "processingGeneration": int(record.get("processingGeneration") or 0),
        "externalProcessingAllowed": bool(record.get("externalProcessingAllowed", False)),
        "retryable": bool(record.get("retryable", False)),
        "failureCode": record.get("failureCode"),
        "derivedSourceId": record.get("derivedSourceId"),
        "updatedAt": str(record["updatedAt"]),
    }


def _intent_public_receipt(record: Mapping[str, Any], *, upload_token: Optional[str]) -> dict[str, Any]:
    result = {
        "uploadIntentId": str(record["uploadIntentId"]),
        "state": str(record["state"]),
        "expiresAt": str(record["expiresAt"]),
        "transport": "authenticatedDirectUpload",
        "uploadMethod": "PUT",
        "uploadTokenHeader": "X-DreamJourney-Upload-Token",
        "requiresClientUpload": str(record["state"]) == "pending",
    }
    if upload_token is not None:
        result["uploadToken"] = upload_token
    return result


class InMemoryOwnerTruthMediaSourceObjectRepository:
    """Semantic double for the private V2 media object persistence contract."""

    def __init__(self, *, vaults: dict[str, dict[str, Any]], lock: RLock) -> None:
        self._vaults = vaults
        self._lock = lock
        self._objects: dict[tuple[str, str], dict[str, Any]] = {}
        self._intents: dict[tuple[str, str], dict[str, Any]] = {}
        self._intent_by_command: dict[tuple[str, str], str] = {}
        self._processing_results: dict[str, dict[str, Any]] = {}

    def create_upload_intent(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: MediaUploadIntentCommand,
        upload_token_hash: str,
        expires_at: datetime,
    ) -> MediaUploadIntentCreateResult:
        with self._lock:
            vault = self._ensure_owned_vault(context)
            existing_intent_id = self._intent_by_command.get(
                (context.vault_id, command.command_id_hash)
            )
            if existing_intent_id is not None:
                intent = self._intents[(context.vault_id, existing_intent_id)]
                if intent["payloadHash"] != command.payload_hash(vault_id=context.vault_id):
                    raise OwnerTruthMediaUploadConflict("command id cannot change media payload")
                source_object = self._objects[(context.vault_id, intent["sourceObjectId"])]
                return MediaUploadIntentCreateResult(
                    outcome="deduplicated",
                    source_object=deepcopy(source_object),
                    upload_intent=deepcopy(intent),
                    upload_token=None,
                )
            current_epoch = int(vault.get("authorityEpoch") or 0)
            if command.expected_authority_epoch != current_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=command.expected_authority_epoch,
                    current_epoch=current_epoch,
                )
            source_object_id = command.source_object_id(vault_id=context.vault_id)
            upload_intent_id = command.upload_intent_id(vault_id=context.vault_id)
            if (context.vault_id, source_object_id) in self._objects:
                raise OwnerTruthMediaUploadConflict("source object id is already present")
            now = _utc_now()
            source_object = {
                "sourceObjectId": source_object_id,
                "vaultId": context.vault_id,
                "ownerSubjectId": context.owner_subject_id,
                "mediaKind": command.media_kind,
                "state": "uploadPending",
                "contentType": command.content_type,
                "magicMime": None,
                "fileName": command.file_name,
                "fileSizeBytes": command.file_size_bytes,
                "contentSha256": command.content_sha256,
                "storageProvider": None,
                "storageKey": None,
                "storageVersion": 0,
                "safetyStatus": "pending",
                "safetyProvider": None,
                "processingStatus": "notQueued",
                "processingGeneration": 0,
                "externalProcessingAllowed": command.external_processing_allowed,
                "processingAttempt": 0,
                "retryable": False,
                "failureCode": None,
                "derivedSourceId": None,
                "lastProcessingResultId": None,
                "authorityEpoch": current_epoch,
                "policyVersion": context.authorization_capture.policy_version
                if context.authorization_capture is not None
                else "release-policy-v1",
                "rowVersion": 1,
                "createdAt": _utc_iso(now),
                "updatedAt": _utc_iso(now),
                "uploadedAt": None,
                "originCommandIdHash": command.command_id_hash,
            }
            intent = {
                "uploadIntentId": upload_intent_id,
                "vaultId": context.vault_id,
                "sourceObjectId": source_object_id,
                "ownerSubjectId": context.owner_subject_id,
                "commandIdHash": command.command_id_hash,
                "payloadHash": command.payload_hash(vault_id=context.vault_id),
                "uploadTokenHash": upload_token_hash,
                "state": "pending",
                "expiresAt": _utc_iso(expires_at),
                "createdAt": _utc_iso(now),
                "updatedAt": _utc_iso(now),
                "uploadedAt": None,
            }
            self._objects[(context.vault_id, source_object_id)] = source_object
            self._intents[(context.vault_id, upload_intent_id)] = intent
            self._intent_by_command[(context.vault_id, command.command_id_hash)] = upload_intent_id
            return MediaUploadIntentCreateResult(
                outcome="created",
                source_object=deepcopy(source_object),
                upload_intent=deepcopy(intent),
                upload_token=None,
            )

    def attach_upload_token(
        self,
        result: MediaUploadIntentCreateResult,
        *,
        upload_token: str,
    ) -> MediaUploadIntentCreateResult:
        return MediaUploadIntentCreateResult(
            outcome=result.outcome,
            source_object=result.source_object,
            upload_intent=result.upload_intent,
            upload_token=upload_token if result.outcome == "created" else None,
        )

    def load_upload_intent(
        self,
        *,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
    ) -> Mapping[str, Any]:
        with self._lock:
            intent = self._intents.get((vault_id, intent_id))
            if intent is None:
                raise OwnerTruthMediaUploadNotFound("upload intent was not found")
            if intent["ownerSubjectId"] != owner_subject_id:
                raise OwnerTruthMediaVaultNotFound("vault was not found")
            source_object = self._objects[(vault_id, intent["sourceObjectId"])]
            return {"intent": deepcopy(intent), "sourceObject": deepcopy(source_object)}

    def complete_upload(
        self,
        *,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
        upload_token_hash: str,
        magic_mime: str,
        storage_provider: str,
        storage_key: str,
        safety_verdict: MediaSafetyVerdict,
    ) -> tuple[str, Mapping[str, Any]]:
        with self._lock:
            intent, source_object = self._owned_upload(
                vault_id=vault_id,
                intent_id=intent_id,
                owner_subject_id=owner_subject_id,
                upload_token_hash=upload_token_hash,
            )
            if intent["state"] == "uploaded":
                return "deduplicated", deepcopy(source_object)
            self._assert_pending_intent(intent)
            now = _utc_now()
            intent.update(state="uploaded", uploadedAt=_utc_iso(now), updatedAt=_utc_iso(now))
            source_object.update(
                state="verified",
                magicMime=magic_mime,
                storageProvider=storage_provider,
                storageKey=storage_key,
                storageVersion=1,
                safetyStatus=safety_verdict.status,
                safetyProvider=safety_verdict.provider,
                processingStatus="notQueued",
                retryable=False,
                failureCode=None,
                uploadedAt=_utc_iso(now),
                updatedAt=_utc_iso(now),
                rowVersion=int(source_object["rowVersion"]) + 1,
            )
            return "uploaded", deepcopy(source_object)

    def reject_upload(
        self,
        *,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
        upload_token_hash: str,
        safety_verdict: MediaSafetyVerdict,
    ) -> Mapping[str, Any]:
        with self._lock:
            intent, source_object = self._owned_upload(
                vault_id=vault_id,
                intent_id=intent_id,
                owner_subject_id=owner_subject_id,
                upload_token_hash=upload_token_hash,
            )
            self._assert_pending_intent(intent)
            now = _utc_now()
            intent.update(state="rejected", updatedAt=_utc_iso(now))
            source_object.update(
                state="quarantined",
                safetyStatus=safety_verdict.status,
                safetyProvider=safety_verdict.provider,
                processingStatus="blocked",
                retryable=safety_verdict.status == "unavailable",
                failureCode=safety_verdict.reason_code,
                updatedAt=_utc_iso(now),
                rowVersion=int(source_object["rowVersion"]) + 1,
            )
            return deepcopy(source_object)

    def get_source_object(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
    ) -> Mapping[str, Any]:
        with self._lock:
            source_object = self._objects.get((vault_id, source_object_id))
            if source_object is None or source_object["ownerSubjectId"] != owner_subject_id:
                raise OwnerTruthMediaVaultNotFound("source object was not found")
            return deepcopy(source_object)

    def queue_processing(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
    ) -> Mapping[str, Any]:
        with self._lock:
            source_object = self._owned_source_object(
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
            )
            if source_object["state"] != "verified" or source_object["safetyStatus"] != "clean":
                raise OwnerTruthMediaUploadConflict("media source object is not verified for processing")
            if source_object["mediaKind"] == "video":
                return self._record_processing_outcome_locked(
                    source_object=source_object,
                    processing_generation=0,
                    attempt=0,
                    processor_id="videoStorageOnly",
                    processor_version="v1",
                    outcome="notApplicable",
                    result_hash=_sha256(
                        f"video-storage-only:{source_object['sourceObjectId']}:{source_object['contentSha256']}"
                    ),
                )
            if source_object["processingStatus"] in {"queued", "processing", "retryableFailed", "succeeded"}:
                return deepcopy(source_object)
            now = _utc_now()
            source_object.update(
                processingStatus="queued",
                processingGeneration=int(source_object.get("processingGeneration") or 0) + 1,
                retryable=False,
                failureCode=None,
                updatedAt=_utc_iso(now),
                rowVersion=int(source_object["rowVersion"]) + 1,
            )
            return deepcopy(source_object)

    def begin_processing(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        expected_authority_epoch: int,
        expected_processing_generation: int,
        attempt: int,
    ) -> Mapping[str, Any]:
        if type(attempt) is not int or attempt < 1:
            raise OwnerTruthMediaUploadInvalid("processing attempt is invalid")
        with self._lock:
            source_object = self._owned_source_object(
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
            )
            if int(source_object["authorityEpoch"]) != expected_authority_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=expected_authority_epoch,
                    current_epoch=int(source_object["authorityEpoch"]),
                )
            if int(source_object.get("processingGeneration") or 0) != expected_processing_generation:
                raise OwnerTruthMediaUploadConflict("media processing generation is no longer current")
            if (
                source_object["state"] != "verified"
                or source_object["safetyStatus"] != "clean"
                or source_object["processingStatus"] not in {"queued", "retryableFailed"}
            ):
                raise OwnerTruthMediaUploadConflict("media source object is not eligible for processing")
            now = _utc_now()
            source_object.update(
                state="processing",
                processingStatus="processing",
                processingAttempt=attempt,
                retryable=False,
                failureCode=None,
                updatedAt=_utc_iso(now),
                rowVersion=int(source_object["rowVersion"]) + 1,
            )
            return deepcopy(source_object)

    def record_processing_outcome(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        processing_generation: int,
        attempt: int,
        processor_id: str,
        processor_version: str,
        outcome: str,
        result_hash: str,
        extracted_text_sha256: Optional[str] = None,
        derived_source_id: Optional[str] = None,
        failure_code: Optional[str] = None,
    ) -> Mapping[str, Any]:
        with self._lock:
            source_object = self._owned_source_object(
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
            )
            return self._record_processing_outcome_locked(
                source_object=source_object,
                processing_generation=processing_generation,
                attempt=attempt,
                processor_id=processor_id,
                processor_version=processor_version,
                outcome=outcome,
                result_hash=result_hash,
                extracted_text_sha256=extracted_text_sha256,
                derived_source_id=derived_source_id,
                failure_code=failure_code,
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sourceObjects": deepcopy(self._objects),
                "uploadIntents": deepcopy(self._intents),
                "processingResults": deepcopy(self._processing_results),
            }

    def _owned_source_object(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
    ) -> dict[str, Any]:
        source_object = self._objects.get((vault_id, source_object_id))
        if source_object is None or source_object["ownerSubjectId"] != owner_subject_id:
            raise OwnerTruthMediaVaultNotFound("source object was not found")
        return source_object

    def _record_processing_outcome_locked(
        self,
        *,
        source_object: dict[str, Any],
        processing_generation: int,
        attempt: int,
        processor_id: str,
        processor_version: str,
        outcome: str,
        result_hash: str,
        extracted_text_sha256: Optional[str] = None,
        derived_source_id: Optional[str] = None,
        failure_code: Optional[str] = None,
    ) -> Mapping[str, Any]:
        if type(processing_generation) is not int or processing_generation < 0:
            raise OwnerTruthMediaUploadInvalid("media processing generation is invalid")
        if int(source_object.get("processingGeneration") or 0) != processing_generation:
            raise OwnerTruthMediaUploadConflict("media processing generation is no longer current")
        if type(attempt) is not int or attempt < 0:
            raise OwnerTruthMediaUploadInvalid("processing attempt is invalid")
        normalized_processor_id = str(processor_id or "").strip()
        normalized_processor_version = str(processor_version or "").strip()
        if not _PURPOSE_PATTERN.fullmatch(normalized_processor_id) or not _PURPOSE_PATTERN.fullmatch(
            normalized_processor_version
        ):
            raise OwnerTruthMediaUploadInvalid("media processor identity is invalid")
        normalized_outcome = str(outcome or "").strip()
        if normalized_outcome not in {"succeeded", "retryableFailed", "failed", "notApplicable"}:
            raise OwnerTruthMediaUploadInvalid("media processing outcome is invalid")
        normalized_result_hash = _normalize_sha256(result_hash)
        normalized_text_hash = (
            None if extracted_text_sha256 is None else _normalize_sha256(extracted_text_sha256)
        )
        normalized_source_id = None
        if derived_source_id is not None:
            try:
                normalized_source_id = str(UUID(str(derived_source_id)))
            except (TypeError, ValueError) as exc:
                raise OwnerTruthMediaUploadInvalid("derived source id is invalid") from exc
        normalized_failure_code = str(failure_code or "").strip() or None
        if normalized_failure_code is not None and not _PURPOSE_PATTERN.fullmatch(normalized_failure_code):
            raise OwnerTruthMediaUploadInvalid("media processing failure code is invalid")
        if normalized_outcome == "succeeded":
            if normalized_text_hash is None or normalized_source_id is None or normalized_failure_code is not None:
                raise OwnerTruthMediaUploadInvalid("successful media processing result is incomplete")
            state, processing_status, retryable = "processed", "succeeded", False
        elif normalized_outcome == "retryableFailed":
            if normalized_text_hash is not None or normalized_source_id is not None or normalized_failure_code is None:
                raise OwnerTruthMediaUploadInvalid("retryable media processing result is incomplete")
            state, processing_status, retryable = "verified", "retryableFailed", True
        elif normalized_outcome == "failed":
            if normalized_text_hash is not None or normalized_source_id is not None or normalized_failure_code is None:
                raise OwnerTruthMediaUploadInvalid("failed media processing result is incomplete")
            # The private object remains valid and readable to future verified
            # processors.  Only this processing request failed terminally.
            state, processing_status, retryable = "verified", "failed", False
        else:
            if normalized_text_hash is not None or normalized_source_id is not None or normalized_failure_code is not None:
                raise OwnerTruthMediaUploadInvalid("not applicable media processing result is invalid")
            state, processing_status, retryable = "verified", "notApplicable", False

        result_id = str(
            uuid5(
                NAMESPACE_URL,
                "dreamjourney-owner-truth-media-processing-v1:"
                f"{source_object['sourceObjectId']}:{normalized_processor_id}:"
                f"{normalized_processor_version}:{processing_generation}:{attempt}",
            )
        )
        result = {
            "processingResultId": result_id,
            "vaultId": source_object["vaultId"],
            "sourceObjectId": source_object["sourceObjectId"],
            "ownerSubjectId": source_object["ownerSubjectId"],
            "processorId": normalized_processor_id,
            "processorVersion": normalized_processor_version,
            "state": normalized_outcome,
            "processingGeneration": processing_generation,
            "attempt": attempt,
            "resultHash": normalized_result_hash,
            "extractedTextSha256": normalized_text_hash,
            "derivedSourceId": normalized_source_id,
            "failureCode": normalized_failure_code,
        }
        existing = self._processing_results.get(result_id)
        if existing is not None and existing != result:
            raise OwnerTruthMediaUploadConflict("media processing result cannot change immutable meaning")
        self._processing_results[result_id] = result
        now = _utc_now()
        source_object.update(
            state=state,
            processingStatus=processing_status,
            processingAttempt=attempt,
            retryable=retryable,
            failureCode=normalized_failure_code,
            derivedSourceId=normalized_source_id,
            lastProcessingResultId=result_id,
            updatedAt=_utc_iso(now),
            rowVersion=int(source_object["rowVersion"]) + 1,
        )
        return deepcopy(source_object)

    def _ensure_owned_vault(self, context: OwnerTruthCommandContext) -> dict[str, Any]:
        vault = self._vaults.get(context.vault_id)
        if vault is None:
            vault = {
                "vaultId": context.vault_id,
                "ownerSubjectId": context.owner_subject_id,
                "authorityEpoch": 0,
                "status": "active",
            }
            self._vaults[context.vault_id] = vault
            return vault
        if vault.get("ownerSubjectId") != context.owner_subject_id:
            raise OwnerTruthMediaVaultNotFound("vault was not found")
        if vault.get("status", "active") != "active":
            raise OwnerTruthMediaUploadConflict("vault is not active")
        return vault

    def _owned_upload(
        self,
        *,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
        upload_token_hash: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        intent = self._intents.get((vault_id, intent_id))
        if intent is None:
            raise OwnerTruthMediaUploadNotFound("upload intent was not found")
        if intent["ownerSubjectId"] != owner_subject_id:
            raise OwnerTruthMediaVaultNotFound("vault was not found")
        if not secrets.compare_digest(str(intent["uploadTokenHash"]), upload_token_hash):
            raise OwnerTruthMediaUploadTokenInvalid("upload token is invalid")
        source_object = self._objects[(vault_id, intent["sourceObjectId"])]
        return intent, source_object

    @staticmethod
    def _assert_pending_intent(intent: Mapping[str, Any]) -> None:
        if intent["state"] != "pending":
            raise OwnerTruthMediaUploadConflict("upload intent is not pending")
        expires_at = _parse_iso(intent["expiresAt"])
        if expires_at <= _utc_now():
            raise OwnerTruthMediaUploadExpired("upload intent has expired")


class PostgresOwnerTruthMediaSourceObjectRepository:
    """Postgres implementation bound to an already-open request Unit of Work."""

    def __init__(self, connection: Any) -> None:
        if connection is None:
            raise ValueError("an active database connection is required")
        self._connection = connection

    def create_upload_intent(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: MediaUploadIntentCommand,
        upload_token_hash: str,
        expires_at: datetime,
    ) -> MediaUploadIntentCreateResult:
        with self._cursor() as cursor:
            vault = self._ensure_owned_vault(cursor=cursor, context=context)
            cursor.execute(
                """
                SELECT id, source_object_id, payload_hash, state, expires_at,
                    created_at, updated_at, uploaded_at
                FROM owner_truth.media_source_object_upload_intents
                WHERE vault_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, command.command_id_hash),
            )
            existing_intent = cursor.fetchone()
            if existing_intent is not None:
                if str(existing_intent["payload_hash"]) != command.payload_hash(
                    vault_id=context.vault_id
                ):
                    raise OwnerTruthMediaUploadConflict("command id cannot change media payload")
                source_object = self._fetch_source_object(
                    cursor=cursor,
                    vault_id=context.vault_id,
                    source_object_id=str(existing_intent["source_object_id"]),
                )
                return MediaUploadIntentCreateResult(
                    outcome="deduplicated",
                    source_object=source_object,
                    upload_intent=self._intent_record(existing_intent, vault_id=context.vault_id),
                    upload_token=None,
                )
            current_epoch = int(vault["authority_epoch"])
            if command.expected_authority_epoch != current_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=command.expected_authority_epoch,
                    current_epoch=current_epoch,
                )
            source_object_id = command.source_object_id(vault_id=context.vault_id)
            intent_id = command.upload_intent_id(vault_id=context.vault_id)
            policy_version = (
                context.authorization_capture.policy_version
                if context.authorization_capture is not None
                else "release-policy-v1"
            )
            cursor.execute(
                """
                INSERT INTO owner_truth.media_source_objects (
                    id, vault_id, owner_subject_id, media_kind, state, content_type,
                    file_name, file_size_bytes, content_sha256, safety_status,
                    processing_status, processing_attempt, retryable, external_processing_allowed, authority_epoch,
                    policy_version, origin_command_id_hash, row_version
                ) VALUES (
                    %s, %s, %s, %s, 'uploadPending', %s,
                    %s, %s, %s, 'pending',
                    'notQueued', 0, FALSE, %s, %s,
                    %s, %s, 1
                )
                RETURNING *
                """,
                (
                    source_object_id,
                    context.vault_id,
                    context.owner_subject_id,
                    command.media_kind,
                    command.content_type,
                    command.file_name,
                    command.file_size_bytes,
                    command.content_sha256,
                    command.external_processing_allowed,
                    current_epoch,
                    policy_version,
                    command.command_id_hash,
                ),
            )
            source_object = self._source_object_record(cursor.fetchone())
            cursor.execute(
                """
                INSERT INTO owner_truth.media_source_object_upload_intents (
                    id, vault_id, source_object_id, owner_subject_id, command_id_hash,
                    payload_hash, upload_token_hash, state, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                RETURNING *
                """,
                (
                    intent_id,
                    context.vault_id,
                    source_object_id,
                    context.owner_subject_id,
                    command.command_id_hash,
                    command.payload_hash(vault_id=context.vault_id),
                    upload_token_hash,
                    expires_at,
                ),
            )
            intent = self._intent_record(cursor.fetchone(), vault_id=context.vault_id)
            return MediaUploadIntentCreateResult(
                outcome="created",
                source_object=source_object,
                upload_intent=intent,
                upload_token=None,
            )

    def load_upload_intent(
        self,
        *,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
    ) -> Mapping[str, Any]:
        with self._cursor() as cursor:
            intent = self._fetch_owned_intent(
                cursor=cursor,
                vault_id=vault_id,
                intent_id=intent_id,
                owner_subject_id=owner_subject_id,
                for_update=False,
            )
            source_object = self._fetch_source_object(
                cursor=cursor,
                vault_id=vault_id,
                source_object_id=str(intent["source_object_id"]),
            )
            return {
                "intent": self._intent_record(intent, vault_id=vault_id),
                "sourceObject": source_object,
                "uploadTokenHash": str(intent["upload_token_hash"]),
            }

    def complete_upload(
        self,
        *,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
        upload_token_hash: str,
        magic_mime: str,
        storage_provider: str,
        storage_key: str,
        safety_verdict: MediaSafetyVerdict,
    ) -> tuple[str, Mapping[str, Any]]:
        with self._cursor() as cursor:
            intent = self._fetch_owned_intent(
                cursor=cursor,
                vault_id=vault_id,
                intent_id=intent_id,
                owner_subject_id=owner_subject_id,
                for_update=True,
            )
            self._assert_upload_token(intent=intent, upload_token_hash=upload_token_hash)
            source_object_id = str(intent["source_object_id"])
            source_object = self._fetch_source_object(
                cursor=cursor,
                vault_id=vault_id,
                source_object_id=source_object_id,
                for_update=True,
            )
            if str(intent["state"]) == "uploaded":
                return "deduplicated", source_object
            self._assert_pending_intent_row(intent)
            cursor.execute(
                """
                UPDATE owner_truth.media_source_objects
                SET state = 'verified', magic_mime = %s, storage_provider = %s,
                    storage_key = %s, storage_version = 1, safety_status = %s,
                    safety_provider = %s, safety_reason_code = NULL,
                    processing_status = 'notQueued', retryable = FALSE,
                    failure_code = NULL, uploaded_at = NOW(),
                    row_version = row_version + 1, updated_at = NOW()
                WHERE vault_id = %s AND id = %s
                RETURNING *
                """,
                (
                    magic_mime,
                    storage_provider,
                    storage_key,
                    safety_verdict.status,
                    safety_verdict.provider,
                    vault_id,
                    source_object_id,
                ),
            )
            updated = self._source_object_record(cursor.fetchone())
            cursor.execute(
                """
                UPDATE owner_truth.media_source_object_upload_intents
                SET state = 'uploaded', uploaded_at = NOW(), updated_at = NOW()
                WHERE vault_id = %s AND id = %s
                """,
                (vault_id, intent_id),
            )
            return "uploaded", updated

    def reject_upload(
        self,
        *,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
        upload_token_hash: str,
        safety_verdict: MediaSafetyVerdict,
    ) -> Mapping[str, Any]:
        with self._cursor() as cursor:
            intent = self._fetch_owned_intent(
                cursor=cursor,
                vault_id=vault_id,
                intent_id=intent_id,
                owner_subject_id=owner_subject_id,
                for_update=True,
            )
            self._assert_upload_token(intent=intent, upload_token_hash=upload_token_hash)
            self._assert_pending_intent_row(intent)
            cursor.execute(
                """
                UPDATE owner_truth.media_source_objects
                SET state = 'quarantined', safety_status = %s, safety_provider = %s,
                    safety_reason_code = %s, processing_status = 'blocked',
                    retryable = %s, failure_code = %s, row_version = row_version + 1,
                    updated_at = NOW()
                WHERE vault_id = %s AND id = %s
                RETURNING *
                """,
                (
                    safety_verdict.status,
                    safety_verdict.provider,
                    safety_verdict.reason_code,
                    safety_verdict.status == "unavailable",
                    safety_verdict.reason_code,
                    vault_id,
                    str(intent["source_object_id"]),
                ),
            )
            updated = self._source_object_record(cursor.fetchone())
            cursor.execute(
                """
                UPDATE owner_truth.media_source_object_upload_intents
                SET state = 'rejected', updated_at = NOW()
                WHERE vault_id = %s AND id = %s
                """,
                (vault_id, intent_id),
            )
            return updated

    def get_source_object(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
    ) -> Mapping[str, Any]:
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM owner_truth.media_source_objects
                WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
                """,
                (vault_id, source_object_id, owner_subject_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise OwnerTruthMediaVaultNotFound("source object was not found")
            return self._source_object_record(row)

    def queue_processing(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
    ) -> Mapping[str, Any]:
        with self._cursor() as cursor:
            source_object = self._fetch_owned_source_object(
                cursor=cursor,
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
                for_update=True,
            )
            if source_object["state"] != "verified" or source_object["safetyStatus"] != "clean":
                raise OwnerTruthMediaUploadConflict("media source object is not verified for processing")
            if source_object["mediaKind"] == "video":
                return self._record_processing_outcome_cursor(
                    cursor=cursor,
                    source_object=source_object,
                    processing_generation=0,
                    attempt=0,
                    processor_id="videoStorageOnly",
                    processor_version="v1",
                    outcome="notApplicable",
                    result_hash=_sha256(
                        f"video-storage-only:{source_object['sourceObjectId']}:{source_object['contentSha256']}"
                    ),
                )
            if source_object["processingStatus"] in {"queued", "processing", "retryableFailed", "succeeded"}:
                return source_object
            cursor.execute(
                """
                UPDATE owner_truth.media_source_objects
                SET processing_status = 'queued', processing_generation = processing_generation + 1,
                    retryable = FALSE, failure_code = NULL,
                    updated_at = NOW(), row_version = row_version + 1
                WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
                RETURNING *
                """,
                (vault_id, source_object_id, owner_subject_id),
            )
            return self._source_object_record(cursor.fetchone())

    def begin_processing(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        expected_authority_epoch: int,
        expected_processing_generation: int,
        attempt: int,
    ) -> Mapping[str, Any]:
        if type(attempt) is not int or attempt < 1:
            raise OwnerTruthMediaUploadInvalid("processing attempt is invalid")
        with self._cursor() as cursor:
            source_object = self._fetch_owned_source_object(
                cursor=cursor,
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
                for_update=True,
            )
            if int(source_object["authorityEpoch"]) != expected_authority_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=expected_authority_epoch,
                    current_epoch=int(source_object["authorityEpoch"]),
                )
            if int(source_object.get("processingGeneration") or 0) != expected_processing_generation:
                raise OwnerTruthMediaUploadConflict("media processing generation is no longer current")
            if (
                source_object["state"] != "verified"
                or source_object["safetyStatus"] != "clean"
                or source_object["processingStatus"] not in {"queued", "retryableFailed"}
            ):
                raise OwnerTruthMediaUploadConflict("media source object is not eligible for processing")
            cursor.execute(
                """
                UPDATE owner_truth.media_source_objects
                SET state = 'processing', processing_status = 'processing',
                    processing_attempt = %s, retryable = FALSE, failure_code = NULL,
                    updated_at = NOW(), row_version = row_version + 1
                WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
                RETURNING *
                """,
                (attempt, vault_id, source_object_id, owner_subject_id),
            )
            return self._source_object_record(cursor.fetchone())

    def record_processing_outcome(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        processing_generation: int,
        attempt: int,
        processor_id: str,
        processor_version: str,
        outcome: str,
        result_hash: str,
        extracted_text_sha256: Optional[str] = None,
        derived_source_id: Optional[str] = None,
        failure_code: Optional[str] = None,
    ) -> Mapping[str, Any]:
        with self._cursor() as cursor:
            source_object = self._fetch_owned_source_object(
                cursor=cursor,
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
                for_update=True,
            )
            return self._record_processing_outcome_cursor(
                cursor=cursor,
                source_object=source_object,
                processing_generation=processing_generation,
                attempt=attempt,
                processor_id=processor_id,
                processor_version=processor_version,
                outcome=outcome,
                result_hash=result_hash,
                extracted_text_sha256=extracted_text_sha256,
                derived_source_id=derived_source_id,
                failure_code=failure_code,
            )

    def _ensure_owned_vault(self, *, cursor: Any, context: OwnerTruthCommandContext) -> Mapping[str, Any]:
        cursor.execute(
            """
            INSERT INTO owner_truth.vaults (vault_id, owner_subject_id)
            VALUES (%s, %s)
            ON CONFLICT (vault_id) DO UPDATE
            SET updated_at = NOW()
            WHERE owner_truth.vaults.owner_subject_id = EXCLUDED.owner_subject_id
                AND owner_truth.vaults.status = 'active'
            RETURNING vault_id, owner_subject_id, authority_epoch, status
            """,
            (context.vault_id, context.owner_subject_id),
        )
        vault = cursor.fetchone()
        if vault is None:
            raise OwnerTruthMediaVaultNotFound("vault was not found")
        return vault

    def _fetch_owned_intent(
        self,
        *,
        cursor: Any,
        vault_id: str,
        intent_id: str,
        owner_subject_id: str,
        for_update: bool,
    ) -> Mapping[str, Any]:
        cursor.execute(
            f"""
            SELECT *
            FROM owner_truth.media_source_object_upload_intents
            WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
            {'FOR UPDATE' if for_update else ''}
            """,
            (vault_id, intent_id, owner_subject_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise OwnerTruthMediaUploadNotFound("upload intent was not found")
        return row

    def _fetch_source_object(
        self,
        *,
        cursor: Any,
        vault_id: str,
        source_object_id: str,
        for_update: bool = False,
    ) -> Mapping[str, Any]:
        cursor.execute(
            f"""
            SELECT *
            FROM owner_truth.media_source_objects
            WHERE vault_id = %s AND id = %s
            {'FOR UPDATE' if for_update else ''}
            """,
            (vault_id, source_object_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise OwnerTruthMediaUploadNotFound("source object was not found")
        return self._source_object_record(row)

    def _fetch_owned_source_object(
        self,
        *,
        cursor: Any,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        for_update: bool,
    ) -> Mapping[str, Any]:
        cursor.execute(
            f"""
            SELECT *
            FROM owner_truth.media_source_objects
            WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
            {'FOR UPDATE' if for_update else ''}
            """,
            (vault_id, source_object_id, owner_subject_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise OwnerTruthMediaVaultNotFound("source object was not found")
        return self._source_object_record(row)

    def _record_processing_outcome_cursor(
        self,
        *,
        cursor: Any,
        source_object: Mapping[str, Any],
        processing_generation: int,
        attempt: int,
        processor_id: str,
        processor_version: str,
        outcome: str,
        result_hash: str,
        extracted_text_sha256: Optional[str] = None,
        derived_source_id: Optional[str] = None,
        failure_code: Optional[str] = None,
    ) -> Mapping[str, Any]:
        if type(processing_generation) is not int or processing_generation < 0:
            raise OwnerTruthMediaUploadInvalid("media processing generation is invalid")
        if int(source_object.get("processingGeneration") or 0) != processing_generation:
            raise OwnerTruthMediaUploadConflict("media processing generation is no longer current")
        if type(attempt) is not int or attempt < 0:
            raise OwnerTruthMediaUploadInvalid("processing attempt is invalid")
        normalized_processor_id = str(processor_id or "").strip()
        normalized_processor_version = str(processor_version or "").strip()
        if not _PURPOSE_PATTERN.fullmatch(normalized_processor_id) or not _PURPOSE_PATTERN.fullmatch(
            normalized_processor_version
        ):
            raise OwnerTruthMediaUploadInvalid("media processor identity is invalid")
        normalized_outcome = str(outcome or "").strip()
        if normalized_outcome not in {"succeeded", "retryableFailed", "failed", "notApplicable"}:
            raise OwnerTruthMediaUploadInvalid("media processing outcome is invalid")
        normalized_result_hash = _normalize_sha256(result_hash)
        normalized_text_hash = (
            None if extracted_text_sha256 is None else _normalize_sha256(extracted_text_sha256)
        )
        normalized_source_id = None
        if derived_source_id is not None:
            try:
                normalized_source_id = str(UUID(str(derived_source_id)))
            except (TypeError, ValueError) as exc:
                raise OwnerTruthMediaUploadInvalid("derived source id is invalid") from exc
        normalized_failure_code = str(failure_code or "").strip() or None
        if normalized_failure_code is not None and not _PURPOSE_PATTERN.fullmatch(normalized_failure_code):
            raise OwnerTruthMediaUploadInvalid("media processing failure code is invalid")
        if normalized_outcome == "succeeded":
            if normalized_text_hash is None or normalized_source_id is None or normalized_failure_code is not None:
                raise OwnerTruthMediaUploadInvalid("successful media processing result is incomplete")
            state, processing_status, retryable = "processed", "succeeded", False
        elif normalized_outcome == "retryableFailed":
            if normalized_text_hash is not None or normalized_source_id is not None or normalized_failure_code is None:
                raise OwnerTruthMediaUploadInvalid("retryable media processing result is incomplete")
            state, processing_status, retryable = "verified", "retryableFailed", True
        elif normalized_outcome == "failed":
            if normalized_text_hash is not None or normalized_source_id is not None or normalized_failure_code is None:
                raise OwnerTruthMediaUploadInvalid("failed media processing result is incomplete")
            state, processing_status, retryable = "verified", "failed", False
        else:
            if normalized_text_hash is not None or normalized_source_id is not None or normalized_failure_code is not None:
                raise OwnerTruthMediaUploadInvalid("not applicable media processing result is invalid")
            state, processing_status, retryable = "verified", "notApplicable", False
        result_id = str(
            uuid5(
                NAMESPACE_URL,
                "dreamjourney-owner-truth-media-processing-v1:"
                f"{source_object['sourceObjectId']}:{normalized_processor_id}:"
                f"{normalized_processor_version}:{processing_generation}:{attempt}",
            )
        )
        cursor.execute(
            """
            INSERT INTO owner_truth.media_source_object_processing_results (
                id, vault_id, source_object_id, owner_subject_id, processor_id,
                processor_version, state, processing_generation, attempt, result_hash, extracted_text_sha256,
                derived_source_id, failure_code
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (
                vault_id, source_object_id, processor_id, processor_version,
                processing_generation, attempt
            )
            DO NOTHING
            RETURNING id, state, processing_generation, result_hash, extracted_text_sha256, derived_source_id, failure_code
            """,
            (
                result_id,
                source_object["vaultId"],
                source_object["sourceObjectId"],
                source_object["ownerSubjectId"],
                normalized_processor_id,
                normalized_processor_version,
                normalized_outcome,
                processing_generation,
                attempt,
                normalized_result_hash,
                normalized_text_hash,
                normalized_source_id,
                normalized_failure_code,
            ),
        )
        persisted = cursor.fetchone()
        if persisted is None:
            cursor.execute(
                """
                SELECT id, state, processing_generation, result_hash, extracted_text_sha256, derived_source_id, failure_code
                FROM owner_truth.media_source_object_processing_results
                WHERE vault_id = %s AND source_object_id = %s AND processor_id = %s
                  AND processor_version = %s AND processing_generation = %s AND attempt = %s
                FOR UPDATE
                """,
                (
                    source_object["vaultId"],
                    source_object["sourceObjectId"],
                    normalized_processor_id,
                    normalized_processor_version,
                    processing_generation,
                    attempt,
                ),
            )
            persisted = cursor.fetchone()
            expected = {
                "id": result_id,
                "state": normalized_outcome,
                "processing_generation": processing_generation,
                "result_hash": normalized_result_hash,
                "extracted_text_sha256": normalized_text_hash,
                "derived_source_id": normalized_source_id,
                "failure_code": normalized_failure_code,
            }
            if persisted is None or any(
                str(persisted.get(key) or "") != str(value or "")
                for key, value in expected.items()
            ):
                raise OwnerTruthMediaUploadConflict(
                    "media processing result cannot change immutable meaning"
                )
        cursor.execute(
            """
            UPDATE owner_truth.media_source_objects
            SET state = %s, processing_status = %s, processing_attempt = %s,
                retryable = %s, failure_code = %s, derived_source_id = %s,
                last_processing_result_id = %s, updated_at = NOW(),
                row_version = row_version + 1
            WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
            RETURNING *
            """,
            (
                state,
                processing_status,
                attempt,
                retryable,
                normalized_failure_code,
                normalized_source_id,
                result_id,
                source_object["vaultId"],
                source_object["sourceObjectId"],
                source_object["ownerSubjectId"],
            ),
        )
        return self._source_object_record(cursor.fetchone())

    @staticmethod
    def _assert_upload_token(*, intent: Mapping[str, Any], upload_token_hash: str) -> None:
        if not secrets.compare_digest(str(intent["upload_token_hash"]), upload_token_hash):
            raise OwnerTruthMediaUploadTokenInvalid("upload token is invalid")

    @staticmethod
    def _assert_pending_intent_row(intent: Mapping[str, Any]) -> None:
        if str(intent["state"]) != "pending":
            raise OwnerTruthMediaUploadConflict("upload intent is not pending")
        expires_at = intent["expires_at"]
        if isinstance(expires_at, datetime):
            instant = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
            if instant.astimezone(timezone.utc) <= _utc_now():
                raise OwnerTruthMediaUploadExpired("upload intent has expired")

    def _cursor(self):
        try:
            from psycopg.rows import dict_row
        except ImportError:  # pragma: no cover - production dependency
            dict_row = None
        return self._connection.cursor(row_factory=dict_row)

    @staticmethod
    def _source_object_record(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "sourceObjectId": str(row["id"]),
            "vaultId": str(row["vault_id"]),
            "ownerSubjectId": str(row["owner_subject_id"]),
            "mediaKind": str(row["media_kind"]),
            "state": str(row["state"]),
            "contentType": str(row["content_type"]),
            "magicMime": row.get("magic_mime"),
            "fileName": str(row["file_name"]),
            "fileSizeBytes": int(row["file_size_bytes"]),
            "contentSha256": str(row["content_sha256"]),
            "storageProvider": row.get("storage_provider"),
            "storageKey": row.get("storage_key"),
            "storageVersion": int(row.get("storage_version") or 0),
            "safetyStatus": str(row["safety_status"]),
            "safetyProvider": row.get("safety_provider"),
            "processingStatus": str(row["processing_status"]),
            "processingGeneration": int(row.get("processing_generation") or 0),
            "externalProcessingAllowed": bool(row.get("external_processing_allowed", False)),
            "processingAttempt": int(row.get("processing_attempt") or 0),
            "retryable": bool(row.get("retryable", False)),
            "failureCode": row.get("failure_code"),
            "derivedSourceId": str(row["derived_source_id"])
            if row.get("derived_source_id")
            else None,
            "lastProcessingResultId": str(row["last_processing_result_id"])
            if row.get("last_processing_result_id")
            else None,
            "authorityEpoch": int(row["authority_epoch"]),
            "rowVersion": int(row["row_version"]),
            "createdAt": _utc_iso(row["created_at"]),
            "updatedAt": _utc_iso(row["updated_at"]),
            "uploadedAt": _utc_iso(row["uploaded_at"]) if row.get("uploaded_at") else None,
            "originCommandIdHash": str(row["origin_command_id_hash"]),
        }

    @staticmethod
    def _intent_record(row: Mapping[str, Any], *, vault_id: str) -> dict[str, Any]:
        del vault_id
        return {
            "uploadIntentId": str(row["id"]),
            "sourceObjectId": str(row["source_object_id"]),
            "state": str(row["state"]),
            "expiresAt": _utc_iso(row["expires_at"]),
            "createdAt": _utc_iso(row["created_at"]),
            "updatedAt": _utc_iso(row["updated_at"]),
            "uploadedAt": _utc_iso(row["uploaded_at"]) if row.get("uploaded_at") else None,
        }


class OwnerTruthMediaIngestionService:
    """Coordinates durable metadata with private bytes and fail-closed safety."""

    def __init__(
        self,
        *,
        store: Any,
        object_store: PrivateMediaObjectStore,
        safety_scanner: MediaContentSafetyScanner,
        enabled: bool,
        max_upload_bytes: int,
        upload_intent_ttl_seconds: int,
        on_verified: Optional[Callable[[OwnerTruthCommandContext, Mapping[str, Any]], Mapping[str, Any]]] = None,
        now: Optional[callable] = None,
    ) -> None:
        self._store = store
        self._object_store = object_store
        self._safety_scanner = safety_scanner
        self._enabled = bool(enabled)
        self._max_upload_bytes = max(1, int(max_upload_bytes))
        self._upload_intent_ttl_seconds = max(60, int(upload_intent_ttl_seconds))
        # The storage transaction remains authoritative.  The optional callback
        # only queues a value-free processing effect after a clean upload, and
        # must run inside the caller's existing Unit of Work.
        self._on_verified = on_verified
        self._now = now or _utc_now

    def create_upload_intent(
        self,
        *,
        context: OwnerTruthCommandContext,
        command: MediaUploadIntentCommand,
    ) -> MediaUploadIntentCreateResult:
        self._require_available()
        if command.file_size_bytes > self._max_upload_bytes:
            raise OwnerTruthMediaUploadInvalid("media file exceeds upload limit")
        upload_token = secrets.token_urlsafe(32)
        expires_at = self._now() + timedelta(seconds=self._upload_intent_ttl_seconds)
        repository = self._repository()
        result = repository.create_upload_intent(
            context=context,
            command=command,
            upload_token_hash=_sha256(upload_token),
            expires_at=expires_at,
        )
        if result.outcome == "created":
            return MediaUploadIntentCreateResult(
                outcome=result.outcome,
                source_object=result.source_object,
                upload_intent=result.upload_intent,
                upload_token=upload_token,
            )
        return result

    def upload_content(
        self,
        *,
        context: OwnerTruthCommandContext,
        intent_id: str,
        upload_token: str,
        payload: bytes,
        request_content_type: Optional[str],
    ) -> tuple[str, Mapping[str, Any]]:
        self._require_available()
        normalized_intent_id = self._normalize_uuid(intent_id, field="upload intent id")
        token = str(upload_token or "").strip()
        if len(token) < 32 or len(token) > 512:
            raise OwnerTruthMediaUploadTokenInvalid("upload token is invalid")
        if not isinstance(payload, bytes) or not payload or len(payload) > self._max_upload_bytes:
            raise OwnerTruthMediaUploadInvalid("uploaded media size is invalid")
        repository = self._repository()
        loaded = repository.load_upload_intent(
            vault_id=context.vault_id,
            intent_id=normalized_intent_id,
            owner_subject_id=context.owner_subject_id,
        )
        intent = loaded["intent"]
        source_object = loaded["sourceObject"]
        stored_token_hash = str(loaded.get("uploadTokenHash") or "")
        token_hash = _sha256(token)
        if stored_token_hash and not secrets.compare_digest(stored_token_hash, token_hash):
            raise OwnerTruthMediaUploadTokenInvalid("upload token is invalid")
        if int(source_object["fileSizeBytes"]) != len(payload):
            raise OwnerTruthMediaUploadInvalid("uploaded media size does not match intent")
        if str(source_object["contentSha256"]) != _sha256(payload):
            raise OwnerTruthMediaUploadInvalid("uploaded media checksum does not match intent")
        expected_content_type = str(source_object["contentType"])
        if request_content_type is not None:
            observed_request_content_type = _normalize_content_type(request_content_type)
            if observed_request_content_type != expected_content_type:
                raise OwnerTruthMediaUploadInvalid("uploaded content type does not match intent")
        magic_mime = inspect_magic_mime(media_kind=str(source_object["mediaKind"]), payload=payload)
        if not _content_type_matches(
            expected=expected_content_type,
            observed=magic_mime,
            media_kind=str(source_object["mediaKind"]),
        ):
            raise OwnerTruthMediaUploadInvalid("uploaded media magic mime does not match intent")
        verdict = self._safety_scanner.inspect(
            media_kind=str(source_object["mediaKind"]),
            content_type=expected_content_type,
            payload=payload,
        )
        if verdict.status != "clean":
            rejected = repository.reject_upload(
                vault_id=context.vault_id,
                intent_id=normalized_intent_id,
                owner_subject_id=context.owner_subject_id,
                upload_token_hash=token_hash,
                safety_verdict=verdict,
            )
            return "quarantined", rejected
        storage_key = self._storage_key(
            vault_id=context.vault_id,
            source_object_id=str(source_object["sourceObjectId"]),
            content_sha256=str(source_object["contentSha256"]),
        )
        self._object_store.write(storage_key=storage_key, payload=payload)
        try:
            outcome, completed = repository.complete_upload(
                vault_id=context.vault_id,
                intent_id=normalized_intent_id,
                owner_subject_id=context.owner_subject_id,
                upload_token_hash=token_hash,
                magic_mime=magic_mime,
                storage_provider=self._object_store.provider_name,
                storage_key=storage_key,
                safety_verdict=verdict,
            )
            if outcome == "uploaded" and self._on_verified is not None:
                completed = self._on_verified(context, completed)
            return outcome, completed
        except Exception:
            self._object_store.delete(storage_key=storage_key)
            raise

    def get_source_object(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object_id: str,
    ) -> Mapping[str, Any]:
        return self._repository().get_source_object(
            vault_id=context.vault_id,
            source_object_id=self._normalize_uuid(source_object_id, field="source object id"),
            owner_subject_id=context.owner_subject_id,
        )

    @staticmethod
    def public_upload_intent_response(result: MediaUploadIntentCreateResult) -> dict[str, Any]:
        return {
            "schemaVersion": OWNER_TRUTH_MEDIA_UPLOAD_INTENT_SCHEMA_VERSION,
            "status": result.outcome,
            "sourceObject": _object_public_receipt(result.source_object),
            "uploadIntent": _intent_public_receipt(
                result.upload_intent,
                upload_token=result.upload_token,
            ),
        }

    @staticmethod
    def public_source_object_response(source_object: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schemaVersion": OWNER_TRUTH_MEDIA_SOURCE_OBJECT_SCHEMA_VERSION,
            "sourceObject": _object_public_receipt(source_object),
        }

    def _repository(self) -> OwnerTruthMediaSourceObjectRepository:
        getter = getattr(self._store, "owner_truth_media_source_object_repository", None)
        if not callable(getter):
            raise OwnerTruthMediaCaptureUnavailable("media source object store is unavailable")
        return getter()

    def _require_available(self) -> None:
        if not self._enabled or self._object_store.provider_name == "disabled":
            raise OwnerTruthMediaCaptureUnavailable("media capture is disabled")

    @staticmethod
    def _storage_key(*, vault_id: str, source_object_id: str, content_sha256: str) -> str:
        vault_digest = _sha256(vault_id)[:24]
        return f"owner-truth/v1/{vault_digest}/{source_object_id}/{content_sha256}.bin"

    @staticmethod
    def _normalize_uuid(value: object, *, field: str) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthMediaUploadInvalid(f"{field} is invalid") from exc


__all__ = [
    "ClamAVMediaContentSafetyScanner",
    "DisabledMediaContentSafetyScanner",
    "FilesystemPrivateMediaObjectStore",
    "InMemoryOwnerTruthMediaSourceObjectRepository",
    "MediaSafetyVerdict",
    "MediaUploadIntentCommand",
    "MediaUploadIntentCreateResult",
    "OwnerTruthMediaAuthorityEpochConflict",
    "OwnerTruthMediaCaptureUnavailable",
    "OwnerTruthMediaIngestionError",
    "OwnerTruthMediaIngestionService",
    "OwnerTruthMediaSourceObjectRepository",
    "OwnerTruthMediaUploadConflict",
    "OwnerTruthMediaUploadExpired",
    "OwnerTruthMediaUploadInvalid",
    "OwnerTruthMediaUploadNotFound",
    "OwnerTruthMediaUploadTokenInvalid",
    "OwnerTruthMediaVaultNotFound",
    "OWNER_TRUTH_MEDIA_SOURCE_OBJECT_SCHEMA_VERSION",
    "OWNER_TRUTH_MEDIA_UPLOAD_INTENT_SCHEMA_VERSION",
    "PostgresOwnerTruthMediaSourceObjectRepository",
    "PrivateMediaObjectStore",
    "TestOnlyCleanMediaContentSafetyScanner",
    "build_media_content_safety_scanner",
    "build_private_media_object_store",
    "inspect_magic_mime",
]
