import unittest

from app.domain.owner_truth.contracts import (
    CandidateDecision,
    MemoryKind,
    OwnerTruthContractError,
    PerspectiveType,
    advance_candidate_decision,
    decision_receipt_matches_candidate,
)
from app.domain.owner_truth.ontology import (
    MEMORY_ONTOLOGY_V1,
    OWNER_TRUTH_SCHEMA_VERSION,
    validate_memory_payload,
)


class OwnerTruthDomainTests(unittest.TestCase):
    def test_ontology_v1_contains_required_orthogonal_memory_kinds(self):
        self.assertEqual(
            set(MEMORY_ONTOLOGY_V1),
            {MemoryKind.EXPERIENCE, MemoryKind.KNOWLEDGE, MemoryKind.EMOTION},
        )
        self.assertNotEqual(MemoryKind.EXPERIENCE.value, PerspectiveType.FIRST_PERSON.value)

    def test_known_schema_accepts_kind_specific_payload(self):
        result = validate_memory_payload(
            kind=MemoryKind.KNOWLEDGE,
            payload={"claim": "The family lived near the river."},
            schema_version=OWNER_TRUTH_SCHEMA_VERSION,
        )

        self.assertTrue(result.accepted)
        self.assertFalse(result.quarantined)
        self.assertEqual(result.code, "accepted")

    def test_unknown_schema_is_quarantined_not_coerced(self):
        result = validate_memory_payload(
            kind=MemoryKind.EMOTION,
            payload={"label": "calm"},
            schema_version="future-owner-truth-v2",
        )

        self.assertFalse(result.accepted)
        self.assertTrue(result.quarantined)
        self.assertEqual(result.code, "unknownSchemaVersion")

    def test_known_schema_missing_required_field_is_denied_not_quarantined(self):
        result = validate_memory_payload(
            kind=MemoryKind.EXPERIENCE,
            payload={"summary": ""},
            schema_version=OWNER_TRUTH_SCHEMA_VERSION,
        )

        self.assertFalse(result.accepted)
        self.assertFalse(result.quarantined)
        self.assertEqual(result.code, "missingRequiredField")

    def test_terminal_candidate_decision_cannot_change(self):
        accepted = advance_candidate_decision(
            CandidateDecision.PENDING,
            CandidateDecision.ACCEPTED,
        )
        self.assertEqual(accepted, CandidateDecision.ACCEPTED)
        self.assertEqual(
            advance_candidate_decision(accepted, CandidateDecision.ACCEPTED),
            CandidateDecision.ACCEPTED,
        )
        with self.assertRaises(OwnerTruthContractError):
            advance_candidate_decision(accepted, CandidateDecision.REJECTED)

    def test_decision_receipt_must_match_terminal_candidate_state(self):
        self.assertTrue(
            decision_receipt_matches_candidate(
                candidate_decision=CandidateDecision.ACCEPTED,
                receipt_decision=CandidateDecision.ACCEPTED,
            )
        )
        self.assertFalse(
            decision_receipt_matches_candidate(
                candidate_decision=CandidateDecision.PENDING,
                receipt_decision=CandidateDecision.ACCEPTED,
            )
        )
        self.assertFalse(
            decision_receipt_matches_candidate(
                candidate_decision=CandidateDecision.ACCEPTED,
                receipt_decision=CandidateDecision.REJECTED,
            )
        )


if __name__ == "__main__":
    unittest.main()
