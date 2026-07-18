"""Owner-scoped export and terminal-cleanup contracts for data-rights work.

The account lifecycle already removes a substantial amount of application data.
This module makes that work observable without treating object storage, provider
records, or backup retention as if they were deleted by the application.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


DATA_RIGHTS_EXPORT_SCHEMA_VERSION = 1
DATA_RIGHTS_CLEANUP_SCHEMA_VERSION = 1

_REDACTED_VALUE = "[redacted]"
_SENSITIVE_KEY_MARKERS = (
    "access_token",
    "accesstoken",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "private_key",
    "privatekey",
    "refresh_token",
    "refreshtoken",
    "secret",
    "signature",
    "token",
)


def build_module_owned_data_export(
    store: Any,
    *,
    user_id: str,
    generated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a bounded, owner-scoped export without credentials or media bytes.

    This is deliberately a synchronous *application-data* export.  It does not
    imply that provider-held voice assets, object bytes, or retained backups are
    included.  Those boundaries remain explicit in ``externalBoundaries``.
    """

    subject_id = _required_text(user_id, "user_id")
    account = _call_required(store, "get_user", subject_id)
    if account is None:
        raise LookupError("account was not found")

    module_records = [
        _module_record(
            "account",
            "accountProfile",
            "users",
            [_export_account(account)],
        ),
        _module_record(
            "profile",
            "profile",
            "profiles",
            _as_items(_call_optional(store, "get_profile", subject_id)),
        ),
        _module_record(
            "knowledge",
            "knowledgeSnapshot",
            "kb_snapshots",
            _as_items(_call_optional(store, "get_kb_snapshot_record", subject_id)),
        ),
        _module_record(
            "knowledge",
            "knowledgeChange",
            "kb_changes",
            _as_list(_call_optional(store, "list_kb_changes", subject_id, 0, limit=1000)),
        ),
        _module_record("memory", "memory", "memories", _as_list(_call_optional(store, "list_memories", subject_id))),
        _module_record("archive", "archiveMetadata", "archive_items", _as_list(_call_optional(store, "list_archive_items", subject_id))),
        _module_record("mailbox", "inAppMessage", "mailbox_letters", _as_list(_call_optional(store, "list_mailbox_letters", subject_id))),
        _module_record("echo", "delayedReply", "echo_delayed_replies", _as_list(_call_optional(store, "list_echo_delayed_replies", subject_id))),
        _module_record("voice", "voiceProfile", "voice_profiles", _as_list(_call_optional(store, "list_voice_profiles", subject_id))),
        _module_record("family", "familyMember", "family_members", _as_list(_call_optional(store, "list_family_members", subject_id))),
        _module_record(
            "family",
            "ownerRelationship",
            "family_relationships",
            _as_list(_call_optional(store, "list_family_relationships", subject_id)),
            status="partial",
            reason_code="ownerScopedRelationshipProjection",
        ),
        _module_record(
            "care",
            "careSnapshot",
            "care_snapshots",
            _as_list(_call_optional(store, "list_care_snapshots", subject_id, limit=1000)),
        ),
    ]
    status_summary = _status_summary(module_records)
    external_boundaries = [
        {
            "moduleId": "objectStorage",
            "resourceType": "mediaObjectBytes",
            "status": "unsupported",
            "reasonCode": "applicationExportContainsMetadataOnly",
        },
        {
            "moduleId": "providerVoice",
            "resourceType": "voiceCloneTrainingAsset",
            "status": "unsupported",
            "reasonCode": "providerExportAdapterNotConfigured",
        },
        {
            "moduleId": "providerDigitalHuman",
            "resourceType": "digitalHumanProviderRecord",
            "status": "unsupported",
            "reasonCode": "providerExportAdapterNotConfigured",
        },
        {
            "moduleId": "security",
            "resourceType": "credentialsAndSessions",
            "status": "unsupported",
            "reasonCode": "credentialMaterialIsNeverExportable",
        },
        {
            "moduleId": "backupRetention",
            "resourceType": "backupCopy",
            "status": "pending",
            "reasonCode": "backupRetentionHasExternalOperatorBoundary",
        },
    ]
    export_time = generated_at or datetime.now(timezone.utc).isoformat()
    module_summaries = [
        {
            "moduleId": record["moduleId"],
            "resourceType": record["resourceType"],
            "itemCount": record["itemCount"],
            "status": record["status"],
            **(
                {"reasonCode": record["reasonCode"]}
                if record.get("reasonCode")
                else {}
            ),
        }
        for record in module_records
    ]
    return {
        "schemaVersion": DATA_RIGHTS_EXPORT_SCHEMA_VERSION,
        "status": "ready",
        "generatedAt": export_time,
        "ownerUserId": subject_id,
        "humanReadable": {
            "title": "寻梦环游个人数据副本",
            "summary": "此副本包含本应用可直接导出的个人数据与元数据，不包含凭据、会话令牌、媒体二进制文件或第三方服务留存的数据。",
            "moduleSummaries": module_summaries,
        },
        "machineReadable": {
            "objects": module_records,
            "statusSummary": status_summary,
            "sourceManifest": [
                {
                    "moduleId": record["moduleId"],
                    "resourceType": record["resourceType"],
                    "source": record["source"],
                    "status": record["status"],
                    **(
                        {"reasonCode": record["reasonCode"]}
                        if record.get("reasonCode")
                        else {}
                    ),
                }
                for record in module_records
            ],
        },
        "externalBoundaries": external_boundaries,
    }


