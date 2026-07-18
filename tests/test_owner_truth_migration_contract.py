import json
import unittest

from app.db.migrator import default_migrations_dir, load_migrations


class OwnerTruthMigrationContractTests(unittest.TestCase):
    def setUp(self):
        self.migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0011"
        )

    def test_manifest_keeps_owner_truth_inert_and_additive(self):
        metadata = json.loads(self.migration.sql_path.with_suffix(".json").read_text())

        self.assertEqual(self.migration.name, "owner_truth_core")
        self.assertEqual(self.migration.phase, "expand")
        self.assertEqual(self.migration.compatibility, "additive")
        self.assertEqual(metadata["runtimeCompatibility"], "ownerTruthV1SchemaOnly")
        self.assertFalse(metadata["releaseFlags"]["ownerTruthV1Read"])
        self.assertFalse(metadata["releaseFlags"]["ownerTruthV1Write"])

    def test_schema_is_namespaced_and_does_not_mutate_legacy_memories(self):
        sql = self.migration.sql

        self.assertIn("CREATE SCHEMA IF NOT EXISTS owner_truth", sql)
        for relation in (
            "vaults",
            "sources",
            "source_links",
            "extraction_results",
            "memory_candidates",
            "decision_receipts",
            "memories",
            "memory_versions",
            "memory_relations",
            "correction_links",
        ):
            self.assertIn(f"CREATE TABLE owner_truth.{relation}", sql)
        self.assertNotIn("ALTER TABLE memories", sql)
        self.assertNotIn("UPDATE memories", sql)

    def test_database_constraints_cover_v1_invariants(self):
        sql = self.migration.sql

        self.assertIn("owner_truth_memory_versions_one_current", sql)
        self.assertIn("owner_truth_memory_requires_current_version", sql)
        self.assertIn("owner_truth_memory_version_current_integrity", sql)
        self.assertIn("owner_truth_memory_relations_no_cycle", sql)
        self.assertIn("owner_truth_decision_receipts_no_update", sql)
        self.assertIn("owner_truth_decision_receipts_no_delete", sql)
        self.assertIn("owner_truth_decision_receipts_validate_candidate", sql)
        self.assertIn("ON DELETE RESTRICT", sql)
        self.assertIn("owner truth record authority epoch is stale", sql)


if __name__ == "__main__":
    unittest.main()
