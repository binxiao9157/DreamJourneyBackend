"""Replay the device's supported/false grouping verdict without accepting it."""
import unittest
from types import SimpleNamespace
from app.core.config import Settings
from app.domain.owner_truth.live_topics import digest, validate_theme_review
from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler
from app.async_effects.owner_truth_live_private_themes import RecoveryPrivateThemePlanner
from app.services.owner_truth_live_memory_contract_errors import LiveMemoryContractFailure
from app.services.owner_truth_live_long_memory import LiveLongMemoryBudgetExhausted


def atoms(count=2):
    texts=['练陶艺时我把手机放远，便于专心。','我和弟弟互查陶艺动作，不会的先记下再问老师。']
    return [dict(atomId=f'a{i}',content={'summary':texts[i] if i<2 else f'独立事实{i}'},
        contentHash=digest({'i':i}),supportState='supported',dimensions=['habits'],
        evidenceIds=[f'e{i}'],evidence=[dict(evidenceId=f'e{i}',text=texts[i] if i<2 else f'独立事实{i}')]) for i in range(count)]


def proposal(page,groups=None):
    groups=groups or [page]
    return dict(schemaVersion='owner-truth-live-theme-v1',inputHash=digest(page),omittedAtomIds=[],themes=[
        dict(key=f'g{i}',title='陶艺学习',summary='；'.join(a['content']['summary'] for a in group),
             atomIds=[a['atomId'] for a in group],evidenceIds=[e for a in group for e in a['evidenceIds']],dimensions=['habits'])
        for i,group in enumerate(groups)])


def review(page,p,*,verdict='supported',corrections=True,time_conflict=False):
    return dict(schemaVersion='owner-truth-live-theme-support-v1',inputHash=digest(page),proposalHash=digest(p),themes=[
        dict(key=t['key'],atomIds=t['atomIds'],evidenceIds=t['evidenceIds'],verdict=verdict,
             sameSubjectEvent=True if time_conflict else len(t['atomIds'])==1,
             compatibleTime=len(t['atomIds'])==1 if time_conflict else True,correctionsResolved=corrections)
        for t in p['themes']])


class ReplayMixin:
    def __init__(self):
        super().__init__(SimpleNamespace(_settings=Settings(deepseek_api_key='synthetic')))
        self.calls=[];self.reject_single=False;self.exhaust_second=False;self.bad_binding=False;self.allow_all=False
    def _call(self,**kw):
        page=kw['atoms'];p=kw.get('proposal');self.calls.append((kw['page_key'],len(page),p is not None))
        if self.exhaust_second and 'regroup' in kw['page_key'] and page[0]['atomId']=='a1':
            raise LiveLongMemoryBudgetExhausted('syntheticBudget')
        if self.bad_binding and 'regroup' in kw['page_key']:
            raise RuntimeError('authorityLost')
        if p is None:return proposal(page)
        value=review(page,p,verdict='uncertain' if self.reject_single and len(page)==1 else 'supported')
        if self.allow_all:
            for t in value['themes']:t['sameSubjectEvent']=True
        return value

class FinalReplay(ReplayMixin,RecoveryThemeAssembler):pass
class PrivateReplay(ReplayMixin,RecoveryPrivateThemePlanner):pass

