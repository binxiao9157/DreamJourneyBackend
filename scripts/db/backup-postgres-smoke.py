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
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.migrator import default_migrations_dir, load_migrations

BACKUP_SCRIPT = ROOT_DIR / "scripts/db/backup_postgres.sh"
VERIFY_SCRIPT = ROOT_DIR / "scripts/db/verify_backup_manifest.py"
VERIFY_LATEST_SCRIPT = ROOT_DIR / "scripts/db/verify_latest_backup.py"
RETENTION_SCRIPT = ROOT_DIR / "scripts/db/audit_backup_retention.py"
ALERT_SCRIPT = ROOT_DIR / "scripts/db/backup_alert.sh"
DEPLOYED_SMOKE = ROOT_DIR / "scripts/db/backup-deployed-smoke.py"
BACKUP_SHELL = "/bin/bash" if Path("/bin/bash").is_file() else shutil.which("bash")
CURRENT_SCHEMA_HEAD = load_migrations(default_migrations_dir())[-1].version


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
    require(
        result.returncode == expected,
        f"command failed: {command[0]} status={result.returncode} "
        f"stdout={result.stdout[-500:]} stderr={result.stderr[-500:]}",
    )
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
        print(os.environ["FAKE_SCHEMA_HEAD"])
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
                "FAKE_SCHEMA_HEAD": CURRENT_SCHEMA_HEAD,
            }
        )

        require(BACKUP_SHELL, "bash is required for backup smoke")
        first = run([BACKUP_SHELL, str(BACKUP_SCRIPT)], env=env)
        second = run([BACKUP_SHELL, str(BACKUP_SCRIPT)], env=env)
        require('"status":"verified"' in first.stdout, "first verified backup")
        require('"status":"verified"' in second.stdout, "second verified backup")
        manifests = sorted(backup_root.glob("*.manifest.json"))
        require(len(manifests) == 2, "two consecutive backup manifests")
        for manifest in manifests:
            report = run(
                [sys.executable, str(VERIFY_SCRIPT), str(manifest), "--expected-schema-head", CURRENT_SCHEMA_HEAD],
                env=env,
            )
            require(json.loads(report.stdout)["status"] == "verified", "manifest verification")
        latest = run(
            [
                sys.executable,
                str(VERIFY_LATEST_SCRIPT),
                str(backup_root),
                "--expected-schema-head",
                CURRENT_SCHEMA_HEAD,
                "--max-age-hours",
                "36",
            ],
            env=env,
        )
        require(json.loads(latest.stdout)["freshnessGate"] == "passed", "latest backup gate")

        mismatch_env = dict(env)
        mismatch_env["FAKE_SCHEMA_HEAD"] = "0000" if CURRENT_SCHEMA_HEAD != "0000" else "0001"
        mismatched = subprocess.run(
            [BACKUP_SHELL, str(BACKUP_SCRIPT)],
            cwd=ROOT_DIR,
            env=mismatch_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        require(mismatched.returncode != 0, "schema head mismatch must fail")
        mismatch_failures = sorted((backup_root / "failures").glob("*.failure.json"))
        require(mismatch_failures, "schema head mismatch failure receipt")
        mismatch_payload = json.loads(mismatch_failures[-1].read_text(encoding="utf-8"))
        require(mismatch_payload["errorCode"] == "schemaHeadMismatch", "schema head mismatch code")

        retention = run(
            [
                sys.executable,
                str(RETENTION_SCRIPT),
                str(backup_root),
                "--keep-minimum",
                "1",
                "--output",
                str(backup_root / "retention-latest.json"),
            ],
            env=env,
        )
        retention_payload = json.loads(retention.stdout)
        require(retention_payload["action"] == "auditOnly", "retention must be audit only")
        require(retention_payload["automaticDeletion"] is False, "automatic deletion forbidden")
        require(not (backup_root / ".backup.lock.d").exists(), "successful backup lock release")

        failure_env = dict(env)
        failure_env["BACKUP_MIN_FREE_BYTES"] = "1000000000000000"
        failed = subprocess.run(
            [BACKUP_SHELL, str(BACKUP_SCRIPT)],
            cwd=ROOT_DIR,
            env=failure_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        require(
            failed.returncode != 0,
            "insufficient space must fail: "
            f"stdout={failed.stdout[-500:]} stderr={failed.stderr[-500:]}",
        )
        failure_manifests = sorted((backup_root / "failures").glob("*.failure.json"))
        require(failure_manifests, "failure manifest")
        failure_payload = json.loads(failure_manifests[-1].read_text(encoding="utf-8"))
        require(failure_payload["errorCode"] == "insufficientSpace", "failure machine code")
        require(failure_payload["status"] == "failed", "failure status")

        interrupted_env = dict(env)
        interrupted_env["FAKE_DOCKER_DELAY_DUMP"] = "1"
        interrupted = subprocess.Popen(
            [BACKUP_SHELL, str(BACKUP_SCRIPT)],
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
        # Interrupt the whole backup process group.  Signalling only the
        # parent shell leaves the foreground fake pg_dump alive for 30 seconds,
        # so Bash cannot run its TERM trap and write the interruption receipt.
        os.killpg(interrupted.pid, signal.SIGTERM)
        try:
            interrupted_stdout, interrupted_stderr = interrupted.communicate(timeout=5)
        finally:
            try:
                os.killpg(interrupted.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        require(interrupted.returncode != 0, "interrupted backup must fail")
        interrupted_manifests = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (backup_root / "failures").glob("*.failure.json")
        ]
        interrupted_codes = [item.get("errorCode") for item in interrupted_manifests]
        require(
            "backupInterrupted" in interrupted_codes,
            "interruption failure receipt: "
            f"codes={interrupted_codes} stdout={interrupted_stdout[-500:]} "
            f"stderr={interrupted_stderr[-500:]}",
        )

        run(["bash", str(ALERT_SCRIPT), "dreamjourney-db-backup.service"], env=env)
        alerts = sorted((backup_root / "alerts").glob("backup-alert-*.json"))
        require(alerts, "failure alert receipt")
        alert_payload = json.loads(alerts[-1].read_text(encoding="utf-8"))
        require(alert_payload["owner"] == "backup-smoke-owner", "alert owner")

        deployed_env = dict(env)
        deployed_env["EXPECTED_SCHEMA_HEAD"] = CURRENT_SCHEMA_HEAD
        deployed_env["BACKUP_TIMER_VERIFIED"] = "1"
        deployed = run([sys.executable, str(DEPLOYED_SMOKE)], env=deployed_env)
        require(json.loads(deployed.stdout)["status"] == "passed", "deployed evidence smoke")

        first_payload = json.loads(manifests[0].read_text(encoding="utf-8"))
        artifact = backup_root / first_payload["artifactFile"]
        artifact.write_bytes(artifact.read_bytes() + b"corrupt")
        corrupt = subprocess.run(
            [sys.executable, str(VERIFY_SCRIPT), str(manifests[0]), "--expected-schema-head", CURRENT_SCHEMA_HEAD],
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
                    "schemaHeadMismatchFailClosed": True,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
