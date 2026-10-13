from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch
import httpx
from app.core.config import Settings
from app.domain.owner_truth.person_biography import apply_composition, build_biography
from app.async_effects.owner_truth_biography_worker import DeepSeekBiographyComposer
from app.domain.owner_truth.person_memory_model import _normalize_entry
from test_s4_biography import fact,blocks,build_person_memory_model,build_person_memory_model_incremental

class S4CompositionTests(unittest.TestCase):
    def setUp(self):
        self.block=blocks(build_person_memory_model([fact(1,'我偶尔喝咖啡。'),fact(2,'我打算周末喝咖啡。')]))[0]
        self.ids=[s['sentenceId'] for s in self.block['sentences']]
    def test_missing_duplicate_invented_and_free_prose_are_rejected(self):
        for response in ({'sentenceIds':self.ids[:1]}, {'sentenceIds':[self.ids[0]]*2}, {'sentenceIds':['invented',self.ids[1]]}, {'sentenceIds':self.ids,'text':'我每天喝咖啡。'},None,{'sentenceIds':[[self.ids[0]],self.ids[1]]}):
            with self.subTest(response=response),self.assertRaises(ValueError):apply_composition(self.block,response)
    def test_valid_order_preserves_every_qualifier_and_evidence(self):
        result=apply_composition(self.block,{'sentenceIds':list(reversed(self.ids))})
        self.assertEqual({s['sentenceId']:s for s in result['sentences']},{s['sentenceId']:s for s in self.block['sentences']})
        self.assertIn('偶尔',result['text']);self.assertIn('打算',result['text'])
    def test_known_chronology_cannot_be_reversed(self):
        self.block['sentences'][0]['time']={'precision':'year','start':'2017'}
        self.block['sentences'][1]['time']={'precision':'year','start':'2024'}
        with self.assertRaises(ValueError):apply_composition(self.block,{'sentenceIds':list(reversed(self.ids))})
    def test_same_fact_new_version_keeps_paragraph_identity(self):
        entry=fact(1,'我喜欢咖啡。');before=build_person_memory_model([entry]);entry=deepcopy(entry)
        entry['memoryVersionId']='10000000-0000-0000-0000-000000000009';entry['contentHash']='a'*64;entry['content']['summary']='我现在偶尔喝咖啡。'
        after=build_person_memory_model_incremental(previous_model=before,entries=[entry])
        self.assertEqual(blocks(before)[0]['blockId'],blocks(after)[0]['blockId'])
        self.assertNotEqual(blocks(before)[0]['sourceRevision'],blocks(after)[0]['sourceRevision'])
    def test_arbitrary_typed_object_and_subject_identity(self):
        records=[_normalize_entry(fact(1,'我每周练习陶笛。')),_normalize_entry(fact(2,'我喜欢学习陶笛。'))]
        for r in records:r['object']={'label':'陶笛'};r['claimSubjectId']='me'
        doc=build_biography(records,'x');self.assertEqual(len(doc['sections'][0]['blocks']),1)
        records[1]['claimSubjectId']='roommate';doc=build_biography(records,'x');self.assertEqual(len(doc['sections'][0]['blocks']),2)
    def test_relative_time_remains_original_statement_without_invented_anchor(self):
        b=blocks(build_person_memory_model([fact(1,'最近两年，我偶尔喝咖啡。')]))[0]
        self.assertIn('原记录中提到',b['text']);self.assertIn('最近两年',b['text']);self.assertNotIn('2026',b['text'])
    def test_prompt_change_invalidates_same_fact_artifact(self):
        old=build_person_memory_model([fact(1,'我喝咖啡。')])
        with patch('app.domain.owner_truth.person_biography.PROMPT_VERSION','changed'):
            new=build_person_memory_model_incremental(previous_model=old,entries=[fact(1,'我喝咖啡。')])
        self.assertNotEqual(blocks(old)[0]['sourceRevision'],blocks(new)[0]['sourceRevision'])
    def test_provider_http_429_and_402_are_different_retry_classes(self):
        client=httpx.Client
        for status,error in ((429,ConnectionError),(503,ConnectionError),(402,ValueError)):
            with self.subTest(status=status):
                transport=httpx.MockTransport(lambda request:httpx.Response(status,json={'error':'synthetic'}))
                with patch('app.async_effects.owner_truth_biography_worker.httpx.Client',side_effect=lambda **kw:client(transport=transport,**kw)):
                    with self.assertRaises(error):DeepSeekBiographyComposer(replace(Settings(),deepseek_api_key='synthetic'))(self.block)
    def test_provider_actual_request_and_parse_contract(self):
        client=httpx.Client;seen=[]
        draft={'sentences':[{'text':'我偶尔喝咖啡，也打算在周末喝。','sourceSentenceIds':self.ids}]}
        audit={'verdict':'supported','facts':[{'sentenceId':x,'covered':True,'qualifiersPreserved':True} for x in self.ids],
               'sentences':[{'index':0,'supported':True}],'issues':[]}
        def handle(request):
            body=json.loads(request.content);seen.append(body)
            value=json.loads(json.dumps(draft if len(seen)==1 else audit))
            if len(seen)==1:
                value['sentences'][0]['sourceSentenceIds']=['f1','f2']
                value['paragraph']=''.join(x['text'] for x in value['sentences'])
            else:
                for i,f in enumerate(value['facts']):f['sentenceId']='f'+str(i+1)
            return httpx.Response(200,json={'model':'deepseek-flash','choices':[{'message':{'content':json.dumps(value)}}]})
        with patch('app.async_effects.owner_truth_biography_worker.httpx.Client',side_effect=lambda **kw:client(transport=httpx.MockTransport(handle),**kw)):
            composer=DeepSeekBiographyComposer(replace(Settings(),deepseek_api_key='synthetic'))
            result=composer(self.block)
        self.assertEqual(result,dict(draft,audit=audit));self.assertEqual(len(seen),2)
        sent=json.loads(seen[0]['messages'][1]['content']);self.assertEqual([s['sentenceId'] for s in sent['sentences']],['f1','f2'])
        review=json.loads(seen[1]['messages'][1]['content']);self.assertEqual(review['original'],sent)
        self.assertEqual(review['proposed']['sentences'][0]['sourceSentenceIds'],['f1','f2']);self.assertEqual(review['proposed']['sentences'][0]['text'],draft['sentences'][0]['text'])
        self.assertEqual(seen[0]['model'],'deepseek-chat')
        self.assertEqual(composer.last_metadata['requests'][1]['reportedModel'],'deepseek-flash')
        self.assertEqual(apply_composition(self.block,result)['qualityState'],'polished')

    def test_transport_that_ignores_cancel_has_bounded_caller_and_no_audit(self):
        from threading import Event
        from time import monotonic
        release=Event();calls=[]
        composer=DeepSeekBiographyComposer(replace(Settings(),deepseek_api_key='synthetic'))
        composer.request_deadline_seconds=0.02
        def stall(*args):
            calls.append(args[0]);release.wait(2)
            return {'sentences':[]}
        with patch.object(composer,'_perform_request',side_effect=stall):
            started=monotonic()
            try:
                with self.assertRaises(TimeoutError):composer(self.block)
                self.assertLess(monotonic()-started,0.5)
                self.assertEqual(calls,['write'])
            finally:release.set()
