import json
import unittest
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace as NS
import httpx
from app.core.config import Settings
from app.domain.owner_truth.live_topics import SupportedLiveTheme, digest
from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler, RecoveryThemePageFailed
from app.services.owner_truth_live_memory_contract_errors import contract_failure


def atom(index, prefix='new', text=None):
    text = text or f'我在2024年参加同一次杭州旅行，参观了第{index}个展厅。'
    content = dict(memoryKind='experience', summary=text, dimensions=['dailyLife'])
    return dict(atomId=f'{prefix}-{index}', content=content, contentHash=digest(content),
        supportState='supported', dimensions=['dailyLife'], evidenceIds=[f'e-{prefix}-{index}'],
        evidence=[dict(evidenceId=f'e-{prefix}-{index}', text=text)])


def theme(atoms, key='trip'):
    return SupportedLiveTheme(key, '一次旅行', '我在2024年的杭州旅行中参观展厅。',
        tuple(a['atomId'] for a in atoms), tuple(e for a in atoms for e in a['evidenceIds']), ('dailyLife',), digest(atoms))


def target(atoms=None, state='pending', key='prior'):
    atoms = atoms or [atom(0, 'old', '我在2020年去上海跑马拉松。')]
    t = theme(atoms, key)
    return dict(topicId=key, version=1, proposalHash=digest(t.payload()), state=state, theme=t.payload(), atoms=atoms,
        revision=dict(topicId=key, version=1, state=state,
            members={a['atomId']:dict(sourceId='old-source', marker=a['atomId']) for a in atoms}))


class Probe(RecoveryThemeAssembler):
    def __init__(self, targets, decide=None):
        self.targets=targets; self.decide=decide; self.calls=[]; self.fail_at=None; self.summaries=[]
        repo=NS(relation_catalog=lambda **kw: deepcopy(self.targets), formal_targets=lambda **kw: {i:'formal:'+i for i in kw['atom_ids']})
        super().__init__(NS(_settings=Settings(deepseek_api_key='controlled', deepseek_base_url='https://controlled.invalid'),
            _store=NS(owner_truth_live_topic_repository=lambda:repo)))
        self.provider._client=lambda: httpx.Client(transport=httpx.MockTransport(self.http))
    def _uow(self,*args):return nullcontext()
    def _admit(self,*args):return None
    def _call(self,**kw):
        if self.fail_at is not None and len(self.calls)==self.fail_at:raise RecoveryThemePageFailed('controlledExhausted')
        stage,request=kw['prepared'];self.calls.append((stage,json.loads(request.payload()['messages'][1]['content']),len(request.body)))
        result=self.provider.request_prepared(stage=stage,request=request).payload
        kw['validator'](result);return result
    def http(self,request):
        body=json.loads(request.content);data=json.loads(body['messages'][1]['content'])
        if 'relationScreen' in data:
            value=dict(schemaVersion='live-relation-screen-v1',inputHash=data['inputHash'],targets=[
                dict(topicId=t['topicId'],version=t['version'],proposalHash=t['proposalHash'],verdict='possible')
                for t in data['relationScreen']['targets']])
            return httpx.Response(200,json=dict(choices=[dict(message=dict(content=json.dumps(value)),finish_reason='stop')]))
        material=data['material']
        if 'proposal' in data:
            p=data['proposal'];value=dict(schemaVersion='owner-truth-live-theme-relation-support-v1',
                inputHash=data['inputHash'],proposalHash=data['proposalHash'],verdict='supported',
                sameSubjectEvent=p['relation'] not in {'none','uncertain'},compatibleTime=True,
                correctionsResolved=p['relation']=='correction',summarySupported=True,correctionIntentSupported=p['relation']=='correction')
        else:
            t=material['targets'][0];kind,duplicates,replaces=(self.decide(material) if self.decide else ('none',[],{}))
            value=dict(schemaVersion='owner-truth-live-theme-relation-v1',inputHash=data['inputHash'],relation=kind,targetTopicId=None)
            if kind not in {'none','uncertain'}:
                value.update(targetTopicId=t['topicId'],targetVersion=t['version'],targetHash=t['proposalHash'],
                    duplicateAtomIds=duplicates,replaces=replaces,title=material['theme']['title'],summary=material['theme']['summary'])
                if replaces:value['correctionEvidence']={a['atomId']:dict(evidenceId=a['evidenceIds'][0],quote=a['evidence'][0]['text']) for a in material['atoms'] if a['atomId'] in replaces}
        return httpx.Response(200,json=dict(choices=[dict(message=dict(content=json.dumps(value)),finish_reason='stop')],usage={}))
    def organize_pages(self,**kw):
        # Controlled summary is explicit; independent real-provider coverage is separate.
        self.summaries.append(kw['catalog']);return (theme(kw['catalog']),),(),()
    def guard_partial_themes(self, **kw):
        return kw["themes"], ()  # Controlled safety; deny cases have a separate test.
    def run(self,atoms,extra=()):
        return self.relate_themes(lease=None,intent=NS(target=NS(vault_id='v',owner_subject_id='o')),
            source=NS(source_id='s',source_metadata={'snapshotRevision':1,'conversationTurns':[{'text':'本场完整原文'}]}),run_id='r',
            themes=[theme(atoms)]+[theme(a,k) for k,a in extra],catalog=atoms+[a for _,items in extra for a in items])


