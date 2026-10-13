from copy import deepcopy
from dataclasses import replace
from contextlib import nullcontext
import json,unittest
from unittest.mock import Mock,patch
import httpx
from app.core.config import Settings
from app.domain.owner_truth.person_biography import apply_composition
from app.services.owner_truth_biography import read_composed_model,BiographyWorker
from app.async_effects.owner_truth_biography_worker import DeepSeekBiographyComposer
from test_s4_biography import fact,blocks,build_person_memory_model
from test_s4_biography_polish import approved

class RetentionTests(unittest.TestCase):
 def setUp(self):
  self.entries=[fact(1,'我偶尔在上午喝咖啡。'),fact(2,'我打算周末喝咖啡。')]
  self.old=blocks(build_person_memory_model(self.entries))[0]
  self.response=approved(self.old,'我偶尔在上午喝咖啡，也打算在周末喝。')
  self.current=build_person_memory_model(self.entries+[fact(3,'我用蓝色杯子喝咖啡。'),fact(4,'我打算整理照片。')])
  self.repo=Mock();self.repo.completed_many.return_value={}
  self.repo.retained_many.return_value={self.old['topicId']:[{'payload':self.old,'result':self.response}]}
 def read(self):return read_composed_model(self.repo,context=Mock(),model=self.current)
 def test_addition_failure_keeps_old_prose_new_fact_and_unrelated_block(self):
  before=deepcopy(self.current);result=self.read();coffee=next(b for b in blocks(result) if '咖啡' in b['text'])
  self.assertTrue(coffee['text'].startswith(self.response['sentences'][0]['text']))
  self.assertIn('蓝色杯子',coffee['text']);self.assertEqual(coffee['qualityState'],'retainedWithAdditions')
  self.assertEqual(coffee['evidence'],next(b for b in blocks(before) if b['topicId']==coffee['topicId'])['evidence'])
  self.assertEqual(next(b for b in blocks(before) if '照片' in b['text']),next(b for b in blocks(result) if '照片' in b['text']))
  self.assertEqual(self.current,before)
 def test_corrected_or_revoked_fact_never_reuses_stale_prose(self):
  for mode in ['remove','version','hash','text','qualifier','time','source','subject']:
   with self.subTest(mode=mode):
    before=deepcopy(self.current);coffee=next(b for b in blocks(self.current) if b['topicId']==self.old['topicId']);s=coffee['sentences'][0]
    if mode=='remove':coffee['sentences'].pop(0)
    elif mode=='version':s['evidence'][0]['memoryVersionId']='changed'
    elif mode=='hash':s['evidence'][0]['contentHash']='changed'
    elif mode=='text':s['text']='我不喝咖啡。'
    elif mode=='qualifier':s['qualifiers']={'polarity':'negative'}
    elif mode=='time':s['time']={'start':'2026'}
    elif mode=='source':s['evidence'][0]['sourceId']='revoked'
    else:self.repo.retained_many.return_value[self.old['topicId']][0]['payload']=dict(self.old,groupId='different-subject')
    self.assertEqual(blocks(self.read()),blocks(self.current));self.current=before;self.repo.retained_many.return_value[self.old['topicId']][0]['payload']=self.old
 def test_current_ready_wins_and_corrupt_history_does_not_leak(self):
  coffee=next(b for b in blocks(self.current) if b['topicId']==self.old['topicId']);result=approved(coffee,'我偶尔在上午喝咖啡，也打算在周末喝。喝咖啡时我用的是蓝色杯子。')
  self.repo.completed_many.return_value={(coffee['topicId'],coffee['sourceRevision']):result}
  self.assertEqual(next(b for b in blocks(self.read()) if b['topicId']==coffee['topicId'])['qualityState'],'polished')
  self.repo.completed_many.return_value={};self.repo.retained_many.return_value[self.old['topicId']][0]['result']={'sentences':[]}
  self.assertEqual(blocks(self.read()),blocks(self.current))
 def test_read_never_enqueues_or_calls_provider(self):
  for _ in range(3):self.read()
  self.repo.ensure.assert_not_called();self.repo.claim.assert_not_called();self.repo.settle.assert_not_called()

