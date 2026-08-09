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
import socket
import struct
import subprocess
from threading import RLock
from typing import Any, Callable, Mapping, Optional, Protocol
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5
import zipfile

from app.domain.owner_truth.source_commands import OwnerTruthCommandContext


OWNER_TRUTH_MEDIA_SOURCE_OBJECT_SCHEMA_VERSION = "owner-truth-media-source-object-v1"
OWNER_TRUTH_MEDIA_UPLOAD_INTENT_SCHEMA_VERSION = "owner-truth-media-upload-intent-v1"
OWNER_TRUTH_MEDIA_DELETION_RESPONSE_SCHEMA_VERSION = "owner-truth-media-deletion-response-v1"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PURPOSE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,79}$")
_PRIVATE_MEDIA_OBJECT_SHA256_METADATA_KEY = "dreamjourney-sha256"
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


class OwnerTruthMediaObjectNotFound(OwnerTruthMediaIngestionError):
    """The authoritative private object no longer exists in its configured store."""

    code = "ownerTruthMediaObjectNotFound"


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


class OwnerTruthMediaAccessRevoked(OwnerTruthMediaIngestionError):
    """A private object was revoked before the requested mutation could commit."""

    code = "ownerTruthMediaAccessRevoked"


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
class MediaDeletionCommand:
    """An Owner-authored, idempotent request to revoke one private object.

    The command deliberately contains no storage location, provider choice, or
    bytes.  Those remain server-side implementation details and are handled by
    the later deletion worker.
    """

    command_id: str
    expected_authority_epoch: int
    client_requested_at: datetime

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "MediaDeletionCommand":
        required_fields = {"commandId", "expectedAuthorityEpoch", "clientRequestedAt"}
        if set(payload) != required_fields:
            raise OwnerTruthMediaUploadInvalid("media deletion payload is invalid")
        try:
            command_id = str(UUID(str(payload.get("commandId"))))
        except (TypeError, ValueError) as exc:
            raise OwnerTruthMediaUploadInvalid("deletion command id is invalid") from exc
        expected_epoch = payload.get("expectedAuthorityEpoch")
        if type(expected_epoch) is not int or expected_epoch < 0:
            raise OwnerTruthMediaUploadInvalid("deletion authority epoch is invalid")
        return cls(
            command_id=command_id,
            expected_authority_epoch=expected_epoch,
            client_requested_at=_parse_iso(payload.get("clientRequestedAt")),
        )

    @property
    def command_id_hash(self) -> str:
        return _sha256(self.command_id)

    def payload_hash(self, *, vault_id: str, source_object_id: str) -> str:
        return _sha256(
            _canonical_json(
                {
                    "clientRequestedAt": _utc_iso(self.client_requested_at),
                    "commandId": self.command_id,
                    "expectedAuthorityEpoch": self.expected_authority_epoch,
                    "schemaVersion": OWNER_TRUTH_MEDIA_DELETION_RESPONSE_SCHEMA_VERSION,
                    "sourceObjectId": source_object_id,
                    "vaultId": vault_id,
                }
            )
        )

    def deletion_command_id(self, *, vault_id: str, source_object_id: str) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                "dreamjourney-owner-truth-media-deletion-command-v1:"
                f"{vault_id}:{source_object_id}:{self.command_id}",
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


_CLAMAV_DAEMON_STREAM_CHUNK_BYTES = 1024 * 1024
_CLAMAV_DAEMON_MAX_REPLY_BYTES = 64 * 1024
_CLAMAV_DAEMON_READINESS_PROBE = b"dreamjourney-clamav-runtime-probe-v1"


def _clamav_daemon_host(value: object) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized or None


