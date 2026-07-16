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
    parser = argparse.ArgumentParser(description="Verify a database backup manifest and artifact.")
    parser.add_argument("manifest")
    parser.add_argument("--expected-schema-head")
    parser.add_argument("--max-age-hours", type=float)
    args = parser.parse_args()
    try:
        report = verify_backup_manifest(
            Path(args.manifest),
            expected_schema_head=args.expected_schema_head,
            max_age=(
                timedelta(hours=args.max_age_hours)
                if args.max_age_hours is not None
                else None
            ),
        )
    except BackupManifestError as error:
        print(json.dumps({"status": "failed", "reason": error.code}, sort_keys=True))
        raise SystemExit(1)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