class CapacityTests(unittest.TestCase):
    def test_scene_97_and_16_both_publishable(self):
        first=[atom(i) for i in range(97)];second=[atom(i,'second') for i in range(16)]
        p=Probe([target()]);out=p.run(first,[('second',second)])
        self.assertFalse(out[2]);self.assertEqual(len(out[0]),2)
        self.assertEqual({i for t in out[0] for i in t.atom_ids},{a['atomId'] for a in first+second})
        self.assertTrue(all(len(d['material']['atoms'])<=64 and size<=48000 for _,d,size in p.calls))
    def test_1_64_65_97_300_and_small_path(self):
        for n in (1,64,65,97,300):
            with self.subTest(n=n):
                p=Probe([target()]);out=p.run([atom(i) for i in range(n)])
                self.assertFalse(out[2]);self.assertEqual(len(out[0][0].atom_ids),n)
                self.assertEqual(len(p.calls),2 if n==1 else len(p.calls))
    def test_exact_cartesian_coverage_new_and_old(self):
        p=Probe([target([atom(i,'old') for i in range(75)])]);new=[atom(i) for i in range(97)];out=p.run(new)
        self.assertFalse(out[2]);seen=[]
        for stage,data,_ in p.calls:
            if stage=='themeRelation':seen.extend((a['atomId'],b['atomId']) for a in data['material']['atoms'] for b in data['material']['targets'][0]['atoms'])
        expected={(a['atomId'],b['atomId']) for a in new for b in p.targets[0]['atoms']}
        self.assertEqual(set(seen),expected);self.assertEqual(len(seen),len(expected))
    def test_supplement_pending_preserves_complete_members(self):
        p=Probe([target()],lambda m:('supplement',[],{}));new=[atom(i) for i in range(97)];out=p.run(new)
        self.assertFalse(out[2]);self.assertEqual(set(out[0][0].atom_ids),{a['atomId'] for a in new+p.targets[0]['atoms']})
        self.assertEqual(len(p.summaries),1);self.assertEqual(set(out[1]),{'trip'})
    def test_accepted_supplement_keeps_formal_immutable(self):
        p=Probe([target(state='accepted')],lambda m:('supplement',[],{}));old=deepcopy(p.targets);out=p.run([atom(i) for i in range(97)])
        self.assertFalse(out[2]);self.assertEqual(len(out[0][0].atom_ids),97);self.assertFalse(out[4]);self.assertEqual(p.targets,old)
    def test_all_duplicate_across_old_pages_is_no_change(self):
        new=[atom(i) for i in range(97)];old=[atom(i,'old') for i in range(97)]
        def decide(m):
            old_text={a['content']['summary'] for a in m['targets'][0]['atoms']}
            dup=[a['atomId'] for a in m['atoms'] if a['content']['summary'] in old_text]
            return ('duplicate' if len(dup)==len(m['atoms']) else 'supplement',dup,{})
        p=Probe([target(old)],decide);out=p.run(new)
        self.assertFalse(out[0]);self.assertFalse(out[2]);self.assertEqual(len(out[3]),1)
    def test_cross_page_correction_exact_formal_binding(self):
        new=[atom(i,text=f'更正：我第{i}次行程应在苏州，不是在杭州。') for i in range(97)]
        old=[atom(i,'old') for i in range(97)]
        def decide(m):
            old_ids={a['atomId'] for a in m['targets'][0]['atoms']}
            repl={a['atomId']:'old-'+a['atomId'].split('-')[-1] for a in m['atoms'] if 'old-'+a['atomId'].split('-')[-1] in old_ids}
            return ('correction' if repl else 'supplement',[],repl)
        p=Probe([target(old,state='accepted')],decide);out=p.run(new)
        self.assertFalse(out[2]);self.assertEqual(out[4],{a['atomId']:'formal:old-'+a['atomId'].split('-')[-1] for a in new})
    def test_uncertain_or_multiple_targets_never_silent_none(self):
        for targets,decide in [([target()],lambda m:('uncertain',[],{})),([target(key='one'),target(key='two')],lambda m:('supplement',[],{}))]:
            out=Probe(targets,decide).run([atom(i) for i in range(97)])
            self.assertEqual(len(out[0]),1);self.assertFalse(out[1]);self.assertFalse(out[2]);self.assertFalse(out[4])
    def test_mixed_pages_defer_old_update_but_keep_current_theme(self):
        p=Probe([target()],lambda m:('none' if any(a['atomId']=='new-96' for a in m['atoms']) else 'supplement',[],{}));out=p.run([atom(i) for i in range(97)])
        self.assertFalse(out[1]);self.assertFalse(out[4]);self.assertEqual(len(out[0][0].atom_ids),97)
        self.assertEqual(p.deferred_relations[0]['reason'],'relationMixedIndependentPages')
    def test_failed_middle_page_does_not_block_small_independent_theme(self):
        p=Probe([target()]);p.fail_at=2
        # Throw only once to model another theme retaining an independent budget unit.
        original=p._call
        def once(**kw):
            if len(p.calls)==2 and p.fail_at is not None:p.fail_at=None;raise RecoveryThemePageFailed('controlledExhausted')
            return original(**kw)
        p._call=once;out=p.run([atom(i) for i in range(97)],[('small',[atom(0,'small')])])
        self.assertEqual([t.key for t in out[0]],['trip','small']);self.assertFalse(out[2])
        self.assertEqual(len(out[0][0].atom_ids),97);self.assertTrue(p.deferred_relations)
    def test_unfit_optional_comparison_defers_but_authority_still_blocks(self):
        p=Probe([target()]);big=atom(0,text='字'*20000);out=p.run([big],[('small',[atom(0,'small')])])
        self.assertEqual([t.key for t in out[0]],['trip','small']);self.assertFalse(out[2])
        self.assertFalse(out[1]);self.assertTrue(p.deferred_relations)
        p=Probe([target()]);p._call=lambda **kw:(_ for _ in ()).throw(contract_failure('themeValidate','sourceAuthorityChanged',category='domain'))
        with self.assertRaisesRegex(Exception,'sourceAuthorityChanged'):p.run([atom(i) for i in range(97)])
    def test_incomplete_final_summary_blocks_pending_update(self):
        p=Probe([target()],lambda m:('supplement',[],{}));p.organize_pages=lambda **kw:((theme(kw['catalog'][:-1]),),(),())
        out=p.run([atom(i) for i in range(97)]);self.assertEqual(len(out[0][0].atom_ids),97);self.assertFalse(out[1])
        self.assertFalse(out[4]);self.assertEqual(p.deferred_relations[0]['reason'],'relationMergedSummaryIncomplete')

