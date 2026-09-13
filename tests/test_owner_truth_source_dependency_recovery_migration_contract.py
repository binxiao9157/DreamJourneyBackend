from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "db/migrations/0121_owner_truth_source_dependency_recovery.sql"
MANIFEST = SQL.with_suffix(".json")


class OwnerTruthSourceDependencyRecoveryMigrationContractTests(unittest.TestCase):
    def test_multi_source_dependencies_and_durable_recovery_are_additive(self) -> None:
        sql = SQL.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["version"], "0121")
        self.assertFalse(manifest["production"]["rewritesUserFacts"])
        self.assertIn("jsonb_array_elements", sql)
        self.assertIn("evidenceRefs", sql)
        self.assertIn("sourceVersion", sql)
        self.assertIn("OLD.source_version", sql)
        self.assertIn("NEW.source_version", sql)
        self.assertIn("terminal_reason_code", sql)
        self.assertIn("lease_until", sql)
        self.assertIn("available_at", sql)
        self.assertIn("max_attempts", sql)
        self.assertNotIn("UPDATE owner_truth.memory_versions", sql)
        self.assertNotIn("DELETE FROM owner_truth", sql)


if __name__ == "__main__":
    unittest.main()
