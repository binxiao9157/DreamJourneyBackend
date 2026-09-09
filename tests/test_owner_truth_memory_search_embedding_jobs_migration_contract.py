from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db/migrations/0116_owner_truth_memory_search_embedding_jobs.sql"
MANIFEST_PATH = ROOT / "db/migrations/0116_owner_truth_memory_search_embedding_jobs.json"


class OwnerTruthMemorySearchEmbeddingJobsMigrationContractTests(unittest.TestCase):
    def test_embedding_job_migration_is_additive_checkpoint_bound_and_retry_safe(self) -> None:
        sql = SQL_PATH.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0116")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertTrue(manifest["production"]["requiresEmbeddingEgressApproval"])
        self.assertTrue(manifest["production"]["requiresPostgresIntegrationEvidence"])
        self.assertIn("CREATE TABLE owner_truth.search_document_embedding_jobs", sql)
        self.assertIn("state IN ('queued', 'leased', 'retry', 'ready', 'failed', 'stale')", sql)
        self.assertIn("owner_truth_search_document_embedding_jobs_validate_source", sql)
        self.assertIn("ON DELETE CASCADE", sql)
        self.assertIn("CREATE INDEX owner_truth_search_document_embedding_jobs_claim", sql)
        self.assertNotIn("DROP TABLE", sql.upper())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
