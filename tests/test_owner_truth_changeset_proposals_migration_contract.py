import json
from pathlib import Path
import unittest
from uuid import uuid4

from app.services.owner_truth_candidate_review import (
    PostgresOwnerTruthCandidateReviewRepository,
    _receipt_changeset_binding_matches,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SQL = ROOT / "db/migrations/0112_owner_truth_changeset_proposals.sql"
MIGRATION_MANIFEST = ROOT / "db/migrations/0112_owner_truth_changeset_proposals.json"


class OwnerTruthChangeSetProposalMigrationContractTests(unittest.TestCase):
    """Static guard for the additive proposal-binding migration.

    A disposable PostgreSQL migration run remains required before release; this
    test makes accidental removal of the fail-closed owner-review contract
    visible in ordinary local CI.
    """

    def test_proposal_rows_are_immutable_and_receipts_bind_the_seen_diff(self) -> None:
        manifest = json.loads(MIGRATION_MANIFEST.read_text(encoding="utf-8"))
        sql = MIGRATION_SQL.read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0112")
        self.assertEqual(manifest["phase"], "expand")
        self.assertEqual(manifest["compatibility"], "additive")
        self.assertTrue(manifest["production"]["requiresPostgresIntegrationEvidence"])
        self.assertIn("CREATE TABLE owner_truth.memory_changeset_proposals", sql)
        self.assertIn("CREATE TABLE owner_truth.memory_changeset_proposal_operations", sql)
        self.assertIn("depends_on_operation_indexes INTEGER[]", sql)
        self.assertIn("payload JSONB NOT NULL", sql)
        self.assertIn("owner_truth_memory_changeset_proposals_immutable", sql)
        self.assertIn("owner_truth_memory_changeset_proposal_operations_immutable", sql)
        self.assertIn("expected_change_set_id UUID", sql)
        self.assertIn("expected_proposal_hash TEXT", sql)
        self.assertIn("owner_truth_decision_receipts_changeset_binding_paired", sql)
        self.assertIn("owner_truth_decision_receipts_changeset_binding_exists", sql)
        self.assertIn("REFERENCES owner_truth.memory_changeset_proposals", sql)
        self.assertNotIn("DROP TABLE", sql.upper())

    def test_dependency_indexes_are_bound_as_a_postgres_array_not_jsonb(self) -> None:
        params = PostgresOwnerTruthCandidateReviewRepository._adapt_changeset_operation_params(
            (["candidate", "assertion"], ["statement"]),
            dependencies=[0, 2],
        )

        self.assertEqual(params[-1], [0, 2])
        self.assertEqual(type(params[-1]), list)

    def test_operation_insert_conflict_key_matches_the_composite_primary_key(self) -> None:
        service_source = (
            ROOT / "app/services/owner_truth_candidate_review.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "ON CONFLICT (vault_id, proposal_id, operation_index) DO NOTHING",
            service_source,
        )
        self.assertNotIn(
            "ON CONFLICT (proposal_id, operation_index) DO NOTHING",
            service_source,
        )

    def test_activation_receipt_read_keeps_the_reviewed_candidate_version(self) -> None:
        class _Cursor:
            statement = ""

            def execute(self, statement, _params) -> None:
                self.statement = " ".join(str(statement).split())

            def fetchone(self):
                return None

        cursor = _Cursor()

        PostgresOwnerTruthCandidateReviewRepository._receipt_by_id(
            cursor,
            vault_id="vault-contract",
            receipt_id="receipt-contract",
        )

        self.assertIn("expected_candidate_version", cursor.statement)

    def test_database_uuid_and_equal_domain_string_share_one_changeset_identity(self) -> None:
        change_set_id = uuid4()
        proposal_hash = "a" * 64

        self.assertTrue(
            _receipt_changeset_binding_matches(
                {
                    "expected_change_set_id": change_set_id,
                    "expected_proposal_hash": proposal_hash,
                },
                change_set_id=str(change_set_id),
                proposal_hash=proposal_hash,
            )
        )
        self.assertFalse(
            _receipt_changeset_binding_matches(
                {
                    "expected_change_set_id": uuid4(),
                    "expected_proposal_hash": proposal_hash,
                },
                change_set_id=str(change_set_id),
                proposal_hash=proposal_hash,
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
