import unittest
from pathlib import Path


class RecoveryIntegrityAuditContractTests(unittest.TestCase):
    def test_recovery_audit_is_schema_discovered_and_domain_bound(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "db"
            / "verify_recovery_integrity.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("OWNER_TABLES", source)
        self.assertIn("information_schema.columns", source)
        self.assertIn("information_schema.tables", source)
        self.assertIn("checkedTables", source)
        self.assertIn("orphanOwnerCountsByTable", source)
        self.assertIn("purgedOwnerViolationCountsByTable", source)
        self.assertIn("LEFT JOIN public.users owner", source)
        self.assertIn("ownerTruthVaultScope", source)
        self.assertIn("asyncEffectsOperationScope", source)
        self.assertIn("worker_loss_observations", source)
        self.assertIn("LEFT JOIN owner_truth.vaults vault", source)
        self.assertIn("LEFT JOIN async_effects.operations operation", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
