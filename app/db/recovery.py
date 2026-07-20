from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


BACKUP_ID_PATTERN = re.compile(r"^dj-\d{8}T\d{6}Z-[a-z0-9]{8,32}$")
RECOVERY_ID_PATTERN = re.compile(r"^recovery-\d{8}T\d{6}Z-[a-z0-9]{8,32}$")
RECOVERY_TARGET_PATTERN = re.compile(r"^dj_recovery_[a-z0-9_]{4,48}$")
LSN_PATTERN = re.compile(r"^(?:[0-9A-F]+/[0-9A-F]+|unknown)$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MACHINE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
RECEIPT_KINDS = ("command", "outbox", "deletion", "provider")
TERMINAL_RECEIPT_STATUSES = ("applied", "verified", "terminal")
LEGACY_INTEGRITY_SCHEMA_VERSION = 1
DIRECT_USER_ID_AUDIT_SCHEMA_VERSION = 2
INTEGRITY_SCHEMA_VERSION = 3
INTEGRITY_AUDIT_DOMAIN_NAMES = (
    "publicDirectUserId",
    "ownerTruthVaultScope",
    "asyncEffectsOperationScope",
)


class RecoveryContractError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _timestamp(value: Any, *, code: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise RecoveryContractError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecoveryContractError(code)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _machine_value(value: Any, *, code: str) -> str:
    normalized = str(value or "").strip()
    if MACHINE_VALUE_PATTERN.fullmatch(normalized) is None:
        raise RecoveryContractError(code)
    return normalized


def _backup_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if BACKUP_ID_PATTERN.fullmatch(normalized) is None:
        raise RecoveryContractError("invalidBackupId")
    return normalized


def _recovery_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if RECOVERY_ID_PATTERN.fullmatch(normalized) is None:
        raise RecoveryContractError("invalidRecoveryId")
    return normalized


def _lsn(value: Any, *, code: str) -> str:
    raw = str(value or "").strip()
    normalized = "unknown" if raw.lower() == "unknown" else raw.upper()
    if LSN_PATTERN.fullmatch(normalized) is None:
        raise RecoveryContractError(code)
    return normalized


def _known_lsn(value: Any, *, code: str, unknown_code: str) -> str:
    normalized = _lsn(value, code=code)
    if normalized == "unknown":
        raise RecoveryContractError(unknown_code)
    return normalized


def _lsn_order(value: str) -> int:
    high, low = value.split("/", 1)
    return (int(high, 16) << 32) + int(low, 16)


def _sha256(value: Any, *, code: str) -> str:
    normalized = str(value or "").strip().lower()
    if SHA256_PATTERN.fullmatch(normalized) is None:
        raise RecoveryContractError(code)
    return normalized


def _require_digest(payload: Mapping[str, Any], *, field: str, code: str) -> str:
    supplied = _sha256(payload.get(field), code=code)
    expected = _canonical_hash({key: value for key, value in payload.items() if key != field})
    if supplied != expected:
        raise RecoveryContractError(code)
    return supplied


def validate_recovery_target(target_database: str, production_database: str) -> str:
    target = str(target_database or "").strip().lower()
    production = str(production_database or "").strip().lower()
    forbidden = {"postgres", "template0", "template1", production}
    if RECOVERY_TARGET_PATTERN.fullmatch(target) is None or target in forbidden:
        raise RecoveryContractError("unsafeRecoveryTarget")
    return target


def build_replay_plan(
    bundle: Optional[Mapping[str, Any]],
    *,
    backup_id: str,
    cutoff_lsn: str,
) -> Dict[str, Any]:
    expected_backup_id = _backup_id(backup_id)
    expected_cutoff = _known_lsn(
        cutoff_lsn,
        code="invalidCutoffLSN",
        unknown_code="unknownCutoffLSN",
    )
    if bundle is None:
        payload = {
            "schemaVersion": 1,
            "backupId": expected_backup_id,
            "cutoffLSN": expected_cutoff,
            "status": "incomplete",
            "rangeStartExclusive": expected_cutoff,
            "rangeEndInclusive": None,
            "sourceEvidenceId": None,
            "sourceEvidencePresent": False,
            "coverage": {kind: False for kind in RECEIPT_KINDS},
            "receiptCounts": {kind: 0 for kind in RECEIPT_KINDS},
            "uniqueReceiptCount": 0,
            "duplicateReceiptCount": 0,
            "receiptSetDigest": _canonical_hash([]),
            "deletionReplayStatus": "pending",
            "blockers": ["replayBundleMissing"],
        }
        payload["replayDigest"] = _canonical_hash(payload)
        return payload

    if int(bundle.get("schemaVersion") or 0) != 1:
        raise RecoveryContractError("replaySchemaUnsupported")
    if _backup_id(bundle.get("backupId")) != expected_backup_id:
        raise RecoveryContractError("replayBackupMismatch")
    if _known_lsn(
        bundle.get("cutoffLSN"),
        code="invalidReplayCutoffLSN",
        unknown_code="unknownReplayCutoffLSN",
    ) != expected_cutoff:
        raise RecoveryContractError("replayCutoffMismatch")
    range_end = _known_lsn(
        bundle.get("rangeEndLSN"),
        code="invalidReplayRangeEndLSN",
        unknown_code="unknownReplayRangeEndLSN",
    )
    if _lsn_order(range_end) < _lsn_order(expected_cutoff):
        raise RecoveryContractError("invalidReplayRange")
    source_evidence_id = _sha256(
        bundle.get("sourceEvidenceId"),
        code="invalidReplaySourceEvidenceId",
    )

    raw_coverage = bundle.get("coverage")
    if not isinstance(raw_coverage, Mapping):
        raise RecoveryContractError("invalidReplayCoverage")
    coverage = {
        "command": raw_coverage.get("commandReceipts") is True,
        "outbox": raw_coverage.get("outboxReceipts") is True,
        "deletion": raw_coverage.get("deletionReceipts") is True,
        "provider": raw_coverage.get("providerReceipts") is True,
    }
    blockers = [f"{kind}CoverageMissing" for kind, complete in coverage.items() if not complete]

    raw_receipts = bundle.get("receipts")
    if not isinstance(raw_receipts, list):
        raise RecoveryContractError("invalidReplayReceipts")
    unique: Dict[str, Dict[str, str]] = {}
    counts = {kind: 0 for kind in RECEIPT_KINDS}
    duplicate_count = 0
    deletion_terminal = True
    for raw_receipt in raw_receipts:
        if not isinstance(raw_receipt, Mapping):
            raise RecoveryContractError("invalidReplayReceipt")
        receipt_id = _machine_value(raw_receipt.get("receiptId"), code="invalidReceiptId")
        kind = _machine_value(raw_receipt.get("kind"), code="invalidReceiptKind")
        if kind not in RECEIPT_KINDS:
            raise RecoveryContractError("invalidReceiptKind")
        receipt_lsn = _known_lsn(
            raw_receipt.get("lsn"),
            code="invalidReceiptLSN",
            unknown_code="unknownReceiptLSN",
        )
        if not (
            _lsn_order(expected_cutoff)
            < _lsn_order(receipt_lsn)
            <= _lsn_order(range_end)
        ):
            raise RecoveryContractError("receiptOutsideReplayRange")
        owner_hash = _sha256(raw_receipt.get("ownerIdHash"), code="invalidOwnerIdHash")
        payload_hash = _sha256(raw_receipt.get("payloadHash"), code="invalidReceiptPayloadHash")
        status = _machine_value(raw_receipt.get("status"), code="invalidReceiptStatus")
        normalized = {
            "kind": kind,
            "lsn": receipt_lsn,
            "ownerIdHash": owner_hash,
            "payloadHash": payload_hash,
            "status": status,
        }
        previous = unique.get(receipt_id)
        if previous is not None:
            if previous != normalized:
                raise RecoveryContractError("receiptConflict")
            duplicate_count += 1
            continue
        unique[receipt_id] = normalized
        counts[kind] += 1
        if status not in TERMINAL_RECEIPT_STATUSES:
            blocker = "providerReceiptUnknown" if kind == "provider" and status == "unknown" else f"{kind}ReceiptNotApplied"
            if blocker not in blockers:
                blockers.append(blocker)
        if kind == "deletion" and status not in TERMINAL_RECEIPT_STATUSES:
            deletion_terminal = False

    if coverage["deletion"] and not deletion_terminal:
        blocker = "deletionReceiptNotApplied"
        if blocker not in blockers:
            blockers.append(blocker)
    receipt_set = [
        {"receiptId": receipt_id, **unique[receipt_id]}
        for receipt_id in sorted(unique)
    ]
    payload = {
        "schemaVersion": 1,
        "backupId": expected_backup_id,
        "cutoffLSN": expected_cutoff,
        "status": "ready" if not blockers else "incomplete",
        "rangeStartExclusive": expected_cutoff,
        "rangeEndInclusive": range_end,
        "sourceEvidenceId": source_evidence_id,
        "sourceEvidencePresent": True,
        "coverage": coverage,
        "receiptCounts": counts,
        "uniqueReceiptCount": len(unique),
        "duplicateReceiptCount": duplicate_count,
        "receiptSetDigest": _canonical_hash(receipt_set),
        "deletionReplayStatus": "pending",
        "blockers": sorted(blockers),
    }
    payload["replayDigest"] = _canonical_hash(payload)
    return payload


def finalize_replay_plan(
    plan: Mapping[str, Any],
    *,
    application_evidence: Mapping[str, Any],
) -> Dict[str, Any]:
    if int(plan.get("schemaVersion") or 0) != 1:
        raise RecoveryContractError("replaySchemaUnsupported")
    _require_digest(plan, field="replayDigest", code="invalidReplayDigest")
    if plan.get("status") != "ready" or plan.get("blockers"):
        raise RecoveryContractError("replayPlanNotReady")
    if not isinstance(application_evidence, Mapping):
        raise RecoveryContractError("invalidReplayApplicationEvidence")
    if int(application_evidence.get("schemaVersion") or 0) != 1:
        raise RecoveryContractError("replayApplicationSchemaUnsupported")
    if application_evidence.get("status") != "applied":
        raise RecoveryContractError("replayApplicationNotApplied")
    binding_fields = {
        "backupId": plan.get("backupId"),
        "cutoffLSN": plan.get("cutoffLSN"),
        "rangeEndLSN": plan.get("rangeEndInclusive"),
        "sourceEvidenceId": plan.get("sourceEvidenceId"),
        "planDigest": plan.get("replayDigest"),
    }
    if any(application_evidence.get(key) != value for key, value in binding_fields.items()):
        raise RecoveryContractError("replayApplicationPlanMismatch")
    application_evidence_id = _sha256(
        application_evidence.get("applicationEvidenceId"),
        code="invalidReplayApplicationEvidenceId",
    )
    applied_receipt_counts = application_evidence.get("appliedReceiptCounts")
    if not isinstance(applied_receipt_counts, Mapping):
        raise RecoveryContractError("invalidReplayApplicationCounts")
    expected_counts = plan.get("receiptCounts")
    if not isinstance(expected_counts, Mapping):
        raise RecoveryContractError("invalidReplayReceiptCounts")
    applied_counts: Dict[str, int] = {}
    for kind in RECEIPT_KINDS:
        expected = int(expected_counts.get(kind) or 0)
        applied = int(applied_receipt_counts.get(kind) or 0)
        if applied < 0 or applied != expected:
            raise RecoveryContractError("replayApplicationCountMismatch")
        applied_counts[kind] = applied
    payload = dict(plan)
    payload["status"] = "complete"
    payload["appliedReceiptCounts"] = applied_counts
    payload["applicationEvidencePresent"] = True
    payload["applicationEvidenceId"] = application_evidence_id
    payload["deletionReplayStatus"] = "verified"
    payload["replayDigest"] = _canonical_hash(
        {key: value for key, value in payload.items() if key != "replayDigest"}
    )
    return payload


def build_restore_evidence(
    *,
    backup_id: str,
    backup_checksum: str,
    backup_completed_at: Any,
    schema_head: str,
    cutoff_lsn: str,
    started_at: Any,
    completed_at: Any,
    target_database: str,
    production_database: str,
    source_manifest_digest: str,
    migration_evidence_id: str,
) -> Dict[str, Any]:
    normalized_backup_id = _backup_id(backup_id)
    normalized_checksum = _sha256(backup_checksum, code="invalidBackupChecksum")
    normalized_schema_head = _machine_value(schema_head, code="invalidSchemaHead")
    normalized_cutoff = _known_lsn(
        cutoff_lsn,
        code="invalidCutoffLSN",
        unknown_code="unknownCutoffLSN",
    )
    backup_completed = _timestamp(backup_completed_at, code="invalidBackupCompletedAt")
    started = _timestamp(started_at, code="invalidRecoveryStartedAt")
    completed = _timestamp(completed_at, code="invalidRecoveryCompletedAt")
    if completed < started or started < backup_completed:
        raise RecoveryContractError("invalidRecoveryTimeline")
    target = validate_recovery_target(target_database, production_database)
    payload = {
        "schemaVersion": 1,
        "status": "restored",
        "backupId": normalized_backup_id,
        "backupChecksum": normalized_checksum,
        "backupCompletedAt": _iso(backup_completed),
        "schemaHead": normalized_schema_head,
        "cutoffLSN": normalized_cutoff,
        "startedAt": _iso(started),
        "completedAt": _iso(completed),
        "targetIsolation": "ephemeralDatabase",
        "targetDatabaseHash": hashlib.sha256(target.encode("utf-8")).hexdigest(),
        "sourceManifestDigest": _sha256(
            source_manifest_digest,
            code="invalidSourceManifestDigest",
        ),
        "migrationEvidenceId": _sha256(
            migration_evidence_id,
            code="invalidMigrationEvidenceId",
        ),
    }
    payload["evidenceId"] = _canonical_hash(payload)
    return payload


def verify_restore_evidence(
    evidence: Mapping[str, Any],
    *,
    backup_id: str,
    backup_checksum: str,
    schema_head: str,
    cutoff_lsn: str,
    target_database: str,
    production_database: str,
) -> Dict[str, Any]:
    payload = dict(evidence)
    if int(payload.get("schemaVersion") or 0) != 1:
        raise RecoveryContractError("restoreEvidenceSchemaUnsupported")
    if payload.get("status") != "restored" or payload.get("targetIsolation") != "ephemeralDatabase":
        raise RecoveryContractError("restoreEvidenceIncomplete")
    _require_digest(payload, field="evidenceId", code="invalidRestoreEvidenceId")
    target = validate_recovery_target(target_database, production_database)
    expected = {
        "backupId": _backup_id(backup_id),
        "backupChecksum": _sha256(backup_checksum, code="invalidBackupChecksum"),
        "schemaHead": _machine_value(schema_head, code="invalidSchemaHead"),
        "cutoffLSN": _known_lsn(
            cutoff_lsn,
            code="invalidCutoffLSN",
            unknown_code="unknownCutoffLSN",
        ),
        "targetDatabaseHash": hashlib.sha256(target.encode("utf-8")).hexdigest(),
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise RecoveryContractError("restoreEvidenceMismatch")
    _sha256(payload.get("sourceManifestDigest"), code="invalidSourceManifestDigest")
    _sha256(payload.get("migrationEvidenceId"), code="invalidMigrationEvidenceId")
    backup_completed = _timestamp(payload.get("backupCompletedAt"), code="invalidBackupCompletedAt")
    started = _timestamp(payload.get("startedAt"), code="invalidRecoveryStartedAt")
    completed = _timestamp(payload.get("completedAt"), code="invalidRecoveryCompletedAt")
    if completed < started or started < backup_completed:
        raise RecoveryContractError("invalidRecoveryTimeline")
    return payload


def verify_integrity_metrics(
    metrics: Mapping[str, Any],
    *,
    expected_schema_head: str,
    backup_id: str,
    cutoff_lsn: str,
    target_database: str,
    production_database: str,
) -> Dict[str, Any]:
    integrity_schema_version = int(metrics.get("schemaVersion") or 0)
    if integrity_schema_version not in {
        LEGACY_INTEGRITY_SCHEMA_VERSION,
        DIRECT_USER_ID_AUDIT_SCHEMA_VERSION,
        INTEGRITY_SCHEMA_VERSION,
    }:
        raise RecoveryContractError("integritySchemaUnsupported")
    expected_head = _machine_value(expected_schema_head, code="invalidExpectedSchemaHead")
    normalized_backup_id = _backup_id(backup_id)
    normalized_cutoff = _known_lsn(
        cutoff_lsn,
        code="invalidCutoffLSN",
        unknown_code="unknownCutoffLSN",
    )
    target = validate_recovery_target(target_database, production_database)
    schema_head = _machine_value(metrics.get("schemaHead"), code="invalidRestoredSchemaHead")
    target_head = _machine_value(metrics.get("targetSchemaHead"), code="invalidTargetSchemaHead")
    migration_state = _machine_value(metrics.get("migrationState"), code="invalidMigrationState")
    relation_count = int(metrics.get("relationCount") or 0)
    row_counts_raw = metrics.get("rowCounts")
    if not isinstance(row_counts_raw, Mapping):
        raise RecoveryContractError("invalidRowCounts")
    row_counts: Dict[str, int] = {}
    for table, raw_count in row_counts_raw.items():
        name = _machine_value(table, code="invalidTableName")
        count = int(raw_count)
        if count < 0:
            raise RecoveryContractError("invalidRowCount")
        row_counts[name] = count

    owner_orphans = int(metrics.get("orphanOwnerCount") or 0)
    invalid_hashes = int(metrics.get("invalidPayloadHashCount") or 0)
    purged_violations = int(metrics.get("purgedOwnerViolationCount") or 0)
    if min(relation_count, owner_orphans, invalid_hashes, purged_violations) < 0:
        raise RecoveryContractError("invalidIntegrityCount")
    audit = _integrity_owner_audit(
        metrics,
        integrity_schema_version=integrity_schema_version,
        orphan_owner_count=owner_orphans,
        purged_owner_violation_count=purged_violations,
    )
    blockers = []
    if schema_head != expected_head or target_head != expected_head or migration_state != "ready":
        blockers.append("schemaHeadMismatch")
    if relation_count < 1:
        blockers.append("restoredSchemaMissing")
    blockers.extend(audit["blockers"])
    if owner_orphans:
        blockers.append("ownerOrphansPresent")
    if invalid_hashes:
        blockers.append("invalidPayloadHashesPresent")
    if purged_violations:
        blockers.append("purgedOwnerDataResurrected")

    payload = {
        "schemaVersion": integrity_schema_version,
        "status": "verified" if not blockers else "failed",
        "backupId": normalized_backup_id,
        "cutoffLSN": normalized_cutoff,
        "targetDatabaseHash": hashlib.sha256(target.encode("utf-8")).hexdigest(),
        "expectedSchemaHead": expected_head,
        "schemaHead": schema_head,
        "targetSchemaHead": target_head,
        "relationCount": relation_count,
        "rowCounts": dict(sorted(row_counts.items())),
        "orphanOwnerCount": owner_orphans,
        "auditCoverageStatus": audit["auditCoverageStatus"],
        "checkedDirectUserIdTables": audit["checkedDirectUserIdTables"],
        "orphanOwnerCountsByTable": audit["orphanOwnerCountsByTable"],
        "invalidPayloadHashCount": invalid_hashes,
        "purgedOwnerViolationCount": purged_violations,
        "purgedOwnerViolationCountsByTable": audit["purgedOwnerViolationCountsByTable"],
        "auditDomains": audit["auditDomains"],
        "explicitExemptions": audit["explicitExemptions"],
        "migrationState": migration_state,
        "blockers": blockers,
    }
    payload["integrityDigest"] = _canonical_hash(payload)
    return payload


def _integrity_owner_audit(
    metrics: Mapping[str, Any],
    *,
    integrity_schema_version: int,
    orphan_owner_count: int,
    purged_owner_violation_count: int,
) -> Dict[str, Any]:
    """Validate recovery ownership audit evidence without accepting partial scope.

    V1 predates dynamic discovery. V2 discovered legacy ``public.user_id``
    tables only. Both remain readable for historical evidence but are never a
    complete recovery audit. V3 requires the public, Owner Truth, and async
    effect domains plus an explicit exemption inventory.
    """

    if integrity_schema_version == LEGACY_INTEGRITY_SCHEMA_VERSION:
        return _unverified_integrity_audit()

    if integrity_schema_version == DIRECT_USER_ID_AUDIT_SCHEMA_VERSION:
        public_audit = _validate_public_direct_user_audit(
            metrics,
            owner_orphan_count=orphan_owner_count,
            purged_owner_violation_count=purged_owner_violation_count,
            source="legacy",
        )
        result = _unverified_integrity_audit()
        result.update(
            {
                "checkedDirectUserIdTables": public_audit["checkedTables"],
                "orphanOwnerCountsByTable": public_audit["orphanOwnerCountsByTable"],
                "purgedOwnerViolationCountsByTable": public_audit[
                    "purgedOwnerViolationCountsByTable"
                ],
            }
        )
        return result

    raw_domains = metrics.get("auditDomains")
    if not isinstance(raw_domains, Mapping) or set(raw_domains) != set(INTEGRITY_AUDIT_DOMAIN_NAMES):
        raise RecoveryContractError("invalidIntegrityAuditDomains")
    public_audit = _validate_public_direct_user_audit(
        raw_domains.get("publicDirectUserId"),
        owner_orphan_count=orphan_owner_count,
        purged_owner_violation_count=purged_owner_violation_count,
        source="v3",
    )
    owner_truth_audit = _validate_owner_truth_audit(raw_domains.get("ownerTruthVaultScope"))
    async_effects_audit = _validate_async_effects_audit(
        raw_domains.get("asyncEffectsOperationScope")
    )
    exemptions = _validate_explicit_exemptions(metrics.get("explicitExemptions"))
    audit_domains = {
        "publicDirectUserId": public_audit,
        "ownerTruthVaultScope": owner_truth_audit,
        "asyncEffectsOperationScope": async_effects_audit,
    }
    blockers = []
    if any(domain["status"] != "complete" for domain in audit_domains.values()):
        blockers.append("integrityAuditCoverageUnverified")
    if owner_truth_audit["scopeViolationCount"]:
        blockers.append("ownerTruthVaultScopeViolation")
    if owner_truth_audit["identityRootStatus"] != "verified":
        blockers.append("ownerTruthIdentityRootUnverified")
    if async_effects_audit["scopeViolationCount"]:
        blockers.append("asyncEffectsOperationScopeViolation")
    if async_effects_audit["rootAuthorityStatus"] != "verified":
        blockers.append("asyncEffectsRootAuthorityUnverified")
    return {
        "auditCoverageStatus": "complete"
        if "integrityAuditCoverageUnverified" not in blockers
        else "unverified",
        "checkedDirectUserIdTables": public_audit["checkedTables"],
        "orphanOwnerCountsByTable": public_audit["orphanOwnerCountsByTable"],
        "purgedOwnerViolationCountsByTable": public_audit[
            "purgedOwnerViolationCountsByTable"
        ],
        "auditDomains": audit_domains,
        "explicitExemptions": exemptions,
        "blockers": blockers,
    }


def _unverified_integrity_audit() -> Dict[str, Any]:
    return {
        "auditCoverageStatus": "unverified",
        "checkedDirectUserIdTables": [],
        "orphanOwnerCountsByTable": {},
        "purgedOwnerViolationCountsByTable": {},
        "auditDomains": {},
        "explicitExemptions": [],
        "blockers": ["integrityAuditCoverageUnverified"],
    }


def _validate_public_direct_user_audit(
    value: Any,
    *,
    owner_orphan_count: int,
    purged_owner_violation_count: int,
    source: str,
) -> Dict[str, Any]:
    domain = value if source == "v3" else {
        "status": "complete",
        "checkedTables": value.get("checkedDirectUserIdTables") if isinstance(value, Mapping) else None,
        "orphanOwnerCountsByTable": value.get("orphanOwnerCountsByTable")
        if isinstance(value, Mapping)
        else None,
        "purgedOwnerViolationCountsByTable": value.get("purgedOwnerViolationCountsByTable")
        if isinstance(value, Mapping)
        else None,
    }
    if not isinstance(domain, Mapping):
        raise RecoveryContractError("invalidIntegrityPublicAudit")
    status = _audit_status(domain.get("status"), code="invalidIntegrityPublicAudit")
    tables = _audit_table_names(
        domain.get("checkedTables"),
        schema="public",
        code="invalidIntegrityAuditTables",
    )
    orphan_counts = _integrity_count_map(
        domain.get("orphanOwnerCountsByTable"),
        tables=tables,
        schema="public",
        code="invalidIntegrityAuditCounts",
    )
    purged_counts = _integrity_count_map(
        domain.get("purgedOwnerViolationCountsByTable"),
        tables=tables,
        schema="public",
        code="invalidIntegrityAuditCounts",
    )
    if sum(orphan_counts.values()) != owner_orphan_count:
        raise RecoveryContractError("integrityAuditCountMismatch")
    if sum(purged_counts.values()) != purged_owner_violation_count:
        raise RecoveryContractError("integrityAuditCountMismatch")
    return {
        "status": status,
        "checkedTables": list(tables),
        "orphanOwnerCountsByTable": orphan_counts,
        "purgedOwnerViolationCountsByTable": purged_counts,
    }


def _validate_owner_truth_audit(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RecoveryContractError("invalidOwnerTruthAudit")
    status = _audit_status(value.get("status"), code="invalidOwnerTruthAudit")
    tables = _audit_table_names(
        value.get("checkedTables"),
        schema="owner_truth",
        code="invalidOwnerTruthAuditTables",
    )
    missing_vault_counts = _integrity_count_map(
        value.get("missingVaultCountsByTable"),
        tables=tables,
        schema="owner_truth",
        code="invalidOwnerTruthAuditCounts",
    )
    owner_mismatch_counts = _integrity_count_map(
        value.get("ownerSubjectMismatchCountsByTable"),
        tables=tables,
        schema="owner_truth",
        code="invalidOwnerTruthAuditCounts",
    )
    unclassified_tables = _audit_table_subset(
        value.get("unclassifiedTables"),
        tables=tables,
        schema="owner_truth",
        code="invalidOwnerTruthUnclassifiedTables",
    )
    if bool(unclassified_tables) != (status == "unverified"):
        raise RecoveryContractError("invalidOwnerTruthAuditStatus")
    identity_root_status = _root_authority_status(
        value.get("identityRootStatus"),
        code="invalidOwnerTruthIdentityRootStatus",
    )
    return {
        "status": status,
        "checkedTables": list(tables),
        "missingVaultCountsByTable": missing_vault_counts,
        "ownerSubjectMismatchCountsByTable": owner_mismatch_counts,
        "unclassifiedTables": list(unclassified_tables),
        "identityRootStatus": identity_root_status,
        "scopeViolationCount": sum(missing_vault_counts.values()) + sum(owner_mismatch_counts.values()),
    }


def _validate_async_effects_audit(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RecoveryContractError("invalidAsyncEffectsAudit")
    status = _audit_status(value.get("status"), code="invalidAsyncEffectsAudit")
    tables = _audit_table_names(
        value.get("checkedTables"),
        schema="async_effects",
        code="invalidAsyncEffectsAuditTables",
    )
    missing_operation_counts = _integrity_count_map(
        value.get("missingOperationCountsByTable"),
        tables=tables,
        schema="async_effects",
        code="invalidAsyncEffectsAuditCounts",
    )
    scope_mismatch_counts = _integrity_count_map(
        value.get("scopeMismatchCountsByTable"),
        tables=tables,
        schema="async_effects",
        code="invalidAsyncEffectsAuditCounts",
    )
    unclassified_tables = _audit_table_subset(
        value.get("unclassifiedTables"),
        tables=tables,
        schema="async_effects",
        code="invalidAsyncEffectsUnclassifiedTables",
    )
    if bool(unclassified_tables) != (status == "unverified"):
        raise RecoveryContractError("invalidAsyncEffectsAuditStatus")
    root_vault_missing_count = _audit_nonnegative_count(
        value.get("rootVaultMissingCount"),
        code="invalidAsyncEffectsRootCount",
    )
    root_owner_mismatch_count = _audit_nonnegative_count(
        value.get("rootOwnerSubjectMismatchCount"),
        code="invalidAsyncEffectsRootCount",
    )
    root_authority_status = _root_authority_status(
        value.get("rootAuthorityStatus"),
        code="invalidAsyncEffectsRootAuthorityStatus",
    )
    return {
        "status": status,
        "checkedTables": list(tables),
        "missingOperationCountsByTable": missing_operation_counts,
        "scopeMismatchCountsByTable": scope_mismatch_counts,
        "unclassifiedTables": list(unclassified_tables),
        "rootVaultMissingCount": root_vault_missing_count,
        "rootOwnerSubjectMismatchCount": root_owner_mismatch_count,
        "rootAuthorityStatus": root_authority_status,
        "scopeViolationCount": (
            sum(missing_operation_counts.values())
            + sum(scope_mismatch_counts.values())
            + root_vault_missing_count
            + root_owner_mismatch_count
        ),
    }


def _validate_explicit_exemptions(value: Any) -> list[Dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise RecoveryContractError("invalidIntegrityAuditExemptions")
    exemptions: list[Dict[str, str]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != {"table", "reason"}:
            raise RecoveryContractError("invalidIntegrityAuditExemptions")
        table = _qualified_table_name(
            raw.get("table"),
            schema="async_effects",
            code="invalidIntegrityAuditExemptions",
        )
        reason = _machine_value(raw.get("reason"), code="invalidIntegrityAuditExemptions")
        exemptions.append({"table": table, "reason": reason})
    if exemptions != sorted(exemptions, key=lambda item: (item["table"], item["reason"])):
        raise RecoveryContractError("invalidIntegrityAuditExemptions")
    if len({item["table"] for item in exemptions}) != len(exemptions):
        raise RecoveryContractError("invalidIntegrityAuditExemptions")
    if not any(item["table"] == "async_effects.worker_loss_observations" for item in exemptions):
        raise RecoveryContractError("missingWorkerLossObservationExemption")
    return exemptions


def _audit_status(value: Any, *, code: str) -> str:
    status = _machine_value(value, code=code)
    if status not in {"complete", "unverified"}:
        raise RecoveryContractError(code)
    return status


def _root_authority_status(value: Any, *, code: str) -> str:
    status = _machine_value(value, code=code)
    if status not in {"verified", "unverified"}:
        raise RecoveryContractError(code)
    return status


def _audit_table_names(value: Any, *, schema: str, code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise RecoveryContractError(code)
    tables = tuple(_qualified_table_name(raw, schema=schema, code=code) for raw in value)
    if tables != tuple(sorted(set(tables))):
        raise RecoveryContractError(code)
    return tables


def _audit_table_subset(
    value: Any,
    *,
    tables: tuple[str, ...],
    schema: str,
    code: str,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RecoveryContractError(code)
    candidates = tuple(_qualified_table_name(raw, schema=schema, code=code) for raw in value)
    if candidates != tuple(sorted(set(candidates))) or not set(candidates).issubset(tables):
        raise RecoveryContractError(code)
    return candidates


def _qualified_table_name(value: Any, *, schema: str, code: str) -> str:
    normalized = _machine_value(value, code=code)
    prefix = f"{schema}."
    if not normalized.startswith(prefix) or normalized.count(".") != 1:
        raise RecoveryContractError(code)
    return normalized


def _integrity_count_map(
    value: Any,
    *,
    tables: tuple[str, ...],
    schema: str,
    code: str,
) -> Dict[str, int]:
    if not isinstance(value, Mapping):
        raise RecoveryContractError(code)
    counts: Dict[str, int] = {}
    for raw_table, raw_count in value.items():
        table = _qualified_table_name(raw_table, schema=schema, code=code)
        counts[table] = _audit_nonnegative_count(raw_count, code=code)
    if tuple(sorted(counts)) != tables:
        raise RecoveryContractError(code)
    return dict(sorted(counts.items()))


def _audit_nonnegative_count(value: Any, *, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RecoveryContractError(code)
    return value


def build_recovery_record(
    *,
    recovery_id: str,
    backup_id: str,
    cutoff_lsn: str,
    backup_completed_at: Any,
    started_at: Any,
    completed_at: Any,
    target_database: str,
    production_database: str,
    backup_checksum: str,
    schema_head: str,
    restore: Mapping[str, Any],
    integrity: Mapping[str, Any],
    replay: Mapping[str, Any],
    traffic_recovery: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    normalized_recovery_id = _recovery_id(recovery_id)
    normalized_backup_id = _backup_id(backup_id)
    normalized_lsn = _known_lsn(
        cutoff_lsn,
        code="invalidCutoffLSN",
        unknown_code="unknownCutoffLSN",
    )
    normalized_checksum = _sha256(backup_checksum, code="invalidBackupChecksum")
    normalized_schema_head = _machine_value(schema_head, code="invalidSchemaHead")
    backup_completed = _timestamp(backup_completed_at, code="invalidBackupCompletedAt")
    started = _timestamp(started_at, code="invalidRecoveryStartedAt")
    completed = _timestamp(completed_at, code="invalidRecoveryCompletedAt")
    if completed < started or started < backup_completed:
        raise RecoveryContractError("invalidRecoveryTimeline")
    target = validate_recovery_target(target_database, production_database)
    target_hash = hashlib.sha256(target.encode("utf-8")).hexdigest()
    restore_payload = verify_restore_evidence(
        restore,
        backup_id=normalized_backup_id,
        backup_checksum=normalized_checksum,
        schema_head=normalized_schema_head,
        cutoff_lsn=normalized_lsn,
        target_database=target,
        production_database=production_database,
    )
    if (
        restore_payload.get("backupCompletedAt") != _iso(backup_completed)
        or restore_payload.get("startedAt") != _iso(started)
        or restore_payload.get("completedAt") != _iso(completed)
    ):
        raise RecoveryContractError("restoreTimelineMismatch")
    integrity_payload = dict(integrity)
    replay_payload = dict(replay)
    integrity_schema_version = int(integrity_payload.get("schemaVersion") or 0)
    if integrity_schema_version not in {
        LEGACY_INTEGRITY_SCHEMA_VERSION,
        DIRECT_USER_ID_AUDIT_SCHEMA_VERSION,
        INTEGRITY_SCHEMA_VERSION,
    }:
        raise RecoveryContractError("integritySchemaUnsupported")
    _require_digest(
        integrity_payload,
        field="integrityDigest",
        code="invalidIntegrityDigest",
    )
    integrity_binding = {
        "backupId": normalized_backup_id,
        "cutoffLSN": normalized_lsn,
        "targetDatabaseHash": target_hash,
        "expectedSchemaHead": normalized_schema_head,
    }
    if any(integrity_payload.get(key) != value for key, value in integrity_binding.items()):
        raise RecoveryContractError("integrityEvidenceMismatch")
    if int(replay_payload.get("schemaVersion") or 0) != 1:
        raise RecoveryContractError("replaySchemaUnsupported")
    _require_digest(replay_payload, field="replayDigest", code="invalidReplayDigest")
    replay_binding = {
        "backupId": normalized_backup_id,
        "cutoffLSN": normalized_lsn,
        "rangeStartExclusive": normalized_lsn,
    }
    if any(replay_payload.get(key) != value for key, value in replay_binding.items()):
        raise RecoveryContractError("replayEvidenceMismatch")

    traffic_payload = dict(traffic_recovery or {"schemaVersion": 1, "status": "notAttempted"})
    if int(traffic_payload.get("schemaVersion") or 0) != 1:
        raise RecoveryContractError("trafficRecoverySchemaUnsupported")
    traffic_status = _machine_value(
        traffic_payload.get("status"),
        code="invalidTrafficRecoveryStatus",
    )
    if traffic_status not in {"notAttempted", "restored", "failed"}:
        raise RecoveryContractError("invalidTrafficRecoveryStatus")
    if traffic_status != "notAttempted":
        traffic_payload["evidenceId"] = _sha256(
            traffic_payload.get("evidenceId"),
            code="invalidTrafficRecoveryEvidenceId",
        )
    integrity_verified = (
        integrity_payload.get("status") == "verified"
        and integrity_schema_version == INTEGRITY_SCHEMA_VERSION
        and integrity_payload.get("auditCoverageStatus") == "complete"
    )
    replay_complete = (
        replay_payload.get("status") == "complete"
        and replay_payload.get("deletionReplayStatus") == "verified"
    )
    go = integrity_verified and replay_complete and traffic_status != "failed"
    if not integrity_verified:
        status = "integrityFailed"
    elif not replay_complete:
        status = "replayPending"
    elif traffic_status == "failed":
        status = "trafficRecoveryFailed"
    else:
        status = "verified"
    record = {
        "schemaVersion": 1,
        "recoveryId": normalized_recovery_id,
        "backupId": normalized_backup_id,
        "backupChecksum": normalized_checksum,
        "cutoffLSN": normalized_lsn,
        "schemaHead": normalized_schema_head,
        "startedAt": _iso(started),
        "completedAt": _iso(completed),
        "targetIsolation": "ephemeralDatabase",
        "targetDatabaseHash": target_hash,
        "observedRpoSeconds": int((started - backup_completed).total_seconds()),
        "observedRtoSeconds": int((completed - started).total_seconds()),
        "restoreEvidenceId": restore_payload["evidenceId"],
        "integrity": integrity_payload,
        "replay": replay_payload,
        "replayRange": {
            "startExclusive": replay_payload.get("rangeStartExclusive"),
            "endInclusive": replay_payload.get("rangeEndInclusive"),
        },
        "hashCounts": {
            "invalidPayloadHashCount": integrity_payload.get("invalidPayloadHashCount"),
            "uniqueReceiptCount": replay_payload.get("uniqueReceiptCount"),
            "duplicateReceiptCount": replay_payload.get("duplicateReceiptCount"),
        },
        "deletionReplay": replay_payload.get("deletionReplayStatus"),
        "trafficRecovery": traffic_payload,
        "cutoverDecision": "GO" if go else "NO_GO",
        "status": status,
        "rpoRtoClass": "observedDrillOnly",
    }
    record["evidenceId"] = _canonical_hash(record)
    return record


def write_recovery_record_atomic(path: Path, record: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    try:
        with partial.open("w", encoding="utf-8") as handle:
            json.dump(dict(record), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        partial.chmod(0o600)
        os.replace(partial, destination)
        destination.chmod(0o600)
    finally:
        if partial.exists():
            partial.unlink()