def build_terminal_cleanup_plan(
    *,
    request_id: str,
    terminal_purge_receipt_id: str,
    updated_at: str,
    resource_counts: Mapping[str, int],
) -> List[Dict[str, Any]]:
    """Return append-safe module execution plans for a terminal account purge.

    ``completed`` means the application deleted the listed local database
    records in its own transaction. ``pending`` remains intentionally open for
    external provider/retention effects. ``unsupported`` is a terminal report
    for an unconfigured adapter, not a successful deletion.
    """

    request = _required_text(request_id, "request_id")
    terminal_receipt = _required_text(terminal_purge_receipt_id, "terminal_purge_receipt_id")
    timestamp = _required_text(updated_at, "updated_at")
    normalized_counts = {
        str(name): max(0, int(value or 0))
        for name, value in resource_counts.items()
    }
    plans: List[Dict[str, Any]] = []
    for module_id, resource_type, count_keys, outcome in _LOCAL_CLEANUP_MODULES:
        count = sum(normalized_counts.get(key, 0) for key in count_keys)
        effective_outcome = outcome
        if module_id == "voice" and normalized_counts.get("voiceCloneSlotRetired", 0) > 0:
            effective_outcome = "partial"
        plans.append(
            _cleanup_plan(
                request_id=request,
                terminal_purge_receipt_id=terminal_receipt,
                module_id=module_id,
                resource_type=resource_type,
                resource_count=count,
                outcome=effective_outcome,
                updated_at=timestamp,
            )
        )
    for module_id, resource_type, outcome in _EXTERNAL_CLEANUP_MODULES:
        plans.append(
            _cleanup_plan(
                request_id=request,
                terminal_purge_receipt_id=terminal_receipt,
                module_id=module_id,
                resource_type=resource_type,
                resource_count=0,
                outcome=outcome,
                updated_at=timestamp,
            )
        )
    return plans


def record_terminal_cleanup_plan(
    store: Any,
    *,
    request_id: str,
    terminal_purge_receipt_id: str,
    updated_at: str,
    resource_counts: Mapping[str, int],
    retention_until: Optional[str],
) -> List[Dict[str, Any]]:
    """Persist current cleanup execution state and terminal receipts where valid.

    Accounts created by older routes may not have a corresponding rights
    request.  Those purges retain the terminal account receipt but cannot gain
    a retroactive module receipt, so this deliberately becomes a no-op.
    """

    if not str(request_id or "").strip() or store.get_rights_request(request_id) is None:
        return []

    recorded: List[Dict[str, Any]] = []
    for plan in build_terminal_cleanup_plan(
        request_id=request_id,
        terminal_purge_receipt_id=terminal_purge_receipt_id,
        updated_at=updated_at,
        resource_counts=resource_counts,
    ):
        execution = store.record_rights_execution(
            request_id,
            module_id=plan["moduleId"],
            resource_type=plan["resourceType"],
            execution_id_hash=plan["executionIdHash"],
            outcome=plan["outcome"],
            evidence_id_hash=plan["evidenceIdHash"],
            updated_at=updated_at,
        )
        receipt = None
        if plan["outcome"] != "pending":
            receipt = store.append_resource_deletion_receipt(
                receipt_id=plan["receiptId"],
                request_id=request_id,
                execution_id_hash=plan["executionIdHash"],
                module_id=plan["moduleId"],
                resource_scope_hash=plan["resourceScopeHash"],
                outcome=plan["outcome"],
                receipt_hash=plan["receiptHash"],
                evidence_event_id_hash=plan["evidenceIdHash"],
                created_at=updated_at,
                retention_until=retention_until,
            )
        recorded.append(
            {
                "moduleId": plan["moduleId"],
                "resourceType": plan["resourceType"],
                "outcome": plan["outcome"],
                "executionOutcome": execution["outcome"],
                "receiptOutcome": None if receipt is None else receipt["outcome"],
            }
        )
    return recorded


_LOCAL_CLEANUP_MODULES = (
    ("profile", "profileAndCredential", ("profile", "passwordCredential"), "completed"),
    ("knowledge", "knowledgeState", ("knowledgeSnapshot", "knowledgeChange", "knowledgeReceipt", "knowledgeFeedState"), "completed"),
    ("memory", "memory", ("memory",), "completed"),
    ("archive", "archiveMetadata", ("archive",), "completed"),
    ("mailbox", "inAppMessage", ("mailbox",), "completed"),
    ("family", "familyRelationship", ("familyMember", "familyRelationship", "accessGrant", "grantEvent"), "completed"),
    ("care", "careSnapshot", ("care",), "completed"),
    ("echo", "delayedReply", ("echo",), "completed"),
    ("notification", "pushTokenRegistration", ("pushToken",), "completed"),
    ("voice", "voiceProfile", ("voiceProfile", "voiceCloneSlotRetired"), "completed"),
    ("digitalHuman", "sessionLease", ("digitalHumanSession",), "completed"),
    ("auth", "sessionFamily", ("authSession", "authTokenFamily", "authSessionEvent"), "completed"),
)

