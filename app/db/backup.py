from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


BACKUP_ID_PATTERN = re.compile(r"^dj-\d{8}T\d{6}Z-[a-z0-9]{8,32}$")
SCHEMA_HEAD_PATTERN = re.compile(r"^(?:\d{4,}|unknown)$")
LSN_PATTERN = re.compile(r"^(?:[0-9A-F]+/[0-9A-F]+|unknown)$")
MACHINE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BackupManifestError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _datetime(value: Any, *, code: str) -> datetime:
    if isinstance(value, datetime):
        candidate = value
    else:
        try:
            candidate = datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise BackupManifestError(code) from exc
    if candidate.tzinfo is None:
        raise BackupManifestError(code)
    return candidate.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _datetime(value, code="invalidTimestamp").isoformat()


def _machine_value(value: str, *, code: str) -> str:
    normalized = str(value or "").strip()
    if MACHINE_VALUE_PATTERN.fullmatch(normalized) is None:
        raise BackupManifestError(code)
    return normalized


def _backup_id(value: str) -> str:
    normalized = str(value or "").strip()
    if BACKUP_ID_PATTERN.fullmatch(normalized) is None:
        raise BackupManifestError("invalidBackupId")
    return normalized


def _schema_head(value: str) -> str:
    normalized = str(value or "").strip()
    if SCHEMA_HEAD_PATTERN.fullmatch(normalized) is None:
        raise BackupManifestError("invalidSchemaHead")
    return normalized


def _lsn(value: str) -> str:
    raw = str(value or "").strip()
    normalized = "unknown" if raw.lower() == "unknown" else raw.upper()
    if LSN_PATTERN.fullmatch(normalized) is None:
        raise BackupManifestError("invalidLSN")
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_completed_manifest(
    *,
    backup_id: str,
    created_at: datetime,
    completed_at: datetime,
    schema_head: str,
    lsn: str,
    artifact_path: Path,
    encryption_ref: str,
    retention_class: str,
    retention_days: int,
) -> Dict[str, Any]:
    artifact = Path(artifact_path)
    if not artifact.is_file() or artifact.name != str(artifact.name):
        raise BackupManifestError("artifactMissing")
    created = _datetime(created_at, code="invalidCreatedAt")
    completed = _datetime(completed_at, code="invalidCompletedAt")
    if completed < created:
        raise BackupManifestError("invalidCompletedAt")
    days = int(retention_days)
    if days < 1 or days > 3650:
        raise BackupManifestError("invalidRetentionDays")
    return {
        "schemaVersion": 1,
        "backupId": _backup_id(backup_id),
        "createdAt": _iso(created),
        "completedAt": _iso(completed),
        "schemaHead": _schema_head(schema_head),
        "lsn": _lsn(lsn),
        "checksum": _sha256(artifact),
        "size": artifact.stat().st_size,
        "encryptionRef": _machine_value(
            encryption_ref,
            code="invalidEncryptionRef",
        ),
        "retentionClass": _machine_value(
            retention_class,
            code="invalidRetentionClass",
        ),
        "retentionDays": days,
        "expiresAt": _iso(created + timedelta(days=days)),
        "status": "verified",
        "format": "pgCustom",
        "artifactFile": artifact.name,
    }


def build_failed_manifest(
    *,
    backup_id: str,
    created_at: datetime,
    schema_head: str,
    lsn: str,
    encryption_ref: str,
    retention_class: str,
    error_code: str,
    owner: str,
) -> Dict[str, Any]:
    created = _datetime(created_at, code="invalidCreatedAt")
    return {
        "schemaVersion": 1,
        "backupId": _backup_id(backup_id),
        "createdAt": _iso(created),
        "completedAt": None,
        "schemaHead": _schema_head(schema_head),
        "lsn": _lsn(lsn),
        "checksum": None,
        "size": 0,
        "encryptionRef": _machine_value(
            encryption_ref,
            code="invalidEncryptionRef",
        ),
        "retentionClass": _machine_value(
            retention_class,
            code="invalidRetentionClass",
        ),
        "retentionDays": None,
        "expiresAt": None,
        "status": "failed",
        "format": "pgCustom",
        "artifactFile": None,
        "errorCode": _machine_value(error_code, code="invalidErrorCode"),
        "owner": _machine_value(owner, code="invalidOwner"),
    }


