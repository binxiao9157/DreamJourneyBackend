import json
import unittest
from pathlib import Path
from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace as NS
from app.core.config import Settings
from app.domain.owner_truth.live_topics import digest,SupportedLiveTheme,LiveThemeConflict
from app.domain.owner_truth.live_theme_relations import validate_relation
from app.services.owner_truth_live_theme_provider import DeepSeekLiveThemeProvider
from app.async_effects.owner_truth_live_recovery_worker import RecoveryThemeAssembler

FIXTURES=json.loads(Path(__file__).with_name('observed-synthetic-relations.json').read_text())

class ReviewApplicabilityTests(unittest.TestCase):
    def test_actual_duplicate_and_supplement_are_accepted(self):
        for kind in ('duplicate','supplement'):
            with self.subTest(kind=kind):
                f=FIXTURES[kind]
                self.assertIsNotNone(validate_relation(**f))

    def assemble(self,kind):
        f=deepcopy(FIXTURES[kind]);material=f['material'];target=material['targets'][0]
        target['revision']=dict(topicId=target['topicId'],version=target['version'],state='pending',members={a['atomId']:{'sourceId':'old-source'} for a in target['atoms']})
        raw=material['theme'];theme=SupportedLiveTheme(raw['key'],raw['title'],raw['summary'],tuple(raw['atomIds']),tuple(raw['evidenceIds']),tuple(raw['dimensions']),raw['supportHash'])
        class Probe(RecoveryThemeAssembler):
            def _uow(self,*args):return nullcontext()
            def _admit(self,*args):return None
            def _call(self,**kw):
                stage,request=kw['prepared'];data=json.loads(request.payload()['messages'][1]['content'])
                value=deepcopy(f['proposal' if stage=='themeRelation' else 'review']);value['inputHash']=data['inputHash']
                if stage=='themeRelationSupport':value['proposalHash']=data['proposalHash']
                kw['validator'](value);return value
        host=NS(_settings=Settings(deepseek_api_key='local'),_store=NS(owner_truth_live_topic_repository=lambda:NS(relation_catalog=lambda **kw:[target])))
        return Probe(host).relate_themes(lease=None,intent=NS(target=NS(vault_id='v',owner_subject_id='o')),source=NS(source_id='new-source',source_metadata={'snapshotRevision':1}),run_id='r',themes=[theme],catalog=material['atoms'])

    def test_duplicate_reaches_unchanged_instead_of_blocked(self):
        accepted,relations,blocked,unchanged,corrections=self.assemble('duplicate')
        self.assertFalse(blocked);self.assertFalse(accepted);self.assertEqual(len(unchanged),1)

    def test_supplement_retains_old_and_new_members(self):
        accepted,relations,blocked,unchanged,corrections=self.assemble('supplement')
        f=FIXTURES['supplement'];expected={a['atomId'] for a in f['material']['atoms']+f['material']['targets'][0]['atoms']}
        self.assertFalse(blocked);self.assertEqual(len(accepted),1);self.assertEqual(set(accepted[0].atom_ids),expected)

    def test_invalid_correction_is_declined_current_theme_stays_reviewable(self):
        self.assertIsNone(validate_relation(**FIXTURES['correction']))
        out=self.assemble('correction')
        self.assertEqual(len(out[0]),1);self.assertFalse(out[1]);self.assertFalse(out[4])
        self.assertEqual(out[0][0].summary,FIXTURES['correction']['material']['theme']['summary'])

    def test_unrelated_predicates_and_types_remain_strict(self):
        for kind in ('duplicate','supplement','correction'):
            for key,value in [('sameSubjectEvent',False),('compatibleTime',False),('summarySupported',False),('verdict','unsupported'),('verdict','uncertain')]:
                f=deepcopy(FIXTURES[kind]);f['review'].update(verdict='supported',sameSubjectEvent=True,compatibleTime=True,summarySupported=True,correctionsResolved=True);f['review'][key]=value
                self.assertIsNone(validate_relation(**f))
            for value in (None,0,1,'false'):
                f=deepcopy(FIXTURES[kind]);f['review']['correctionsResolved']=value
                with self.assertRaisesRegex(LiveThemeConflict,'PredicateInvalid'):validate_relation(**f)
        f=deepcopy(FIXTURES['correction']);f['review'].update(verdict='supported',summarySupported=True,correctionsResolved=False)
        self.assertIsNone(validate_relation(**f))

    def test_correction_review_receives_exact_valid_fact_scope(self):
        f=deepcopy(FIXTURES['correction']);target=f['material']['targets'][0]
        extra=deepcopy(target['atoms'][0]);extra['atomId']='unrelated-old';extra['content']['summary']='我喜欢画画。';target['atoms'].append(extra)
        f['proposal']['inputHash']=digest(f['material'])
        provider=DeepSeekLiveThemeProvider(Settings(deepseek_api_key='local'))
        stage,request=provider.prepare_relation(material=f['material'],proposal=f['proposal'])
        data=json.loads(request.payload()['messages'][1]['content']);scope=data['summaryScope']
        new={a['atomId'] for a in f['material']['atoms']};removed=set(f['proposal']['replaces'].values())
        self.assertEqual(set(scope['validAtomIds']),new|{'unrelated-old'})
        self.assertEqual(set(scope['replacedAtomIds']),removed)
        self.assertEqual(scope['targetState'],'pending')
        target['state']='accepted';f['proposal']['inputHash']=digest(f['material'])
        _,req=provider.prepare_relation(material=f['material'],proposal=f['proposal'])
        self.assertEqual(set(json.loads(req.payload()['messages'][1]['content'])['summaryScope']['validAtomIds']),new)

if __name__=='__main__':unittest.main()
