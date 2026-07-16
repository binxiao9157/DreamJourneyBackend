import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db.backup import (
    BackupManifestError,
    build_completed_manifest,
    build_failed_manifest,
    plan_backup_retention,
    verify_backup_manifest,
    write_manifest_atomic,
)


NOW = datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc)


class BackupManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def artifact(self, name="backup.dump.enc", content=b"encrypted-backup-bytes"):
        path = self.root / name
        path.write_bytes(content)
        return path

    def completed(self, artifact, *, backup_id="dj-20260716T140000Z-a1b2c3d4", days=35):
        return build_completed_manifest(
            backup_id=backup_id,
            created_at=NOW,
            completed_at=NOW + timedelta(seconds=3),
            schema_head="0001",
            lsn="0/16B6A40",
            artifact_path=artifact,
            encryption_ref="server-local-key:v1",
            retention_class="operationalBackup35d",
            retention_days=days,
        )

    def test_completed_manifest_is_value_free_and_verifies_artifact(self):
        artifact = self.artifact()
        manifest_path = self.root / "backup.manifest.json"
        manifest = self.completed(artifact)

        write_manifest_atomic(manifest_path, manifest)
        report = verify_backup_manifest(
            manifest_path,
            expected_schema_head="0001",
            now=NOW + timedelta(hours=1),
        )

        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["backupId"], manifest["backupId"])
        self.assertEqual(manifest["size"], len(artifact.read_bytes()))
        self.assertEqual(len(manifest["checksum"]), 64)
        self.assertEqual(manifest["artifactFile"], artifact.name)
        serialized = json.dumps(manifest, sort_keys=True).lower()
        for forbidden in ("postgresql://", "database_url", "password", "token", "user payload"):
            self.assertNotIn(forbidden, serialized)
        self.assertFalse((self.root / "backup.manifest.json.partial").exists())

    def test_corruption_wrong_schema_and_expiry_fail_closed(self):
        artifact = self.artifact()
        manifest_path = self.root / "backup.manifest.json"
        write_manifest_atomic(manifest_path, self.completed(artifact, days=1))

        artifact.write_bytes(b"corrupted-backup-bytes")
        with self.assertRaisesRegex(BackupManifestError, "artifactChecksumMismatch"):
            verify_backup_manifest(manifest_path, expected_schema_head="0001", now=NOW)

        artifact.write_bytes(b"encrypted-backup-bytes")
        with self.assertRaisesRegex(BackupManifestError, "schemaHeadMismatch"):
            verify_backup_manifest(manifest_path, expected_schema_head="0002", now=NOW)
        with self.assertRaisesRegex(BackupManifestError, "backupStale"):
            verify_backup_manifest(
                manifest_path,
                expected_schema_head="0001",
                now=NOW + timedelta(hours=2),
                max_age=timedelta(hours=1),
            )
        with self.assertRaisesRegex(BackupManifestError, "backupExpired"):
            verify_backup_manifest(
                manifest_path,
                expected_schema_head="0001",
                now=NOW + timedelta(days=2),
            )

    def test_failed_manifest_never_claims_a_verified_artifact(self):
        manifest = build_failed_manifest(
            backup_id="dj-20260716T140000Z-failed01",
            created_at=NOW,
            schema_head="unknown",
            lsn="unknown",
            encryption_ref="server-local-key:v1",
            retention_class="operationalBackup35d",
            error_code="insufficientSpace",
            owner="backend-operations",
        )

        self.assertEqual(manifest["status"], "failed")
        self.assertIsNone(manifest["checksum"])
        self.assertIsNone(manifest["artifactFile"])
        self.assertEqual(manifest["size"], 0)
        self.assertNotIn("errorMessage", manifest)

    def test_retention_plan_never_removes_last_valid_backup(self):
        old_artifact = self.artifact("old.dump.enc", b"old")
        new_artifact = self.artifact("new.dump.enc", b"new")
        old_manifest_path = self.root / "old.manifest.json"
        new_manifest_path = self.root / "new.manifest.json"
        old_manifest = self.completed(
            old_artifact,
            backup_id="dj-20260501T140000Z-old00001",
            days=1,
        )
        old_manifest["createdAt"] = "2026-05-01T14:00:00+00:00"
        old_manifest["expiresAt"] = "2026-05-02T14:00:00+00:00"
        new_manifest = self.completed(
            new_artifact,
            backup_id="dj-20260716T140000Z-new00001",
            days=35,
        )
        write_manifest_atomic(old_manifest_path, old_manifest)
        write_manifest_atomic(new_manifest_path, new_manifest)

        plan = plan_backup_retention(
            [old_manifest_path, new_manifest_path],
            now=NOW + timedelta(days=1),
            keep_minimum=1,
        )
        only_plan = plan_backup_retention(
            [old_manifest_path],
            now=NOW + timedelta(days=1),
            keep_minimum=1,
        )

        self.assertEqual(plan["action"], "auditOnly")
        self.assertEqual(plan["eligibleBackupIds"], [old_manifest["backupId"]])
        self.assertEqual(only_plan["eligibleBackupIds"], [])
        self.assertEqual(only_plan["protectedBackupIds"], [old_manifest["backupId"]])

    def test_manifest_rejects_missing_encryption_reference_and_unsafe_identity(self):
        artifact = self.artifact()
        with self.assertRaisesRegex(BackupManifestError, "invalidEncryptionRef"):
            build_completed_manifest(
                backup_id="dj-20260716T140000Z-a1b2c3d4",
                created_at=NOW,
                completed_at=NOW,
                schema_head="0001",
                lsn="0/1",
                artifact_path=artifact,
                encryption_ref="",
                retention_class="operationalBackup35d",
                retention_days=35,
            )
        with self.assertRaisesRegex(BackupManifestError, "invalidBackupId"):
            build_completed_manifest(
                backup_id="../../unsafe",
                created_at=NOW,
                completed_at=NOW,
                schema_head="0001",
                lsn="0/1",
                artifact_path=artifact,
                encryption_ref="server-local-key:v1",
                retention_class="operationalBackup35d",
                retention_days=35,
            )


if __name__ == "__main__":
    unittest.main()
