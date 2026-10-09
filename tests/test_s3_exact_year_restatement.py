import json,unittest
from pathlib import Path
from copy import deepcopy
from dataclasses import replace
from itertools import permutations
from tests import test_owner_truth_memory_changeset as h
from app.domain.owner_truth.contracts import MemoryKind
from app.domain.owner_truth.ontology import enrich_memory_payload_v5
from app.domain.owner_truth.memory_changeset import build_memory_changeset,build_memory_changeset_proposal,OwnerTruthMemoryChangeOperationKind as Kind

DATA=json.loads((Path(__file__).parent/'fixtures/s3_same_statement_class_drift.json').read_text())
class ExactYearRestatementTests(unittest.TestCase):
 setUp=h.OwnerTruthMemoryChangeSetTests.setUp
 _content=h.OwnerTruthMemoryChangeSetTests._content
 _candidate=h.OwnerTruthMemoryChangeSetTests._candidate
 _current=h.OwnerTruthMemoryChangeSetTests._current
 def current(self,name):
  v=DATA[name];return replace(self._current(content=self._content(),source_id='00000000-0000-4000-8000-000000000011'),memory_kind=MemoryKind(v['kind']),content=deepcopy(v['content']))
 def candidate(self,name):
  v=DATA[name];b=self._candidate(content=self._content());c=deepcopy(v['content']);return replace(b,memory_kind=MemoryKind(v['kind']),content_hash=h._hash(c),payload={**b.payload,'content':c})
 def result(self,candidate,target):return build_memory_changeset(candidate=candidate,current_memories=(target,),base_memory_revision=3)
 def test_real_provider_kind_and_year_precision_drift_targets_recent_in_both_orders(self):
  old=self.current('period-2016');recent=self.current('period-2025');new=self.candidate('period-repeat')
  for order in permutations((old,recent)):
   p=build_memory_changeset_proposal(candidate=new,current_memories=order,base_memory_revision=3)
   self.assertEqual(p.change_set.operation.kind,Kind.ADD_EVIDENCE)
   self.assertEqual(p.change_set.operation.target_memory_id,recent.memory_id)
   self.assertEqual(p.rendered_operations[0]['factDiff']['before'],p.rendered_operations[0]['factDiff']['after'])
 def test_reverse_kind_drift_preserves_existing_knowledge(self):
  self.assertEqual(self.result(self.candidate('period-2025'),self.current('period-repeat')).operation.kind,Kind.ADD_EVIDENCE)
 def test_same_kind_precision_drift_is_not_a_new_period(self):
  target=self.current('period-repeat');c=self.candidate('period-repeat');content=deepcopy(c.content);content['qualifiers']['validTime']['precision']='unknown';c=replace(c,content_hash=h._hash(content),payload={**c.payload,'content':content})
  self.assertEqual(self.result(c,target).operation.kind,Kind.ADD_EVIDENCE)
 def test_ambiguous_equivalent_targets_do_not_choose_one(self):
  a=self.current('period-2025');b=self.current('period-repeat');c=self.candidate('period-repeat')
  for order in permutations((a,b)):
   result=build_memory_changeset(candidate=c,current_memories=order,base_memory_revision=3)
   self.assertEqual(result.operation.kind,Kind.ADD);self.assertIsNone(result.operation.target_memory_id)
 def test_business_differences_never_become_cross_kind_evidence(self):
  variants=[('subject','different-person'),('polarity','negative'),('strengthExpression','最喜欢'),('scenario','加班'),('place',{'label':'学校'}),('year','2024年'),('bound','2025-06-01'),('extra','和家人一起喝'),('relative','最近两年')]
  for field,value in variants:
   with self.subTest(field=field):
    c=self.candidate('period-repeat');content=deepcopy(c.content)
    if field=='subject':content['claimSubjectId']=value
    elif field=='year':content['qualifiers']['validTime']['expression']=value
    elif field=='bound':content['qualifiers']['validTime']['start']=value
    elif field=='extra':content['exceptions']=[value]
    elif field=='relative':content['qualifiers']['validTime']['expression']=value
    else:content['qualifiers'][field]=value
    if field=='polarity':content['statement']=content['statement'].replace('喜欢','不喜欢')
    content=enrich_memory_payload_v5(kind=c.memory_kind,payload=content)
    c=replace(c,content_hash=h._hash(content),payload={**c.payload,'content':content});self.assertNotIn(self.result(c,self.current('period-2025')).operation.kind,(Kind.ADD_EVIDENCE,Kind.DUPLICATE))
 def test_explicit_correction_does_not_use_restatement_shortcut(self):
  c=self.candidate('period-repeat');c=replace(c,payload={**c.payload,'reviewMode':'correction'})
  self.assertNotEqual(self.result(c,self.current('period-2025')).operation.kind,Kind.ADD_EVIDENCE)

 def test_same_evidence_does_not_create_another_version(self):
  target=self.current('period-2025');c=self.candidate('period-repeat')
  c=replace(c,source_id=target.evidence_refs[0]['sourceId'],payload={**c.payload,'evidenceRefs':list(target.evidence_refs)})
  self.assertEqual(self.result(c,target).operation.kind,Kind.DUPLICATE)
 def test_relative_year_expression_is_not_collapsed(self):
  from app.domain.owner_truth.memory_changeset import _exact_year_preference_shape
  value=deepcopy(DATA['period-repeat']['content']);value['statement']='去年，我喜欢喝乌龙茶。';value['qualifiers']['validTime']['expression']='去年'
  self.assertIsNone(_exact_year_preference_shape(value))
