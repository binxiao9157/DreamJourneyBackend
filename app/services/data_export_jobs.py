"""Owner-scoped asynchronous data-export job contracts.

The job layer deliberately reuses ``build_module_owned_data_export`` as the
only data scanner. It adds lifecycle, idempotency and a machine-readable copy
manifest without claiming that provider-held media or backups were exported.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Mapping, Optional
from uuid import uuid4


DATA_EXPORT_JOB_SCHEMA_VERSION = 2
COPY_EXPORT_MANIFEST_SCHEMA_VERSION = 1
FULL_ACCOUNT_ARCHIVE_EXPORT_TYPE = "fullAccountArchive"
FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE = "formalMemoryMarkdown"
DATA_EXPORT_TYPES = frozenset(
    {FULL_ACCOUNT_ARCHIVE_EXPORT_TYPE, FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE}
)
DATA_EXPORT_JOB_STATES = frozenset(
    {"queued", "running", "ready", "partial", "failed", "cancelled", "expired"}
)
DATA_EXPORT_DOWNLOADABLE_STATES = frozenset({"ready", "partial"})
DEFAULT_DATA_EXPORT_TTL_SECONDS = 15 * 60
DEFAULT_DATA_EXPORT_DOWNLOAD_CREDENTIAL_TTL_SECONDS = 60


class DataExportJobError(ValueError):
    pass


class DataExportJobStateError(DataExportJobError):
    pass


def create_data_export_job_record(
    *,
    owner_user_id: Any,
    request_key: Any,
    now: Optional[Any] = None,
    expires_at: Optional[Any] = None,
    job_id: Optional[str] = None,
    export_type: Any = FULL_ACCOUNT_ARCHIVE_EXPORT_TYPE,
    scope_id: Any = "account",
) -> Dict[str, Any]:
    owner = _required_text(owner_user_id, field="owner_user_id", maximum=256)
    request = _required_text(request_key, field="request_key", maximum=128)
    created_at = _timestamp(now)
    expiry = _timestamp(
        expires_at
        or (
            datetime.fromisoformat(created_at)
            + timedelta(seconds=DEFAULT_DATA_EXPORT_TTL_SECONDS)
        )
    )
    if datetime.fromisoformat(expiry) <= datetime.fromisoformat(created_at):
        raise DataExportJobError("expires_at must be later than now")
    normalized_job_id = job_id or f"dej_{uuid4().hex}"
    if not normalized_job_id.startswith("dej_") or len(normalized_job_id) > 80:
        raise DataExportJobError("job_id is invalid")
    normalized_export_type = _required_text(
        export_type,
        field="export_type",
        maximum=64,
    )
    if normalized_export_type not in DATA_EXPORT_TYPES:
        raise DataExportJobError("export_type is unsupported")
    normalized_scope_id = _required_text(scope_id, field="scope_id", maximum=256)
    return {
        "id": normalized_job_id,
        "ownerUserId": owner,
        "exportType": normalized_export_type,
        "scopeId": normalized_scope_id,
        "requestKeyHash": _sha256(request),
        "status": "queued",
        "attempt": 0,
        "artifactHash": None,
        "artifact": None,
        "manifest": None,
        "failureCode": None,
        "createdAt": created_at,
        "updatedAt": created_at,
        "expiresAt": expiry,
        "readyAt": None,
        "contractVersion": DATA_EXPORT_JOB_SCHEMA_VERSION,
    }


def build_copy_export_manifest(
    export: Mapping[str, Any],
    *,
    job_id: str,
    generated_at: str,
    expires_at: str,
) -> Dict[str, Any]:
    if not isinstance(export, Mapping):
        raise DataExportJobError("export must be an object")
    objects = export.get("machineReadable", {}).get("objects", [])
    permission_manifest = export.get("machineReadable", {}).get("permissionManifest")
    boundaries = export.get("externalBoundaries", [])
    if (
        not isinstance(objects, list)
        or not isinstance(permission_manifest, Mapping)
        or not isinstance(boundaries, list)
    ):
        raise DataExportJobError("export inventory is malformed")

    module_summaries = []
    incomplete = False
    for item in objects:
        if not isinstance(item, Mapping):
            raise DataExportJobError("export object inventory is malformed")
        status = str(item.get("status") or "partial")
        incomplete = incomplete or status != "completed"
        module_summaries.append(
            {
                "moduleId": str(item.get("moduleId") or "unknown"),
                "resourceType": str(item.get("resourceType") or "unknown"),
                "itemCount": max(0, int(item.get("itemCount") or 0)),
                "status": status,
                **(
                    {"reasonCode": str(item.get("reasonCode"))}
                    if item.get("reasonCode")
                    else {}
                ),
            }
        )

    boundary_summaries = []
    for item in boundaries:
        if not isinstance(item, Mapping):
            raise DataExportJobError("export boundary inventory is malformed")
        uncompleted = bool(item.get("uncompleted"))
        status = str(item.get("status") or "pending")
        incomplete = incomplete or uncompleted or status != "completed"
        boundary_summaries.append(
            {
                "moduleId": str(item.get("moduleId") or "unknown"),
                "resourceType": str(item.get("resourceType") or "unknown"),
                "status": status,
                "retentionState": str(item.get("retentionState") or "unknown"),
                "uncompleted": uncompleted,
                **(
                    {"reasonCode": str(item.get("reasonCode"))}
                    if item.get("reasonCode")
                    else {}
                ),
            }
        )

    return {
        "schemaVersion": COPY_EXPORT_MANIFEST_SCHEMA_VERSION,
        "jobId": _required_text(job_id, field="job_id", maximum=80),
        "packageStatus": "partial" if incomplete else "ready",
        "exportSchemaVersion": int(export.get("schemaVersion") or 1),
        "generatedAt": _timestamp(generated_at),
        "expiresAt": _timestamp(expires_at),
        "dataHash": _hash_json(export),
        "permissionManifestHash": _hash_json(permission_manifest),
        "permissionResourceCount": len(permission_manifest.get("resources") or []),
        "moduleSummaries": module_summaries,
        "externalBoundaries": boundary_summaries,
    }


def materialize_data_export_job(
    store: Any,
    *,
    job_id: str,
    owner_user_id: str,
    export_builder: Callable[..., Dict[str, Any]],
    now: Optional[Any] = None,
) -> Dict[str, Any]:
    timestamp = _timestamp(now)
    claimed = store.claim_data_export_job(
        job_id,
        owner_user_id=owner_user_id,
        updated_at=timestamp,
    )
    job = claimed.get("job")
    if not isinstance(job, Mapping):
        raise DataExportJobStateError("data export job does not exist")
    if claimed.get("outcome") != "claimed":
        return dict(job)
    try:
        export = export_builder(store, user_id=owner_user_id, generated_at=timestamp)
        manifest = build_copy_export_manifest(
            export,
            job_id=job_id,
            generated_at=timestamp,
            expires_at=str(job["expiresAt"]),
        )
        artifact = {
            "schemaVersion": DATA_EXPORT_JOB_SCHEMA_VERSION,
            "manifest": manifest,
            "dataExport": export,
        }
        result = store.complete_data_export_job(
            job_id,
            owner_user_id=owner_user_id,
            status=manifest["packageStatus"],
            artifact_hash=_hash_json(artifact),
            artifact=artifact,
            manifest=manifest,
            ready_at=timestamp,
        )
        return result["job"]
    except Exception:
        store.fail_data_export_job(
            job_id,
            owner_user_id=owner_user_id,
            failure_code="exportMaterializationFailed",
            updated_at=timestamp,
        )
        raise


def public_data_export_job(job: Mapping[str, Any]) -> Dict[str, Any]:
    status = str(job.get("status") or "")
    if status not in DATA_EXPORT_JOB_STATES:
        raise DataExportJobError("job status is invalid")
    manifest = job.get("manifest")
    return {
        "schemaVersion": DATA_EXPORT_JOB_SCHEMA_VERSION,
        "jobId": str(job.get("id") or ""),
        "exportType": str(job.get("exportType") or FULL_ACCOUNT_ARCHIVE_EXPORT_TYPE),
        "scopeId": str(job.get("scopeId") or "account"),
        "status": status,
        "attempt": max(0, int(job.get("attempt") or 0)),
        "failureCode": job.get("failureCode"),
        "createdAt": str(job.get("createdAt") or ""),
        "updatedAt": str(job.get("updatedAt") or ""),
        "expiresAt": str(job.get("expiresAt") or ""),
        "readyAt": job.get("readyAt"),
        "downloadAvailable": status in DATA_EXPORT_DOWNLOADABLE_STATES,
        "manifest": dict(manifest) if isinstance(manifest, Mapping) else None,
    }


def create_data_export_download_credential(
    *,
    job_id: Any,
    owner_user_id: Any,
    job_expires_at: Any,
    now: Optional[Any] = None,
    ttl_seconds: int = DEFAULT_DATA_EXPORT_DOWNLOAD_CREDENTIAL_TTL_SECONDS,
) -> Dict[str, Any]:
    normalized_job_id = _required_text(job_id, field="job_id", maximum=80)
    if not normalized_job_id.startswith("dej_"):
        raise DataExportJobError("job_id is invalid")
    owner = _required_text(owner_user_id, field="owner_user_id", maximum=256)
    issued_at = _timestamp(now)
    issued = datetime.fromisoformat(issued_at)
    job_expiry = datetime.fromisoformat(_timestamp(job_expires_at))
    bounded_ttl = max(15, min(int(ttl_seconds), 5 * 60))
    expires = min(job_expiry, issued + timedelta(seconds=bounded_ttl))
    if expires <= issued:
        raise DataExportJobStateError("data export job is expired")
    token = f"dec_{secrets.token_urlsafe(32)}"
    return {
        "jobId": normalized_job_id,
        "ownerUserId": owner,
        "token": token,
        "tokenHash": _sha256(token),
        "issuedAt": issued_at,
        "expiresAt": expires.astimezone(timezone.utc).isoformat(),
    }


def public_data_export_download_credential(credential: Mapping[str, Any]) -> Dict[str, Any]:
    token = _required_text(credential.get("token"), field="token", maximum=128)
    if not token.startswith("dec_"):
        raise DataExportJobError("download credential token is invalid")
    return {
        "schemaVersion": 1,
        "jobId": _required_text(credential.get("jobId"), field="job_id", maximum=80),
        "downloadToken": token,
        "expiresAt": _timestamp(credential.get("expiresAt")),
    }


def is_data_export_job_expired(job: Mapping[str, Any], *, now: Optional[Any] = None) -> bool:
    current = datetime.fromisoformat(_timestamp(now))
    expiry = datetime.fromisoformat(_timestamp(job.get("expiresAt")))
    return current >= expiry


def _required_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DataExportJobError(f"{field} is required")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise DataExportJobError(f"{field} is too long")
    return normalized


def _timestamp(value: Optional[Any]) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise DataExportJobError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataExportJobError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _hash_json(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DataExportJobError("export artifact must be JSON serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "COPY_EXPORT_MANIFEST_SCHEMA_VERSION",
    "DEFAULT_DATA_EXPORT_DOWNLOAD_CREDENTIAL_TTL_SECONDS",
    "DATA_EXPORT_DOWNLOADABLE_STATES",
    "DATA_EXPORT_TYPES",
    "DATA_EXPORT_JOB_SCHEMA_VERSION",
    "DATA_EXPORT_JOB_STATES",
    "DataExportJobError",
    "DataExportJobStateError",
    "FORMAL_MEMORY_MARKDOWN_EXPORT_TYPE",
    "FULL_ACCOUNT_ARCHIVE_EXPORT_TYPE",
    "build_copy_export_manifest",
    "create_data_export_download_credential",
    "create_data_export_job_record",
    "is_data_export_job_expired",
    "materialize_data_export_job",
    "public_data_export_download_credential",
    "public_data_export_job",
]
