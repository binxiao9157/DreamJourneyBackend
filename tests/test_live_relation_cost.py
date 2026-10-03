"""Controlled HTTP exercises product screening; durable budgets are tested separately.

No timing claim: the model semantic answers here are synthetic test inputs.
"""
import json
import unittest
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS
import httpx
from tests.test_live_theme_capacity import Probe, atom, theme, target
from app.domain.owner_truth.live_topics import digest
from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler
from app.services.owner_truth_live_long_memory import (InMemoryLiveLongMemoryRepository,
    LiveLongMemoryRunIdentity, LiveLongMemoryUnitPlan, LiveLongMemoryBudgetPolicy,
    LiveLongMemoryBudgetExhausted)

class ScreenProbe(Probe):
    def __init__(self, targets, verdicts=None, decide=None):
        super().__init__(targets,decide);self.verdicts=verdicts or {};self.mutate=None
    def http(self,request):
        data=json.loads(json.loads(request.content)['messages'][1]['content'])
        if 'relationScreen' not in data:return super().http(request)
        value=dict(schemaVersion='live-relation-screen-v1',inputHash=data['inputHash'],targets=[
            dict(topicId=t['topicId'],version=t['version'],proposalHash=t['proposalHash'],
                verdict=self.verdicts.get(t['topicId'],'unlikely')) for t in data['relationScreen']['targets']])
        if self.mutate:self.mutate(value)
        return httpx.Response(200,json=dict(choices=[dict(message=dict(content=json.dumps(value)),finish_reason='stop')]))

class RelationCostTests(unittest.TestCase):
    def history(self):
        return [target([atom(i,'old'+str(j),text=f'历史事项{j}的记录{i}') for i in range(97)],key='prior'+str(j)) for j in range(8)]
    def test_large_unrelated_history_one_screen_preserves_all_current_facts(self):
        p=ScreenProbe(self.history());new=[atom(i) for i in range(97)];before=deepcopy(p.targets)
        result=p.run(new)
        self.assertEqual(result[0],(theme(new),));self.assertFalse(result[1]);self.assertFalse(result[4])
        self.assertEqual(p.targets,before);self.assertEqual(len(p.calls),1)
        self.assertEqual(p.relation_screens[0]['excludedTargetIds'],[t['topicId'] for t in p.targets])
        self.assertEqual(p.relation_screens[0]['semantics'],'retrievalOnlyNotVerifiedNoRelation')
    def test_last_target_is_still_reviewed_not_first_k(self):
        history=[target(key='prior'+str(j)) for j in range(8)]
        p=ScreenProbe(history,{'prior7':'possible'},lambda m:('supplement',[],{}));new=[atom(1)]
        result=p.run(new)
        self.assertEqual(result[1]['trip']['previous']['topicId'],'prior7')
        self.assertEqual([stage for stage,_,_ in p.calls],['themeRelationScreen','themeRelation','themeRelationSupport'])
        self.assertEqual(set(result[0][0].atom_ids),{'new-1','old-0'})
    def test_exact_fact_match_cannot_be_screened_out(self):
        p=ScreenProbe([target([atom(1,'old')]),target(key='other')],decide=lambda m:('duplicate',[a['atomId'] for a in m['atoms']],{}))
        result=p.run([atom(1)])
        self.assertFalse(result[0]);self.assertEqual(len(result[3]),1)
        self.assertEqual(p.relation_screens[0]['exactMatchTargetIds'],['prior'])
    def test_uncertain_retrieval_is_retained_for_fact_review(self):
        p=ScreenProbe([target(key='one'),target(key='two')],{'two':'uncertain'})
        self.assertEqual(len(p.run([atom(1)])[0]),1)
        self.assertEqual(p.relation_screens[0]['selectedTargetIds'],['two']);self.assertEqual(len(p.calls),3)
    def test_multiple_possible_relations_do_not_force_historical_write(self):
        p=ScreenProbe([target(key='one'),target(key='two')],{'one':'possible','two':'possible'},lambda m:('supplement',[],{}))
        result=p.run([atom(i) for i in range(65)])
        self.assertFalse(result[1]);self.assertFalse(result[4]);self.assertEqual(len(result[0]),1)
    def test_short_single_history_does_not_add_screen_request(self):
        p=ScreenProbe([target()]);p.run([atom(1)])
        self.assertEqual([s for s,_,_ in p.calls],['themeRelation','themeRelationSupport'])
    def test_malformed_target_identity_is_not_used(self):
        changes=[lambda v:v.update(inputHash='wrong'),lambda v:v['targets'].pop(),
            lambda v:v['targets'][0].update(version=True),lambda v:v['targets'][0].update(proposalHash='wrong'),
            lambda v:v['targets'].append(v['targets'][0]),lambda v:v['targets'][0].update(topicId='foreign')]
        for change in changes:
            p=ScreenProbe([target(key='one'),target(key='two')]);p.mutate=change
            result=p.run([atom(1)])
            self.assertEqual(result[0],(theme([atom(1)]),));self.assertFalse(result[1]);self.assertFalse(result[4])
            self.assertIn('relationScreen',p.deferred_relations[0]['reason'])
    def test_screen_over_capacity_defers_without_truncation(self):
        targets=[target(key='one'),target(key='two')]
        targets[0]['theme']['summary']='字'*20000
        p=ScreenProbe(targets);result=p.run([atom(1)])
        self.assertEqual(len(p.calls),0);self.assertEqual(len(result[0]),1);self.assertFalse(result[1])
        self.assertEqual(p.deferred_relations[0]['reason'],'relationScreenOverCapacity')

class DurableRelationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.repo=InMemoryLiveLongMemoryRepository();self.identity=LiveLongMemoryRunIdentity('owner','vault','test',1,1)
        self.repo.begin_or_load(self.identity,LiveLongMemoryBudgetPolicy())
    def reserve(self,index,stage='themeRelation',recovery=False):
        plan=LiveLongMemoryUnitPlan(run_id=self.identity.run_id,ordinal=index,kind=stage,generation=1,ownership=({'index':index},))
        self.repo.record_unit(plan)
        return self.repo.reserve_provider_attempt(run_id=self.identity.run_id,unit_id=plan.unit_id,stage=stage,
            request_hash=digest(index),reserved_input_tokens=10,reserved_output_tokens=10,recovery=recovery)
    def test_shared_cap_counts_failures_reviews_and_retries_not_required_work(self):
        for i in range(32):self.reserve(i,('themeRelation','themeRelationSupport','themeRelationScreen')[i%3],recovery=i<2)
        self.repo.begin_or_load(self.identity,LiveLongMemoryBudgetPolicy())
        with self.assertRaisesRegex(LiveLongMemoryBudgetExhausted,'optionalRelation'):self.reserve(32)
        self.reserve(33,'themeSupport');self.assertEqual(len(self.repo.snapshot(self.identity.run_id)['attempts']),33)
    def test_concurrent_reservations_cannot_exceed_cap(self):
        def reserve(i):
            try:self.reserve(i);return 1
            except LiveLongMemoryBudgetExhausted:return 0
        with ThreadPoolExecutor(max_workers=8) as pool:result=list(pool.map(reserve,range(48)))
        self.assertEqual(sum(result),32)
    def test_invalid_screen_is_rejected_not_completed_or_used_for_history(self):
        p=ScreenProbe([target(key='one'),target(key='two')]);p.mutate=lambda v:v['targets'][0].update(topicId='foreign')
        p.host._store.owner_truth_live_long_memory_repository=lambda:self.repo
        p._call=RecoveryThemeAssembler._call.__get__(p)
        def run():return p.relate_themes(lease=NS(job_id='job'),intent=NS(target=NS(vault_id='v',owner_subject_id='o')),
            source=NS(source_id='s',source_metadata={'snapshotRevision':1}),run_id=self.identity.run_id,
            themes=[theme([atom(1)])],catalog=[atom(1)])
        for _ in range(2):
            result=run();self.assertEqual(result[0],(theme([atom(1)]),));self.assertFalse(result[1]);self.assertFalse(result[4])
        snapshot=self.repo.snapshot(self.identity.run_id)
        self.assertEqual(len(snapshot['attempts']),1)
        self.assertEqual(snapshot['attempts'][0]['exposureState'],'rejected')
        self.assertEqual(snapshot['units'][0]['state'],'failed')

    def test_screen_http_and_timeout_use_existing_two_attempt_budget_then_defer(self):
        from app.services.owner_truth_live_memory_contract_errors import LiveMemoryContractFailure
        for failure in (429,503,'timeout','invalidJson'):
            with self.subTest(failure=failure):
                self.setUp()
                p=ScreenProbe([target(key='one'),target(key='two')]);sent=[]
                def fail(request):
                    sent.append(True)
                    if failure=='invalidJson':return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'not-json'}}]})
                    if failure=='timeout':raise httpx.ReadTimeout('controlled',request=request)
                    return httpx.Response(failure,json={'error':{'type':'server_error'}})
                p.http=fail;p.host._store.owner_truth_live_long_memory_repository=lambda:self.repo
                p._call=RecoveryThemeAssembler._call.__get__(p)
                def run():return p.relate_themes(lease=NS(job_id='job'),intent=NS(target=NS(vault_id='v',owner_subject_id='o')),
                    source=NS(source_id='s',source_metadata={'snapshotRevision':1}),run_id=self.identity.run_id,
                    themes=[theme([atom(1)])],catalog=[atom(1)])
                if failure!='invalidJson':
                    with self.assertRaises(LiveMemoryContractFailure):run()
                for _ in range(2):
                    result=run();self.assertEqual(result[0],(theme([atom(1)]),));self.assertFalse(result[1]);self.assertFalse(result[4])
                self.assertEqual(len(sent),1 if failure=='invalidJson' else 2)
                self.assertEqual(len(self.repo.snapshot(self.identity.run_id)['attempts']),len(sent))

    def test_production_call_budget_fallback_and_completed_replay(self):
        p=ScreenProbe([target(key='one'),target(key='two')],{'one':'possible','two':'possible'})
        p.host._store.owner_truth_live_long_memory_repository=lambda:self.repo
        p._call=RecoveryThemeAssembler._call.__get__(p)
        source=NS(source_id='s',source_metadata={'snapshotRevision':1});lease=NS(job_id='job')
        def run():return p.relate_themes(lease=lease,intent=NS(target=NS(vault_id='v',owner_subject_id='o')),
            source=source,run_id=self.identity.run_id,themes=[theme([atom(1)])],catalog=[atom(1)])
        for i in range(31):self.reserve(i)
        result=run();self.assertEqual(len(result[0]),1);self.assertFalse(result[1]);self.assertFalse(result[4])
        self.assertEqual(p.deferred_relations[0]['reason'],'optionalRelationBudgetExhausted')
        self.assertEqual(len(self.repo.snapshot(self.identity.run_id)['attempts']),32)
        self.assertEqual(run(),result)
        self.assertEqual(len(self.repo.snapshot(self.identity.run_id)['attempts']),32)

if __name__=='__main__':unittest.main()