_EXTERNAL_CLEANUP_MODULES = (
    ("objectStorage", "mediaObjectBytes", "unsupported"),
    ("providerVoice", "voiceCloneAsset", "pending"),
    ("providerDigitalHuman", "providerSessionAndAsset", "pending"),
    ("backupRetention", "backupCopy", "pending"),
    ("evidenceRetention", "immutableEvidence", "pending"),
)


def _module_record(
    module_id: str,
    resource_type: str,
    source: str,
    items: Sequence[Any],
    *,
    status: str = "completed",
    reason_code: Optional[str] = None,
) -> Dict[str, Any]:
    safe_items = [_redact_export_value(item) for item in items]
    record = {
        "moduleId": module_id,
        "resourceType": resource_type,
        "source": source,
        "status": status,
        "itemCount": len(safe_items),
        "items": safe_items,
    }
    if reason_code:
        record["reasonCode"] = reason_code
    return record


def _status_summary(records: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for record in records:
        status = str(record.get("status") or "unknown")
        summary[status] = summary.get(status, 0) + 1
    return dict(sorted(summary.items()))


def _cleanup_plan(
    *,
    request_id: str,
    terminal_purge_receipt_id: str,
    module_id: str,
    resource_type: str,
    resource_count: int,
    outcome: str,
    updated_at: str,
) -> Dict[str, Any]:
    scope = {
        "schemaVersion": DATA_RIGHTS_CLEANUP_SCHEMA_VERSION,
        "moduleId": module_id,
        "resourceType": resource_type,
        "resourceCount": max(0, int(resource_count)),
        "terminalPurgeReceiptIdHash": _sha256(terminal_purge_receipt_id),
    }
    scope_hash = _sha256(_canonical_json(scope))
    execution_id_hash = _sha256(
        f"rights-terminal-cleanup-v1|{request_id}|{module_id}|{resource_type}|{terminal_purge_receipt_id}"
    )
    receipt_id = "rrc_" + _sha256(
        f"rights-terminal-receipt-v1|{request_id}|{module_id}|{resource_type}"
    )[:40]
    evidence_id_hash = _sha256(f"terminal-purge-receipt|{terminal_purge_receipt_id}")
    receipt_hash = _sha256(
        _canonical_json(
            {
                "schemaVersion": DATA_RIGHTS_CLEANUP_SCHEMA_VERSION,
                "requestId": request_id,
                "moduleId": module_id,
                "resourceType": resource_type,
                "resourceScopeHash": scope_hash,
                "outcome": outcome,
                "updatedAt": updated_at,
                "evidenceIdHash": evidence_id_hash,
            }
        )
    )
    return {
        "moduleId": module_id,
        "resourceType": resource_type,
        "outcome": outcome,
        "executionIdHash": execution_id_hash,
        "receiptId": receipt_id,
        "resourceScopeHash": scope_hash,
        "evidenceIdHash": evidence_id_hash,
        "receiptHash": receipt_hash,
    }


def _export_account(account: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(account.get("id") or ""),
        "nickname": str(account.get("nickname") or ""),
        "phoneLast4": _phone_last_four(account.get("phone")),
        "deletionState": str(account.get("deletionState") or "active"),
        "updatedAt": account.get("updatedAt"),
    }


def _phone_last_four(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[-4:] if digits else ""


def _call_required(store: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    method = getattr(store, method_name, None)
    if not callable(method):
        raise RuntimeError(f"store does not support {method_name}")
    return method(*args, **kwargs)


def _call_optional(store: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    method = getattr(store, method_name, None)
    if not callable(method):
        return []
    return method(*args, **kwargs)


def _as_items(value: Any) -> List[Any]:
    return [] if value is None else [value]


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _redact_export_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if _is_sensitive_key(key):
        return _REDACTED_VALUE
    if depth >= 12:
        return "[truncated]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_export_value(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_export_value(item, depth=depth + 1) for item in value]
    return deepcopy(value)


def _is_sensitive_key(value: str) -> bool:
    normalized = "".join(character for character in str(value).lower() if character.isalnum() or character == "_")
    return any(marker.replace("_", "") in normalized.replace("_", "") for marker in _SENSITIVE_KEY_MARKERS)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_text(value: Any, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


__all__ = [
    "DATA_RIGHTS_CLEANUP_SCHEMA_VERSION",
    "DATA_RIGHTS_EXPORT_SCHEMA_VERSION",
    "build_module_owned_data_export",
    "build_terminal_cleanup_plan",
    "record_terminal_cleanup_plan",
]
