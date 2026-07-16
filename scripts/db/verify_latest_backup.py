#!/usr/bin/env python3
import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.backup import BackupManifestError, verify_backup_manifest


def main():
    parser = argparse.ArgumentParser(description="Verify the newest current database backup.")
    parser.add_argument("backup_root")
    parser.add_argument("--expected-schema-head", required=True)
    parser.add_argument("--max-age-hours", type=float, default=36.0)
    args = parser.parse_args()
    manifests = sorted(
        Path(args.backup_root).glob("*.manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not manifests:
        print(json.dumps({"status": "failed", "reason": "backupMissing"}, sort_keys=True))
        raise SystemExit(1)
    failures = []
    for manifest in manifests:
        try:
            report = verify_backup_manifest(
                manifest,
                expected_schema_head=args.expected_schema_head,
                max_age=timedelta(hours=args.max_age_hours),
            )
            report["freshnessGate"] = "passed"
            print(json.dumps(report, sort_keys=True))
            return
        except BackupManifestError as error:
            failures.append(error.code)
    print(
        json.dumps(
            {
                "status": "failed",
                "reason": "noCurrentVerifiedBackup",
                "candidateFailureCodes": sorted(set(failures)),
            },
            sort_keys=True,
        )
    )
    raise SystemExit(1)


if __name__ == "__main__":
    main()
