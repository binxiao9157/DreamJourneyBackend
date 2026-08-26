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
    OWNER_TRUTH_CURRENT_SCHEMA_VERSION,
    OWNER_TRUTH_SCHEMA_VERSION,
    OWNER_TRUTH_SCHEMA_VERSION_V2,
    OWNER_TRUTH_SCHEMA_VERSION_V3,
    OWNER_TRUTH_SCHEMA_VERSION_V4,
    canonicalize_memory_payload,
    empty_memory_facets,
    enrich_memory_payload_v4,
    flatten_memory_facets,
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

    def test_owner_truth_v2_accepts_typed_facets_and_flattens_only_values(self):
        facets = empty_memory_facets(confidence=0.82)
        facets["people"] = [
            {
                "value": "外公",
                "evidenceMode": "ownerStated",
                "confidence": 1.0,
                "subjectId": "must-not-be-authority",
            }
        ]
        facets["relationships"] = [
            {
                "value": "祖孙",
                "evidenceMode": "inferred",
                "confidence": 0.61,
                "grantId": "must-not-be-authority",
            }
        ]
        result = validate_memory_payload(
            kind=MemoryKind.EXPERIENCE,
            payload={"summary": "小时候常和外公散步。", "facets": facets},
            schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
        )

        self.assertTrue(result.accepted)
        self.assertFalse(result.quarantined)
        self.assertEqual(OWNER_TRUTH_CURRENT_SCHEMA_VERSION, OWNER_TRUTH_SCHEMA_VERSION_V4)
        terms = flatten_memory_facets(facets)
        self.assertIn("people:外公", terms)
        self.assertIn("relationships:祖孙", terms)
        self.assertNotIn("must-not-be-authority", " ".join(terms))
        self.assertNotIn("ownerstated", " ".join(terms))

    def test_owner_truth_v2_rejects_unlabelled_inference_and_invalid_confidence(self):
        facets = empty_memory_facets(confidence=0.7)
        facets["places"] = [{"value": "河边", "confidence": 0.7}]
        missing_mode = validate_memory_payload(
            kind=MemoryKind.EXPERIENCE,
            payload={"summary": "小时候常去河边。", "facets": facets},
            schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
        )
        self.assertFalse(missing_mode.accepted)
        self.assertEqual(missing_mode.code, "invalidFacetEvidenceMode")

        facets = empty_memory_facets(confidence=1.5)
        invalid_confidence = validate_memory_payload(
            kind=MemoryKind.KNOWLEDGE,
            payload={"claim": "陪伴很重要。", "facets": facets},
            schema_version=OWNER_TRUTH_SCHEMA_VERSION_V2,
        )
        self.assertFalse(invalid_confidence.accepted)
        self.assertEqual(invalid_confidence.code, "invalidFacetConfidence")

    def test_v1_remains_readable_without_synthetic_facets(self):
        payload = {"label": "怀念"}
        result = validate_memory_payload(
            kind=MemoryKind.EMOTION,
            payload=payload,
            schema_version=OWNER_TRUTH_SCHEMA_VERSION,
        )

        self.assertTrue(result.accepted)
        self.assertNotIn("facets", payload)

    def test_v4_correction_rebuilds_semantic_projection_before_validation(self):
        payload = enrich_memory_payload_v4(
            kind=MemoryKind.EXPERIENCE,
            payload={
                "event": "大学毕业后，我开始每天写日记。",
                "facets": {
                    **empty_memory_facets(confidence=1.0),
                    "habits": [
                        {
                            "value": "每天写日记",
                            "evidenceMode": "ownerStated",
                            "confidence": 1.0,
                        }
                    ],
                },
            },
        )
        payload["event"] = "毕业后，我养成了每天写日记的习惯。"

        stale = validate_memory_payload(
            kind=MemoryKind.EXPERIENCE,
            payload=payload,
            schema_version=OWNER_TRUTH_SCHEMA_VERSION_V4,
        )
        self.assertFalse(stale.accepted)
        self.assertEqual(stale.code, "inconsistentSemanticProjection")

        canonical = canonicalize_memory_payload(
            kind=MemoryKind.EXPERIENCE,
            payload=payload,
            schema_version=OWNER_TRUTH_SCHEMA_VERSION_V4,
        )
        self.assertEqual(canonical["semantic"]["narrative"], canonical["event"])
        self.assertIn("habit", canonical["semantic"]["facets"])
        self.assertTrue(
            validate_memory_payload(
                kind=MemoryKind.EXPERIENCE,
                payload=canonical,
                schema_version=OWNER_TRUTH_SCHEMA_VERSION_V4,
            ).accepted
        )

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
