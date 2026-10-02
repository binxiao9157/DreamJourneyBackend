import unittest,itertools,json
from copy import deepcopy
import httpx
from app.core.config import Settings
from app.domain.owner_truth.live_topics import digest,LiveThemeConflict
from app.domain.owner_truth.live_theme_relations import validate_relation,SCHEMA
from app.services.owner_truth_live_theme_provider import DeepSeekLiveThemeProvider
class IndependentSemanticsTests(unittest.TestCase):
 def setUp(self):
  self.material=dict(atoms=[dict(atomId='new',content={'memoryKind':'knowledge','claim':'共同事实'})],targets=[dict(topicId='old-topic',version=1,proposalHash=digest('old'),atoms=[dict(atomId='old',content={'memoryKind':'knowledge','claim':'共同事实'})])])
  self.proposal=dict(schemaVersion=SCHEMA,inputHash=digest(self.material),relation='none',targetTopicId=None)
  self.review=dict(schemaVersion='owner-truth-live-theme-relation-support-v1',inputHash=digest(self.material),proposalHash=digest(self.proposal),verdict='supported',sameSubjectEvent=False,compatibleTime=True,correctionsResolved=False,summarySupported=True)
 def check(self):return validate_relation(material=self.material,proposal=self.proposal,review=self.review)
 def test_independent_all_not_applicable_combinations(self):
  for time,correction in itertools.product([True,False],repeat=2):
   with self.subTest(time=time,correction=correction):
    self.review.update(compatibleTime=time,correctionsResolved=correction)
    self.assertEqual(self.check(),self.proposal)
 def test_semantic_denial_and_contradiction_do_not_publish(self):
  for key,value in [('verdict','unsupported'),('verdict','uncertain'),('sameSubjectEvent',True),('summarySupported',False)]:
   with self.subTest(key=key,value=value):
    old=self.review[key];self.review[key]=value;self.assertIsNone(self.check());self.review[key]=old
 def test_uncertain_proposal_cannot_be_promoted_by_supported_review(self):
  self.proposal['relation']='uncertain';self.review['proposalHash']=digest(self.proposal)
  self.assertIsNone(self.check())
 def test_boolean_types_and_missing_fields_are_not_truthy_coerced(self):
  for key in ['sameSubjectEvent','compatibleTime','correctionsResolved','summarySupported']:
   old=self.review[key]
   for value in [None,0,1,'true','false',{},[]]:
    with self.subTest(key=key,value=value):
     self.review[key]=value
     with self.assertRaisesRegex(LiveThemeConflict,'PredicateInvalid'):self.check()
   del self.review[key]
   with self.assertRaisesRegex(LiveThemeConflict,'PredicateInvalid'):self.check()
   self.review[key]=old
 def test_wrong_bindings_are_rejected(self):
  for key in ['schemaVersion','inputHash','proposalHash']:
   old=self.review[key];self.review[key]='wrong'
   with self.assertRaisesRegex(LiveThemeConflict,'ReviewBindingMismatch'):self.check()
   self.review[key]=old
 def test_independent_cannot_carry_merge_targets_or_operations(self):
  for key,value in [('targetTopicId','old-topic'),('targetVersion',1),('targetHash',digest('old')),('duplicateAtomIds',['new']),('replaces',{'new':'old'}),('replaces',[]),('duplicateAtomIds',{})]:
   original=deepcopy(self.proposal);self.proposal[key]=value;self.review['proposalHash']=digest(self.proposal)
   with self.subTest(key=key,value=value):
    with self.assertRaises(LiveThemeConflict):self.check()
   self.proposal=original
 def test_related_branches_require_applicable_semantic_predicates(self):
  for relation in ['supplement','duplicate','correction']:
   self.proposal.update(relation=relation,targetTopicId='old-topic',targetVersion=1,targetHash=digest('old'),duplicateAtomIds=['new'] if relation=='duplicate' else [],replaces={'new':'old'} if relation=='correction' else {},title='主题',summary='共同事实')
   if relation=='correction':
    self.material['atoms'][0].update(evidenceIds=['new-e'],evidence=[dict(evidenceId='new-e',text='更正一下，之前说错了。')])
    self.proposal['correctionEvidence']={'new':dict(evidenceId='new-e',quote='更正一下，之前说错了。')}
    self.proposal['inputHash']=digest(self.material);self.review['inputHash']=digest(self.material)
   self.review.update(proposalHash=digest(self.proposal),sameSubjectEvent=True,compatibleTime=True,correctionsResolved=True,summarySupported=True,correctionIntentSupported=True)
   self.assertIsNotNone(self.check())
   for key in ['sameSubjectEvent','compatibleTime','correctionsResolved','summarySupported']:
    with self.subTest(relation=relation,key=key):
     self.review[key]=False
     if key=='correctionsResolved' and relation!='correction':self.assertIsNotNone(self.check())
     else:self.assertIsNone(self.check())
     self.review[key]=True
 def test_transport_roundtrip_with_real_observed_predicates(self):
  calls=[]
  def handler(req):
   body=json.loads(req.content);data=json.loads(body['messages'][1]['content']);calls.append(data)
   payload=dict(self.review,inputHash=data['inputHash'],proposalHash=data['proposalHash'])
   return httpx.Response(200,request=req,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(payload)}}]})
  p=DeepSeekLiveThemeProvider(Settings(deepseek_api_key='local',deepseek_base_url='https://controlled.invalid'),transport=httpx.MockTransport(handler))
  stage,request=p.prepare_relation(material=self.material,proposal=self.proposal)
  response=p.request_prepared(stage=stage,request=request)
  self.assertIsNotNone(p.validate_relation_payload(material=self.material,proposal=self.proposal,payload=response.payload));self.assertEqual(len(calls),1)
if __name__=='__main__':unittest.main()
