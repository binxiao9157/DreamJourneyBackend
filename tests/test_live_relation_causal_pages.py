"""No self/future targets at the real batch dispatch boundary; strict results."""
import unittest
from copy import deepcopy
from tests import test_owner_truth_live_contract_recovery_repair as fixtures
from app.services.deepseek import DeepSeekLiveMemoryOrganizationProxy as Proxy
from app.services.owner_truth_live_memory_contract_errors import LiveMemoryContractFailure
from app.services.owner_truth_live_long_memory import LiveLongMemoryBudgetExhausted,LiveLongMemoryBudgetPolicy
from dataclasses import replace

class CausalPagesTests(unittest.TestCase):
    def setUp(self):
        fixtures.Relations.setUp(self)
        for i,m in enumerate(self.memories):m['_atomIds']=[f'atom-{i}']
        self.requests=[];self.duplicate=False;self.invalid=False
        def provider(**kw):
            self.requests.append(deepcopy(kw))
            output=[]
            for i,m in enumerate(kw['incoming']):
                target=None
                # A realistic hostile response when the request offers self.
                for j,old in enumerate(kw['existing']):
                    if set(m['_atomIds']) & set(old['_atomIds']):target=j;break
                    if self.duplicate and m['_atomIds']==['atom-7'] and 'atom-0' in old['_atomIds']:target=j
                if self.invalid:target=len(kw['existing'])
                output.append(dict(incomingIndex=i,scannedExistingCount=len(kw['existing']),
                    decisions=[] if target is None else [dict(existingIndex=target,relation='duplicate')]))
            return dict(results=output)
        self.extractor._relation_reviewer.request_relation_batch_review=provider
    def facts(self,n):
        return [{**deepcopy(self.memories[i%8]),'claim':f'Fact {i}','_atomIds':[f'atom-{i}']} for i in range(n)]
    def run_batch(self,n):
        return self.extractor._resolve_cross_batch_relations(turns=self.turns,memories=self.facts(n),run_identity=self.identity,retry_context=None)
    def assert_requests_causal(self):
        for request in self.requests:
            self.assertFalse(request['intra_batch'])
            self.assertLessEqual(len(request['incoming']),8);self.assertLessEqual(len(request['existing']),32)
            incoming={int(a.split('-')[1]) for m in request['incoming'] for a in m['_atomIds']}
            existing={int(a.split('-')[1]) for m in request['existing'] for a in m['_atomIds']}
            self.assertTrue(incoming.isdisjoint(existing));self.assertLess(max(existing),min(incoming))
    def test_nine_facts_not_self_rejected_and_cache_survives(self):
        result=self.run_batch(9)
        self.assertEqual(result,(self.facts(9),set(),False));self.assert_requests_causal()
        before=self.repo.snapshot(self.identity.run_id)
        self.assertEqual(len(self.requests),8)
        self.assertEqual(self.run_batch(9),result)
        after=self.repo.snapshot(self.identity.run_id)
        self.assertEqual(after['units'],before['units']);self.assertEqual(after['providerRequestCount'],8)
        self.assertEqual(len(self.requests),8)
    def test_34_fact_set_and_identity_exact_not_only_count(self):
        resolved,retracted,changed=self.run_batch(34)
        self.assertEqual(resolved,self.facts(34));self.assertFalse(retracted);self.assertFalse(changed)
        self.assert_requests_causal();self.assertEqual(len(self.requests),33)
    def test_legitimate_duplicate_preserves_all_lineage(self):
        self.duplicate=True
        resolved,retracted,changed=self.run_batch(9)
        self.assertTrue(changed);self.assertFalse(retracted);self.assertEqual(len(resolved),8)
        self.assertEqual({a for m in resolved for a in m['_atomIds']},{f'atom-{i}' for i in range(9)})
        self.assertTrue(any(set(m['_atomIds'])=={'atom-0','atom-7'} for m in resolved))
        self.assertTrue(all(m['_sourceEvidenceRanges'] for m in resolved));self.assert_requests_causal()
    def test_future_or_out_of_page_answer_not_normalized(self):
        self.invalid=True
        with self.assertRaisesRegex(LiveMemoryContractFailure,'targetInvalid'):self.run_batch(9)
        self.assertFalse(any(u['state']=='completed' for u in self.repo.snapshot(self.identity.run_id)['units']))
    def test_one_fact_prefix_does_not_call_provider(self):
        result=self.extractor._relation_intra_batch_pages(turns=self.turns,memories=self.facts(1),run_identity=self.identity,retry_context=None,ordinal=2009000)
        self.assertEqual(result,[dict(incomingIndex=0,scannedExistingCount=1,decisions=[])])
        self.assertFalse(self.requests)
    def test_prefix_requests_cannot_get_new_run_budget(self):
        self.extractor._run_budget_policy=replace(LiveLongMemoryBudgetPolicy(),maximum_provider_requests=2)
        # A run's durable budget was frozen at begin, so create another with the small policy.
        identity=replace(self.identity,product_session_id='small-budget')
        self.identity=identity;self.repo.begin_or_load(identity,self.extractor._run_budget_policy)
        with self.assertRaises(LiveLongMemoryBudgetExhausted):self.run_batch(9)
        self.assertEqual(self.repo.snapshot(identity.run_id)['providerRequestCount'],2)
        with self.assertRaises(LiveLongMemoryBudgetExhausted):self.run_batch(9)
        self.assertEqual(len(self.requests),2)

class PromptTests(unittest.TestCase):
    def test_intra_adapter_single_identity_table(self):
        memories=[{'claim':'uniqueA'},{'claim':'uniqueB'}]
        prompt=Proxy._build_relation_batch_prompt(turns=[],incoming=memories,existing=memories,intra_batch=True)
        self.assertEqual(prompt.count('uniqueA'),1);self.assertIn('allowedExistingIndices',prompt)
    def test_intra_adapter_mismatched_identity_rejected(self):
        with self.assertRaisesRegex(LiveMemoryContractFailure,'intraBatchIdentityMismatch'):
            Proxy._build_relation_batch_prompt(turns=[],incoming=[{'claim':'A'}],existing=[{'claim':'B'}],intra_batch=True)

if __name__=='__main__':unittest.main(verbosity=2)
