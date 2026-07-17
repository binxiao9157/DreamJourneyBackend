#!/usr/bin/env python3
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.backup import verify_backup_manifest
from app.db.recovery import (
    RecoveryContractError,
    build_recovery_record,
    write_recovery_record_atomic,
)


def load_object(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RecoveryContractError("recoveryEvidenceInvalid")
    return payload


def main():
    parser = argparse.ArgumentParser(description="Bind a deployed isolated recovery drill evidence set.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--restore-evidence", type=Path, required=True)
    parser.add_argument("--integrity-evidence", type=Path, required=True)
    parser.add_argument("--replay-evidence", type=Path, required=True)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--production-database", default="dreamjourney")
    parser.add_argument("--recovery-id", required=True)
    parser.add_argument("--expected-cutover", choices=("GO", "NO_GO"), default="NO_GO")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        manifest = load_object(args.manifest)
        verify_backup_manifest(
            args.manifest,
            expected_schema_head=str(manifest.get("schemaHead") or ""),
        )
        restore = load_object(args.restore_evidence)
        if restore.get("sourceManifestDigest") != hashlib.sha256(args.manifest.read_bytes()).hexdigest():
            raise RecoveryContractError("restoreManifestEvidenceMismatch")
        integrity = load_object(args.integrity_evidence)
        replay = load_object(args.replay_evidence)
        record = build_recovery_record(
            recovery_id=args.recovery_id,
            backup_id=str(manifest.get("backupId") or ""),
            cutoff_lsn=str(manifest.get("lsn") or ""),
            backup_completed_at=restore.get("backupCompletedAt"),
            started_at=restore.get("startedAt"),
            completed_at=restore.get("completedAt"),
            target_database=args.target_database,
            production_database=args.production_database,
            backup_checksum=str(manifest.get("checksum") or ""),
            schema_head=str(manifest.get("schemaHead") or ""),
            restore=restore,
            integrity=integrity,
            replay=replay,
        )
        if record["cutoverDecision"] != args.expected_cutover:
            raise RecoveryContractError("unexpectedCutoverDecision")
        write_recovery_record_atomic(args.output, record)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecoveryContractError) as exc:
        code = exc.code if isinstance(exc, RecoveryContractError) else type(exc).__name__
        print(json.dumps({"schemaVersion": 1, "status": "failed", "errorCode": code}), file=sys.stderr)
        raise SystemExit(1) from exc

    print(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "passed",
                "backupId": record["backupId"],
                "restoreStatus": "verified",
                "integrityStatus": record["integrity"]["status"],
                "replayStatus": record["replay"]["status"],
                "cutoverDecision": record["cutoverDecision"],
                "evidenceId": record["evidenceId"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
