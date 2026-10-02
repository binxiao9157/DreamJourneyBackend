import json,unittest
from copy import deepcopy
from app.core.config import Settings
from app.domain.owner_truth.live_topics import digest
from app.domain.owner_truth.live_theme_relations import validate_relation
from app.services.owner_truth_live_theme_provider import DeepSeekLiveThemeProvider
from app.services.owner_truth_live_memory_contract_errors import LiveMemoryContractFailure

class RelationContractTests(unittest.TestCase):
 def setUp(self):
  self.p=DeepSeekLiveThemeProvider(Settings(deepseek_api_key='synthetic-only'))
  self.material=dict(atoms=[dict(atomId='new',content={'memoryKind':'knowledge','claim':'我在杭州读书'})],targets=[dict(topicId='topic',version=3,proposalHash=digest('prior'),state='pending',atoms=[dict(atomId='old',content={'memoryKind':'knowledge','claim':'我在杭州读书'})])])
 def test_all_relation_examples_roundtrip_current_validator_and_transport(self):
  stage,request=self.p.prepare_relation(material=self.material)
  instruction=request.payload()['messages'][0]['content']
  examples=self.p._relation_output_examples()
  self.assertIn(json.dumps(examples,ensure_ascii=False,sort_keys=True),instruction)
  self.assertEqual({x['relation'] for x in examples},{'none','uncertain','supplement','duplicate','correction'})
  for example in examples:
   with self.subTest(relation=example['relation']):
    payload=json.loads(json.dumps(example).replace('<inputHash>',digest(self.material)).replace('<targetTopicId>','topic').replace('<proposalHash>',digest('prior')).replace('<newAtomId>','new').replace('<oldAtomId>','old'))
    if payload.get('targetTopicId') is not None:payload['targetVersion']=3
    self.assertIsNotNone(validate_relation(material=self.material,proposal=payload))
  self.assertLessEqual(len(request.body),self.p.maximum_input_bytes)
  self.assertEqual(request.payload()['max_tokens'],4096)
 def proposal(self):
  return dict(schemaVersion='owner-truth-live-theme-relation-v1',inputHash=digest(self.material),relation='duplicate',targetTopicId='topic',targetVersion=3,targetHash=digest('prior'),duplicateAtomIds=['new'],replaces={},title='求学',summary='我在杭州读书')
 def test_actual_array_failure_remains_rejected_not_normalized(self):
  p=self.proposal();p['replaces']=[]
  with self.assertRaisesRegex(LiveMemoryContractFailure,'themeRelationMemberMismatch'):
   self.p.validate_relation_payload(material=self.material,payload=p)
  self.assertEqual(p['replaces'],[])
 def test_empty_summary_remains_rejected(self):
  p=self.proposal();p['summary']=''
  with self.assertRaisesRegex(LiveMemoryContractFailure,'invalidRelationSummary'):
   self.p.validate_relation_payload(material=self.material,payload=p)
 def test_valid_output_still_needs_independent_support(self):
  p=self.proposal();review=dict(schemaVersion='owner-truth-live-theme-relation-support-v1',inputHash=digest(self.material),proposalHash=digest(p),verdict='supported',sameSubjectEvent=True,compatibleTime=True,correctionsResolved=True,summarySupported=False)
  self.assertIsNone(self.p.validate_relation_payload(material=self.material,proposal=p,payload=review))
  review['summarySupported']=True
  self.assertIsNotNone(self.p.validate_relation_payload(material=self.material,proposal=p,payload=review))
 def test_wrong_target_and_nonidentical_duplicate_still_rejected(self):
  p=self.proposal();p['targetHash']=digest('wrong')
  with self.assertRaisesRegex(LiveMemoryContractFailure,'themeRelationTargetMismatch'):self.p.validate_relation_payload(material=self.material,payload=p)
  p=self.proposal();self.material['atoms'][0]['content']['claim']='我在上海读书';p['inputHash']=digest(self.material)
  with self.assertRaisesRegex(LiveMemoryContractFailure,'unprovenThemeDuplicate'):self.p.validate_relation_payload(material=self.material,payload=p)
if __name__=='__main__':unittest.main()
