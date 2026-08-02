import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/backend-owner-truth-candidate-route-postgres-smoke.py"
RUNNER = ROOT / "scripts/run-backend-owner-truth-candidate-route-postgres-smoke.sh"


class OwnerTruthCandidateRoutePostgresSmokeContractTests(unittest.TestCase):
    def test_smoke_is_isolated_and_covers_qa_and_closed_pilot_route_boundaries(self):
        source = SCRIPT.read_text(encoding="utf-8")
        runner = RUNNER.read_text(encoding="utf-8")

        self.assertIn('database_name = f"dj_owner_truth_candidate_route_smoke_', source)
        self.assertIn("drop_database(admin_dsn, database_name)", source)
        self.assertIn("TestClient(main_module.app)", source)
        self.assertIn("OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = False", source)
        self.assertIn("OWNER_TRUTH_CANDIDATE_REVIEW_QA_ENABLED = True", source)
        self.assertIn('"/v2/vaults/{vault_id}/candidates"', source)
        self.assertIn("candidates/{candidate_id}/decisions", source)
        self.assertIn("candidate route must remain hidden by default", source)
        self.assertIn("candidate route must require the QA header", source)
        self.assertIn("owner QA inbox must return the reviewable candidate preview", source)
        self.assertIn("cross-vault candidate lookup must be denied", source)
        self.assertIn("non-owner candidate lookup must be denied", source)
        self.assertIn("same command must replay instead of writing again", source)
        self.assertIn("terminal candidate must leave pending inbox", source)
        self.assertIn("proposal_summary not in str(decision_body)", source)
        self.assertIn("ownerTextCaptureV1", source)
        self.assertIn("OwnerTruthCandidateExtractionWorkerRuntime", source)
        self.assertIn("OwnerTruthMemoryProjectionWorkerRuntime", source)
        self.assertIn("closed-pilot Source must persist one extraction request", source)
        self.assertIn("closed-pilot worker must create one pending Candidate from Source", source)
        self.assertIn("closed-pilot Candidate review must not use a QA header", source)
        self.assertIn("accepted closed-pilot Candidate must rebuild confirmed Projection", source)
        self.assertIn("closed-pilot Context must use confirmed Projection authority", source)
        self.assertIn("closedPilotSourceCandidate=true", source)
        self.assertIn("backend-owner-truth-candidate-route-postgres-smoke.py", runner)
        self.assertIn("DATABASE_URL is required", runner)


if __name__ == "__main__":
    unittest.main()
