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

    def test_create_source_migration_preserves_immutable_payload_and_receipts(self):
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0012"
        )
        metadata = json.loads(migration.sql_path.with_suffix(".json").read_text())

        self.assertEqual(migration.name, "owner_truth_source_commands")
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(metadata["runtimeCompatibility"], "ownerTruthV1ShadowSourceWrite")
        self.assertIn("ADD COLUMN IF NOT EXISTS content_payload", migration.sql)
        self.assertIn("CREATE TABLE owner_truth.source_command_receipts", migration.sql)
        self.assertIn("owner_truth_source_command_receipts_no_update", migration.sql)
        self.assertIn("owner_truth_source_command_receipts_no_delete", migration.sql)
        self.assertIn("owner_truth_sources_payload_immutable", migration.sql)

    def test_candidate_decision_migration_keeps_terminal_review_append_only(self):
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0014"
        )
        metadata = json.loads(migration.sql_path.with_suffix(".json").read_text())

        self.assertEqual(migration.name, "owner_truth_candidate_decisions")
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(metadata["runtimeCompatibility"], "ownerTruthV1CandidateReviewShadow")
        self.assertFalse(metadata["releaseFlags"]["candidateReviewV1"])
        self.assertIn("command_id_hash", migration.sql)
        self.assertIn("expected_candidate_version", migration.sql)
        self.assertIn("candidate_before_hash", migration.sql)
        self.assertIn("candidate_after_hash", migration.sql)
        self.assertIn("candidate_decision_values", migration.sql)
        self.assertIn("owner_truth_corrected_decision_requires_value", migration.sql)
        self.assertIn("owner_truth_candidate_decision_values_no_update", migration.sql)
        self.assertIn("owner_truth_decision_receipts_vault_command_id_hash_unique", migration.sql)

    def test_memory_activation_migration_binds_one_memory_to_one_receipt(self):
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0015"
        )
        metadata = json.loads(migration.sql_path.with_suffix(".json").read_text())

        self.assertEqual(migration.name, "owner_truth_memory_activation")
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(metadata["runtimeCompatibility"], "ownerTruthV1DecisionMemoryShadow")
        self.assertFalse(metadata["releaseFlags"]["memoryActivationV1"])
        self.assertIn("decision_receipt_id", migration.sql)
        self.assertIn("owner_truth_memories_one_per_decision_receipt", migration.sql)
        self.assertIn("owner_truth_memories_validate_decision_receipt", migration.sql)
        self.assertIn("owner_truth_memories_decision_receipt_immutable", migration.sql)


if __name__ == "__main__":
    unittest.main()