class AdditionalCapacityTests(unittest.TestCase):
    def test_real_memory_kinds_not_only_summary_fixture(self):
        for kind,field in [('knowledge','claim'),('emotion','label')]:
            atoms=[atom(i) for i in range(65)]
            for a in atoms:
                text=a['content'].pop('summary');a['content'].update(memoryKind=kind,**{field:text})
            out=Probe([target()]).run(atoms)
            self.assertFalse(out[2]);self.assertEqual(len(out[0][0].atom_ids),65)
    def test_late_page_unresolved_preserves_other_theme(self):
        p=Probe([target()],lambda m:('uncertain' if any(a['atomId']=='new-96' for a in m['atoms']) else 'none',[],{}))
        out=p.run([atom(i) for i in range(97)],[('small',[atom(0,'small')])])
        self.assertEqual([t.key for t in out[0]],['trip','small']);self.assertFalse(out[2])
        self.assertEqual(p.deferred_relations[0]['reason'],'themeRelationUnresolved')
        self.assertIn('new-96',out[0][0].atom_ids)
    def test_cross_page_replacements_cannot_share_old_target(self):
        p=Probe([target()],lambda m:('correction',[],{m['atoms'][0]['atomId']:'old-0'}))
        out=p.run([atom(i) for i in range(97)])
        self.assertEqual(len(out[0][0].atom_ids),97);self.assertFalse(out[1]);self.assertFalse(out[4])
        self.assertEqual(p.deferred_relations[0]['reason'],'relationConflictingReplacement')
    def test_old_64_guard_not_relaxed(self):
        p=Probe([target()]);atoms=[atom(i) for i in range(65)]
        with self.assertRaisesRegex(Exception,'invalidRelationPage'):
            p.provider.prepare_relation(material=dict(theme=theme(atoms).payload(),atoms=atoms,targets=p.targets))
    def test_pending_correction_removes_old_assertion_keeps_unrelated_members(self):
        old=[atom(i,'old') for i in range(3)]
        def decision(m):
            repl={a['atomId']:'old-0' for a in m['atoms'] if a['atomId']=='new-96'}
            return ('correction' if repl else 'supplement',[],repl)
        p=Probe([target(old)],decision);new=[atom(i) for i in range(97)];out=p.run(new)
        self.assertFalse(out[2]);self.assertEqual(set(out[0][0].atom_ids),{a['atomId'] for a in new}|{'old-1','old-2'})
        self.assertNotIn('old-0',{a['atomId'] for a in p.summaries[0]})

if __name__=='__main__':unittest.main()