def write_manifest_atomic(path: Path, manifest: Dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    try:
        with partial.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        partial.chmod(0o600)
        os.replace(partial, destination)
        destination.chmod(0o600)
    finally:
        if partial.exists():
            partial.unlink()


def _load_manifest(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupManifestError("manifestUnreadable") from exc
    if not isinstance(payload, dict):
        raise BackupManifestError("manifestInvalid")
    return payload


def _validate_common(manifest: Dict[str, Any]) -> None:
    if manifest.get("schemaVersion") != 1:
        raise BackupManifestError("manifestSchemaUnsupported")
    _backup_id(str(manifest.get("backupId") or ""))
    _datetime(manifest.get("createdAt"), code="invalidCreatedAt")
    _schema_head(str(manifest.get("schemaHead") or ""))
    _lsn(str(manifest.get("lsn") or ""))
    _machine_value(str(manifest.get("encryptionRef") or ""), code="invalidEncryptionRef")
    _machine_value(str(manifest.get("retentionClass") or ""), code="invalidRetentionClass")


def _verify_artifact(manifest_path: Path, manifest: Dict[str, Any]) -> Path:
    artifact_name = str(manifest.get("artifactFile") or "")
    if not artifact_name or Path(artifact_name).name != artifact_name:
        raise BackupManifestError("artifactReferenceInvalid")
    artifact = Path(manifest_path).parent / artifact_name
    if not artifact.is_file():
        raise BackupManifestError("artifactMissing")
    if int(manifest.get("size") or 0) != artifact.stat().st_size:
        raise BackupManifestError("artifactSizeMismatch")
    checksum = str(manifest.get("checksum") or "")
    if SHA256_PATTERN.fullmatch(checksum) is None:
        raise BackupManifestError("manifestChecksumInvalid")
    if _sha256(artifact) != checksum:
        raise BackupManifestError("artifactChecksumMismatch")
    return artifact


def verify_backup_manifest(
    manifest_path: Path,
    *,
    expected_schema_head: Optional[str] = None,
    now: Optional[datetime] = None,
    max_age: Optional[timedelta] = None,
) -> Dict[str, Any]:
    path = Path(manifest_path)
    manifest = _load_manifest(path)
    _validate_common(manifest)
    if manifest.get("status") != "verified":
        raise BackupManifestError("backupNotVerified")
    _datetime(manifest.get("completedAt"), code="invalidCompletedAt")
    expires_at = _datetime(manifest.get("expiresAt"), code="invalidExpiresAt")
    if expected_schema_head is not None and manifest.get("schemaHead") != _schema_head(
        expected_schema_head
    ):
        raise BackupManifestError("schemaHeadMismatch")
    current = _datetime(now or datetime.now(timezone.utc), code="invalidNow")
    created_at = _datetime(manifest.get("createdAt"), code="invalidCreatedAt")
    if max_age is not None and current - created_at > max_age:
        raise BackupManifestError("backupStale")
    if expires_at <= current:
        raise BackupManifestError("backupExpired")
    artifact = _verify_artifact(path, manifest)
    return {
        "schemaVersion": 1,
        "status": "verified",
        "backupId": manifest["backupId"],
        "schemaHead": manifest["schemaHead"],
        "checksum": manifest["checksum"],
        "size": artifact.stat().st_size,
        "expiresAt": manifest["expiresAt"],
    }


def plan_backup_retention(
    manifest_paths: Iterable[Path],
    *,
    now: Optional[datetime] = None,
    keep_minimum: int = 1,
) -> Dict[str, Any]:
    current = _datetime(now or datetime.now(timezone.utc), code="invalidNow")
    minimum = max(1, int(keep_minimum))
    valid: List[Dict[str, Any]] = []
    invalid_count = 0
    for path in manifest_paths:
        candidate_path = Path(path)
        try:
            manifest = _load_manifest(candidate_path)
            _validate_common(manifest)
            if manifest.get("status") != "verified":
                continue
            _verify_artifact(candidate_path, manifest)
            manifest = dict(manifest)
            manifest["_created"] = _datetime(
                manifest.get("createdAt"),
                code="invalidCreatedAt",
            )
            manifest["_expires"] = _datetime(
                manifest.get("expiresAt"),
                code="invalidExpiresAt",
            )
            valid.append(manifest)
        except BackupManifestError:
            invalid_count += 1
    valid.sort(key=lambda item: item["_created"], reverse=True)
    protected = valid[:minimum]
    protected_ids = {str(item["backupId"]) for item in protected}
    eligible = [
        str(item["backupId"])
        for item in valid
        if str(item["backupId"]) not in protected_ids and item["_expires"] <= current
    ]
    return {
        "schemaVersion": 1,
        "action": "auditOnly",
        "evaluatedAt": _iso(current),
        "validBackupCount": len(valid),
        "invalidManifestCount": invalid_count,
        "eligibleBackupIds": sorted(eligible),
        "protectedBackupIds": sorted(protected_ids),
        "automaticDeletion": False,
    }
