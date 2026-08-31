import json
from pathlib import Path
import unittest

from app.domain.narrative.contracts import BookProjectType, NarrativeNarratorType
from app.domain.narrative.fact_guard import (
    FactGuardRejected,
    FactLedgerEntry,
    NarrativeClaim,
    validate_claims,
)


FIXTURE = Path(__file__).parent / "fixtures/narrative/golden_corpus_v1.json"


class NarrativeGoldenCorpusTests(unittest.TestCase):
    def test_deidentified_quality_cases_remain_fail_closed(self):
        corpus = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(corpus["schemaVersion"], "narrative-golden-corpus-v1")
        self.assertGreaterEqual(len(corpus["cases"]), 4)
        for case in corpus["cases"]:
            with self.subTest(caseId=case["caseId"]):
                ledger = {
                    item["memoryVersionId"]: FactLedgerEntry(
                        memory_version_id=item["memoryVersionId"],
                        content_hash="fixture-hash",
                        text=item["text"],
                        uncertain=item["uncertain"],
                        supports_direct_quote=item["supportsDirectQuote"],
                    )
                    for item in case["ledger"]
                }
                claim = NarrativeClaim(
                    claim_id=case["caseId"],
                    text=case["claim"]["text"],
                    memory_version_ids=tuple(case["claim"]["memoryVersionIds"]),
                )
                if case["expected"] == "pass":
                    result = validate_claims(
                        claims=(claim,),
                        ledger=ledger,
                        project_type=BookProjectType(case["projectType"]),
                        narrator_type=NarrativeNarratorType(case["narratorType"]),
                    )
                    self.assertEqual(result.coverage, 1.0)
                else:
                    with self.assertRaises(FactGuardRejected) as caught:
                        validate_claims(
                            claims=(claim,),
                            ledger=ledger,
                            project_type=BookProjectType(case["projectType"]),
                            narrator_type=NarrativeNarratorType(case["narratorType"]),
                        )
                    self.assertIn(case["expected"], [reason.value for reason in caught.exception.reasons])


if __name__ == "__main__":
    unittest.main()
