from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json, unittest
from test_owner_truth_person_memory_model import _entry
from app.domain.owner_truth.person_memory_model import build_person_memory_model
from app.domain.owner_truth.fact_representation import explicit_year, formal_representation_match, coalesce_source_memories
from app.domain.owner_truth.memory_changeset import build_memory_changeset, OwnerTruthMemoryChangeOperationKind as Kind
from app.domain.owner_truth.contracts import MemoryKind
from tests import test_owner_truth_memory_changeset as h
DATA=json.loads((Path(__file__).parent/'fixtures/s4_device_run02.json').read_text())
def entries(names):
 return [_entry(suffix=i+1,kind=DATA[n]['candidateKind'],content=deepcopy(DATA[n]['content']),schema_version=DATA[n]['contentSchemaVersion']) for i,n in enumerate(names)]
def blocks(rows):
 return [b for s in build_person_memory_model(rows,biography_topics=True)['biographyProjection']['sections'] for b in s['blocks']]
class ProviderShapeTests(unittest.TestCase):
 setUp=h.OwnerTruthMemoryChangeSetTests.setUp
 _content=h.OwnerTruthMemoryChangeSetTests._content
 _candidate=h.OwnerTruthMemoryChangeSetTests._candidate
 _current=h.OwnerTruthMemoryChangeSetTests._current
 def proposal(self,old,new):
  current=replace(self._current(content=self._content(),source_id='00000000-0000-4000-8000-000000000011'),memory_kind=MemoryKind(old['candidateKind']),content=deepcopy(old['content']))
  base=self._candidate(content=self._content());candidate=replace(base,memory_kind=MemoryKind(new['candidateKind']),content_hash=h._hash(new['content']),payload={**base.payload,'content':deepcopy(new['content'])})
  return build_memory_changeset(candidate=candidate,current_memories=[current],base_memory_revision=3)
 def test_exact_device_origami_payloads_group_without_mutation(self):
  rows=entries(['origami-practice','origami-storage']);before=deepcopy(rows);bs=blocks(rows)
  self.assertEqual(len(bs),1);self.assertEqual(len(bs[0]['evidence']),2);self.assertEqual(rows,before)
  self.assertEqual({r['memoryVersionId'] for r in bs[0]['evidence']},{e['memoryVersionId'] for e in rows})
 def test_storage_location_variants_and_new_nouns(self):
  for noun in ['折纸','陶笛','书法']:
   for verb in ['放在','放进','放入']:
    for label in ['',noun,'白色收纳盒里','白色收纳盒']:
     rows=entries(['origami-practice','origami-storage'])
     for row in rows:row['content']=json.loads(json.dumps(row['content'],ensure_ascii=False).replace('折纸',noun))
     c=rows[1]['content'];c['statement']=c['event']=c['semantic']['narrative']='我把'+noun+verb+'白色收纳盒里。';c['object']={'label':label} if label else None
     self.assertEqual(len(blocks(rows)),1,(noun,verb,label))
 def test_storage_conflicts_do_not_merge(self):
  for label,extra in [('黑色盒子',{}),('白色收纳盒里',{'entityId':'different-entity'})]:
   rows=entries(['origami-practice','origami-storage']);rows[1]['content']['object']={'label':label,**extra};self.assertEqual(len(blocks(rows)),2)
  for text in ['我把陶笛放在白色收纳盒里。','我把折纸和陶笛放在白色收纳盒里。','我把它放在白色收纳盒里。','朋友把折纸放在白色收纳盒里。']:
   rows=entries(['origami-practice','origami-storage']);c=rows[1]['content'];c['statement']=c['event']=c['semantic']['narrative']=text;self.assertEqual(len(blocks(rows)),2,text)
 def test_actual_period_changeset_both_orders(self):
  for a,b in [('coffee-2017','coffee-2024'),('coffee-2024','coffee-2017')]:
   old,new=deepcopy(DATA[a]),deepcopy(DATA[b]);before=deepcopy((old,new));self.assertEqual(self.proposal(old,new).operation.kind,Kind.TEMPORAL_CHANGE);self.assertEqual((old,new),before)
 def test_same_year_verb_object_variants_add_evidence(self):
  old=deepcopy(DATA['coffee-2017']);new=json.loads(json.dumps(DATA['coffee-2024']).replace('2023','2018'))
  self.assertEqual(self.proposal(old,new).operation.kind,Kind.ADD_EVIDENCE);self.assertEqual(self.proposal(new,old).operation.kind,Kind.ADD_EVIDENCE)
 def test_year_and_preference_cartesian_variants(self):
  for noun in ['咖啡','红茶','豆浆']:
   a=json.loads(json.dumps(DATA['coffee-2017'],ensure_ascii=False).replace('咖啡',noun))['content']
   for predicate in ['喜欢','喜欢喝','liked','likes']:
    for label in [noun,'喝'+noun]:
     for start,end in [(None,None),('2023',None),('2023','2023')]:
      b=json.loads(json.dumps(DATA['coffee-2024'],ensure_ascii=False).replace('咖啡',noun))['content'];b['predicate']=predicate;b['object']['label']=label;b['qualifiers']['validTime'].update(start=start,end=end)
      self.assertTrue(formal_representation_match(a,b,different_years=True),(noun,predicate,label,start,end));self.assertTrue(formal_representation_match(b,a,different_years=True))
 def test_true_intervals_precision_and_cutoff_not_normalized(self):
  base=deepcopy(DATA['coffee-2024']['content'])
  for start,end,precision,expression in [(None,'2023','year','2023年'),('2022','2023','year','2023年'),('2023','2024','year','2023年'),('2023-01','2023-01','month','2023年'),('2023','2023','day','2023年'),('2023','2023','year','2022年')]:
   c=deepcopy(base);c['qualifiers']['validTime'].update(start=start,end=end,precision=precision,expression=expression);self.assertIsNone(explicit_year(c))
  for text in ['截至2023年，我喜欢喝咖啡。','从2023年开始，我喜欢喝咖啡。','2023年至2024年，我喜欢喝咖啡。']:
   c=deepcopy(base);c['statement']=text;self.assertIsNone(explicit_year(c));self.assertFalse(formal_representation_match(DATA['coffee-2017']['content'],c,different_years=True))
 def test_semantic_qualifications_remain_distinct(self):
  a=deepcopy(DATA['coffee-2017']['content']);base=deepcopy(DATA['coffee-2024']['content'])
  for key,value in [('polarity','negative'),('strengthExpression','最喜欢'),('superlativeAsserted',True),('scenario','工作时'),('currentApplicability','current')]:
   b=deepcopy(base);b['qualifiers'][key]=value;self.assertFalse(formal_representation_match(a,b,different_years=True),key)
  for key,value in [('claimSubjectId','other'),('exceptions',['晚上不喝']),('predicate','disliked'),('frequency','每天')]:
   b=deepcopy(base);b[key]=value;self.assertFalse(formal_representation_match(a,b,different_years=True),key)
  for obj in [{'label':'茶'},{'label':'咖啡','entityId':'different'},{'label':'咖啡','category':'different'}]:
   b=deepcopy(base);b['object']=obj;self.assertFalse(formal_representation_match(a,b,different_years=True))
 def test_different_years_never_coalesce(self):
  rows=[{**deepcopy(DATA[n]),'memoryKind':DATA[n]['candidateKind']} for n in ['coffee-2017','coffee-2024']];before=deepcopy(rows);self.assertEqual(len(coalesce_source_memories(rows)),2);self.assertEqual(rows,before)
 def test_real_payload_topic_update_retains_photo(self):
  rows=entries(['origami-practice','origami-storage']);photo=_entry(suffix=3,kind='experience',content={'summary':'我打算整理照片。'});before=blocks([rows[0],photo]);after=blocks(rows+[photo]);self.assertEqual(len(after),2)
  self.assertEqual(next(b for b in before if '照片' in b['text']),next(b for b in after if '照片' in b['text']));self.assertEqual(next(b for b in before if '折纸' in b['text'])['topicId'],next(b for b in after if '折纸' in b['text'])['topicId'])

 def test_actual_provider_verbatim_response_stays_quality_fallback(self):
  from app.domain.owner_truth.person_biography import apply_composition
  fixture=json.loads((Path(__file__).parent/'fixtures/s4_verbatim_provider_run02.json').read_text())
  with self.assertRaisesRegex(ValueError,'compositionNotIntegrated'):
   apply_composition(fixture['block'],fixture['response'])

 def test_actual_provider_scope_rejection_is_not_overridden(self):
  from app.domain.owner_truth.person_biography import apply_composition
  fixture=json.loads((Path(__file__).parent/'fixtures/s4_scope_rejected_provider_run02.json').read_text())
  with self.assertRaisesRegex(ValueError,'semantic audit did not approve'):
   apply_composition(fixture['block'],fixture['response'])

 def test_full_shape_ambiguous_targets_never_choose_by_order(self):
  from itertools import permutations
  from app.domain.owner_truth.memory_changeset import build_memory_changeset_proposal
  old=deepcopy(DATA['coffee-2017']);new=json.loads(json.dumps(DATA['coffee-2024']).replace('2023','2018'))
  currents=[replace(self._current(content=deepcopy(old['content']),source_id='00000000-0000-4000-8000-000000000011'),memory_kind=MemoryKind(old['candidateKind'])) for _ in range(2)]
  base=self._candidate(content=self._content());candidate=replace(base,memory_kind=MemoryKind(new['candidateKind']),content_hash=h._hash(new['content']),payload={**base.payload,'content':deepcopy(new['content'])})
  proposals=[build_memory_changeset_proposal(candidate=candidate,current_memories=order,base_memory_revision=3) for order in permutations(currents)]
  self.assertEqual(proposals[0].proposal_hash,proposals[1].proposal_hash)
  self.assertTrue(all(p.change_set.operation.kind==Kind.ADD and p.change_set.operation.target_memory_id is None for p in proposals))
