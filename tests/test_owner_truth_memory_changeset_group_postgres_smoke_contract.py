from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backend-owner-truth-memory-changeset-group-postgres-smoke.py"
RUNNER = ROOT / "scripts/run-backend-owner-truth-memory-changeset-group-postgres-smoke.sh"


class OwnerTruthMemoryChangeSetGroupPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_uses_disposable_database_and_real_postgres_stores(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("create_database(admin_dsn, database_name)", source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn("PostgresMigrator", source)
        self.assertIn("PostgresStore", source)
        self.assertIn("ThreadPoolExecutor", source)
        self.assertIn("OwnerTruthMemoryChangeSetGroupReviewService", source)
        self.assertIn("pgvector extension must be installed", source)
        self.assertNotIn("InMemoryOwnerTruthCandidateReviewRepository", source)

    def test_smoke_covers_required_atomic_failure_and_replay_paths(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('("decision", "activation", "effect")', source)
        self.assertIn('outcomes.count("created") == 1', source)
        self.assertIn('outcomes.count("conflict") == 1', source)
        self.assertIn('replayed.outcome == "deduplicated"', source)
        self.assertIn('counts["memoryRevision"] == 0', source)

    def test_runner_requires_an_explicit_database_url(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("DATABASE_URL:?DATABASE_URL is required", source)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
