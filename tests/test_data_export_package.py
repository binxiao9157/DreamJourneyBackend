from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

from app.services.data_export_package import (
    DataExportPackageCancelled,
    DataExportPackageError,
    materialize_data_export_package,
)


class DataExportPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.payload = b"private-image-bytes"
        self.artifact = {
            "schemaVersion": 1,
            "manifest": {"jobId": "dej_package_test"},
            "dataExport": {
                "machineReadable": {
                    "permissionManifest": {
                        "ownerUserId": "owner-package",
                        "resources": [{"type": "profile", "id": "owner-package"}],
                    }
                }
            },
        }
        self.media = {
            "sourceObjectId": "media-source-1",
            "vaultId": "vault-package",
            "ownerSubjectId": "owner-package",
            "state": "verified",
            "accessState": "available",
            "fileName": "memory.jpg",
            "fileSizeBytes": len(self.payload),
            "contentSha256": sha256(self.payload).hexdigest(),
            "contentType": "image/jpeg",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_verifiable_package_and_explicit_cleanup_removes_temp_file(self) -> None:
        result = materialize_data_export_package(
            job_id="dej_package_test",
            owner_user_id="owner-package",
            artifact=self.artifact,
            media_objects=[self.media],
            media_reader=lambda _item: self.payload,
            temp_root=self.temporary.name,
        )
        path = Path(result.path)
        self.assertTrue(path.exists())
        self.assertEqual(result.media_count, 1)
        self.assertEqual(len(result.content_sha256), 64)
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            self.assertIn("data-export.json", names)
            self.assertIn("permissions.json", names)
            self.assertIn("package-manifest.json", names)
            media_name = next(name for name in names if name.startswith("media/"))
            self.assertEqual(archive.read(media_name), self.payload)
            manifest = json.loads(archive.read("package-manifest.json"))
            self.assertEqual(manifest["mediaCount"], 1)
            self.assertNotIn("storageKey", json.dumps(manifest))
        result.cleanup()
        self.assertFalse(path.exists())

    def test_owner_mismatch_integrity_failure_limit_and_cancel_all_remove_partial_file(self) -> None:
        fixtures = []
        fixtures.append((
            {**self.media, "ownerSubjectId": "other"},
            lambda _item: self.payload,
            1024,
            None,
            "dataExportMediaOwnerMismatch",
        ))
        fixtures.append((
            self.media,
            lambda _item: b"tampered",
            1024,
            None,
            "dataExportMediaIntegrityMismatch",
        ))
        fixtures.append((
            self.media,
            lambda _item: self.payload,
            8,
            None,
            "dataExportPackageTooLarge",
        ))
        for media, reader, limit, cancelled, code in fixtures:
            with self.subTest(code=code):
                with self.assertRaises(DataExportPackageError) as raised:
                    materialize_data_export_package(
                        job_id="dej_package_test",
                        owner_user_id="owner-package",
                        artifact=self.artifact,
                        media_objects=[media],
                        media_reader=reader,
                        temp_root=self.temporary.name,
                        max_package_bytes=limit,
                        cancelled=cancelled,
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

        with self.assertRaises(DataExportPackageCancelled):
            materialize_data_export_package(
                job_id="dej_package_test",
                owner_user_id="owner-package",
                artifact=self.artifact,
                media_objects=[self.media],
                media_reader=lambda _item: self.payload,
                temp_root=self.temporary.name,
                cancelled=lambda: True,
            )
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])

    def test_revoked_and_unverified_media_are_not_read(self) -> None:
        calls = 0

        def reader(_item):
            nonlocal calls
            calls += 1
            return self.payload

        result = materialize_data_export_package(
            job_id="dej_package_test",
            owner_user_id="owner-package",
            artifact=self.artifact,
            media_objects=[
                {**self.media, "accessState": "accessRevoked"},
                {**self.media, "state": "uploadPending"},
            ],
            media_reader=reader,
            temp_root=self.temporary.name,
        )
        self.assertEqual(calls, 0)
        self.assertEqual(result.media_count, 0)
        result.cleanup()

    def test_storage_identifiers_cannot_create_zip_traversal_paths(self) -> None:
        result = materialize_data_export_package(
            job_id="dej_package_test",
            owner_user_id="owner-package",
            artifact=self.artifact,
            media_objects=[
                {
                    **self.media,
                    "sourceObjectId": "../../private-source",
                    "fileName": "../memory.jpg",
                }
            ],
            media_reader=lambda _item: self.payload,
            temp_root=self.temporary.name,
        )
        try:
            with zipfile.ZipFile(result.path) as archive:
                media_name = next(
                    name for name in archive.namelist() if name.startswith("media/")
                )
                self.assertNotIn("..", media_name)
                self.assertFalse(media_name.startswith("/"))
        finally:
            result.cleanup()


if __name__ == "__main__":
    unittest.main()
