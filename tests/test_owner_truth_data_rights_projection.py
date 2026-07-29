import unittest

from app.services.owner_truth_data_rights import (
    count_owner_truth_data_rights_records,
    empty_owner_truth_data_rights_records,
    read_owner_truth_data_rights_records,
)


class OwnerTruthDataRightsProjectionTests(unittest.TestCase):
    def test_read_projection_is_owner_parameterized_and_read_only(self):
        queries = []

        def fetchall(query, params):
            queries.append((query, params))
            self.assertEqual(params, ("owner-a",))
            if "FROM owner_truth.sources" in query:
                return [{"payload": {"id": "source-a", "contentPayload": {"text": "本人来源"}}}]
            if "FROM owner_truth.memory_candidates" in query:
                return [{"payload": {"candidateId": "candidate-a"}}]
            if "FROM owner_truth.decision_receipts" in query:
                return [{"payload": {"id": "decision-a"}}]
            if "FROM owner_truth.memory_versions" in query:
                return [{"payload": {"memoryVersionId": "version-a"}}]
            if "FROM owner_truth.answers" in query:
                return [{"payload": {"answerId": "answer-a", "citations": []}}]
            if "FROM owner_truth.answer_feedback" in query:
                return [{"payload": {"feedbackId": "feedback-a", "metricEligible": False}}]
            if "FROM owner_truth.correction_requests" in query:
                return [{"payload": {"correctionRequestId": "correction-a"}}]
            if "FROM owner_truth.vaults" in query:
                return [{"payload": {"vaultId": "vault-a", "ownerSubjectId": "owner-a"}}]
            self.fail("unexpected owner truth export query")

        records = read_owner_truth_data_rights_records(
            subject_id="owner-a",
            fetchall=fetchall,
        )

        self.assertEqual(records["vault"][0]["vaultId"], "vault-a")
        self.assertEqual(records["source"][0]["contentPayload"]["text"], "本人来源")
        self.assertEqual(records["candidate"][0]["candidateId"], "candidate-a")
        self.assertEqual(records["decisionReceipt"][0]["id"], "decision-a")
        self.assertEqual(records["memoryVersion"][0]["memoryVersionId"], "version-a")
        self.assertEqual(records["answerCitation"][0]["answerId"], "answer-a")
        self.assertEqual(records["answerFeedback"][0]["feedbackId"], "feedback-a")
        self.assertEqual(records["correction"][0]["correctionRequestId"], "correction-a")
        self.assertEqual(len(queries), 8)
        self.assertTrue(all("DELETE" not in query.upper() for query, _ in queries))

    def test_count_projection_is_owner_parameterized_and_empty_subject_is_zero(self):
        queries = []

        def fetchone(query, params):
            queries.append((query, params))
            self.assertEqual(params, ("owner-a",))
            return {"count": 3}

        counts = count_owner_truth_data_rights_records(
            subject_id="owner-a",
            fetchone=fetchone,
        )

        self.assertEqual(set(counts), {
            "ownerTruthVault",
            "ownerTruthSource",
            "ownerTruthCandidate",
            "ownerTruthDecisionReceipt",
            "ownerTruthMemoryVersion",
            "ownerTruthAnswerCitation",
            "ownerTruthAnswerFeedback",
            "ownerTruthCorrection",
        })
        self.assertTrue(all(value == 3 for value in counts.values()))
        self.assertEqual(len(queries), 8)
        self.assertTrue(all("DELETE" not in query.upper() for query, _ in queries))
        self.assertEqual(
            count_owner_truth_data_rights_records(subject_id="", fetchone=fetchone),
            {key: 0 for key in counts},
        )
        self.assertEqual(empty_owner_truth_data_rights_records()["source"], [])


if __name__ == "__main__":
    unittest.main()
