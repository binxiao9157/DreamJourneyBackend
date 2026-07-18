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

    def test_memory_projection_migration_is_additive_and_default_off(self):
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0016"
        )
        metadata = json.loads(migration.sql_path.with_suffix(".json").read_text())

        self.assertEqual(migration.name, "owner_truth_memory_projection")
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(metadata["runtimeCompatibility"], "ownerTruthV1MemoryProjectionShadow")
        self.assertFalse(metadata["releaseFlags"]["memoryProjectionV1"])
        self.assertIn("memory_projection_checkpoints", migration.sql)
        self.assertIn("memory_projection_entries", migration.sql)
        self.assertIn("projection_source", migration.sql)
        self.assertIn("authority_epoch", migration.sql)
        self.assertIn("NEW.payload - ARRAY['content', 'evidenceRefs']", migration.sql)

    def test_memory_projection_trigger_fix_preserves_applied_migration_history(self):
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0017"
        )
        metadata = json.loads(migration.sql_path.with_suffix(".json").read_text())

        self.assertEqual(migration.name, "owner_truth_memory_projection_trigger_fix")
        self.assertEqual(migration.phase, "contract")
        self.assertEqual(migration.compatibility, "backwardCompatible")
        self.assertEqual(metadata["runtimeCompatibility"], "ownerTruthV1MemoryProjectionTriggerFix")
        self.assertFalse(metadata["releaseFlags"]["memoryProjectionV1"])
        self.assertIn("version_schema_version TEXT", migration.sql)
        self.assertIn("version_schema_version,", migration.sql)
        self.assertIn("NEW.content_schema_version IS DISTINCT FROM version_schema_version", migration.sql)

    def test_answer_citation_migration_is_hash_only_and_default_off(self):
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0018"
        )
        metadata = json.loads(migration.sql_path.with_suffix(".json").read_text())

        self.assertEqual(migration.name, "owner_truth_answer_citations")
        self.assertEqual(migration.phase, "expand")
        self.assertEqual(metadata["runtimeCompatibility"], "ownerTruthV1AnswerCitationShadow")
        self.assertFalse(metadata["releaseFlags"]["answerCitationV1"])
        self.assertIn("CREATE TABLE owner_truth.answers", migration.sql)
        self.assertIn("CREATE TABLE owner_truth.answer_citations", migration.sql)
        self.assertIn("owner_truth_answer_citations_validate_memory", migration.sql)
        self.assertIn("owner_truth_answers_no_update", migration.sql)
        self.assertIn("owner_truth_answer_citations_no_delete", migration.sql)
        self.assertIn("command_payload_hash", migration.sql)

    def test_answer_citation_trigger_fix_preserves_applied_migration_history(self):
        migration = next(
            item
            for item in load_migrations(default_migrations_dir())
            if item.version == "0019"
        )
        metadata = json.loads(migration.sql_path.with_suffix(".json").read_text())

        self.assertEqual(migration.name, "owner_truth_answer_citation_trigger_fix")
        self.assertEqual(migration.phase, "contract")
        self.assertEqual(migration.compatibility, "backwardCompatible")
        self.assertEqual(metadata["runtimeCompatibility"], "ownerTruthV1AnswerCitationTriggerFix")
        self.assertFalse(metadata["releaseFlags"]["answerCitationV1"])
        self.assertIn("CREATE OR REPLACE FUNCTION owner_truth.validate_answer_citation", migration.sql)
        self.assertIn("memory_version.version_number", migration.sql)
        self.assertIn("version_number_value", migration.sql)


if __name__ == "__main__":
    unittest.main()
