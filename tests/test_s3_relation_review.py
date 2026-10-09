from dataclasses import replace
from copy import deepcopy
import unittest
from tests import test_owner_truth_memory_changeset_review as h

class S3RelationReviewTests(unittest.TestCase):
    setUp = h.OwnerTruthMemoryChangeSetReviewTests.setUp
    _content = h.OwnerTruthMemoryChangeSetReviewTests._content
    _candidate = h.OwnerTruthMemoryChangeSetReviewTests._candidate
    _accept = h.OwnerTruthMemoryChangeSetReviewTests._accept

    def candidate_for_year(self,year):
        original=self._candidate()
        from tests import test_owner_truth_memory_changeset as domain_helpers
        content=domain_helpers.OwnerTruthMemoryChangeSetTests._content(self,statement=f'我在{year}年喜欢吃东坡肉',time=f'{year}年')
        return replace(original,content_hash=h._hash(content),payload={**original.payload,'content':content})

    def test_confirmation_revises_only_repeated_period_and_keeps_old_version(self):
        accepted=[]
        for idx,year in enumerate((2016,2025,2025)):
            candidate=self.candidate_for_year(year)
            self.store.repository.seed(candidate)
            accepted.append(self._accept(candidate,command_id=f's3-confirm-{idx}'))
        old,recent,repeated=accepted
        self.assertNotEqual(old.memory_activation.memory_id,recent.memory_activation.memory_id)
        self.assertEqual(repeated.memory_activation.outcome,'revised')
        self.assertEqual(repeated.memory_activation.memory_id,recent.memory_activation.memory_id)
        old_history=self.service.list_memory_version_history(memory_id=old.memory_activation.memory_id,context=self.context)
        recent_history=self.service.list_memory_version_history(memory_id=recent.memory_activation.memory_id,context=self.context)
        self.assertEqual([v.status for v in old_history.versions],['current'])
        self.assertEqual([v.status for v in recent_history.versions],['current','superseded'])
        self.assertEqual(self.store.repository.memory_revision(context=self.context),3)
