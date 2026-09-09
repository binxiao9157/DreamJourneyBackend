from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backend-owner-truth-b-migration-execution-postgres-smoke.py"
RUNNER = ROOT / "scripts/run-backend-owner-truth-b-migration-execution-postgres-smoke.sh"


class OwnerTruthBMigrationExecutionPostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_uses_disposable_database_and_production_store(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("CREATE DATABASE", source)
        self.assertIn("DROP DATABASE IF EXISTS", source)
        self.assertIn("PostgresMigrator", source)
        self.assertIn("PostgresStore", source)
        self.assertIn("OwnerTruthBMigrationExecutionService", source)
        self.assertIn("ThreadPoolExecutor", source)
        self.assertIn("synthetic failure after Source and effect", source)
        self.assertIn('"formalMemories"', source)
        self.assertIn('"formalMemoryWriteCount": 0', source)
        self.assertNotIn("InMemoryStore", source)

    def test_runner_requires_explicit_isolated_database_url(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")

        self.assertIn('if [[ -z "${DATABASE_URL:-}" ]]', source)
        self.assertIn("isolated PostgreSQL server", source)
        self.assertIn("backend-owner-truth-b-migration-execution-postgres-smoke.py", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
