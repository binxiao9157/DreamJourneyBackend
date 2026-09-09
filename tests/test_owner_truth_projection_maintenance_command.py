import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/rebuild-owner-truth-derived-projections.py"
SPEC = importlib.util.spec_from_file_location("projection_maintenance_command", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class OwnerTruthProjectionMaintenanceCommandTests(unittest.TestCase):
    def target(self):
        return MODULE.ProjectionTarget(
            vault_id="private-vault-id",
            owner_subject_id="private-owner-id",
            authority_epoch=3,
            memory_state="rebuilding",
            search_state="rebuilding",
        )

    def test_dry_run_is_value_free_and_never_rebuilds(self):
        calls = []
        report = MODULE.run_maintenance(
            apply=False,
            limit=10,
            target_supplier=lambda _limit: ((self.target(),), 1),
            target_rebuilder=lambda target: calls.append(target),
        )

        self.assertEqual(report["status"], "pending")
        self.assertEqual(report["eligibleCount"], 1)
        self.assertEqual(calls, [])
        rendered = json.dumps(report)
        self.assertNotIn("private-vault-id", rendered)
        self.assertNotIn("private-owner-id", rendered)

    def test_apply_rebuilds_once_and_verifies_no_remaining_target(self):
        target = self.target()
        supplies = iter([((target,), 1), ((), 0)])
        report = MODULE.run_maintenance(
            apply=True,
            limit=10,
            target_supplier=lambda _limit: next(supplies),
            target_rebuilder=lambda _target: {
                "memoryOutcome": "rebuilt",
                "searchOutcome": "rebuilt",
                "memoryRevision": 7,
                "memoryEntryCount": 19,
                "searchDocumentCount": 19,
                "checkpoint": "a" * 64,
            },
        )

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["rebuiltCount"], 1)
        self.assertEqual(report["remainingEligibleCount"], 0)

    def test_apply_reports_only_error_type_and_hashed_target(self):
        target = self.target()
        supplies = iter([((target,), 1), ((target,), 1)])

        def fail(_target):
            raise RuntimeError("private-vault-id must never be rendered")

        report = MODULE.run_maintenance(
            apply=True,
            limit=10,
            target_supplier=lambda _limit: next(supplies),
            target_rebuilder=fail,
        )

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failureCount"], 1)
        self.assertEqual(report["failures"][0]["errorType"], "RuntimeError")
        rendered = json.dumps(report)
        self.assertNotIn("private-vault-id", rendered)
        self.assertNotIn("private-owner-id", rendered)

    def test_no_eligible_target_is_idempotently_ready(self):
        report = MODULE.run_maintenance(
            apply=True,
            limit=10,
            target_supplier=lambda _limit: ((), 0),
            target_rebuilder=lambda _target: self.fail("must not rebuild"),
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["rebuiltCount"], 0)


if __name__ == "__main__":
    unittest.main()
