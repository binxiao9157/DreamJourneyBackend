#!/usr/bin/env python3
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = ROOT_DIR / "scripts/db/backup_postgres.sh"
VERIFY_SCRIPT = ROOT_DIR / "scripts/db/verify_backup_manifest.py"
VERIFY_LATEST_SCRIPT = ROOT_DIR / "scripts/db/verify_latest_backup.py"
RETENTION_SCRIPT = ROOT_DIR / "scripts/db/audit_backup_retention.py"
ALERT_SCRIPT = ROOT_DIR / "scripts/db/backup_alert.sh"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def run(command, *, env, expected=0):
    result = subprocess.run(
        command,
        cwd=ROOT_DIR,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    require(result.returncode == expected, f"command failed: {command[0]} status={result.returncode}")
    return result


def main():
    openssl = shutil.which("openssl")
    require(openssl, "openssl is required for backup smoke")
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        backup_root = temp / "backups"
        key_path = temp / "backup.key"
        key_path.write_text("smoke-only-backup-passphrase\n", encoding="utf-8")
        key_path.chmod(0o600)
        fake_docker = temp / "fake-docker"
        fake_docker.write_text(
            """#!/usr/bin/env python3
import os
import sys
import time

args = sys.argv[1:]
if "psql" in args:
    query = args[-1]
    if "schema_migrations" in query:
        print("0001")
    elif "pg_current_wal_lsn" in query:
        print("0/16B6A40")
    elif "pg_database_size" in query:
        print("1024")
    else:
        raise SystemExit(2)
elif "pg_dump" in args:
    if os.environ.get("FAKE_DOCKER_DELAY_DUMP") == "1":
        time.sleep(30)
    sys.stdout.buffer.write(b"PGDMP-dreamjourney-smoke")
elif "pg_restore" in args:
    payload = sys.stdin.buffer.read()
    if not payload.startswith(b"PGDMP"):
        raise SystemExit(3)
else:
    raise SystemExit(4)
""",
            encoding="utf-8",
        )
        fake_docker.chmod(0o755)

        env = dict(os.environ)
        env.update(
            {
                "PYTHON_BIN": sys.executable,
                "DOCKER_BIN": str(fake_docker),
                "OPENSSL_BIN": openssl,
                "BACKUP_ROOT": str(backup_root),
                "BACKUP_ENCRYPTION_KEY_FILE": str(key_path),
                "BACKUP_ENCRYPTION_REF": "smoke-key:v1",
                "BACKUP_RETENTION_CLASS": "operationalBackup35d",
                "BACKUP_RETENTION_DAYS": "35",
                "BACKUP_MIN_FREE_BYTES": "1",
                "BACKUP_ALERT_OWNER": "backup-smoke-owner",
            }
        )

        first = run(["bash", str(BACKUP_SCRIPT)], env=env)
        second = run(["bash", str(BACKUP_SCRIPT)], env=env)
        require('"status":"verified"' in first.stdout, "first verified backup")
        require('"status":"verified"' in second.stdout, "second verified backup")
        manifests = sorted(backup_root.glob("*.manifest.json"))
        require(len(manifests) == 2, "two consecutive backup manifests")
        for manifest in manifests:
            report = run(
                [sys.executable, str(VERIFY_SCRIPT), str(manifest), "--expected-schema-head", "0001"],
                env=env,
            )
            require(json.loads(report.stdout)["status"] == "verified", "manifest verification")
        latest = run(
            [
                sys.executable,
                str(VERIFY_LATEST_SCRIPT),
                str(backup_root),
                "--expected-schema-head",
                "0001",
                "--max-age-hours",
                "36",
            ],
            env=env,
        )
        require(json.loads(latest.stdout)["freshnessGate"] == "passed", "latest backup gate")

        retention = run(
            [sys.executable, str(RETENTION_SCRIPT), str(backup_root), "--keep-minimum", "1"],
            env=env,
        )
        retention_payload = json.loads(retention.stdout)
        require(retention_payload["action"] == "auditOnly", "retention must be audit only")
        require(retention_payload["automaticDeletion"] is False, "automatic deletion forbidden")

        failure_env = dict(env)
        failure_env["BACKUP_MIN_FREE_BYTES"] = "1000000000000000"
        failed = subprocess.run(
            ["bash", str(BACKUP_SCRIPT)],
            cwd=ROOT_DIR,
            env=failure_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        require(failed.returncode != 0, "insufficient space must fail")
        failure_manifests = sorted((backup_root / "failures").glob("*.failure.json"))
        require(failure_manifests, "failure manifest")
        failure_payload = json.loads(failure_manifests[-1].read_text(encoding="utf-8"))
        require(failure_payload["errorCode"] == "insufficientSpace", "failure machine code")
        require(failure_payload["status"] == "failed", "failure status")

        interrupted_env = dict(env)
        interrupted_env["FAKE_DOCKER_DELAY_DUMP"] = "1"
        interrupted = subprocess.Popen(
            ["bash", str(BACKUP_SCRIPT)],
            cwd=ROOT_DIR,
            env=interrupted_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        for _ in range(50):
            if (backup_root / ".backup.lock.d").exists():
                break
            time.sleep(0.02)
        time.sleep(0.1)
        os.killpg(interrupted.pid, signal.SIGTERM)
        interrupted.communicate(timeout=5)
        require(interrupted.returncode != 0, "interrupted backup must fail")
        interrupted_manifests = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (backup_root / "failures").glob("*.failure.json")
        ]
        require(
            any(item.get("errorCode") == "backupInterrupted" for item in interrupted_manifests),
            "interruption failure receipt",
        )

        run(["bash", str(ALERT_SCRIPT), "dreamjourney-db-backup.service"], env=env)
        alerts = sorted((backup_root / "alerts").glob("backup-alert-*.json"))
        require(alerts, "failure alert receipt")
        alert_payload = json.loads(alerts[-1].read_text(encoding="utf-8"))
        require(alert_payload["owner"] == "backup-smoke-owner", "alert owner")

        first_payload = json.loads(manifests[0].read_text(encoding="utf-8"))
        artifact = backup_root / first_payload["artifactFile"]
        artifact.write_bytes(artifact.read_bytes() + b"corrupt")
        corrupt = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), str(manifests[0]), "--expected-schema-head", "0001"],
            cwd=ROOT_DIR,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        require(corrupt.returncode != 0, "corrupt backup must fail verification")
        require(not list(backup_root.glob("*.partial")), "partial backup files must be cleaned")
        require(not (backup_root / ".backup.lock.d").exists(), "backup lock must be released")

        print(
            json.dumps(
                {
                    "status": "passed",
                    "schemaVersion": 1,
                    "consecutiveVerifiedBackups": 2,
                    "encryptedArchiveAccess": True,
                    "checksumCorruptionFailClosed": True,
                    "insufficientSpaceReceipt": True,
                    "interruptionReceipt": True,
                    "alertReceipt": True,
                    "freshnessGate": True,
                    "retentionAuditOnly": True,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
