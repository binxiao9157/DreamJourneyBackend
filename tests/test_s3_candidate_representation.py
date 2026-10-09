import json,unittest
from copy import deepcopy
from dataclasses import replace
from itertools import permutations
from pathlib import Path
from app.services.deepseek import DeepSeekTextMemoryOrganizationProxy as Proxy
from app.domain.owner_truth.contracts import MemoryKind
from app.domain.owner_truth.memory_changeset import build_memory_changeset,OwnerTruthMemoryChangeOperationKind as Kind
from tests import test_owner_truth_memory_changeset as h
DATA=json.loads((Path(__file__).parent/'fixtures/s3_same_source_candidates.json').read_text())
def raw(name):
 return [dict(memoryKind=x['payload']['candidateKind'],content=deepcopy(x['payload']['content'])) for x in DATA[name]]
def parse(items,subject=False):
 return Proxy.parse_organization(json.dumps(dict(memories=items),ensure_ascii=False),require_subject_role=subject)['memories']
class CandidateRepresentationTests(unittest.TestCase):
 def test_real_same_source_candidates_collapse_without_losing_year_or_assertion(self):
  for name in ['period-2016','period-2025']:
   outputs=[]
   for order in permutations(raw(name)):
    result=parse(order);self.assertEqual(len(result),1);self.assertEqual(result[0]['content']['statement'],raw(name)[0]['content']['statement']);self.assertEqual(result[0]['content']['factType'],'preference');outputs.append(result)
   self.assertEqual(*outputs)
 def test_idempotence_and_input_not_mutated(self):
  items=raw('period-2025');before=deepcopy(items);result=parse(items);self.assertEqual(items,before);self.assertEqual(parse(result+result),result)
 def test_same_text_different_scopes_not_dropped_even_same_kind(self):
  a=raw('period-2025')[0];b=deepcopy(a);a['content']['qualifiers']['scenario']='在家';b['content']['qualifiers']['scenario']='在学校'
  self.assertEqual(len(parse([a,b])),2)
 def test_distinct_roles_subjects_and_provenance_not_merged(self):
  for field in ['subjectRole','claimSubjectId','speakerPersonId']:
   a=raw('period-2025')[0];b=deepcopy(a)
   if field=='subjectRole':a[field]='memorySubject';b[field]='reporterSelf'
   elif field=='speakerPersonId':b['content']['provenance'][field]='other'
   else:b['content'][field]='other'
   self.assertEqual(len(parse([a,b],subject=field=='subjectRole')),2,field)
 def test_conflicting_objects_and_scopes_not_merged(self):
  for field,value in [('object',{'label':'咖啡'}),('place',{'label':'学校'}),('scenario','工作时'),('strengthExpression','最喜欢'),('bound','2025-06-01')]:
   a,b=raw('period-2025')
   if field=='object':b['content'][field]=value
   elif field=='bound':b['content']['qualifiers']['validTime']['start']=value
   else:b['content']['qualifiers'][field]=value
   # A richer compatible representation may dominate; different explicit values may not.
   if field in ('place','scenario'):a['content']['qualifiers'][field]='家里'
   self.assertEqual(len(parse([a,b])),2,field)
 def test_different_periods_and_plans_remain_separate(self):
  self.assertEqual(len(parse([raw('period-2016')[0],raw('period-2025')[1]])),2)
  a=raw('period-2025')[0];b=deepcopy(a);a['content']['statement']=a['content']['event']='我打算学习陶艺。';b['content']['statement']=b['content']['event']='我已经学会陶艺。'
  self.assertEqual(len(parse([a,b])),2)
 def test_complementary_business_details_not_silently_lost(self):
  a,b=raw('period-2025');a['content']['location']='家里';b['content']['exceptions']=['晚上不喝'];self.assertEqual(len(parse([a,b])),2)
 def test_metadata_union_is_preserved(self):
  a,b=raw('period-2025');b['content']['facets']['habits']=[{'value':'喝乌龙茶','confidence':1.0,'evidenceMode':'ownerStated'}]
  result=parse([a,b]);self.assertEqual(len(result),1);self.assertEqual(result[0]['content']['facets']['habits'],b['content']['facets']['habits'])
 def test_emotion_is_not_collapsed_into_fact(self):
  a=raw('period-2025')[0];b=dict(memoryKind='emotion',content={'emotion':'开心','expression':a['content']['statement'],'trigger':None,'intensity':None})
  self.assertEqual(len(parse([a,b])),2)
