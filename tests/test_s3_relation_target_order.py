from itertools import permutations
import unittest
from tests import test_owner_truth_memory_changeset as helpers
from app.domain.owner_truth.memory_changeset import build_memory_changeset, OwnerTruthMemoryChangeOperationKind as Kind

class S3RelationTargetOrderTests(unittest.TestCase):
    setUp = helpers.OwnerTruthMemoryChangeSetTests.setUp
    _content = helpers.OwnerTruthMemoryChangeSetTests._content
    _candidate = helpers.OwnerTruthMemoryChangeSetTests._candidate
    _current = helpers.OwnerTruthMemoryChangeSetTests._current
    def test_repeated_recent_preference_targets_recent_fact_in_any_order(self):
        past = self._current(content=self._content(time='2016年'), source_id='00000000-0000-4000-8000-000000000001')
        recent_content = self._content(statement='2025年我喜欢吃东坡肉', time='2025年')
        recent = self._current(content=recent_content, source_id='00000000-0000-4000-8000-000000000002')
        candidate = self._candidate(content=recent_content, source_id='00000000-0000-4000-8000-000000000003')
        for memories in permutations((past, recent)):
            with self.subTest(first=memories[0].memory_id):
                result = build_memory_changeset(candidate=candidate,current_memories=memories,base_memory_revision=9)
                self.assertEqual(result.operation.kind, Kind.ADD_EVIDENCE)
                self.assertEqual(result.operation.target_memory_version_id, recent.memory_version_id)

    def test_exact_restatement_not_disputed_against_other_period(self):
        past = self._current(content=self._content(time='2016年',polarity='negative'),source_id='00000000-0000-4000-8000-000000000001')
        content = self._content(time='2025年')
        recent = self._current(content=content,source_id='00000000-0000-4000-8000-000000000002')
        candidate = self._candidate(content=content,source_id='00000000-0000-4000-8000-000000000002')
        for memories in permutations((past,recent)):
            result=build_memory_changeset(candidate=candidate,current_memories=memories,base_memory_revision=9)
            self.assertEqual(result.operation.kind,Kind.DUPLICATE)
            self.assertEqual(result.operation.target_memory_id,recent.memory_id)

    def test_untargeted_correction_cannot_arbitrarily_replace_multiple_periods(self):
        past = self._current(content=self._content(time='2016年'),source_id='00000000-0000-4000-8000-000000000001')
        recent = self._current(content=self._content(time='2025年'),source_id='00000000-0000-4000-8000-000000000002')
        candidate=self._candidate(content=self._content(time=None),review_mode='correction')
        for memories in permutations((past,recent)):
            result=build_memory_changeset(candidate=candidate,current_memories=memories,base_memory_revision=9)
            self.assertEqual(result.operation.kind,Kind.ADD)
            self.assertIsNone(result.operation.target_memory_version_id)

    def test_unique_scope_beats_unrelated_period_for_explicit_correction(self):
        past=self._current(content=self._content(time='2016年'),source_id='00000000-0000-4000-8000-000000000001')
        recent=self._current(content=self._content(time='2025年'),source_id='00000000-0000-4000-8000-000000000002')
        candidate=self._candidate(content=self._content(statement='2025年我不喜欢吃东坡肉',time='2025年',polarity='negative'),review_mode='correction')
        for order in permutations((past,recent)):
            result=build_memory_changeset(candidate=candidate,current_memories=order,base_memory_revision=9)
            self.assertEqual(result.operation.kind,Kind.CORRECT)
            self.assertEqual(result.operation.target_memory_version_id,recent.memory_version_id)

    def test_unique_scope_keeps_existing_conflict_protection(self):
        past=self._current(content=self._content(time='2016年'),source_id='00000000-0000-4000-8000-000000000001')
        recent=self._current(content=self._content(time='2025年'),source_id='00000000-0000-4000-8000-000000000002')
        candidate=self._candidate(content=self._content(statement='2025年我不喜欢吃东坡肉',time='2025年',polarity='negative'))
        for order in permutations((past,recent)):
            result=build_memory_changeset(candidate=candidate,current_memories=order,base_memory_revision=9)
            self.assertEqual(result.operation.kind,Kind.DISPUTE)
            self.assertEqual(result.operation.target_memory_version_id,recent.memory_version_id)

    def test_ambiguous_equal_facts_do_not_choose_arbitrary_formal_identity(self):
        content=self._content()
        originals=tuple(self._current(content=content,source_id='00000000-0000-4000-8000-000000000001') for _ in range(2))
        candidate=self._candidate(content=content)
        from app.domain.owner_truth.memory_changeset import build_memory_changeset_proposal
        proposals=[build_memory_changeset_proposal(candidate=candidate,current_memories=order,base_memory_revision=9) for order in permutations(originals)]
        self.assertEqual(proposals[0].proposal_hash,proposals[1].proposal_hash)
        self.assertEqual(proposals[0].change_set.operation.kind,Kind.ADD)
        self.assertIsNone(proposals[0].change_set.operation.target_memory_id)

    def test_explicit_target_has_priority_over_scope(self):
        past=self._current(content=self._content(time='2016年'),source_id='00000000-0000-4000-8000-000000000001')
        recent=self._current(content=self._content(time='2025年'),source_id='00000000-0000-4000-8000-000000000002')
        candidate=self._candidate(content=self._content(time='2025年'),review_mode='correction',correction_of_memory_version_id=past.memory_version_id)
        for order in permutations((past,recent)):
            result=build_memory_changeset(candidate=candidate,current_memories=order,base_memory_revision=9)
            self.assertEqual(result.operation.kind,Kind.CORRECT)
            self.assertEqual(result.operation.target_memory_version_id,past.memory_version_id)

    def test_missing_explicit_target_never_redirects_to_another_fact(self):
        from uuid import uuid4
        recent=self._current(content=self._content(),source_id='00000000-0000-4000-8000-000000000002')
        candidate=self._candidate(content=self._content(),review_mode='correction',correction_of_memory_version_id=str(uuid4()))
        result=build_memory_changeset(candidate=candidate,current_memories=(recent,),base_memory_revision=9)
        self.assertEqual(result.operation.kind,Kind.ADD)
        self.assertIsNone(result.operation.target_memory_version_id)

    def test_proposal_hash_and_before_after_are_stable_across_order(self):
        from app.domain.owner_truth.memory_changeset import build_memory_changeset_proposal
        past=self._current(content=self._content(time='2016年'),source_id='00000000-0000-4000-8000-000000000001')
        recent=self._current(content=self._content(time='2025年'),source_id='00000000-0000-4000-8000-000000000002')
        candidate=self._candidate(content=self._content(time='2025年'))
        proposals=[build_memory_changeset_proposal(candidate=candidate,current_memories=order,base_memory_revision=9) for order in permutations((past,recent))]
        self.assertEqual(proposals[0].proposal_hash,proposals[1].proposal_hash)
        self.assertEqual(proposals[0].rendered_operations,proposals[1].rendered_operations)
