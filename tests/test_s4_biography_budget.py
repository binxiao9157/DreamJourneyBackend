from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import Mock
import unittest
from app.services.owner_truth_biography import BiographyWorker
from test_s4_biography import fact, blocks, build_person_memory_model

class BudgetTests(unittest.TestCase):
    def test_oversized_topic_keeps_formal_input_and_never_calls_model(self):
        # The builder now partitions large topics. Exercise the Worker guard
        # independently with an actual malformed/legacy oversize task.
        parts=blocks(build_person_memory_model([fact(i,'我喜欢咖啡'+str(i)+'。') for i in range(1,22)]))
        block=deepcopy(parts[0])
        block['sentences']=[s for part in parts for s in part['sentences']]
        before=deepcopy(block)
        repo=Mock();repo.claim.return_value={'payload':block,'attempts':1,'source_revision':block['sourceRevision']}
        repo.settle.return_value=True
        store=Mock();store.request_unit_of_work.side_effect=lambda **kw:nullcontext()
        store.owner_truth_biography_repository.return_value=repo
        compose=Mock();worker=BiographyWorker(store,compose);worker._snapshot=lambda context:[block]
        self.assertEqual(worker.run_once(context=Mock())['status'],'fallback')
        compose.assert_not_called();self.assertEqual(block,before)
        self.assertIsNone(repo.settle.call_args.kwargs['metadata'])