class TransportTests(unittest.TestCase):
 def setUp(self):self.block=blocks(build_person_memory_model([fact(1,'我偶尔在上午喝咖啡。'),fact(2,'我打算周末喝咖啡。')]))[0]
 def test_compact_ids_roundtrip_to_exact_formal_evidence(self):
  seen=[];client=httpx.Client
  def handler(request):
   body=json.loads(request.content);seen.append(body);material=json.loads(body['messages'][1]['content'])
   if len(seen)==1:
    self.assertEqual([s['sentenceId'] for s in material['sentences']],['f1','f2'])
    value={'sentences':[{'text':'我偶尔在上午喝咖啡，也打算在周末喝。','sourceSentenceIds':['f1','f2']}]}
   else:value={'verdict':'supported','facts':[{'sentenceId':x,'covered':True,'qualifiersPreserved':True} for x in ['f1','f2']],'sentences':[{'index':0,'supported':True}],'issues':[]}
   if len(seen)==1:value['paragraph']=''.join(x['text'] for x in value['sentences'])
   return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]})
  with patch('app.async_effects.owner_truth_biography_worker.httpx.Client',side_effect=lambda **kw:client(transport=httpx.MockTransport(handler),**kw)):
   result=DeepSeekBiographyComposer(replace(Settings(),deepseek_api_key='synthetic'))(self.block)
  self.assertEqual(seen[0]['max_tokens'],4096);self.assertEqual(result['sentences'][0]['sourceSentenceIds'],[s['sentenceId'] for s in self.block['sentences']]);self.assertEqual(apply_composition(self.block,result)['qualityState'],'polished')
 def test_truncated_even_parseable_json_is_never_accepted(self):
  client=httpx.Client
  with patch('app.async_effects.owner_truth_biography_worker.httpx.Client',side_effect=lambda **kw:client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})),**kw)):
   with self.assertRaisesRegex(ValueError,'responseTruncated'):DeepSeekBiographyComposer(replace(Settings(),deepseek_api_key='synthetic'))(self.block)
 def test_unknown_wire_identity_fails_before_audit(self):
  composer=DeepSeekBiographyComposer(replace(Settings(),deepseek_api_key='synthetic'))
  with patch.object(composer,'_request',return_value={'paragraph':'句子','sentences':[{'text':'句子','sourceSentenceIds':['f999']}]}) as call:
   with self.assertRaises(ValueError):composer(self.block)
   self.assertEqual(call.call_count,1)
 def test_failed_worker_classifies_without_persisting_model_text(self):
  for error,code in [('semantic audit uncertain','auditUncertain'),('polish dropped a key qualifier','qualifierMismatch'),('responseTruncated','responseTruncated')]:
   with self.subTest(code=code):
    repo=Mock();repo.claim.return_value={'payload':self.block,'attempts':1,'source_revision':self.block['sourceRevision']};repo.settle.return_value=True
    store=Mock();store.request_unit_of_work.side_effect=lambda **kw:nullcontext();store.owner_truth_biography_repository.return_value=repo
    compose=Mock(side_effect=ValueError(error));worker=BiographyWorker(store,compose);worker._snapshot=lambda ctx:[self.block]
    self.assertEqual(worker.run_once(context=Mock())['status'],'pending');self.assertEqual(repo.settle.call_args.kwargs['error'],code);self.assertIsNone(repo.settle.call_args.kwargs['result'])

 def test_retry_is_bounded_and_feedback_cannot_change_original_sources(self):
  for attempt,status in [(1,'pending'),(2,'fallback')]:
   repo=Mock();repo.claim.return_value={'payload':self.block,'attempts':attempt,'source_revision':self.block['sourceRevision'],'error_code':'compositionNotIntegrated'};repo.settle.return_value=True
   store=Mock();store.request_unit_of_work.side_effect=lambda **kw:nullcontext();store.owner_truth_biography_repository.return_value=repo
   compose=Mock(side_effect=ValueError('compositionNotIntegrated'));worker=BiographyWorker(store,compose);worker._snapshot=lambda ctx:[self.block]
   before=deepcopy(self.block);self.assertEqual(worker.run_once(context=Mock())['status'],status);self.assertEqual(self.block,before)
   material=compose.call_args.args[0];self.assertEqual(material['sentences'],self.block['sentences'])
   self.assertEqual(material.get('compositionFeedback'),'compositionNotIntegrated' if attempt==2 else None)
 def test_paragraph_and_bound_sentences_must_match(self):
  ids=['f1','f2'];composer=DeepSeekBiographyComposer(replace(Settings(),deepseek_api_key='synthetic'))
  with patch.object(composer,'_request',return_value={'paragraph':'我每天喝。','sentences':[{'text':'我偶尔在上午喝咖啡，也打算在周末喝。','sourceSentenceIds':ids}]}) as call:
   with self.assertRaisesRegex(ValueError,'invalidWriterShape'):composer(self.block)
   self.assertEqual(call.call_count,1)

 def test_captured_real_provider_results_keep_truth_and_quality_separate(self):
  from pathlib import Path
  from app.domain.owner_truth.person_biography import composition_error_code
  cases=json.loads((Path(__file__).parent/'fixtures/biography_local_rewrite_provider.json').read_text())
  for case in cases:
   with self.subTest(case=case['case']):
    if case['expected']=='PASS':
     result=apply_composition(case['block'],case['response']);self.assertEqual(result['qualityState'],'polished');self.assertEqual(result['evidence'],case['block']['evidence'])
    else:
     with self.assertRaises(ValueError) as error:apply_composition(case['block'],case['response'])
     self.assertEqual(composition_error_code(error.exception),case['expected'])
