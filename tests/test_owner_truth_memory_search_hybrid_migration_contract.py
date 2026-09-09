from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "db/migrations/0113_owner_truth_memory_search_hybrid_pgvector.sql"
MANIFEST_PATH = ROOT / "db/migrations/0113_owner_truth_memory_search_hybrid_pgvector.json"


class OwnerTruthMemorySearchHybridMigrationContractTests(unittest.TestCase):
    def test_pgvector_migration_is_additive_and_binds_embeddings_to_current_documents(self) -> None:
        sql = SQL_PATH.read_text(encoding="utf-8")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0113")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertTrue(manifest["production"]["requiresEmbeddingEgressApproval"])
        self.assertTrue(manifest["production"]["requiresSyntheticChineseEvaluation"])
        self.assertIn("CREATE EXTENSION IF NOT EXISTS vector", sql)
        self.assertIn("CREATE TABLE owner_truth.search_document_embeddings", sql)
        self.assertIn("embedding vector NOT NULL", sql)
        self.assertIn("vector_dims(embedding) = embedding_dimensions", sql)
        self.assertIn("USING hnsw", sql)
        self.assertIn("owner_truth_search_document_embeddings_validate_source", sql)
        self.assertIn("ON DELETE CASCADE", sql)
        self.assertNotIn("DROP TABLE", sql.upper())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
