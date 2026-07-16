#!/usr/bin/env python3
import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._:-]{1,127}$")


def safe(value, fallback):
    normalized = str(value or "").strip()
    return normalized if SAFE_VALUE.fullmatch(normalized) else fallback


def main():
    parser = argparse.ArgumentParser(description="Write a value-free backup failure alert receipt.")
    parser.add_argument("--alert-root", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    root = Path(args.alert_root)
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    event = {
        "schemaVersion": 1,
        "eventType": "databaseBackupFailed",
        "status": "open",
        "occurredAt": now.isoformat(),
        "unit": safe(args.unit, "databaseBackupUnit"),
        "owner": safe(args.owner, "backend-operations"),
    }
    destination = root / f"backup-alert-{now.strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}.json"
    partial = destination.with_suffix(".json.partial")
    partial.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.chmod(0o600)
    os.replace(partial, destination)
    print(json.dumps(event, sort_keys=True))


if __name__ == "__main__":
    main()
