"""Owner-scoped, short-lived ZIP export package materialization.

The existing export job remains the authority for text and metadata.  This
module adds verified private media bytes without ever exposing object-store
keys or permanent URLs.  Packages are written to a temporary file so callers
can stream them and reliably remove them after response completion.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Iterable, Mapping
import zipfile


DATA_EXPORT_PACKAGE_SCHEMA_VERSION = 1
DEFAULT_EXPORT_PACKAGE_MAX_BYTES = 512 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class DataExportPackageError(RuntimeError):
    """Stable package failure that never includes Provider detail."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class DataExportPackageCancelled(DataExportPackageError):
    def __init__(self) -> None:
        super().__init__("dataExportPackageCancelled")


@dataclass(frozen=True)
class DataExportPackageResult:
    path: str
    content_sha256: str
    size_bytes: int
    media_count: int
    package_manifest: Mapping[str, Any]

    def cleanup(self) -> None:
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass


def materialize_data_export_package(
    *,
    job_id: str,
    owner_user_id: str,
    artifact: Mapping[str, Any],
    media_objects: Iterable[Mapping[str, Any]],
    media_reader: Callable[[Mapping[str, Any]], bytes],
    temp_root: str | None = None,
    max_package_bytes: int = DEFAULT_EXPORT_PACKAGE_MAX_BYTES,
    cancelled: Callable[[], bool] | None = None,
) -> DataExportPackageResult:
    """Write one deterministic, verifiable ZIP package to a temporary file.

    ``media_reader`` must repeat the Owner/Vault authorization and storage
    integrity checks.  This function additionally checks the returned bytes
    against the source-object receipt before admitting them to the package.
    """

    if not isinstance(artifact, Mapping):
        raise DataExportPackageError("dataExportArtifactUnavailable")
    normalized_job_id = _required(job_id, "jobId")
    normalized_owner = _required(owner_user_id, "ownerUserId")
    limit = int(max_package_bytes)
    if limit < 1:
        raise DataExportPackageError("dataExportPackageLimitInvalid")
    cancellation_check = cancelled or (lambda: False)
    root = Path(temp_root) if temp_root else None
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)

    descriptor, path = tempfile.mkstemp(
        prefix=f"dreamjourney-export-{_safe_name(normalized_job_id)}-",
        suffix=".zip",
        dir=str(root) if root is not None else None,
    )
    os.close(descriptor)
    package_path = Path(path)
    media_manifest: list[dict[str, Any]] = []
    admitted_bytes = 0
    try:
        if cancellation_check():
            raise DataExportPackageCancelled()
        permission_manifest = artifact.get("dataExport", {}).get(
            "machineReadable", {}
        ).get("permissionManifest")
        if not isinstance(permission_manifest, Mapping):
            raise DataExportPackageError("dataExportPermissionManifestUnavailable")
        with zipfile.ZipFile(
            package_path,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            admitted_bytes = _write_json(
                archive,
                "data-export.json",
                artifact,
                admitted_bytes=admitted_bytes,
                limit=limit,
            )
            admitted_bytes = _write_json(
                archive,
                "permissions.json",
                permission_manifest,
                admitted_bytes=admitted_bytes,
                limit=limit,
            )
            for index, item in enumerate(media_objects):
                if cancellation_check():
                    raise DataExportPackageCancelled()
                if str(item.get("ownerSubjectId") or "") != normalized_owner:
                    raise DataExportPackageError("dataExportMediaOwnerMismatch")
                if str(item.get("accessState") or "available") != "available":
                    continue
                if str(item.get("state") or "") != "verified":
                    continue
                payload = media_reader(item)
                if not isinstance(payload, bytes):
                    raise DataExportPackageError("dataExportMediaReadFailed")
                expected_size = int(item.get("fileSizeBytes") or -1)
                expected_hash = str(item.get("contentSha256") or "").lower()
                if len(payload) != expected_size or sha256(payload).hexdigest() != expected_hash:
                    raise DataExportPackageError("dataExportMediaIntegrityMismatch")
                admitted_bytes = _admit_size(admitted_bytes, len(payload), limit)
                source_object_id = _required(item.get("sourceObjectId"), "sourceObjectId")
                safe_source_object_id = _safe_name(source_object_id)
                filename = _safe_name(str(item.get("fileName") or source_object_id))
                member_name = f"media/{index + 1:04d}-{safe_source_object_id}-{filename}"
                archive.writestr(member_name, payload)
                media_manifest.append(
                    {
                        "sourceObjectId": source_object_id,
                        "vaultId": _required(item.get("vaultId"), "vaultId"),
                        "path": member_name,
                        "contentType": str(item.get("contentType") or "application/octet-stream"),
                        "fileSizeBytes": len(payload),
                        "contentSha256": expected_hash,
                    }
                )
            manifest = {
                "schemaVersion": DATA_EXPORT_PACKAGE_SCHEMA_VERSION,
                "jobId": normalized_job_id,
                "ownerUserId": normalized_owner,
                "artifactHash": _hash_json(artifact),
                "permissionManifestHash": _hash_json(permission_manifest),
                "mediaCount": len(media_manifest),
                "media": media_manifest,
            }
            _write_json(
                archive,
                "package-manifest.json",
                manifest,
                admitted_bytes=admitted_bytes,
                limit=limit,
            )
        size_bytes = package_path.stat().st_size
        if size_bytes > limit:
            raise DataExportPackageError("dataExportPackageTooLarge")
        content_hash = _hash_file(package_path)
        return DataExportPackageResult(
            path=str(package_path),
            content_sha256=content_hash,
            size_bytes=size_bytes,
            media_count=len(media_manifest),
            package_manifest=manifest,
        )
    except Exception:
        try:
            package_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _write_json(
    archive: zipfile.ZipFile,
    name: str,
    value: Any,
    *,
    admitted_bytes: int,
    limit: int,
) -> int:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DataExportPackageError("dataExportPackageSerializationFailed") from exc
    admitted = _admit_size(admitted_bytes, len(payload), limit)
    archive.writestr(name, payload)
    return admitted


def _admit_size(current: int, addition: int, limit: int) -> int:
    total = current + max(0, int(addition))
    if total > limit:
        raise DataExportPackageError("dataExportPackageTooLarge")
    return total


def _hash_json(value: Any) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(value: Any, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise DataExportPackageError(f"dataExport{field}Missing")
    return normalized


def _safe_name(value: str) -> str:
    normalized = _SAFE_NAME.sub("_", value.strip())[:120].strip("._")
    return normalized or "file"


__all__ = [
    "DATA_EXPORT_PACKAGE_SCHEMA_VERSION",
    "DEFAULT_EXPORT_PACKAGE_MAX_BYTES",
    "DataExportPackageCancelled",
    "DataExportPackageError",
    "DataExportPackageResult",
    "materialize_data_export_package",
]
