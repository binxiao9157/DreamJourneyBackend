#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.backup import (
    build_completed_manifest,
    build_failed_manifest,
    write_manifest_atomic,
)


def timestamp(value):
    return datetime.fromisoformat(value)


def parser():
    root = argparse.ArgumentParser(description="Write value-free database backup manifests.")
    commands = root.add_subparsers(dest="command", required=True)

    complete = commands.add_parser("complete")
    complete.add_argument("--manifest", required=True)
    complete.add_argument("--artifact", required=True)
    complete.add_argument("--backup-id", required=True)
    complete.add_argument("--created-at", required=True, type=timestamp)
    complete.add_argument("--completed-at", required=True, type=timestamp)
    complete.add_argument("--schema-head", required=True)
    complete.add_argument("--lsn", required=True)
    complete.add_argument("--encryption-ref", required=True)
    complete.add_argument("--retention-class", required=True)
    complete.add_argument("--retention-days", required=True, type=int)

    failed = commands.add_parser("failed")
    failed.add_argument("--manifest", required=True)
    failed.add_argument("--backup-id", required=True)
    failed.add_argument("--created-at", required=True, type=timestamp)
    failed.add_argument("--schema-head", default="unknown")
    failed.add_argument("--lsn", default="unknown")
    failed.add_argument("--encryption-ref", required=True)
    failed.add_argument("--retention-class", required=True)
    failed.add_argument("--error-code", required=True)
    failed.add_argument("--owner", required=True)
    return root


def main():
    args = parser().parse_args()
    if args.command == "complete":
        manifest = build_completed_manifest(
            backup_id=args.backup_id,
            created_at=args.created_at,
            completed_at=args.completed_at,
            schema_head=args.schema_head,
            lsn=args.lsn,
            artifact_path=Path(args.artifact),
            encryption_ref=args.encryption_ref,
            retention_class=args.retention_class,
            retention_days=args.retention_days,
        )
    else:
        manifest = build_failed_manifest(
            backup_id=args.backup_id,
            created_at=args.created_at,
            schema_head=args.schema_head,
            lsn=args.lsn,
            encryption_ref=args.encryption_ref,
            retention_class=args.retention_class,
            error_code=args.error_code,
            owner=args.owner,
        )
    write_manifest_atomic(Path(args.manifest), manifest)
    print(
        json.dumps(
            {
                "backupId": manifest["backupId"],
                "schemaVersion": 1,
                "status": manifest["status"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
