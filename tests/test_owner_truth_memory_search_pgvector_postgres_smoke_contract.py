from __future__ import annotations

from pathlib import Path
import unittest


ROOT_DIR = Path(__file__).resolve().parents[1]
SMOKE = ROOT_DIR / "scripts/backend-owner-truth-memory-search-pgvector-postgres-smoke.py"
RUNNER = ROOT_DIR / "scripts/run-backend-owner-truth-memory-search-pgvector-postgres-smoke.sh"


class OwnerTruthMemorySearchPgvectorPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_is_disposable_value_safe_and_covers_required_pipeline(self) -> None:
        source = SMOKE.read_text(encoding="utf-8")

        self.assertIn("CREATE DATABASE", source)
        self.assertIn("DROP DATABASE IF EXISTS", source)
        self.assertIn("SyntheticEmbeddingProvider", source)
        self.assertIn("build_configured_embedding_provider", source)
        self.assertIn("OWNER_TRUTH_REAL_EMBEDDING_PG_SMOKE_APPROVED", source)
        self.assertIn('provider_mode == "configured"', source)
        self.assertIn('"realModelEvidence": provider_mode == "configured"', source)
        self.assertIn("OwnerTruthMemoryProjectionService", source)
        self.assertIn("OwnerTruthMemorySearchDocumentProjectionService", source)
        self.assertIn("OwnerTruthMemorySearchEmbeddingWorkerRuntime", source)
        self.assertIn("retryWait", source)
        self.assertIn("pgvector-smoke-restarted-worker", source)
        self.assertIn("OwnerTruthMemorySearchReadService", source)
        self.assertIn("owner_truth_postgres_hybrid_search_sql", source)
        self.assertIn("EXPLAIN (COSTS FALSE, FORMAT JSON)", source)
        self.assertIn("owner_truth_search_document_embeddings_bge_m3_hnsw", source)
        self.assertIn('stale_read.state == "rebuilding"', source)
        self.assertIn("supersedes_version_id", source)
        self.assertIn("superseded vectors must never survive", source)
        self.assertIn('"privateVaultRead": False', source)
        self.assertIn('"credentialRetained": False', source)
        self.assertNotIn("settings.database_url)", source.split("create_database", 1)[0])

    def test_runner_requires_an_explicit_database_url(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")

        self.assertIn("DATABASE_URL must point to an isolated PostgreSQL server", source)
        self.assertIn("backend-owner-truth-memory-search-pgvector-postgres-smoke.py", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
