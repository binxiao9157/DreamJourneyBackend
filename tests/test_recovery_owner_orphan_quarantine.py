import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

from app.db.recovery import RecoveryContractError
from app.db.recovery_owner_orphan_quarantine import (
    RecoveryOwnerOrphanCandidate,
    RecoveryOwnerOrphanTableInventory,
    build_owner_orphan_quarantine_manifest,
)


class RecoveryOwnerOrphanQuarantineTests(unittest.TestCase):
    redaction_key = b"recovery-owner-orphan-test-key"

    def candidate(self):
        return RecoveryOwnerOrphanCandidate(
            schema_name="public",
            table_name="archive_items",
            primary_key_columns=("id",),
            primary_key_values=("private-primary-key-123",),
            owner_id="private-owner-456",
        )

    def inventory(self, **overrides):
        payload = {
            "schema_name": "public",
            "table_name": "archive_items",
            "primary_key_columns": ("id",),
            "orphan_count": 1,
            "candidates": (self.candidate(),),
            "candidate_limit": 100,
        }
        payload.update(overrides)
        return RecoveryOwnerOrphanTableInventory(**payload)

    def manifest(self, inventories, *, redaction_key=None):
        return build_owner_orphan_quarantine_manifest(
            target_database="dj_recovery_orphans_20260730",
            production_database="dreamjourney",
            schema_head="0047",
            table_inventories=inventories,
            redaction_key=redaction_key or self.redaction_key,
        )

    def script_module(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "db"
            / "build_recovery_owner_orphan_quarantine_manifest.py"
        )
        spec = importlib.util.spec_from_file_location(
            "recovery_owner_orphan_quarantine_manifest_script",
            path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_manifest_is_value_free_and_explicitly_non_mutating(self):
        manifest = self.manifest((self.inventory(),))
        rendered = str(manifest)

        self.assertEqual(manifest["status"], "quarantineRequired")
        self.assertEqual(manifest["mode"], "readOnlyInventory")
        self.assertFalse(manifest["automaticMutation"])
        self.assertFalse(manifest["automaticOwnerClaim"])
        self.assertFalse(manifest["automaticDelete"])
        self.assertEqual(manifest["redactionMode"], "hmacSha256KeyNotPersisted")
        self.assertEqual(manifest["operatorActionMap"], "notIncluded")
        self.assertIn("ownerOrphansPresent", manifest["blockers"])
        self.assertIn("ownerOrphanQuarantineRequired", manifest["blockers"])
        self.assertNotIn("private-primary-key-123", rendered)
        self.assertNotIn("private-owner-456", rendered)
        candidate = manifest["tableInventories"][0]["candidates"][0]
        self.assertEqual(
            set(candidate),
            {"primaryKeyColumns", "locatorDigest", "ownerDigest"},
        )
        self.assertEqual(len(candidate["locatorDigest"]), 64)
        self.assertEqual(len(candidate["ownerDigest"]), 64)
        self.assertNotIn("private-primary-key-123", str(self.candidate()))
        self.assertNotIn("private-owner-456", str(self.candidate()))

    def test_manifest_orders_tables_and_has_stable_digest(self):
        profiles = RecoveryOwnerOrphanTableInventory(
            schema_name="public",
            table_name="profiles",
            primary_key_columns=("id",),
            orphan_count=0,
            candidate_limit=100,
        )
        first = self.manifest((profiles, self.inventory()))
        second = self.manifest((self.inventory(), profiles))

        self.assertEqual(first["manifestDigest"], second["manifestDigest"])
        self.assertEqual(
            [entry["table"] for entry in first["tableInventories"]],
            ["public.archive_items", "public.profiles"],
        )

    def test_redaction_key_prevents_fixed_hashes_and_requires_a_secret(self):
        first = self.manifest((self.inventory(),), redaction_key=b"a" * 16)
        second = self.manifest((self.inventory(),), redaction_key=b"b" * 16)

        self.assertNotEqual(
            first["tableInventories"][0]["candidates"][0]["locatorDigest"],
            second["tableInventories"][0]["candidates"][0]["locatorDigest"],
        )
        with self.assertRaisesRegex(
            RecoveryContractError,
            "invalidRecoveryOrphanRedactionKey",
        ):
            self.manifest((self.inventory(),), redaction_key=b"short")

    def test_unlocatable_table_blocks_manual_quarantine_decision(self):
        unlocatable = RecoveryOwnerOrphanTableInventory(
            schema_name="public",
            table_name="legacy_rows",
            primary_key_columns=(),
            orphan_count=2,
            candidate_limit=100,
        )
        manifest = self.manifest((unlocatable,))
        inventory = manifest["tableInventories"][0]

        self.assertEqual(inventory["candidateCollectionStatus"], "unlocatable")
        self.assertIn("public.legacy_rows", manifest["unlocatableTables"])
        self.assertIn("ownerOrphanLocatorUnavailable", manifest["blockers"])

    def test_clear_inventory_stays_clear(self):
        manifest = self.manifest(
            (
                RecoveryOwnerOrphanTableInventory(
                    schema_name="public",
                    table_name="archive_items",
                    primary_key_columns=("id",),
                    orphan_count=0,
                    candidate_limit=100,
                ),
            )
        )

        self.assertEqual(manifest["status"], "clear")
        self.assertEqual(manifest["orphanOwnerCount"], 0)
        self.assertEqual(manifest["blockers"], [])

    def test_rejects_unsafe_targets_and_invalid_candidate_binding(self):
        with self.assertRaisesRegex(RecoveryContractError, "unsafeRecoveryTarget"):
            build_owner_orphan_quarantine_manifest(
                target_database="dreamjourney",
                production_database="dreamjourney",
                schema_head="0047",
                table_inventories=(),
                redaction_key=self.redaction_key,
            )

        candidate = self.candidate()
        with self.assertRaisesRegex(
            RecoveryContractError,
            "recoveryOrphanCandidateTableMismatch",
        ):
            RecoveryOwnerOrphanTableInventory(
                schema_name="public",
                table_name="profiles",
                primary_key_columns=("id",),
                orphan_count=1,
                candidates=(candidate,),
            )

    def test_connected_target_and_existing_directory_are_fail_closed(self):
        script = self.script_module()

        class Cursor:
            def __init__(self, database):
                self.database = database
                self.queries = []

            def execute(self, query):
                self.queries.append(query)

            def fetchone(self):
                return (self.database,)

        accepted = Cursor("dj_recovery_orphans_20260730")
        script._validate_connected_target(
            accepted,
            "dj_recovery_orphans_20260730",
        )
        self.assertEqual(accepted.queries, ["SELECT current_database()"])
        with self.assertRaisesRegex(
            RecoveryContractError,
            "recoveryConnectedTargetMismatch",
        ):
            script._validate_connected_target(
                Cursor("dreamjourney"),
                "dj_recovery_orphans_20260730",
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared"
            shared.mkdir()
            shared.chmod(0o750)
            script._prepare_output_directory(shared / "manifest.json")
            self.assertEqual(stat.S_IMODE(shared.stat().st_mode), 0o750)

            private = root / "new-private"
            script._prepare_output_directory(private / "manifest.json")
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)

    def test_script_is_read_only_and_recovery_scoped(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "db"
            / "build_recovery_owner_orphan_quarantine_manifest.py"
        ).read_text(encoding="utf-8")
        upper_source = source.upper()

        self.assertIn("SET TRANSACTION READ ONLY", source)
        self.assertIn("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ", source)
        self.assertIn("SELECT current_database()", source)
        self.assertIn("validate_recovery_target", source)
        self.assertIn("conninfo_to_dict", source)
        self.assertIn("write_recovery_record_atomic", source)
        self.assertIn("--redaction-key-file", source)
        self.assertIn("if output_directory.exists()", source)
        self.assertIn("information_schema.tables", source)
        self.assertIn("information_schema.columns", source)
        for forbidden in (
            "INSERT ",
            "UPDATE ",
            "DELETE ",
            "CREATE ",
            "DROP ",
            "ALTER ",
            "TRUNCATE ",
        ):
            self.assertNotIn(forbidden, upper_source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
