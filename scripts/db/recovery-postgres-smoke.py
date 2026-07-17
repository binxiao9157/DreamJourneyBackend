#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.db.backup import build_completed_manifest, write_manifest_atomic
from app.db.recovery import build_replay_plan, verify_integrity_metrics, write_recovery_record_atomic


RESTORE_SCRIPT = ROOT_DIR / "scripts/db/restore_postgres.sh"
REPLAY_SCRIPT = ROOT_DIR / "scripts/db/replay_recovery.py"
DEPLOYED_SMOKE = ROOT_DIR / "scripts/db/recovery-deployed-smoke.py"


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
        f"stdout={result.stdout[-600:]} stderr={result.stderr[-600:]}",
    )
    return result


def main():
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)
        artifact = temp / "dj-20260717T010203Z-a1b2c3d4.dump"
        artifact.write_bytes(b"PGDMP-isolated-recovery-smoke")
        manifest_path = temp / "dj-20260717T010203Z-a1b2c3d4.manifest.json"
        now = datetime.now(timezone.utc)
        manifest = build_completed_manifest(
            backup_id="dj-20260717T010203Z-a1b2c3d4",
            created_at=now - timedelta(minutes=3),
            completed_at=now - timedelta(minutes=2),
            schema_head="0001",
            lsn="0/16B6A40",
            artifact_path=artifact,
            encryption_ref="smoke-only:v1",
            retention_class="operationalBackup35d",
            retention_days=35,
        )
        write_manifest_atomic(manifest_path, manifest)

        fake_docker = temp / "fake-docker"
        fake_docker.write_text(
            """#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if "pg_restore" in args:
    payload = sys.stdin.buffer.read()
    if not payload.startswith(b"PGDMP"):
        raise SystemExit(3)
elif "psql" in args:
    query = args[-1]
    if "FROM pg_database" in query:
        print("")
elif "createdb" in args or "dropdb" in args:
    pass
elif "run" in args and "scripts/migrate_db.py" in args:
    print(json.dumps({
        "schemaVersion": 1,
        "status": "ready",
        "expectedHead": "0001",
        "appliedHead": "0001",
        "mode": "verify" if "--verify" in args else "apply"
    }))
else:
    raise SystemExit(4)
""",
            encoding="utf-8",
        )
        fake_docker.chmod(0o755)

        output_dir = temp / "recovery-output"
        target = "dj_recovery_smoke_1234"
        env = dict(os.environ)
        env.update(
            {
                "PYTHON_BIN": sys.executable,
                "DOCKER_BIN": str(fake_docker),
                "RECOVERY_MANIFEST_PATH": str(manifest_path),
                "RECOVERY_TARGET_DB": target,
                "RECOVERY_PRODUCTION_DB": "dreamjourney",
                "RECOVERY_DATABASE_URL": f"postgresql://smoke:smoke@postgres:5432/{target}",
                "RECOVERY_OUTPUT_DIR": str(output_dir),
                "RECOVERY_ALLOW_UNENCRYPTED": "1",
            }
        )
        restore_result = run(["bash", str(RESTORE_SCRIPT)], env=env)
        restore = json.loads((output_dir / "restore-evidence.json").read_text(encoding="utf-8"))
        require(restore["status"] == "restored", "restore evidence status")
        require(target not in restore_result.stdout, "target database must not leak in output")

        unsafe_env = dict(env)
        unsafe_env["RECOVERY_TARGET_DB"] = "dreamjourney"
        unsafe_env["RECOVERY_DATABASE_URL"] = "postgresql://smoke:smoke@postgres:5432/dreamjourney"
        unsafe = subprocess.run(
            ["bash", str(RESTORE_SCRIPT)],
            cwd=ROOT_DIR,
            env=unsafe_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        require(unsafe.returncode != 0, "production target must fail closed")

        integrity = verify_integrity_metrics(
            {
                "schemaVersion": 1,
                "schemaHead": "0001",
                "targetSchemaHead": "0001",
                "relationCount": 19,
                "rowCounts": {"users": 1, "archive_items": 1},
                "orphanOwnerCount": 0,
                "invalidPayloadHashCount": 0,
                "purgedOwnerViolationCount": 0,
                "migrationState": "ready",
            },
            expected_schema_head="0001",
            backup_id=manifest["backupId"],
            cutoff_lsn=manifest["lsn"],
            target_database=target,
            production_database="dreamjourney",
        )
        integrity_path = output_dir / "integrity-evidence.json"
        write_recovery_record_atomic(integrity_path, integrity)

        incomplete_replay_path = output_dir / "replay-incomplete.json"
        run(
            [
                sys.executable,
                str(REPLAY_SCRIPT),
                "--backup-id",
                manifest["backupId"],
                "--cutoff-lsn",
                manifest["lsn"],
                "--output",
                str(incomplete_replay_path),
            ],
            env=env,
        )
        incomplete = json.loads(incomplete_replay_path.read_text(encoding="utf-8"))
        require(incomplete["status"] == "incomplete", "missing receipt authority must remain incomplete")

        bundle = {
            "schemaVersion": 1,
            "backupId": manifest["backupId"],
            "cutoffLSN": manifest["lsn"],
            "rangeEndLSN": "0/16B6B00",
            "sourceEvidenceId": "a" * 64,
            "coverage": {
                "commandReceipts": True,
                "outboxReceipts": True,
                "deletionReceipts": True,
                "providerReceipts": True,
            },
            "receipts": [
                {
                    "receiptId": "delete-smoke-1",
                    "kind": "deletion",
                    "lsn": "0/16B6A80",
                    "ownerIdHash": "b" * 64,
                    "payloadHash": "c" * 64,
                    "status": "applied",
                }
            ],
        }
        bundle_path = output_dir / "replay-bundle.json"
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
        plan = build_replay_plan(bundle, backup_id=manifest["backupId"], cutoff_lsn=manifest["lsn"])
        application = {
            "schemaVersion": 1,
            "status": "applied",
            "backupId": manifest["backupId"],
            "cutoffLSN": manifest["lsn"],
            "rangeEndLSN": plan["rangeEndInclusive"],
            "sourceEvidenceId": plan["sourceEvidenceId"],
            "planDigest": plan["replayDigest"],
            "applicationEvidenceId": "d" * 64,
            "appliedReceiptCounts": {
                "command": 0,
                "outbox": 0,
                "deletion": 1,
                "provider": 0,
            },
        }
        application_path = output_dir / "replay-application.json"
        application_path.write_text(json.dumps(application), encoding="utf-8")
        complete_replay_path = output_dir / "replay-complete.json"
        run(
            [
                sys.executable,
                str(REPLAY_SCRIPT),
                "--backup-id",
                manifest["backupId"],
                "--cutoff-lsn",
                manifest["lsn"],
                "--bundle",
                str(bundle_path),
                "--application-evidence",
                str(application_path),
                "--output",
                str(complete_replay_path),
            ],
            env=env,
        )

        recovery_record_path = output_dir / "recovery-record.json"
        deployed = run(
            [
                sys.executable,
                str(DEPLOYED_SMOKE),
                "--manifest",
                str(manifest_path),
                "--restore-evidence",
                str(output_dir / "restore-evidence.json"),
                "--integrity-evidence",
                str(integrity_path),
                "--replay-evidence",
                str(complete_replay_path),
                "--target-database",
                target,
                "--recovery-id",
                "recovery-20260717T020304Z-a1b2c3d4",
                "--expected-cutover",
                "GO",
                "--output",
                str(recovery_record_path),
            ],
            env=env,
        )
        deployed_payload = json.loads(deployed.stdout)
        record = json.loads(recovery_record_path.read_text(encoding="utf-8"))
        require(deployed_payload["cutoverDecision"] == "GO", "bound recovery evidence can decide GO")
        require(record["restoreEvidenceId"] == restore["evidenceId"], "record binds restore evidence")
        require(target not in json.dumps(record), "record must remain value-free")

        print(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "passed",
                    "isolatedRestore": True,
                    "productionTargetRejected": True,
                    "missingReceiptAuthorityNoGo": True,
                    "receiptReplayBinding": True,
                    "integrityBinding": True,
                    "valueFreeRecoveryRecord": True,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