def _clamav_daemon_port(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 3310
    return min(65535, max(1, parsed))


def _clamav_daemon_timeout_seconds(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 30
    return min(60, max(1, parsed))


def _read_clamav_daemon_reply(connection: socket.socket) -> Optional[bytes]:
    """Read one bounded NUL-framed clamd reply without retaining media bytes."""

    reply = bytearray()
    while len(reply) < _CLAMAV_DAEMON_MAX_REPLY_BYTES:
        chunk = connection.recv(min(4096, _CLAMAV_DAEMON_MAX_REPLY_BYTES - len(reply)))
        if not chunk:
            return None
        reply.extend(chunk)
        terminator = reply.find(b"\0")
        if terminator >= 0:
            return bytes(reply[:terminator])
    return None


class ClamAVDaemonMediaContentSafetyScanner:
    """Streams a scan to an internal ``clamd`` sidecar over the Docker network.

    ``clamd`` TCP has no transport authentication. The Compose contract therefore
    deliberately keeps port 3310 internal: this adapter only accepts an explicit
    server-side host and never exposes the scanner to a client or public network.
    A connection, framing, timeout, or reply ambiguity is an unavailable verdict
    so ingestion remains fail-closed before the object store receives any bytes.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 3310,
        timeout_seconds: int = 30,
    ) -> None:
        normalized_host = _clamav_daemon_host(host)
        if normalized_host is None:
            raise ValueError("clamav daemon host is required")
        self._host = normalized_host
        self._port = _clamav_daemon_port(port)
        self._timeout_seconds = _clamav_daemon_timeout_seconds(timeout_seconds)

    def inspect(self, *, media_kind: str, content_type: str, payload: bytes) -> MediaSafetyVerdict:
        del media_kind, content_type
        try:
            with socket.create_connection(
                (self._host, self._port),
                timeout=self._timeout_seconds,
            ) as connection:
                connection.settimeout(self._timeout_seconds)
                connection.sendall(b"zINSTREAM\0")
                for offset in range(0, len(payload), _CLAMAV_DAEMON_STREAM_CHUNK_BYTES):
                    chunk = payload[offset : offset + _CLAMAV_DAEMON_STREAM_CHUNK_BYTES]
                    connection.sendall(struct.pack(">I", len(chunk)))
                    connection.sendall(chunk)
                connection.sendall(struct.pack(">I", 0))
                reply = _read_clamav_daemon_reply(connection)
        except (OSError, ValueError):
            return MediaSafetyVerdict(
                status="unavailable",
                provider="clamav",
                reason_code="contentSafetyScannerUnavailable",
            )

        if reply is None:
            return MediaSafetyVerdict(
                status="unavailable",
                provider="clamav",
                reason_code="contentSafetyScannerUnavailable",
            )
        normalized_reply = reply.upper().strip()
        if normalized_reply.endswith(b" OK"):
            return MediaSafetyVerdict(status="clean", provider="clamav")
        if normalized_reply.endswith(b" FOUND"):
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


def clamav_daemon_runtime_ready(
    *,
    host: str,
    port: int = 3310,
    timeout_seconds: int = 5,
) -> bool:
    """Verify the configured sidecar can scan a value-free clean probe.

    This reaches beyond a TCP connect/PING check: a fixed ``INSTREAM`` scan
    proves clamd can receive data and has loaded a usable signature database.
    The probe is a constant, not user media, and no provider configuration
    details leave the process.
    """

    try:
        scanner = ClamAVDaemonMediaContentSafetyScanner(
            host=host,
            port=port,
            timeout_seconds=timeout_seconds,
        )
    except ValueError:
        return False
    return scanner.inspect(
        media_kind="document",
        content_type="text/plain",
        payload=_CLAMAV_DAEMON_READINESS_PROBE,
    ).status == "clean"


def clamav_scanner_runtime_ready(
    *,
    executable: str = "clamscan",
    timeout_seconds: int = 5,
) -> bool:
    """Return whether a local ClamAV executable can complete an empty scan.

    This is deliberately a dependency readiness probe rather than an upload
    scan: it never receives user bytes. A missing binary, stale/corrupt
    signature database, timeout, or execution failure keeps capture disabled.
    """

    normalized_executable = str(executable or "clamscan").strip() or "clamscan"
    resolved_executable = shutil.which(normalized_executable)
    if resolved_executable is None:
        return False
    try:
        completed = subprocess.run(
            [resolved_executable, "--no-summary", "-"],
            input=b"",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=max(1, int(timeout_seconds)),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


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

    def write(
        self,
        *,
        storage_key: str,
        payload: bytes,
        content_type: Optional[str] = None,
        content_sha256: Optional[str] = None,
    ) -> None:
        ...

    def verify_upload(
        self,
        *,
        storage_key: str,
        expected_file_size_bytes: int,
        expected_content_type: str,
        expected_content_sha256: str,
    ) -> None:
        ...

    def delete(self, *, storage_key: str) -> None:
        ...

    def read(self, *, storage_key: str, max_bytes: Optional[int] = None) -> bytes:
        ...


class DisabledPrivateMediaObjectStore:
    provider_name = "disabled"

    def write(
        self,
        *,
        storage_key: str,
        payload: bytes,
        content_type: Optional[str] = None,
        content_sha256: Optional[str] = None,
    ) -> None:
        del storage_key, payload, content_type, content_sha256
        raise OwnerTruthMediaCaptureUnavailable("private media storage is not configured")

    def verify_upload(
        self,
        *,
        storage_key: str,
        expected_file_size_bytes: int,
        expected_content_type: str,
        expected_content_sha256: str,
    ) -> None:
        del storage_key, expected_file_size_bytes, expected_content_type, expected_content_sha256
        raise OwnerTruthMediaCaptureUnavailable("private media storage is not configured")

    def delete(self, *, storage_key: str) -> None:
        del storage_key

    def read(self, *, storage_key: str, max_bytes: Optional[int] = None) -> bytes:
        del storage_key, max_bytes
        raise OwnerTruthMediaCaptureUnavailable("private media storage is not configured")


class FilesystemPrivateMediaObjectStore:
    """Durable, non-public object adapter backed by a mounted server volume."""

    provider_name = "filesystem"

    def __init__(self, *, root: str | Path) -> None:
        candidate = Path(root).expanduser()
        candidate.mkdir(parents=True, exist_ok=True)
        self._root = candidate.resolve()

    def write(
        self,
        *,
        storage_key: str,
        payload: bytes,
        content_type: Optional[str] = None,
        content_sha256: Optional[str] = None,
    ) -> None:
        if content_type is not None:
            _normalize_content_type(content_type)
        if content_sha256 is not None and _sha256(payload) != _normalize_sha256(content_sha256):
            raise OwnerTruthMediaUploadInvalid("private media payload checksum is invalid")
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

    def read(self, *, storage_key: str, max_bytes: Optional[int] = None) -> bytes:
        limit = _optional_read_limit(max_bytes)
        try:
            with self._resolve(storage_key).open("rb") as handle:
                payload = handle.read() if limit is None else handle.read(limit + 1)
        except FileNotFoundError as exc:
            raise OwnerTruthMediaObjectNotFound("private media object does not exist") from exc
        except OSError as exc:
            raise OwnerTruthMediaCaptureUnavailable("private media object read is unavailable") from exc
        if limit is not None and len(payload) > limit:
            raise OwnerTruthMediaCaptureUnavailable("private media object exceeds read limit")
        return payload

    def verify_upload(
        self,
        *,
        storage_key: str,
        expected_file_size_bytes: int,
        expected_content_type: str,
        expected_content_sha256: str,
    ) -> None:
        # Filesystem storage exists for local/isolated contract runs. Production
        # COS uses HEAD plus persisted object metadata below; both paths still
        # verify the immutable byte length and SHA-256 before DB completion.
        _normalize_content_type(expected_content_type)
        payload = self.read(storage_key=storage_key)
        _assert_private_media_object_integrity(
            payload=payload,
            expected_file_size_bytes=expected_file_size_bytes,
            expected_content_sha256=expected_content_sha256,
        )

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

    def __init__(
        self,
        *,
        provider_name: str = "s3",
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
        normalized_provider = str(provider_name or "").strip().lower()
        if normalized_provider not in {"s3", "cos"}:
            raise OwnerTruthMediaUploadInvalid("private media storage provider is invalid")
        self.provider_name = normalized_provider
        self._bucket = _private_bucket_name(bucket)
        self._prefix = _private_storage_prefix(prefix)
        self._endpoint_url = str(endpoint_url or "").strip() or None
        self._region = str(region or "").strip() or None
        self._server_side_encryption = _optional_identifier(
            server_side_encryption,
            field="server side encryption",
        )
        self._kms_key_id = _optional_identifier(kms_key_id, field="kms key id")
        allowed_encryption = (
            {None, "AES256", "cos/kms"}
            if self.provider_name == "cos"
            else {None, "AES256", "aws:kms"}
        )
        if self._server_side_encryption not in allowed_encryption:
            raise OwnerTruthMediaUploadInvalid("server side encryption is invalid")
        if self._kms_key_id is not None and self._server_side_encryption not in {
            "aws:kms",
            "cos/kms",
        }:
            raise OwnerTruthMediaUploadInvalid("kms key requires kms encryption")
        if self.provider_name == "cos" and not cos_endpoint_matches_region(
            endpoint_url=self._endpoint_url,
            region=self._region,
        ):
            raise OwnerTruthMediaUploadInvalid("cos endpoint and region are invalid")
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - dependency is packaged in production
                raise OwnerTruthMediaCaptureUnavailable("s3 media storage client is unavailable") from exc
            client = boto3.client(
                "s3",
                region_name=self._region,
                endpoint_url=self._endpoint_url,
                aws_access_key_id=str(access_key_id or "").strip() or None,
                aws_secret_access_key=str(secret_access_key or "").strip() or None,
            )
        self._client = client

    def write(
        self,
        *,
        storage_key: str,
        payload: bytes,
        content_type: Optional[str] = None,
        content_sha256: Optional[str] = None,
    ) -> None:
        normalized_content_type = (
            _normalize_content_type(content_type) if content_type is not None else None
        )
        normalized_content_sha256 = (
            _normalize_sha256(content_sha256) if content_sha256 is not None else None
        )
        if normalized_content_sha256 is not None and _sha256(payload) != normalized_content_sha256:
            raise OwnerTruthMediaUploadInvalid("private media payload checksum is invalid")
        request: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": self._object_key(storage_key),
            "Body": payload,
        }
        if normalized_content_type is not None:
            request["ContentType"] = normalized_content_type
        if normalized_content_sha256 is not None:
            request["Metadata"] = {
                _PRIVATE_MEDIA_OBJECT_SHA256_METADATA_KEY: normalized_content_sha256,
            }
        if self._server_side_encryption is not None:
            request["ServerSideEncryption"] = self._server_side_encryption
        if self._kms_key_id is not None:
            request["SSEKMSKeyId"] = self._kms_key_id
        try:
            self._client.put_object(**request)
        except Exception as exc:
            raise OwnerTruthMediaCaptureUnavailable("private media object write is unavailable") from exc

    def verify_upload(
        self,
        *,
        storage_key: str,
        expected_file_size_bytes: int,
        expected_content_type: str,
        expected_content_sha256: str,
    ) -> None:
        expected_size = int(expected_file_size_bytes)
        expected_type = _normalize_content_type(expected_content_type)
        expected_sha256 = _normalize_sha256(expected_content_sha256)
        if expected_size < 1:
            raise OwnerTruthMediaUploadInvalid("private media expected size is invalid")
        try:
            response = self._client.head_object(
                Bucket=self._bucket,
                Key=self._object_key(storage_key),
            )
        except Exception as exc:
            raise OwnerTruthMediaCaptureUnavailable(
                "private media object verification is unavailable"
            ) from exc
        try:
            observed_size = int(response["ContentLength"])
            observed_type = _normalize_content_type(response.get("ContentType"))
            metadata = response.get("Metadata") or {}
            observed_sha256 = _normalize_sha256(
                metadata.get(_PRIVATE_MEDIA_OBJECT_SHA256_METADATA_KEY)
            )
        except (KeyError, TypeError, ValueError, OwnerTruthMediaUploadInvalid) as exc:
            raise OwnerTruthMediaCaptureUnavailable(
                "private media object verification is unavailable"
            ) from exc
        if (
            observed_size != expected_size
            or observed_type != expected_type
            or observed_sha256 != expected_sha256
        ):
            raise OwnerTruthMediaCaptureUnavailable(
                "private media object metadata verification failed"
            )
        if self._server_side_encryption is not None:
            observed_encryption = str(
                response.get("ServerSideEncryption")
                or response.get("x-cos-server-side-encryption")
                or ""
            ).strip()
            if observed_encryption != self._server_side_encryption:
                raise OwnerTruthMediaCaptureUnavailable(
                    "private media object encryption verification failed"
                )

    def delete(self, *, storage_key: str) -> None:
        try:
            response = self._client.delete_object(
                Bucket=self._bucket,
                Key=self._object_key(storage_key),
            )
        except Exception as exc:
            # A deletion worker must distinguish an acknowledged removal from
            # an unavailable object store. Upload rollback handles this
            # exception separately because it is only best-effort cleanup.
            raise OwnerTruthMediaCaptureUnavailable(
                "private media object delete is unavailable"
            ) from exc
        if not _provider_response_is_success(response):
            raise OwnerTruthMediaCaptureUnavailable(
                "private media object delete acknowledgement is unavailable"
            )
        try:
            self._client.head_object(
                Bucket=self._bucket,
                Key=self._object_key(storage_key),
            )
        except Exception as exc:
            if _provider_error_is_object_not_found(exc):
                return
            raise OwnerTruthMediaCaptureUnavailable(
                "private media object delete verification is unavailable"
            ) from exc
        raise OwnerTruthMediaCaptureUnavailable(
            "private media object delete verification failed"
        )

    def read(self, *, storage_key: str, max_bytes: Optional[int] = None) -> bytes:
        limit = _optional_read_limit(max_bytes)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._object_key(storage_key))
            body = response["Body"]
            content_length = response.get("ContentLength")
            if limit is not None and content_length is not None and int(content_length) > limit:
                raise OwnerTruthMediaCaptureUnavailable("private media object exceeds read limit")
            payload = body.read() if limit is None else body.read(limit + 1)
        except OwnerTruthMediaCaptureUnavailable:
            raise
        except Exception as exc:
            if _provider_error_is_object_not_found(exc):
                raise OwnerTruthMediaObjectNotFound("private media object does not exist") from exc
            raise OwnerTruthMediaCaptureUnavailable("private media object read is unavailable") from exc
        if not isinstance(payload, bytes):
            raise OwnerTruthMediaCaptureUnavailable("private media object read is unavailable")
        if limit is not None and len(payload) > limit:
            raise OwnerTruthMediaCaptureUnavailable("private media object exceeds read limit")
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


def cos_endpoint_matches_region(
    *,
    endpoint_url: object,
    region: object,
) -> bool:
    normalized_region = str(region or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", normalized_region):
        return False
    try:
        parsed = urlsplit(str(endpoint_url or "").strip())
        hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
        if parsed.port not in {None, 443}:
            return False
    except ValueError:
        return False
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False
    expected_host = f"cos.{normalized_region}.myqcloud.com"
    return hostname == expected_host or hostname.endswith(f".{expected_host}")


def _provider_response_is_success(response: object) -> bool:
    if not isinstance(response, Mapping):
        return False
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return False
    try:
        status = int(metadata.get("HTTPStatusCode"))
    except (TypeError, ValueError):
        return False
    return 200 <= status < 300


def _provider_error_is_object_not_found(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return False
    metadata = response.get("ResponseMetadata")
    error_payload = response.get("Error")
    try:
        status = (
            int(metadata.get("HTTPStatusCode"))
            if isinstance(metadata, Mapping)
            else None
        )
    except (TypeError, ValueError):
        status = None
    code = (
        str(error_payload.get("Code") or "").strip()
        if isinstance(error_payload, Mapping)
        else ""
    )
    return status == 404 and code in {"", "404", "NoSuchKey", "NotFound", "ObjectNotFound"}


def _optional_identifier(value: object, *, field: str) -> Optional[str]:
    normalized = str(value or "").strip() or None
    if normalized is not None and (len(normalized) > 512 or any(character.isspace() for character in normalized)):
        raise OwnerTruthMediaUploadInvalid(f"{field} is invalid")
    return normalized


def _optional_read_limit(value: object) -> Optional[int]:
    if value is None:
        return None
    if type(value) is not int or value < 1 or value > 50 * 1024 * 1024:
        raise OwnerTruthMediaUploadInvalid("private media read limit is invalid")
    return value


def _assert_private_media_object_integrity(
    *,
    payload: bytes,
    expected_file_size_bytes: int,
    expected_content_sha256: str,
) -> None:
    if (
        len(payload) != int(expected_file_size_bytes)
        or _sha256(payload) != _normalize_sha256(expected_content_sha256)
    ):
        raise OwnerTruthMediaCaptureUnavailable(
            "private media object metadata verification failed"
        )


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
        required = (
            s3_bucket,
            s3_region,
            s3_access_key_id,
            s3_secret_access_key,
            s3_server_side_encryption,
        )
        if normalized == "cos":
            required = (*required, s3_endpoint_url)
        if not all(str(value or "").strip() for value in required):
            return DisabledPrivateMediaObjectStore()
        try:
            return S3PrivateMediaObjectStore(
                provider_name=normalized,
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
    clamav_host: Optional[str] = None,
    clamav_port: int = 3310,
    clamav_timeout_seconds: int = 30,
) -> MediaContentSafetyScanner:
    normalized = str(provider or "").strip().lower()
    if normalized == "clamav":
        if _clamav_daemon_host(clamav_host) is not None:
            return ClamAVDaemonMediaContentSafetyScanner(
                host=str(clamav_host),
                port=clamav_port,
                timeout_seconds=clamav_timeout_seconds,
            )
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


@dataclass(frozen=True)
class MediaSourceObjectDeletionResult:
    outcome: str
    source_object: Mapping[str, Any]
    deletion_effect_required: bool
    cancelled_processing_generation: Optional[int] = None


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

    def list_exportable_source_objects(
        self,
        *,
        owner_subject_id: str,
    ) -> list[Mapping[str, Any]]:
        ...

    def revoke_access_for_family_contribution(
        self,
        *,
        vault_id: str,
        source_object_id: str,
    ) -> None:
        ...

    def request_deletion(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object_id: str,
        command: MediaDeletionCommand,
    ) -> MediaSourceObjectDeletionResult:
        ...

    def retry_deletion(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object_id: str,
        command: MediaDeletionCommand,
    ) -> MediaSourceObjectDeletionResult:
        ...

    def assert_deletion_execution_allowed(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        expected_authority_epoch: int,
        expected_deletion_generation: int,
    ) -> Mapping[str, Any]:
        ...

    def assert_processing_commit_allowed(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        expected_processing_generation: int,
    ) -> Mapping[str, Any]:
        ...

    def record_deletion_outcome(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        deletion_generation: int,
        outcome: str,
        retryable: bool,
        failure_code: Optional[str] = None,
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


def _object_public_deletion_receipt(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the user-explainable deletion state, never provider detail."""

    return {
        "accessState": str(record.get("accessState") or "available"),
        "deletionStatus": str(record.get("deletionStatus") or "notRequested"),
        "retryable": bool(record.get("deletionRetryable", False)),
        "failureCode": record.get("deletionFailureCode"),
        "updatedAt": str(record.get("deletionUpdatedAt") or record["updatedAt"]),
    }


def _deletion_effect_is_required(record: Mapping[str, Any]) -> bool:
    """Keep an accepted effect repairable without reopening private access."""

    return (
        str(record.get("accessState") or "") == "accessRevoked"
        and str(record.get("deletionStatus") or "") == "pending"
        and bool(record.get("storageKey"))
        and int(record.get("storageVersion") or 0) >= 1
    )


def _assert_deletion_retryable(record: Mapping[str, Any]) -> None:
    if (
        str(record.get("accessState") or "") != "accessRevoked"
        or str(record.get("state") or "") != "deleted"
        or str(record.get("deletionStatus") or "") not in {"partial", "unsupported"}
        or record.get("deletionRetryable") is not True
        or not bool(record.get("storageKey"))
        or int(record.get("storageVersion") or 0) < 1
    ):
        raise OwnerTruthMediaUploadConflict("media deletion is not retryable")


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

    def __init__(
        self,
        *,
        vaults: dict[str, dict[str, Any]],
        lock: RLock,
        on_derived_source_access_revoked: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self._vaults = vaults
        self._lock = lock
        self._on_derived_source_access_revoked = on_derived_source_access_revoked
        self._objects: dict[tuple[str, str], dict[str, Any]] = {}
        self._intents: dict[tuple[str, str], dict[str, Any]] = {}
        self._intent_by_command: dict[tuple[str, str], str] = {}
        self._deletion_commands: dict[tuple[str, str, str], dict[str, Any]] = {}
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
                "accessState": "available",
                "deletionStatus": "notRequested",
                "deletionGeneration": 0,
                "deletionRetryable": False,
                "deletionFailureCode": None,
                "deletionRequestedAt": None,
                "deletionUpdatedAt": None,
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
            self._assert_access_active(source_object)
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
            self._assert_access_active(source_object)
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

    def list_exportable_source_objects(
        self,
        *,
        owner_subject_id: str,
    ) -> list[Mapping[str, Any]]:
        """List Owner metadata only; bytes remain behind ``read_content``."""

        normalized_owner = str(owner_subject_id or "").strip()
        if not normalized_owner:
            return []
        with self._lock:
            return [
                deepcopy(item)
                for item in sorted(
                    self._objects.values(),
                    key=lambda value: (
                        str(value.get("createdAt") or ""),
                        str(value.get("sourceObjectId") or ""),
                    ),
                )
                if str(item.get("ownerSubjectId") or "") == normalized_owner
            ]

    def revoke_access_for_family_contribution(
        self,
        *,
        vault_id: str,
        source_object_id: str,
    ) -> None:
        with self._lock:
            value = self._objects.get((vault_id, source_object_id))
            if value is None:
                return
            value["accessState"] = "revoked"
            value["processingStatus"] = "blocked"
            value["retryable"] = False
            value["failureCode"] = "familyContributionGrantRevoked"
            value["rowVersion"] = int(value.get("rowVersion") or 0) + 1
            value["updatedAt"] = _utc_iso(_utc_now())

    def request_deletion(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object_id: str,
        command: MediaDeletionCommand,
    ) -> MediaSourceObjectDeletionResult:
        with self._lock:
            vault = self._ensure_owned_vault(context)
            source_object = self._owned_source_object(
                vault_id=context.vault_id,
                source_object_id=source_object_id,
                owner_subject_id=context.owner_subject_id,
            )
            payload_hash = command.payload_hash(
                vault_id=context.vault_id,
                source_object_id=source_object_id,
            )
            command_key = (context.vault_id, source_object_id, command.command_id_hash)
            existing_command = self._deletion_commands.get(command_key)
            if existing_command is not None:
                if existing_command["payloadHash"] != payload_hash:
                    raise OwnerTruthMediaUploadConflict("deletion command cannot change meaning")
                return MediaSourceObjectDeletionResult(
                    outcome="deduplicated",
                    source_object=deepcopy(source_object),
                    deletion_effect_required=_deletion_effect_is_required(source_object),
                )
            current_epoch = int(vault.get("authorityEpoch") or 0)
            if command.expected_authority_epoch != current_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=command.expected_authority_epoch,
                    current_epoch=current_epoch,
                )

            now = _utc_now()
            already_revoked = str(source_object.get("accessState") or "available") == "accessRevoked"
            cancelled_processing_generation: Optional[int] = None
            if not already_revoked:
                if str(source_object.get("processingStatus") or "") in {
                    "queued",
                    "processing",
                    "retryableFailed",
                }:
                    cancelled_processing_generation = int(
                        source_object.get("processingGeneration") or 0
                    )
                deletion_effect_required = bool(source_object.get("storageKey"))
                deletion_status = "pending" if deletion_effect_required else "completed"
                source_object.update(
                    state="deleted",
                    accessState="accessRevoked",
                    processingStatus="blocked",
                    processingGeneration=int(source_object.get("processingGeneration") or 0) + 1,
                    processingAttempt=0,
                    retryable=False,
                    failureCode=None,
                    deletionStatus=deletion_status,
                    deletionGeneration=int(source_object.get("deletionGeneration") or 0) + 1,
                    deletionRetryable=deletion_effect_required,
                    deletionFailureCode=None,
                    deletionRequestedAt=_utc_iso(now),
                    deletionUpdatedAt=_utc_iso(now),
                    updatedAt=_utc_iso(now),
                    rowVersion=int(source_object["rowVersion"]) + 1,
                )
                derived_source_id = source_object.get("derivedSourceId")
                if derived_source_id and self._on_derived_source_access_revoked is not None:
                    self._on_derived_source_access_revoked(
                        context.vault_id,
                        context.owner_subject_id,
                        str(derived_source_id),
                    )
            self._deletion_commands[command_key] = {
                "deletionCommandId": command.deletion_command_id(
                    vault_id=context.vault_id,
                    source_object_id=source_object_id,
                ),
                "payloadHash": payload_hash,
                "deletionGeneration": int(source_object.get("deletionGeneration") or 0),
                "createdAt": _utc_iso(now),
            }
            return MediaSourceObjectDeletionResult(
                outcome="accepted" if not already_revoked else "alreadyRevoked",
                source_object=deepcopy(source_object),
                deletion_effect_required=_deletion_effect_is_required(source_object),
                cancelled_processing_generation=cancelled_processing_generation,
            )

    def retry_deletion(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object_id: str,
        command: MediaDeletionCommand,
    ) -> MediaSourceObjectDeletionResult:
        with self._lock:
            vault = self._ensure_owned_vault(context)
            source_object = self._owned_source_object(
                vault_id=context.vault_id,
                source_object_id=source_object_id,
                owner_subject_id=context.owner_subject_id,
            )
            payload_hash = command.payload_hash(
                vault_id=context.vault_id,
                source_object_id=source_object_id,
            )
            command_key = (context.vault_id, source_object_id, command.command_id_hash)
            existing_command = self._deletion_commands.get(command_key)
            if existing_command is not None:
                if existing_command["payloadHash"] != payload_hash:
                    raise OwnerTruthMediaUploadConflict("deletion command cannot change meaning")
                return MediaSourceObjectDeletionResult(
                    outcome="deduplicated",
                    source_object=deepcopy(source_object),
                    deletion_effect_required=_deletion_effect_is_required(source_object),
                )
            current_epoch = int(vault.get("authorityEpoch") or 0)
            if command.expected_authority_epoch != current_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=command.expected_authority_epoch,
                    current_epoch=current_epoch,
                )
            _assert_deletion_retryable(source_object)
            now = _utc_now()
            source_object.update(
                state="deleted",
                accessState="accessRevoked",
                processingStatus="blocked",
                retryable=False,
                failureCode=None,
                deletionStatus="pending",
                deletionGeneration=int(source_object.get("deletionGeneration") or 0) + 1,
                deletionRetryable=True,
                deletionFailureCode=None,
                deletionUpdatedAt=_utc_iso(now),
                updatedAt=_utc_iso(now),
                rowVersion=int(source_object["rowVersion"]) + 1,
            )
            self._deletion_commands[command_key] = {
                "deletionCommandId": command.deletion_command_id(
                    vault_id=context.vault_id,
                    source_object_id=source_object_id,
                ),
                "payloadHash": payload_hash,
                "deletionGeneration": int(source_object["deletionGeneration"]),
                "createdAt": _utc_iso(now),
            }
            return MediaSourceObjectDeletionResult(
                outcome="retryAccepted",
                source_object=deepcopy(source_object),
                deletion_effect_required=True,
            )

    def assert_deletion_execution_allowed(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        expected_authority_epoch: int,
        expected_deletion_generation: int,
    ) -> Mapping[str, Any]:
        if type(expected_authority_epoch) is not int or expected_authority_epoch < 0:
            raise OwnerTruthMediaUploadInvalid("media deletion authority epoch is invalid")
        if type(expected_deletion_generation) is not int or expected_deletion_generation < 1:
            raise OwnerTruthMediaUploadInvalid("media deletion generation is invalid")
        with self._lock:
            source_object = self._owned_source_object(
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
            )
            current_epoch = int(source_object.get("authorityEpoch") or 0)
            if current_epoch != expected_authority_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=expected_authority_epoch,
                    current_epoch=current_epoch,
                )
            if int(source_object.get("deletionGeneration") or 0) != expected_deletion_generation:
                raise OwnerTruthMediaUploadConflict("media deletion generation is no longer current")
            if (
                str(source_object.get("accessState") or "") != "accessRevoked"
                or str(source_object.get("state") or "") != "deleted"
                or str(source_object.get("deletionStatus") or "") != "pending"
            ):
                raise OwnerTruthMediaUploadConflict("media deletion is not eligible for execution")
            return deepcopy(source_object)

    def assert_processing_commit_allowed(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        expected_processing_generation: int,
    ) -> Mapping[str, Any]:
        with self._lock:
            source_object = self._owned_source_object(
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
            )
            self._assert_access_active(source_object)
            if int(source_object.get("processingGeneration") or 0) != expected_processing_generation:
                raise OwnerTruthMediaUploadConflict("media processing generation is no longer current")
            if source_object.get("state") != "processing":
                raise OwnerTruthMediaUploadConflict("media source object is no longer processing")
            return deepcopy(source_object)

    def record_deletion_outcome(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        deletion_generation: int,
        outcome: str,
        retryable: bool,
        failure_code: Optional[str] = None,
    ) -> Mapping[str, Any]:
        with self._lock:
            source_object = self._owned_source_object(
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
            )
            return self._record_deletion_outcome_locked(
                source_object=source_object,
                deletion_generation=deletion_generation,
                outcome=outcome,
                retryable=retryable,
                failure_code=failure_code,
            )

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
            self._assert_access_active(source_object)
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
            self._assert_access_active(source_object)
            if int(source_object["authorityEpoch"]) != expected_authority_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=expected_authority_epoch,
                    current_epoch=int(source_object["authorityEpoch"]),
                )
            if int(source_object.get("processingGeneration") or 0) != expected_processing_generation:
                raise OwnerTruthMediaUploadConflict("media processing generation is no longer current")
            fresh_start = (
                source_object["state"] == "verified"
                and source_object["processingStatus"] in {"queued", "retryableFailed"}
            )
            expired_lease_recovery = (
                source_object["state"] == "processing"
                and source_object["processingStatus"] == "processing"
                and attempt > int(source_object.get("processingAttempt") or 0)
            )
            if source_object["safetyStatus"] != "clean" or not (
                fresh_start or expired_lease_recovery
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
            self._assert_access_active(source_object)
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
                "deletionCommands": deepcopy(self._deletion_commands),
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

    @staticmethod
    def _assert_access_active(source_object: Mapping[str, Any]) -> None:
        if (
            str(source_object.get("accessState") or "available") != "available"
            or str(source_object.get("state") or "") == "deleted"
        ):
            raise OwnerTruthMediaAccessRevoked("media source object access was revoked")

    def _record_deletion_outcome_locked(
        self,
        *,
        source_object: dict[str, Any],
        deletion_generation: int,
        outcome: str,
        retryable: bool,
        failure_code: Optional[str],
    ) -> Mapping[str, Any]:
        if type(deletion_generation) is not int or deletion_generation < 1:
            raise OwnerTruthMediaUploadInvalid("media deletion generation is invalid")
        if int(source_object.get("deletionGeneration") or 0) != deletion_generation:
            raise OwnerTruthMediaUploadConflict("media deletion generation is no longer current")
        if str(source_object.get("accessState") or "") != "accessRevoked":
            raise OwnerTruthMediaUploadConflict("media deletion requires revoked access")
        normalized_outcome = str(outcome or "").strip()
        if normalized_outcome not in {"completed", "partial", "unsupported"}:
            raise OwnerTruthMediaUploadInvalid("media deletion outcome is invalid")
        if type(retryable) is not bool:
            raise OwnerTruthMediaUploadInvalid("media deletion retryable flag is invalid")
        normalized_failure_code = str(failure_code or "").strip() or None
        if normalized_failure_code is not None and not _PURPOSE_PATTERN.fullmatch(normalized_failure_code):
            raise OwnerTruthMediaUploadInvalid("media deletion failure code is invalid")
        if normalized_outcome == "completed":
            if retryable or normalized_failure_code is not None:
                raise OwnerTruthMediaUploadInvalid("completed media deletion cannot be retryable")
        elif normalized_failure_code is None:
            raise OwnerTruthMediaUploadInvalid("incomplete media deletion outcome requires failure code")
        now = _utc_now()
        source_object.update(
            state="deleted",
            accessState="accessRevoked",
            processingStatus="blocked",
            retryable=False,
            failureCode=None,
            deletionStatus=normalized_outcome,
            deletionRetryable=retryable,
            deletionFailureCode=normalized_failure_code,
            deletionUpdatedAt=_utc_iso(now),
            updatedAt=_utc_iso(now),
            rowVersion=int(source_object["rowVersion"]) + 1,
        )
        return deepcopy(source_object)

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
        self._assert_access_active(source_object)
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
            self._assert_access_active(source_object)
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
            source_object = self._fetch_source_object(
                cursor=cursor,
                vault_id=vault_id,
                source_object_id=str(intent["source_object_id"]),
                for_update=True,
            )
            self._assert_access_active(source_object)
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

    def revoke_access_for_family_contribution(
        self,
        *,
        vault_id: str,
        source_object_id: str,
    ) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                """
                UPDATE owner_truth.media_source_objects
                SET access_state = 'revoked', processing_status = 'blocked',
                    retryable = FALSE,
                    failure_code = 'familyContributionGrantRevoked',
                    row_version = row_version + 1,
                    updated_at = NOW()
                WHERE vault_id = %s AND id = %s
                  AND access_state = 'available'
                """,
                (vault_id, source_object_id),
            )

    def request_deletion(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object_id: str,
        command: MediaDeletionCommand,
    ) -> MediaSourceObjectDeletionResult:
        with self._cursor() as cursor:
            vault = self._ensure_owned_vault(cursor=cursor, context=context)
            source_object = self._fetch_owned_source_object(
                cursor=cursor,
                vault_id=context.vault_id,
                source_object_id=source_object_id,
                owner_subject_id=context.owner_subject_id,
                for_update=True,
            )
            payload_hash = command.payload_hash(
                vault_id=context.vault_id,
                source_object_id=source_object_id,
            )
            cursor.execute(
                """
                SELECT payload_hash
                FROM owner_truth.media_source_object_deletion_commands
                WHERE vault_id = %s AND source_object_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, source_object_id, command.command_id_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash:
                    raise OwnerTruthMediaUploadConflict("deletion command cannot change meaning")
                return MediaSourceObjectDeletionResult(
                    outcome="deduplicated",
                    source_object=source_object,
                    deletion_effect_required=_deletion_effect_is_required(source_object),
                )
            current_epoch = int(vault["authority_epoch"])
            if command.expected_authority_epoch != current_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=command.expected_authority_epoch,
                    current_epoch=current_epoch,
                )

            already_revoked = str(source_object.get("accessState") or "available") == "accessRevoked"
            cancelled_processing_generation: Optional[int] = None
            if not already_revoked:
                if str(source_object.get("processingStatus") or "") in {
                    "queued",
                    "processing",
                    "retryableFailed",
                }:
                    cancelled_processing_generation = int(
                        source_object.get("processingGeneration") or 0
                    )
                deletion_effect_required = bool(source_object.get("storageKey"))
                deletion_status = "pending" if deletion_effect_required else "completed"
                cursor.execute(
                    """
                    UPDATE owner_truth.media_source_objects
                    SET state = 'deleted', access_state = 'accessRevoked',
                        processing_status = 'blocked',
                        processing_generation = processing_generation + 1,
                        processing_attempt = 0, retryable = FALSE, failure_code = NULL,
                        deletion_status = %s,
                        deletion_generation = deletion_generation + 1,
                        deletion_retryable = %s, deletion_failure_code = NULL,
                        deletion_requested_at = NOW(), deletion_updated_at = NOW(),
                        updated_at = NOW(), row_version = row_version + 1
                    WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
                    RETURNING *
                    """,
                    (
                        deletion_status,
                        deletion_effect_required,
                        context.vault_id,
                        source_object_id,
                        context.owner_subject_id,
                    ),
                )
                source_object = self._source_object_record(cursor.fetchone())
                if source_object.get("derivedSourceId"):
                    cursor.execute(
                        """
                        UPDATE owner_truth.sources
                        SET state = 'deleted', row_version = row_version + 1, updated_at = NOW()
                        WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
                          AND state <> 'deleted'
                        """,
                        (
                            context.vault_id,
                            source_object["derivedSourceId"],
                            context.owner_subject_id,
                        ),
                    )
            cursor.execute(
                """
                INSERT INTO owner_truth.media_source_object_deletion_commands (
                    id, vault_id, source_object_id, owner_subject_id, command_id_hash,
                    payload_hash, deletion_generation
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    command.deletion_command_id(
                        vault_id=context.vault_id,
                        source_object_id=source_object_id,
                    ),
                    context.vault_id,
                    source_object_id,
                    context.owner_subject_id,
                    command.command_id_hash,
                    payload_hash,
                    int(source_object.get("deletionGeneration") or 0),
                ),
            )
            return MediaSourceObjectDeletionResult(
                outcome="accepted" if not already_revoked else "alreadyRevoked",
                source_object=source_object,
                deletion_effect_required=_deletion_effect_is_required(source_object),
                cancelled_processing_generation=cancelled_processing_generation,
            )

    def retry_deletion(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object_id: str,
        command: MediaDeletionCommand,
    ) -> MediaSourceObjectDeletionResult:
        with self._cursor() as cursor:
            vault = self._ensure_owned_vault(cursor=cursor, context=context)
            source_object = self._fetch_owned_source_object(
                cursor=cursor,
                vault_id=context.vault_id,
                source_object_id=source_object_id,
                owner_subject_id=context.owner_subject_id,
                for_update=True,
            )
            payload_hash = command.payload_hash(
                vault_id=context.vault_id,
                source_object_id=source_object_id,
            )
            cursor.execute(
                """
                SELECT payload_hash
                FROM owner_truth.media_source_object_deletion_commands
                WHERE vault_id = %s AND source_object_id = %s AND command_id_hash = %s
                FOR UPDATE
                """,
                (context.vault_id, source_object_id, command.command_id_hash),
            )
            existing = cursor.fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash:
                    raise OwnerTruthMediaUploadConflict("deletion command cannot change meaning")
                return MediaSourceObjectDeletionResult(
                    outcome="deduplicated",
                    source_object=source_object,
                    deletion_effect_required=_deletion_effect_is_required(source_object),
                )
            current_epoch = int(vault["authority_epoch"])
            if command.expected_authority_epoch != current_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=command.expected_authority_epoch,
                    current_epoch=current_epoch,
                )
            _assert_deletion_retryable(source_object)
            cursor.execute(
                """
                UPDATE owner_truth.media_source_objects
                SET state = 'deleted', access_state = 'accessRevoked',
                    processing_status = 'blocked', retryable = FALSE, failure_code = NULL,
                    deletion_status = 'pending', deletion_generation = deletion_generation + 1,
                    deletion_retryable = TRUE, deletion_failure_code = NULL,
                    deletion_updated_at = NOW(), updated_at = NOW(), row_version = row_version + 1
                WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
                RETURNING *
                """,
                (context.vault_id, source_object_id, context.owner_subject_id),
            )
            source_object = self._source_object_record(cursor.fetchone())
            cursor.execute(
                """
                INSERT INTO owner_truth.media_source_object_deletion_commands (
                    id, vault_id, source_object_id, owner_subject_id, command_id_hash,
                    payload_hash, deletion_generation
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    command.deletion_command_id(
                        vault_id=context.vault_id,
                        source_object_id=source_object_id,
                    ),
                    context.vault_id,
                    source_object_id,
                    context.owner_subject_id,
                    command.command_id_hash,
                    payload_hash,
                    int(source_object["deletionGeneration"]),
                ),
            )
            return MediaSourceObjectDeletionResult(
                outcome="retryAccepted",
                source_object=source_object,
                deletion_effect_required=True,
            )

    def assert_deletion_execution_allowed(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        expected_authority_epoch: int,
        expected_deletion_generation: int,
    ) -> Mapping[str, Any]:
        if type(expected_authority_epoch) is not int or expected_authority_epoch < 0:
            raise OwnerTruthMediaUploadInvalid("media deletion authority epoch is invalid")
        if type(expected_deletion_generation) is not int or expected_deletion_generation < 1:
            raise OwnerTruthMediaUploadInvalid("media deletion generation is invalid")
        with self._cursor() as cursor:
            source_object = self._fetch_owned_source_object(
                cursor=cursor,
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
                for_update=True,
            )
            current_epoch = int(source_object.get("authorityEpoch") or 0)
            if current_epoch != expected_authority_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=expected_authority_epoch,
                    current_epoch=current_epoch,
                )
            if int(source_object.get("deletionGeneration") or 0) != expected_deletion_generation:
                raise OwnerTruthMediaUploadConflict("media deletion generation is no longer current")
            if (
                str(source_object.get("accessState") or "") != "accessRevoked"
                or str(source_object.get("state") or "") != "deleted"
                or str(source_object.get("deletionStatus") or "") != "pending"
            ):
                raise OwnerTruthMediaUploadConflict("media deletion is not eligible for execution")
            return source_object

    def assert_processing_commit_allowed(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        expected_processing_generation: int,
    ) -> Mapping[str, Any]:
        with self._cursor() as cursor:
            source_object = self._fetch_owned_source_object(
                cursor=cursor,
                vault_id=vault_id,
                source_object_id=source_object_id,
                owner_subject_id=owner_subject_id,
                for_update=True,
            )
            self._assert_access_active(source_object)
            if int(source_object.get("processingGeneration") or 0) != expected_processing_generation:
                raise OwnerTruthMediaUploadConflict("media processing generation is no longer current")
            if source_object.get("state") != "processing":
                raise OwnerTruthMediaUploadConflict("media source object is no longer processing")
            return source_object

    def record_deletion_outcome(
        self,
        *,
        vault_id: str,
        source_object_id: str,
        owner_subject_id: str,
        deletion_generation: int,
        outcome: str,
        retryable: bool,
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
            return self._record_deletion_outcome_cursor(
                cursor=cursor,
                source_object=source_object,
                deletion_generation=deletion_generation,
                outcome=outcome,
                retryable=retryable,
                failure_code=failure_code,
            )

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
            self._assert_access_active(source_object)
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
            self._assert_access_active(source_object)
            if int(source_object["authorityEpoch"]) != expected_authority_epoch:
                raise OwnerTruthMediaAuthorityEpochConflict(
                    expected_epoch=expected_authority_epoch,
                    current_epoch=int(source_object["authorityEpoch"]),
                )
            if int(source_object.get("processingGeneration") or 0) != expected_processing_generation:
                raise OwnerTruthMediaUploadConflict("media processing generation is no longer current")
            fresh_start = (
                source_object["state"] == "verified"
                and source_object["processingStatus"] in {"queued", "retryableFailed"}
            )
            expired_lease_recovery = (
                source_object["state"] == "processing"
                and source_object["processingStatus"] == "processing"
                and attempt > int(source_object.get("processingAttempt") or 0)
            )
            if source_object["safetyStatus"] != "clean" or not (
                fresh_start or expired_lease_recovery
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
            self._assert_access_active(source_object)
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
        self._assert_access_active(source_object)
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
    def _assert_access_active(source_object: Mapping[str, Any]) -> None:
        if (
            str(source_object.get("accessState") or "available") != "available"
            or str(source_object.get("state") or "") == "deleted"
        ):
            raise OwnerTruthMediaAccessRevoked("media source object access was revoked")

    def _record_deletion_outcome_cursor(
        self,
        *,
        cursor: Any,
        source_object: Mapping[str, Any],
        deletion_generation: int,
        outcome: str,
        retryable: bool,
        failure_code: Optional[str],
    ) -> Mapping[str, Any]:
        if type(deletion_generation) is not int or deletion_generation < 1:
            raise OwnerTruthMediaUploadInvalid("media deletion generation is invalid")
        if int(source_object.get("deletionGeneration") or 0) != deletion_generation:
            raise OwnerTruthMediaUploadConflict("media deletion generation is no longer current")
        if str(source_object.get("accessState") or "") != "accessRevoked":
            raise OwnerTruthMediaUploadConflict("media deletion requires revoked access")
        normalized_outcome = str(outcome or "").strip()
        if normalized_outcome not in {"completed", "partial", "unsupported"}:
            raise OwnerTruthMediaUploadInvalid("media deletion outcome is invalid")
        if type(retryable) is not bool:
            raise OwnerTruthMediaUploadInvalid("media deletion retryable flag is invalid")
        normalized_failure_code = str(failure_code or "").strip() or None
        if normalized_failure_code is not None and not _PURPOSE_PATTERN.fullmatch(normalized_failure_code):
            raise OwnerTruthMediaUploadInvalid("media deletion failure code is invalid")
        if normalized_outcome == "completed":
            if retryable or normalized_failure_code is not None:
                raise OwnerTruthMediaUploadInvalid("completed media deletion cannot be retryable")
        elif normalized_failure_code is None:
            raise OwnerTruthMediaUploadInvalid("incomplete media deletion outcome requires failure code")
        cursor.execute(
            """
            UPDATE owner_truth.media_source_objects
            SET state = 'deleted', access_state = 'accessRevoked', processing_status = 'blocked',
                retryable = FALSE, failure_code = NULL, deletion_status = %s,
                deletion_retryable = %s, deletion_failure_code = %s,
                deletion_updated_at = NOW(), updated_at = NOW(), row_version = row_version + 1
            WHERE vault_id = %s AND id = %s AND owner_subject_id = %s
            RETURNING *
            """,
            (
                normalized_outcome,
                retryable,
                normalized_failure_code,
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
            "accessState": str(row.get("access_state") or "available"),
            "deletionStatus": str(row.get("deletion_status") or "notRequested"),
            "deletionGeneration": int(row.get("deletion_generation") or 0),
            "deletionRetryable": bool(row.get("deletion_retryable", False)),
            "deletionFailureCode": row.get("deletion_failure_code"),
            "deletionRequestedAt": _utc_iso(row["deletion_requested_at"])
            if row.get("deletion_requested_at")
            else None,
            "deletionUpdatedAt": _utc_iso(row["deletion_updated_at"])
            if row.get("deletion_updated_at")
            else None,
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
        wrote_object = False
        try:
            self._object_store.write(
                storage_key=storage_key,
                payload=payload,
                content_type=expected_content_type,
                content_sha256=str(source_object["contentSha256"]),
            )
            wrote_object = True
            self._object_store.verify_upload(
                storage_key=storage_key,
                expected_file_size_bytes=int(source_object["fileSizeBytes"]),
                expected_content_type=expected_content_type,
                expected_content_sha256=str(source_object["contentSha256"]),
            )
        except Exception:
            if wrote_object:
                try:
                    self._object_store.delete(storage_key=storage_key)
                except OwnerTruthMediaCaptureUnavailable:
                    # A failed verification is never committed as an uploaded
                    # SourceObject. Retain the primary failure if rollback is
                    # unavailable; the object remains inaccessible by API.
                    pass
            raise
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
            try:
                self._object_store.delete(storage_key=storage_key)
            except OwnerTruthMediaCaptureUnavailable:
                # Preserve the original metadata/write failure. Any residual
                # private bytes remain inaccessible and can later be removed
                # through the revocation-first deletion lane.
                pass
            raise

    def read_content(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object_id: str,
    ) -> tuple[Mapping[str, Any], bytes]:
        """Read one verified private object through the authorized API boundary.

        Object-store keys and URLs never leave this service. Each read repeats
        the storage metadata verification so a later replacement cannot be
        served merely because its original upload had once been verified.
        """

        source_object = self.get_source_object(
            context=context,
            source_object_id=source_object_id,
        )
        if (
            str(source_object.get("accessState") or "available") != "available"
            or str(source_object.get("state") or "") != "verified"
        ):
            raise OwnerTruthMediaAccessRevoked("media source object access was revoked")
        if str(source_object.get("storageProvider") or "") != self._object_store.provider_name:
            raise OwnerTruthMediaCaptureUnavailable("private media storage is unavailable")
        storage_key = str(source_object.get("storageKey") or "")
        if not storage_key:
            raise OwnerTruthMediaCaptureUnavailable("private media storage is unavailable")
        self._object_store.verify_upload(
            storage_key=storage_key,
            expected_file_size_bytes=int(source_object["fileSizeBytes"]),
            expected_content_type=str(source_object["contentType"]),
            expected_content_sha256=str(source_object["contentSha256"]),
        )
        payload = self._object_store.read(
            storage_key=storage_key,
            max_bytes=int(source_object["fileSizeBytes"]),
        )
        _assert_private_media_object_integrity(
            payload=payload,
            expected_file_size_bytes=int(source_object["fileSizeBytes"]),
            expected_content_sha256=str(source_object["contentSha256"]),
        )
        return source_object, payload

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

    def request_deletion(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object_id: str,
        command: MediaDeletionCommand,
    ) -> MediaSourceObjectDeletionResult:
        self._require_available()
        return self._repository().request_deletion(
            context=context,
            source_object_id=self._normalize_uuid(source_object_id, field="source object id"),
            command=command,
        )

    def retry_deletion(
        self,
        *,
        context: OwnerTruthCommandContext,
        source_object_id: str,
        command: MediaDeletionCommand,
    ) -> MediaSourceObjectDeletionResult:
        self._require_available()
        return self._repository().retry_deletion(
            context=context,
            source_object_id=self._normalize_uuid(source_object_id, field="source object id"),
            command=command,
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

    @staticmethod
    def public_deletion_response(result: MediaSourceObjectDeletionResult) -> dict[str, Any]:
        status = {
            "accepted": "deletionRequested",
            "retryAccepted": "deletionRetryRequested",
            "deduplicated": "deletionDeduplicated",
            "alreadyRevoked": "deletionDeduplicated",
        }.get(result.outcome)
        if status is None:
            raise OwnerTruthMediaUploadInvalid("media deletion outcome is invalid")
        return {
            "schemaVersion": OWNER_TRUTH_MEDIA_DELETION_RESPONSE_SCHEMA_VERSION,
            "status": status,
            "sourceObject": _object_public_receipt(result.source_object),
            "deletion": _object_public_deletion_receipt(result.source_object),
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
    "ClamAVDaemonMediaContentSafetyScanner",
    "ClamAVMediaContentSafetyScanner",
    "DisabledMediaContentSafetyScanner",
    "FilesystemPrivateMediaObjectStore",
    "InMemoryOwnerTruthMediaSourceObjectRepository",
    "MediaDeletionCommand",
    "MediaSafetyVerdict",
    "MediaSourceObjectDeletionResult",
    "MediaUploadIntentCommand",
    "MediaUploadIntentCreateResult",
    "OwnerTruthMediaAuthorityEpochConflict",
    "OwnerTruthMediaAccessRevoked",
    "OwnerTruthMediaCaptureUnavailable",
    "OwnerTruthMediaIngestionError",
    "OwnerTruthMediaIngestionService",
    "OwnerTruthMediaObjectNotFound",
    "OwnerTruthMediaSourceObjectRepository",
    "OwnerTruthMediaUploadConflict",
    "OwnerTruthMediaUploadExpired",
    "OwnerTruthMediaUploadInvalid",
    "OwnerTruthMediaUploadNotFound",
    "OwnerTruthMediaUploadTokenInvalid",
    "OwnerTruthMediaVaultNotFound",
    "OWNER_TRUTH_MEDIA_SOURCE_OBJECT_SCHEMA_VERSION",
    "OWNER_TRUTH_MEDIA_DELETION_RESPONSE_SCHEMA_VERSION",
    "OWNER_TRUTH_MEDIA_UPLOAD_INTENT_SCHEMA_VERSION",
    "PostgresOwnerTruthMediaSourceObjectRepository",
    "PrivateMediaObjectStore",
    "TestOnlyCleanMediaContentSafetyScanner",
    "build_media_content_safety_scanner",
    "build_private_media_object_store",
    "clamav_daemon_runtime_ready",
    "clamav_scanner_runtime_ready",
    "cos_endpoint_matches_region",
    "inspect_magic_mime",
]
