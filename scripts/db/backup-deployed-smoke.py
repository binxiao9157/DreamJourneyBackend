#!/usr/bin/env python3
import json
import os
import stat
import sys
from datetime import timedelta
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.backup import verify_backup_manifest


BACKUP_ROOT = Path(
    os.environ.get("BACKUP_ROOT", "/var/backups/dreamjourney/postgres")
)
EXPECTED_SCHEMA_HEAD = os.environ.get("EXPECTED_SCHEMA_HEAD", "0001")
MAX_AGE_HOURS = float(os.environ.get("BACKUP_MAX_AGE_HOURS", "36"))
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "").strip()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def main():
    require(BACKUP_ROOT.is_dir(), "backup root is missing")
    require(mode(BACKUP_ROOT) == 0o700, "backup root must be mode 700")
    manifests = sorted(
        BACKUP_ROOT.glob("*.manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    require(len(manifests) >= 2, "two consecutive backup manifests are required")
    reports = []
    encrypted = True
    for manifest_path in manifests[:2]:
        report = verify_backup_manifest(
            manifest_path,
            expected_schema_head=EXPECTED_SCHEMA_HEAD,
            max_age=timedelta(hours=MAX_AGE_HOURS),
        )
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifact_path = BACKUP_ROOT / str(payload["artifactFile"])
        require(mode(manifest_path) == 0o600, "manifest must be mode 600")
        require(mode(artifact_path) == 0o600, "artifact must be mode 600")
        encrypted = encrypted and artifact_path.name.endswith(".dump.enc")
        reports.append(report)
    require(encrypted, "deployed backup artifacts must be encrypted")
    require(not list(BACKUP_ROOT.glob("*.partial")), "partial artifacts must not remain")
    require(not (BACKUP_ROOT / ".backup.lock.d").exists(), "backup lock must be released")

    retention_path = BACKUP_ROOT / "retention-latest.json"
    require(retention_path.is_file(), "retention audit report is missing")
    retention = json.loads(retention_path.read_text(encoding="utf-8"))
    require(retention.get("action") == "auditOnly", "retention action")
    require(retention.get("automaticDeletion") is False, "automatic deletion forbidden")
    require(int(retention.get("validBackupCount") or 0) >= 2, "retention backup count")
    require(mode(retention_path) == 0o600, "retention report must be mode 600")

    alerts = list((BACKUP_ROOT / "alerts").glob("backup-alert-*.json"))
    require(alerts, "backup alert receipt is missing")
    require(all(mode(path) == 0o600 for path in alerts), "alert receipts must be mode 600")

    result = {
        "schemaVersion": 1,
        "status": "passed",
        "verifiedCurrentBackupCount": len(reports),
        "schemaHead": EXPECTED_SCHEMA_HEAD,
        "encryptedArtifacts": encrypted,
        "freshnessGate": True,
        "permissions": "root700_artifacts600",
        "retentionAction": "auditOnly",
        "automaticDeletion": False,
        "alertReceiptPresent": True,
        "timerEnabled": os.environ.get("BACKUP_TIMER_VERIFIED") == "1",
    }
    require(result["timerEnabled"], "backup timers are not verified")
    serialized = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if OUTPUT_PATH:
        output = Path(OUTPUT_PATH)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized, encoding="utf-8")
        output.chmod(0o600)
    print(serialized, end="")


if __name__ == "__main__":
    main()