class PeriodRepresentationTests(unittest.TestCase):
 setUp=h.OwnerTruthMemoryChangeSetTests.setUp
 _content=h.OwnerTruthMemoryChangeSetTests._content
 _candidate=h.OwnerTruthMemoryChangeSetTests._candidate
 _current=h.OwnerTruthMemoryChangeSetTests._current
 def current(self,name,index=0):
  v=raw(name)[index];return replace(self._current(content=self._content(),source_id='00000000-0000-4000-8000-000000000011'),memory_kind=MemoryKind(v['memoryKind']),content=v['content'])
 def candidate(self,name,index=0):
  v=raw(name)[index];b=self._candidate(content=self._content());return replace(b,memory_kind=MemoryKind(v['memoryKind']),content_hash=h._hash(v['content']),payload={**b.payload,'content':v['content']})
 def result(self,c,targets):return build_memory_changeset(candidate=c,current_memories=targets,base_memory_revision=3)
 def test_legacy_generic_event_can_link_disjoint_year_without_rewriting_it(self):
  old=self.current('period-2016');before=deepcopy(old.content);c=self.candidate('period-2025')
  self.assertEqual(self.result(c,[old]).operation.kind,Kind.TEMPORAL_CHANGE);self.assertEqual(old.content,before)
 def test_same_year_generic_event_can_add_evidence(self):
  old=self.current('period-2016');c=self.candidate('period-2016',1);p=self.result(c,[old]);self.assertEqual(p.operation.kind,Kind.ADD_EVIDENCE)
 def test_exact_recent_has_priority_in_any_order(self):
  old=self.current('period-2016');recent=self.current('period-2025');c=self.candidate('period-repeat')
  for order in permutations([old,recent]):self.assertEqual(self.result(c,order).operation.target_memory_id,recent.memory_id)
 def test_ambiguous_old_periods_not_arbitrarily_selected(self):
  old=self.current('period-2016');other=replace(old,memory_id=self._current(content=self._content(),source_id='00000000-0000-4000-8000-000000000011').memory_id)
  p=self.result(self.candidate('period-2025'),[old,other]);self.assertEqual(p.operation.kind,Kind.ADD);self.assertIsNone(p.operation.target_memory_id)
 def test_explicit_changes_and_subject_mismatch_do_not_use_generic_bridge(self):
  c=self.candidate('period-2025');old=self.current('period-2016')
  for field in ['claimSubjectId','statement']:
   body=deepcopy(c.content);body[field]='不同的人' if field=='claimSubjectId' else '2025年，我不喜欢喝乌龙茶。'
   if field=='statement':
    body['event']=body['statement']
   from app.domain.owner_truth.ontology import enrich_memory_payload_v5
   body=enrich_memory_payload_v5(kind=c.memory_kind,payload=body)
   v=replace(c,content_hash=h._hash(body),payload={**c.payload,'content':body})
   self.assertNotIn(self.result(v,[old]).operation.kind,(Kind.TEMPORAL_CHANGE,Kind.ADD_EVIDENCE))

class SourceYearTests(unittest.TestCase):
 def test_real_response_restores_literal_year_without_inference(self):
  records=json.loads((Path(__file__).parent/'fixtures/s3_real_provider_v6.json').read_text())
  record=records[0];response=record['raw'][0]['choices'][0]['message']['content']
  result=Proxy.parse_organization(response,source_text=record['input'])['memories']
  self.assertEqual(len(result),1)
  self.assertEqual(result[0]['content']['statement'],record['input'])
  self.assertEqual(result[0]['content']['event'],record['input'])
  self.assertEqual(result[0]['content']['qualifiers']['validTime']['expression'],'2017年')
 def test_year_not_borrowed_from_other_sentence_or_ambiguous_occurrence(self):
  from app.domain.owner_truth.fact_representation import restore_explicit_year_prefix as restore
  body={'statement':'我喜欢阅读科幻小说','qualifiers':{'validTime':{'expression':'2017年','precision':'unknown'}}}
  for source in ['2017年，我去北京。我喜欢阅读科幻小说。','2018年，我喜欢阅读科幻小说。','2017年，我喜欢阅读科幻小说。2017年，我喜欢阅读科幻小说。','如果到了2017年，我喜欢阅读科幻小说。']:
   self.assertEqual(restore(body,source_text=source),body,source)
 def test_distinct_claims_not_force_merged_to_satisfy_count(self):
  records=json.loads((Path(__file__).parent/'fixtures/s3_real_provider_v6.json').read_text())
  record=next(x for x in records if x['case']=='goal')
  result=Proxy.parse_organization(record['raw'][0]['choices'][0]['message']['content'],source_text=record['input'])['memories']
  # Literal overlap alone is insufficient: goal-positive and current-negative
  # have different assertions. Prompt quality must not weaken the safety rule.
  self.assertEqual(len(result),2)
  self.assertTrue(any('还没有开始学习' in x['content']['statement'] for x in result))
