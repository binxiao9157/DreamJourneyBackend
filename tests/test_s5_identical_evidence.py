from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import unittest
from tests import test_owner_truth_memory_changeset as helpers
from app.domain.owner_truth.contracts import MemoryKind
from app.domain.owner_truth.memory_changeset import build_memory_changeset

class IdenticalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.helper = helpers.OwnerTruthMemoryChangeSetTests(); self.helper.setUp()
        self.fixture = json.loads((Path(__file__).parent / "fixtures/s5_identical_evidence.json").read_text())

    def pair(self, reverse=False):
        old, new = deepcopy(self.fixture["formal"]), deepcopy(self.fixture["candidate"])
        if reverse: old, new = new, old
        target = replace(self.helper._current(content=old, source_id="10000000-0000-4000-8000-000000000001"), memory_kind=MemoryKind.EXPERIENCE)
        base = self.helper._candidate(content=self.helper._content(), source_id="10000000-0000-4000-8000-000000000002")
        candidate = replace(base, memory_kind=MemoryKind.EXPERIENCE, payload={**base.payload, "content": new}, content_hash=helpers._hash(new))
        return target, candidate

    def operation(self, target, candidate):
        return build_memory_changeset(candidate=candidate, current_memories=(target,), base_memory_revision=2).operation

    def test_actual_device_identical_fact_adds_evidence_both_directions(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                old, new = self.pair(reverse)
                before = deepcopy(old.content)
                op = self.operation(old, new)
                self.assertEqual(op.kind.value, "addEvidence")
                self.assertEqual(op.target_memory_id, old.memory_id)
                self.assertEqual(old.content, before)

    def test_business_differences_do_not_use_identical_assertion_shortcut(self):
        changes = [
            ("qualifiers", "polarity", "negative"),
            ("qualifiers", "place", "another-place"),
            ("qualifiers", "scenario", "another-scenario"),
            ("qualifiers", "currentApplicability", "current"),
            ("qualifiers", "validTime", {"expression": "2026年", "precision": "year"}),
            ("provenance", "contributorAccountId", "different-contributor"),
            ("provenance", "mode", "inference"),
        ]
        for section, key, value in changes:
            with self.subTest(section=section, key=key):
                old, new = self.pair()
                new.payload["content"][section][key] = value
                self.assertNotEqual(self.operation(old, new).reason, "identicalAssertionUnspecifiedPolarityPreservesReviewedFact")
        for key in ("statement", "claimSubjectId", "memorySubjectId"):
            with self.subTest(key=key):
                old, new = self.pair(); new.payload["content"][key] = "different"
                if key == "statement":
                    new.payload["content"]["event"] = "different"
                    new.payload["content"]["summary"] = "different"
                self.assertNotEqual(self.operation(old, new).reason, "identicalAssertionUnspecifiedPolarityPreservesReviewedFact")

    def test_same_evidence_does_not_create_evidence_operation(self):
        old, new = self.pair()
        new = replace(new, source_id="10000000-0000-4000-8000-000000000001", payload={**new.payload, "evidenceRefs": list(old.evidence_refs)})
        self.assertEqual(self.operation(old, new).kind.value, "duplicate")