class ThemeRegroupBoundaries(unittest.TestCase):
    def organize(self,assembler,page=None):
        return assembler.organize_pages(lease=None,run_id='r',revision=0,source_id='s',catalog=page or atoms())
    def test_observed_group_rejection_recovers_private_and_final_without_repeating_group(self):
        for kind in (PrivateReplay,FinalReplay):
            with self.subTest(path=kind.__name__):
                a=kind();themes,omitted,blocked=self.organize(a)
                self.assertEqual({x for t in themes for x in t.atom_ids},{'a0','a1'})
                self.assertEqual((len(themes),omitted,blocked),(2,(),()))
                repair=[n for key,n,_ in a.calls if 'regroup' in key]
                self.assertTrue(repair);self.assertEqual(set(repair),{1})
                self.assertEqual(len(a.calls),6)
    def test_normal_short_group_keeps_one_card_and_two_calls(self):
        a=FinalReplay();a.allow_all=True
        themes,omitted,_=self.organize(a)
        self.assertEqual((len(themes),omitted,len(a.calls)),(1,(),2))
    def test_uncertain_singletons_are_not_forced_through(self):
        a=FinalReplay();a.reject_single=True
        with self.assertRaises(LiveMemoryContractFailure):self.organize(a)
        self.assertEqual(len(a.calls),6)
    def test_budget_exhaustion_preserves_verified_partition_only(self):
        a=FinalReplay();a.exhaust_second=True
        themes,omitted,_=self.organize(a)
        self.assertEqual({x for t in themes for x in t.atom_ids},{'a0'})
        self.assertEqual(omitted,('a1',))
    def test_authority_error_is_not_downgraded(self):
        a=FinalReplay();a.bad_binding=True
        with self.assertRaisesRegex(RuntimeError,'authorityLost'):self.organize(a)
    def test_group_hint_requires_supported_resolved_multi_member_verdict(self):
        page=atoms();p=proposal(page)
        for verdict,corrections,want in [('supported',True,True),('supported',False,False),('uncertain',True,False),('unsupported',True,False)]:
            result=validate_theme_review(atoms=page,proposal=p,review=review(page,p,verdict=verdict,corrections=corrections))
            self.assertEqual(result.blocked[0].get('groupingRejected',False),want)
        single=atoms(1);p=proposal(single);r=review(single,p);r['themes'][0]['sameSubjectEvent']=False
        result=validate_theme_review(atoms=single,proposal=p,review=r)
        self.assertFalse(result.blocked[0].get('groupingRejected',False))
    def test_two_rejected_groups_are_partitioned_without_fact_duplication(self):
        a=FinalReplay();page=atoms(8)
        blocked=[dict(atomIds=['a0','a1','a2','a3'],groupingRejected=True),
                 dict(atomIds=['a4','a5'],groupingRejected=True)]
        pages=a._regroup_pages(repair=page,blocked=blocked)
        self.assertEqual([[x['atomId'] for x in p] for p in pages],
                         [['a0','a1','a4','a6','a7'],['a2','a3','a5']])
        self.assertEqual(sum(map(len,pages)),len(page))
        for group in blocked:
            for part in pages:self.assertFalse(set(group['atomIds'])<={x['atomId'] for x in part})
    def test_non_grouping_repair_preserves_original_input_page(self):
        a=FinalReplay();page=atoms(3)
        self.assertEqual(a._regroup_pages(repair=page,blocked=[dict(atomIds=['a0','a1'])]),[page])
    def test_accepted_theme_is_not_resent_for_repair(self):
        class Mixed(FinalReplay):
            def _call(self,**kw):
                if kw.get('proposal') is None and 'regroup' not in kw['page_key']:
                    self.calls.append((kw['page_key'],len(kw['atoms']),False))
                    return proposal(kw['atoms'],[kw['atoms'][:2],kw['atoms'][2:]])
                if 'regroup' in kw['page_key']:
                    assert all(x['atomId']!='a2' for x in kw['atoms'])
                return super()._call(**kw)
        a=Mixed();themes,omitted,blocked=self.organize(a,atoms(3))
        self.assertEqual((len(themes),omitted,blocked),(3,(),()))
        self.assertEqual({x for t in themes for x in t.atom_ids},{'a0','a1','a2'})

    def test_time_conflict_also_requires_separate_inputs(self):
        page=atoms();p=proposal(page)
        result=validate_theme_review(atoms=page,proposal=p,review=review(page,p,time_conflict=True))
        self.assertTrue(result.blocked[0].get('groupingRejected',False))
    def test_conflicted_atom_never_gets_regroup_permission(self):
        page=atoms();page[0]['supportState']='conflicted';p=proposal(page)
        result=validate_theme_review(atoms=page,proposal=p,review=review(page,p))
        self.assertFalse(result.blocked[0].get('groupingRejected',False))

if __name__=='__main__':unittest.main()
