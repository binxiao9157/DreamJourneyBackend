import unittest

from app.domain.narrative.contracts import BookProjectType, NarrativeNarratorType
from app.domain.narrative.fact_guard import (
    FactGuardReason,
    FactGuardRejected,
    FactLedgerEntry,
    NarrativeClaim,
    validate_claims,
)


class NarrativeFactGuardTests(unittest.TestCase):
    def setUp(self):
        self.ledger = {
            "memory-confirmed": FactLedgerEntry(
                memory_version_id="memory-confirmed",
                content_hash="hash-confirmed",
                text="在北方的一所大学学习计算机",
            ),
            "memory-uncertain": FactLedgerEntry(
                memory_version_id="memory-uncertain",
                content_hash="hash-uncertain",
                text="似乎是在毕业后的第二年",
                uncertain=True,
            ),
        }

    def assert_rejected(self, claim, reason, *, project_type=BookProjectType.SELF_AUTOBIOGRAPHY,
                        narrator_type=NarrativeNarratorType.SELF_FIRST_PERSON):
        with self.assertRaises(FactGuardRejected) as caught:
            validate_claims(
                claims=(claim,),
                ledger=self.ledger,
                project_type=project_type,
                narrator_type=narrator_type,
            )
        self.assertIn(reason, caught.exception.reasons)

    def test_every_claim_requires_a_snapshot_memory_reference(self):
        self.assert_rejected(
            NarrativeClaim("claim-1", "我在北方求学。", ()),
            FactGuardReason.MISSING_MEMORY_REFERENCE,
        )

    def test_direct_quote_requires_quote_evidence(self):
        self.assert_rejected(
            NarrativeClaim(
                "claim-1",
                "老师说：\"你一定会成功。\"",
                ("memory-confirmed",),
                direct_quote=True,
            ),
            FactGuardReason.UNSUPPORTED_DIRECT_QUOTE,
        )

    def test_quote_detection_does_not_trust_provider_flags(self):
        self.assert_rejected(
            NarrativeClaim(
                "claim-1",
                "老师说：“你一定会成功。”",
                ("memory-confirmed",),
                direct_quote=False,
            ),
            FactGuardReason.UNSUPPORTED_DIRECT_QUOTE,
        )

    def test_uncertain_memory_cannot_be_rendered_as_certain(self):
        self.assert_rejected(
            NarrativeClaim(
                "claim-1",
                "那件事确定发生在毕业后的第二年。",
                ("memory-uncertain",),
            ),
            FactGuardReason.UNCERTAINTY_UPGRADED,
        )

    def test_psychology_or_causality_requires_explicit_evidence(self):
        self.assert_rejected(
            NarrativeClaim(
                "claim-1",
                "那次经历让他从此不再相信别人。",
                ("memory-confirmed",),
                psychology_or_causality=True,
            ),
            FactGuardReason.UNSUPPORTED_PSYCHOLOGY_OR_CAUSALITY,
        )
        self.assert_rejected(
            NarrativeClaim(
                "claim-2",
                "他心里一直害怕失败。",
                ("memory-confirmed",),
                psychology_or_causality=False,
            ),
            FactGuardReason.UNSUPPORTED_PSYCHOLOGY_OR_CAUSALITY,
        )

    def test_ta_story_cannot_impersonate_subject_in_first_person(self):
        self.assert_rejected(
            NarrativeClaim(
                "claim-1",
                "我在北方读大学。",
                ("memory-confirmed",),
            ),
            FactGuardReason.TA_FIRST_PERSON_FORBIDDEN,
            project_type=BookProjectType.TA_STORY,
            narrator_type=NarrativeNarratorType.THIRD_PERSON_BIOGRAPHY,
        )

    def test_ta_witness_first_person_must_name_the_witness_position(self):
        self.assert_rejected(
            NarrativeClaim(
                "claim-1",
                "我在北方读大学。",
                ("memory-confirmed",),
            ),
            FactGuardReason.TA_WITNESS_POSITION_MISSING,
            project_type=BookProjectType.TA_STORY,
            narrator_type=NarrativeNarratorType.CONTROLLER_WITNESS,
        )
        result = validate_claims(
            claims=(NarrativeClaim(
                "claim-2",
                "在我记忆中，他一直珍惜那段北方求学的日子。",
                ("memory-confirmed",),
            ),),
            ledger=self.ledger,
            project_type=BookProjectType.TA_STORY,
            narrator_type=NarrativeNarratorType.CONTROLLER_WITNESS,
        )
        self.assertEqual(result.coverage, 1.0)

    def test_supported_claim_passes_with_full_coverage(self):
        result = validate_claims(
            claims=(NarrativeClaim("claim-1", "我在北方求学。", ("memory-confirmed",)),),
            ledger=self.ledger,
            project_type=BookProjectType.SELF_AUTOBIOGRAPHY,
            narrator_type=NarrativeNarratorType.SELF_FIRST_PERSON,
        )
        self.assertEqual(result.coverage, 1.0)
        self.assertEqual(result.claim_count, 1)


if __name__ == "__main__":
    unittest.main()
