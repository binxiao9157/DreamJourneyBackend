from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0041_owner_truth_interview_decision_audits.sql"
MIGRATION_MANIFEST = MIGRATION_SQL.with_suffix(".json")


class OwnerTruthInterviewDecisionAuditMigrationContractTests(unittest.TestCase):
    def test_audits_are_additive_append_only_and_value_minimized(self) -> None:
        sql = MIGRATION_SQL.read_text(encoding="utf-8")
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0041")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertFalse(manifest["releaseFlags"]["ownerTruthInterviewDecisionAudit"])
        self.assertIn("CREATE TABLE owner_truth.interview_decisions", sql)
        self.assertIn("UNIQUE (vault_id, command_id_hash)", sql)
        self.assertIn("UNIQUE (vault_id, message_id)", sql)
        self.assertIn("validate_interview_decision_audit", sql)
        self.assertIn("owner_truth_interview_decisions_no_update", sql)
        self.assertIn("owner_truth_interview_decisions_no_delete", sql)
        self.assertIn("message_author IS DISTINCT FROM 'owner'", sql)
        self.assertIn("message_kind IS DISTINCT FROM 'narrative'", sql)
        self.assertNotIn("transcript TEXT", sql)
        self.assertNotIn("message_text", sql)
        self.assertNotIn("provider_payload", sql)
        self.assertNotIn("content_payload", sql)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
